"""DB-001: SQLite connection management and versioned migrations.

Security rules enforced here and in repository.py:
- Every SQL statement is a literal string at its call site; values use ``?``
  parameter binding. No concatenation, ``format`` or f-strings build SQL.
- The database file must live under the configured data directory; callers
  validate the path before reaching this module.
- Each migration applies inside one transaction; a failing migration rolls
  back completely and leaves the previous database intact.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Sequence

BUSY_TIMEOUT_MS = 5_000


class Migration:
    """One versioned, transactionally-applied schema step.

    ``apply`` issues DDL through literal ``connection.execute("...")`` calls
    so every SQL text is statically fixed.
    """

    def __init__(self, version: int, name: str, apply: Callable[[sqlite3.Connection], None]) -> None:
        self.version = version
        self.name = name
        self.apply = apply


def _migration_0001(connection: sqlite3.Connection) -> None:
    """Batch-04 initial schema (docs/contract-freezes.md Freeze 3).

    Tables are namespaced per analysis_id: a rerun of the same snapshot
    creates a new analysis and never rewrites rows of a previous one.
    ``snapshots`` is an insert-only cache of immutable snapshot identities.
    """

    connection.execute(
        "CREATE TABLE repositories ("
        " repository_id TEXT PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " canonical_path TEXT NOT NULL UNIQUE,"
        " registered_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE analyses ("
        " analysis_id TEXT PRIMARY KEY,"
        " idempotency_key TEXT NOT NULL UNIQUE,"
        " request_hash TEXT NOT NULL,"
        " repository_id TEXT NOT NULL REFERENCES repositories(repository_id),"
        " base_ref TEXT NOT NULL,"
        " target_ref TEXT NOT NULL,"
        " rules_version TEXT NOT NULL,"
        " snapshot_id TEXT,"
        " status TEXT NOT NULL,"
        " resumable INTEGER NOT NULL DEFAULT 1,"
        " cancel_requested INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL,"
        " updated_at TEXT NOT NULL,"
        " failure_reason TEXT)"
    )
    connection.execute("CREATE INDEX idx_analyses_status ON analyses(status)")
    connection.execute("CREATE INDEX idx_analyses_snapshot ON analyses(snapshot_id)")
    connection.execute(
        "CREATE TABLE stages ("
        " analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),"
        " stage TEXT NOT NULL,"
        " status TEXT NOT NULL,"
        " detail TEXT,"
        " started_at TEXT,"
        " completed_at TEXT,"
        " PRIMARY KEY (analysis_id, stage))"
    )
    connection.execute(
        "CREATE TABLE stage_outputs ("
        " analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),"
        " stage TEXT NOT NULL,"
        " output_json TEXT NOT NULL,"
        " PRIMARY KEY (analysis_id, stage))"
    )
    connection.execute(
        "CREATE TABLE snapshots ("
        " snapshot_id TEXT PRIMARY KEY,"
        " repository_id TEXT NOT NULL,"
        " report_json TEXT NOT NULL,"
        " created_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE analysis_results ("
        " analysis_id TEXT PRIMARY KEY REFERENCES analyses(analysis_id),"
        " snapshot_id TEXT NOT NULL,"
        " result_json TEXT NOT NULL,"
        " persisted_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE maps ("
        " analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),"
        " side TEXT NOT NULL,"
        " snapshot_id TEXT NOT NULL,"
        " map_json TEXT NOT NULL,"
        " PRIMARY KEY (analysis_id, side))"
    )
    connection.execute(
        "CREATE TABLE decisions ("
        " analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),"
        " snapshot_id TEXT NOT NULL,"
        " path TEXT NOT NULL,"
        " status TEXT NOT NULL,"
        " reason TEXT NOT NULL,"
        " rule_id TEXT,"
        " language TEXT,"
        " bytes INTEGER,"
        " PRIMARY KEY (analysis_id, path))"
    )
    connection.execute(
        "CREATE INDEX idx_decisions_status ON decisions(analysis_id, status)"
    )
    connection.execute(
        "CREATE TABLE findings ("
        " analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),"
        " finding_id TEXT NOT NULL,"
        " snapshot_id TEXT NOT NULL,"
        " category TEXT NOT NULL,"
        " kind TEXT NOT NULL,"
        " impact TEXT NOT NULL,"
        " reliability TEXT NOT NULL,"
        " rule_id TEXT,"
        " unresolved_reason TEXT,"
        " evidence_ids_json TEXT NOT NULL,"
        " PRIMARY KEY (analysis_id, finding_id))"
    )
    connection.execute("CREATE INDEX idx_findings_snapshot ON findings(snapshot_id)")
    connection.execute(
        "CREATE INDEX idx_findings_category ON findings(analysis_id, category)"
    )
    connection.execute("CREATE INDEX idx_findings_rule ON findings(analysis_id, rule_id)")
    connection.execute(
        "CREATE TABLE evidence ("
        " analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),"
        " evidence_id TEXT NOT NULL,"
        " snapshot_id TEXT NOT NULL,"
        " path TEXT NOT NULL,"
        " side TEXT NOT NULL,"
        " start_line INTEGER NOT NULL,"
        " end_line INTEGER NOT NULL,"
        " snippet TEXT NOT NULL,"
        " source_kind TEXT NOT NULL,"
        " rule_id TEXT,"
        " commit_sha TEXT,"
        " PRIMARY KEY (analysis_id, evidence_id))"
    )
    connection.execute("CREATE INDEX idx_evidence_snapshot ON evidence(snapshot_id)")
    connection.execute(
        "CREATE TABLE reviews ("
        " analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),"
        " finding_id TEXT NOT NULL,"
        " state TEXT NOT NULL,"
        " note TEXT NOT NULL DEFAULT '',"
        " updated_at TEXT NOT NULL,"
        " PRIMARY KEY (analysis_id, finding_id))"
    )


def _migration_0002(connection: sqlite3.Connection) -> None:
    """Batch-04-R1/R2：引用完整性与恢复输入列。

    - 迁移前在同一事务内检测悬空旧 review（引用不存在的 finding）：
      存在则显式拒绝迁移并整体回滚——历史不一致不等于删除授权，
      人工 note 必须原地保留（B04-R2-01）。错误只含数量，不输出 note 原文。
    - reviews 重建为指向 findings(analysis_id, finding_id) 的复合外键。
    - analyses 增加 manifest_status/manifest_digest（映射配置绑定）与
      options_json（分析选项，恢复输入的一部分）。
    """

    dangling = connection.execute(
        "SELECT COUNT(*) AS n FROM reviews r WHERE NOT EXISTS ("
        " SELECT 1 FROM findings f"
        " WHERE f.analysis_id = r.analysis_id AND f.finding_id = r.finding_id)"
    ).fetchone()["n"]
    if dangling:
        raise RuntimeError(
            f"migration 0002 refused: {dangling} review rows reference missing "
            "findings; resolve or export them manually before upgrading "
            "(automatic deletion of human review notes is not allowed)"
        )
    connection.execute(
        "CREATE TABLE reviews_new ("
        " analysis_id TEXT NOT NULL,"
        " finding_id TEXT NOT NULL,"
        " state TEXT NOT NULL,"
        " note TEXT NOT NULL DEFAULT '',"
        " updated_at TEXT NOT NULL,"
        " PRIMARY KEY (analysis_id, finding_id),"
        " FOREIGN KEY (analysis_id, finding_id)"
        " REFERENCES findings(analysis_id, finding_id))"
    )
    connection.execute(
        "INSERT INTO reviews_new (analysis_id, finding_id, state, note, updated_at)"
        " SELECT r.analysis_id, r.finding_id, r.state, r.note, r.updated_at"
        " FROM reviews r"
    )
    connection.execute("DROP TABLE reviews")
    connection.execute("ALTER TABLE reviews_new RENAME TO reviews")
    connection.execute("ALTER TABLE analyses ADD COLUMN manifest_status TEXT")
    connection.execute("ALTER TABLE analyses ADD COLUMN manifest_digest TEXT")
    connection.execute("ALTER TABLE analyses ADD COLUMN options_json TEXT")


def _migration_0003(connection: sqlite3.Connection) -> None:
    """Batch-06：解释持久化与远程按次授权审计。

    - explanations 属于一个 analysis（外键），status 只有
      completed/failed（由应用层保证；校验失败落 failed，绝不冒充完成）。
    - remote_authorizations 记录每次远程授权：analysis、provider、endpoint
      主机、发送范围（evidence ID 列表 + 输入摘要）、授权与实际使用时间；
      `used_at IS NULL` 表示已授权未消费。授权一次性：消费即打标。
    """

    connection.execute(
        "CREATE TABLE explanations ("
        " explanation_id TEXT PRIMARY KEY,"
        " analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),"
        " subject_type TEXT NOT NULL,"
        " subject_id TEXT NOT NULL,"
        " provider TEXT NOT NULL,"
        " model TEXT NOT NULL,"
        " status TEXT NOT NULL,"
        " claims_json TEXT NOT NULL,"
        " uncertainty TEXT NOT NULL DEFAULT '',"
        " errors_json TEXT NOT NULL DEFAULT '[]',"
        " context_hash TEXT NOT NULL,"
        " duration_ms INTEGER,"
        " authorization_id INTEGER,"
        " created_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX idx_explanations_subject"
        " ON explanations(analysis_id, subject_type, subject_id)"
    )
    connection.execute(
        "CREATE TABLE remote_authorizations ("
        " authorization_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),"
        " provider TEXT NOT NULL,"
        " endpoint_host TEXT NOT NULL,"
        " scope_evidence_ids_json TEXT NOT NULL,"
        " context_hash TEXT NOT NULL,"
        " granted_at TEXT NOT NULL,"
        " used_at TEXT)"
    )
    connection.execute(
        "CREATE INDEX idx_authorizations_analysis"
        " ON remote_authorizations(analysis_id)"
    )


MIGRATIONS: Sequence[Migration] = (
    Migration(1, "0001_initial", _migration_0001),
    Migration(2, "0002_integrity_and_manifest", _migration_0002),
    Migration(3, "0003_explanations", _migration_0003),
)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with the pragmas this layer relies on.

    WAL keeps readers unblocked while the worker writes; foreign keys are
    enforced; a busy timeout absorbs short writer contention.
    """

    connection = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    # BUSY_TIMEOUT_MS = 5000（写死字面量，保持 SQL 文本固定）
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def ensure_wal(db_path: Path) -> None:
    connection = connect(db_path)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.commit()
    finally:
        connection.close()


def current_version(db_path: Path) -> int:
    connection = connect(db_path)
    try:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("schema_migrations",),
        ).fetchone()
        if table is None:
            return 0
        row = connection.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"]) if row is not None and row["v"] is not None else 0
    finally:
        connection.close()


def migrate(db_path: Path, migrations: Sequence[Migration] = MIGRATIONS) -> int:
    """Apply pending migrations; returns the resulting schema version.

    Each migration runs inside ONE explicit transaction covering DDL, DML
    and the schema_migrations row (Python's sqlite3 does not auto-open
    transactions for DDL, so `with connection:` alone is NOT atomic here).
    On failure the transaction rolls back completely — a partially applied
    migration (e.g. valid CREATE TABLE followed by broken DDL) leaves no
    trace. A database schema newer than this build knows is rejected
    outright instead of being written to.
    """

    ensure_wal(db_path)
    supported = max(migration.version for migration in migrations) if migrations else 0
    connection = connect(db_path)
    connection.isolation_level = None  # 显式事务控制，杜绝隐式提交
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        applied = current_version(db_path)
        if applied > supported:
            raise RuntimeError(
                f"database schema version {applied} is newer than supported "
                f"version {supported}; refusing to write"
            )
        for migration in migrations:
            if migration.version <= applied:
                continue
            if migration.version != applied + 1:
                raise RuntimeError(
                    f"migration gap: expected version {applied + 1}, "
                    f"got {migration.version} ({migration.name})"
                )
            connection.execute("BEGIN IMMEDIATE")
            try:
                migration.apply(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (migration.version, migration.name),
                )
                connection.execute("COMMIT")
            except Exception as error:
                # 任意迁移失败（含业务性拒绝）都显式回滚：旧库保持上一个
                # 版本，写入停止；成功执行过的 DDL/DML 不留痕。
                connection.execute("ROLLBACK")
                raise RuntimeError(
                    f"migration {migration.version} ({migration.name}) failed: {error}"
                ) from error
            applied = migration.version
    finally:
        connection.close()
    return applied


@contextmanager
def reader(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def writer(db_path: Path) -> Iterator[sqlite3.Connection]:
    """One transaction: commits on success, rolls back on any exception."""

    connection = connect(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def validate_db_path(db_path: Path, data_dir: Path) -> Path:
    """The SQLite file must live inside the configured data directory."""

    resolved = db_path.resolve()
    root = data_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(
            f"database path must stay inside the data directory: {db_path}"
        )
    return resolved
