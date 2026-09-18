"""DB-001: analysis repository over SQLite.

All statements use ``?`` parameter binding exclusively — no string
concatenation, ``format`` or f-strings build SQL from external input
(enforced by security review; see db/database.py).

Write methods that the worker treats as checkpoints open exactly one
transaction each so a crash either persists the whole checkpoint or nothing.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .database import reader, writer

_TERMINAL_STATUSES = frozenset(
    {"completed", "completed_with_limits", "failed", "cancelled"}
)
_ACTIVE_STATUSES = frozenset({"queued", "running"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AnalysisRepository:
    """Persistence for repositories, analyses, checkpoints and results."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Repository registry
    # ------------------------------------------------------------------

    def register_repository(
        self, repository_id: str, name: str, canonical_path: str
    ) -> None:
        with writer(self.db_path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO repositories"
                " (repository_id, name, canonical_path, registered_at)"
                " VALUES (?, ?, ?, ?)",
                (repository_id, name, canonical_path, utc_now()),
            )

    def get_repository(self, repository_id: str) -> sqlite3.Row | None:
        with reader(self.db_path) as connection:
            return connection.execute(
                "SELECT repository_id, name, canonical_path, registered_at"
                " FROM repositories WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()

    def list_repositories(self) -> list[sqlite3.Row]:
        with reader(self.db_path) as connection:
            rows = connection.execute(
                "SELECT repository_id, name, canonical_path, registered_at"
                " FROM repositories ORDER BY repository_id"
            ).fetchall()
        return list(rows)

    # ------------------------------------------------------------------
    # Analysis lifecycle
    # ------------------------------------------------------------------

    def create_analysis(
        self,
        *,
        analysis_id: str,
        idempotency_key: str,
        request_hash: str,
        repository_id: str,
        base_ref: str,
        target_ref: str,
        rules_version: str,
        options_json: str | None = None,
    ) -> None:
        now = utc_now()
        with writer(self.db_path) as connection:
            connection.execute(
                "INSERT INTO analyses"
                " (analysis_id, idempotency_key, request_hash, repository_id,"
                "  base_ref, target_ref, rules_version, status, resumable,"
                "  cancel_requested, created_at, updated_at, options_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 1, 0, ?, ?, ?)",
                (
                    analysis_id,
                    idempotency_key,
                    request_hash,
                    repository_id,
                    base_ref,
                    target_ref,
                    rules_version,
                    now,
                    now,
                    options_json,
                ),
            )

    def get_analysis(self, analysis_id: str) -> sqlite3.Row | None:
        with reader(self.db_path) as connection:
            return connection.execute(
                "SELECT * FROM analyses WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()

    def get_analysis_by_key(self, idempotency_key: str) -> sqlite3.Row | None:
        with reader(self.db_path) as connection:
            return connection.execute(
                "SELECT * FROM analyses WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()

    def analysis_status(self, analysis_id: str) -> str | None:
        row = self.get_analysis(analysis_id)
        return None if row is None else str(row["status"])

    def recoverable_analyses(self) -> list[sqlite3.Row]:
        """Non-terminal analyses (re-enqueued after a daemon restart)."""

        with reader(self.db_path) as connection:
            rows = connection.execute(
                "SELECT analysis_id FROM analyses"
                " WHERE status IN ('queued', 'running')"
                " ORDER BY created_at, analysis_id"
            ).fetchall()
        return list(rows)

    def set_status(
        self,
        analysis_id: str,
        status: str,
        failure_reason: str | None = None,
    ) -> bool:
        """CAS 状态迁移：仅当当前仍处于 queued/running 才写入。

        返回是否迁移成功；终态不被覆盖——迟到的 fail/complete/cancel
        不能改写已定局的分析。
        """

        with writer(self.db_path) as connection:
            if failure_reason is None:
                cursor = connection.execute(
                    "UPDATE analyses SET status = ?, updated_at = ?"
                    " WHERE analysis_id = ? AND status IN ('queued', 'running')",
                    (status, utc_now(), analysis_id),
                )
            else:
                cursor = connection.execute(
                    "UPDATE analyses SET status = ?, failure_reason = ?, updated_at = ?"
                    " WHERE analysis_id = ? AND status IN ('queued', 'running')",
                    (status, failure_reason, utc_now(), analysis_id),
                )
            return cursor.rowcount > 0

    def request_cancel(self, analysis_id: str) -> None:
        with writer(self.db_path) as connection:
            connection.execute(
                "UPDATE analyses SET cancel_requested = 1, updated_at = ?"
                " WHERE analysis_id = ?",
                (utc_now(), analysis_id),
            )

    def is_cancel_requested(self, analysis_id: str) -> bool:
        row = self.get_analysis(analysis_id)
        return row is not None and bool(row["cancel_requested"])

    def apply_cancel(self, analysis_id: str) -> None:
        """Cooperative cancel took effect: terminal state, stages closed.

        只把仍处于 queued 的阶段记为 skipped；已完成/已失败的阶段保留原状，
        已终态（completed/failed/cancelled...）的分析不被改写。
        """

        now = utc_now()
        with writer(self.db_path) as connection:
            connection.execute(
                "UPDATE analyses SET status = 'cancelled', updated_at = ?"
                " WHERE analysis_id = ? AND status NOT IN"
                " ('completed', 'completed_with_limits', 'failed', 'cancelled')",
                (now, analysis_id),
            )
            connection.execute(
                "UPDATE stages SET status = 'skipped', completed_at = ?"
                " WHERE analysis_id = ? AND status = 'queued'",
                (now, analysis_id),
            )

    # ------------------------------------------------------------------
    # Stage records
    # ------------------------------------------------------------------

    def ensure_stage_rows(self, analysis_id: str, stages: Sequence[str]) -> None:
        with writer(self.db_path) as connection:
            for stage in stages:
                connection.execute(
                    "INSERT OR IGNORE INTO stages"
                    " (analysis_id, stage, status, started_at, completed_at)"
                    " VALUES (?, ?, 'queued', NULL, NULL)",
                    (analysis_id, stage),
                )

    def upsert_stage(
        self,
        analysis_id: str,
        stage: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        now = utc_now()
        with writer(self.db_path) as connection:
            connection.execute(
                "INSERT INTO stages (analysis_id, stage, status, detail, started_at, completed_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(analysis_id, stage) DO UPDATE SET"
                " status = excluded.status, detail = excluded.detail,"
                " started_at = COALESCE(stages.started_at, excluded.started_at),"
                " completed_at = excluded.completed_at",
                (
                    analysis_id,
                    stage,
                    status,
                    detail,
                    now if status == "running" else None,
                    now if status in ("completed", "failed", "skipped") else None,
                ),
            )

    def stage_records(self, analysis_id: str) -> list[sqlite3.Row]:
        with reader(self.db_path) as connection:
            rows = connection.execute(
                "SELECT stage, status, detail, started_at, completed_at"
                " FROM stages WHERE analysis_id = ? ORDER BY rowid",
                (analysis_id,),
            ).fetchall()
        return list(rows)

    def completed_stages(self, analysis_id: str) -> set[str]:
        return {
            str(row["stage"])
            for row in self.stage_records(analysis_id)
            if row["status"] == "completed"
        }

    # ------------------------------------------------------------------
    # Checkpoints (one transaction each — crash-safe resume boundaries)
    # ------------------------------------------------------------------

    def commit_git_checkpoint(
        self,
        *,
        analysis_id: str,
        repository_id: str,
        snapshot_id: str,
        snapshot_report: dict[str, Any],
    ) -> None:
        now = utc_now()
        report_json = json.dumps(snapshot_report, ensure_ascii=True, sort_keys=True)
        with writer(self.db_path) as connection:
            # 快照身份不可变：只插入，绝不覆盖已存在的快照缓存。
            connection.execute(
                "INSERT OR IGNORE INTO snapshots"
                " (snapshot_id, repository_id, report_json, created_at)"
                " VALUES (?, ?, ?, ?)",
                (snapshot_id, repository_id, report_json, now),
            )
            connection.execute(
                "INSERT INTO stage_outputs (analysis_id, stage, output_json)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(analysis_id, stage) DO UPDATE SET"
                " output_json = excluded.output_json",
                (analysis_id, "git", report_json),
            )
            connection.execute(
                "UPDATE analyses SET snapshot_id = ?, updated_at = ? WHERE analysis_id = ?",
                (snapshot_id, now, analysis_id),
            )
            connection.execute(
                "INSERT INTO stages (analysis_id, stage, status, started_at, completed_at)"
                " VALUES (?, 'git', 'completed', ?, ?)"
                " ON CONFLICT(analysis_id, stage) DO UPDATE SET"
                " status = 'completed', completed_at = excluded.completed_at",
                (analysis_id, now, now),
            )

    def commit_parse_checkpoint(
        self,
        *,
        analysis_id: str,
        snapshot_id: str,
        output: dict[str, Any],
        stage_statuses: dict[str, tuple[str, str | None]],
        manifest_status: str | None = None,
        manifest_digest: str | None = None,
    ) -> None:
        """parse checkpoint：diff、两侧 SoftwareMap 状态、选择决策与映射绑定。

        output 是恢复 rules 阶段所需的完整确定性输入（含 BuildResult 状态）；
        stage_statuses 为 selection/parse 的会话阶段记录。
        """

        now = utc_now()
        output_json = json.dumps(output, ensure_ascii=True, sort_keys=True)
        with writer(self.db_path) as connection:
            connection.execute(
                "INSERT INTO stage_outputs (analysis_id, stage, output_json)"
                " VALUES (?, 'parse', ?)"
                " ON CONFLICT(analysis_id, stage) DO UPDATE SET"
                " output_json = excluded.output_json",
                (analysis_id, output_json),
            )
            for side, key in (("base", "base_state"), ("target", "target_state")):
                map_json = json.dumps(
                    output["maps_state"][key]["software_map"],
                    ensure_ascii=True,
                    sort_keys=True,
                )
                connection.execute(
                    "INSERT INTO maps (analysis_id, side, snapshot_id, map_json)"
                    " VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(analysis_id, side) DO UPDATE SET"
                    " map_json = excluded.map_json",
                    (analysis_id, side, snapshot_id, map_json),
                )
            for decision in output["selection_decisions"]:
                connection.execute(
                    "INSERT INTO decisions"
                    " (analysis_id, snapshot_id, path, status, reason, rule_id, language, bytes)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(analysis_id, path) DO UPDATE SET"
                    " status = excluded.status, reason = excluded.reason,"
                    " rule_id = excluded.rule_id, language = excluded.language,"
                    " bytes = excluded.bytes",
                    (
                        analysis_id,
                        snapshot_id,
                        decision["path"],
                        decision["status"],
                        decision["reason"],
                        decision.get("rule_id"),
                        decision.get("language"),
                        decision.get("bytes"),
                    ),
                )
            connection.execute(
                "UPDATE analyses SET manifest_status = ?, manifest_digest = ?, updated_at = ?"
                " WHERE analysis_id = ?",
                (manifest_status, manifest_digest, now, analysis_id),
            )
            for stage, (status, detail) in stage_statuses.items():
                connection.execute(
                    "INSERT INTO stages (analysis_id, stage, status, detail, started_at, completed_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(analysis_id, stage) DO UPDATE SET"
                    " status = excluded.status, detail = excluded.detail,"
                    " completed_at = excluded.completed_at",
                    (analysis_id, stage, status, detail, now, now),
                )

    def commit_rules_checkpoint(
        self,
        *,
        analysis_id: str,
        document: dict[str, Any],
        stage_statuses: dict[str, tuple[str, str | None]],
    ) -> None:
        """rules checkpoint：完整结果文档（findings/evidence/影响/覆盖/限制）。"""

        now = utc_now()
        document_json = json.dumps(document, ensure_ascii=True, sort_keys=True)
        with writer(self.db_path) as connection:
            connection.execute(
                "INSERT INTO stage_outputs (analysis_id, stage, output_json)"
                " VALUES (?, 'rules', ?)"
                " ON CONFLICT(analysis_id, stage) DO UPDATE SET"
                " output_json = excluded.output_json",
                (analysis_id, document_json),
            )
            for stage, (status, detail) in stage_statuses.items():
                connection.execute(
                    "INSERT INTO stages (analysis_id, stage, status, detail, started_at, completed_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(analysis_id, stage) DO UPDATE SET"
                    " status = excluded.status, detail = excluded.detail,"
                    " completed_at = excluded.completed_at",
                    (analysis_id, stage, status, detail, now, now),
                )

    def commit_report_checkpoint(
        self,
        *,
        analysis_id: str,
        snapshot_id: str,
        document: dict[str, Any],
        final_status: str,
    ) -> str:
        """Terminal checkpoint: findings + evidence + result + final status.

        The whole set is one transaction. Finding→evidence integrity is
        validated inside the transaction (every referenced anchor must be
        part of this document — a dangling reference rejects the write).
        The final status transition is linearized with cancellation: if the
        cancel flag was persisted before this transaction commits, the
        analysis becomes `cancelled` (outputs retained) instead of the
        computed final status. Returns the status actually persisted.
        """

        now = utc_now()
        document_json = json.dumps(document, ensure_ascii=True, sort_keys=True)
        known_evidence = {anchor["id"] for anchor in document["evidence"]}
        with writer(self.db_path) as connection:
            for anchor in document["evidence"]:
                connection.execute(
                    "INSERT INTO evidence"
                    " (analysis_id, evidence_id, snapshot_id, path, side, start_line,"
                    "  end_line, snippet, source_kind, rule_id, commit_sha)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        analysis_id,
                        anchor["id"],
                        snapshot_id,
                        anchor["path"],
                        anchor["side"],
                        anchor["start_line"],
                        anchor["end_line"],
                        anchor["snippet"],
                        anchor["source_kind"],
                        anchor.get("rule_id"),
                        anchor.get("commit"),
                    ),
                )
            for finding in document["findings"]:
                referenced = finding.get("evidence_ids") or []
                missing = [eid for eid in referenced if eid not in known_evidence]
                if missing:
                    # 事务回滚：悬空证据引用拒绝写入，不部分提交。
                    raise ValueError(
                        "finding references evidence missing from this analysis: "
                        f"{finding['id']} -> {missing[0]}"
                    )
                connection.execute(
                    "INSERT INTO findings"
                    " (analysis_id, finding_id, snapshot_id, category, kind, impact,"
                    "  reliability, rule_id, unresolved_reason, evidence_ids_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        analysis_id,
                        finding["id"],
                        snapshot_id,
                        finding["category"],
                        finding["kind"],
                        finding["impact"],
                        finding["reliability"],
                        finding.get("rule_id"),
                        finding.get("unresolved_reason"),
                        json.dumps(
                            referenced,
                            ensure_ascii=True,
                        ),
                    ),
                )
            connection.execute(
                "INSERT INTO analysis_results"
                " (analysis_id, snapshot_id, result_json, persisted_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(analysis_id) DO UPDATE SET"
                " result_json = excluded.result_json, persisted_at = excluded.persisted_at",
                (analysis_id, snapshot_id, document_json, now),
            )
            # 终态与取消在同一事务内线性化：CASE 在单条 UPDATE 内读取取消
            # 标记——取消先落库则取消获胜，否则写入计算出的终态。
            cursor = connection.execute(
                "UPDATE analyses"
                " SET status = CASE WHEN cancel_requested = 1 THEN 'cancelled'"
                " ELSE ? END, updated_at = ?"
                " WHERE analysis_id = ? AND status = 'running'",
                (final_status, now, analysis_id),
            )
            persisted = final_status
            if cursor.rowcount == 0:
                # 状态已不是 running（如取消/失败先行落库）：不改写既有终态。
                row = connection.execute(
                    "SELECT status FROM analyses WHERE analysis_id = ?",
                    (analysis_id,),
                ).fetchone()
                persisted = str(row["status"]) if row is not None else final_status
            else:
                check = connection.execute(
                    "SELECT status FROM analyses WHERE analysis_id = ?",
                    (analysis_id,),
                ).fetchone()
                persisted = str(check["status"]) if check is not None else final_status
            connection.execute(
                "INSERT INTO stages (analysis_id, stage, status, started_at, completed_at)"
                " VALUES (?, 'report', 'completed', ?, ?)"
                " ON CONFLICT(analysis_id, stage) DO UPDATE SET"
                " status = 'completed', completed_at = excluded.completed_at",
                (analysis_id, now, now),
            )
            return persisted

    def fail_analysis(
        self,
        analysis_id: str,
        failure_reason: str,
        *,
        running_stages: Sequence[str] = (),
    ) -> bool:
        """记录失败并关闭仍在 running 的阶段；终态分析不被改写。"""

        now = utc_now()
        with writer(self.db_path) as connection:
            cursor = connection.execute(
                "UPDATE analyses SET status = 'failed', failure_reason = ?, updated_at = ?"
                " WHERE analysis_id = ? AND status IN ('queued', 'running')",
                (failure_reason, now, analysis_id),
            )
            if cursor.rowcount == 0:
                return False
            connection.execute(
                "UPDATE stages SET status = 'failed', detail = ?, completed_at = ?"
                " WHERE analysis_id = ? AND status = 'running'",
                (failure_reason, now, analysis_id),
            )
            for stage in running_stages:
                connection.execute(
                    "INSERT INTO stages (analysis_id, stage, status, detail, started_at, completed_at)"
                    " VALUES (?, ?, 'failed', ?, ?, ?)"
                    " ON CONFLICT(analysis_id, stage) DO UPDATE SET"
                    " status = 'failed', detail = excluded.detail,"
                    " completed_at = excluded.completed_at",
                    (analysis_id, stage, failure_reason, now, now),
                )
            return True

    # ------------------------------------------------------------------
    # Outputs / queries (API-002)
    # ------------------------------------------------------------------

    def load_stage_output(self, analysis_id: str, stage: str) -> dict[str, Any] | None:
        with reader(self.db_path) as connection:
            row = connection.execute(
                "SELECT output_json FROM stage_outputs"
                " WHERE analysis_id = ? AND stage = ?",
                (analysis_id, stage),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["output_json"])

    def result_document(self, analysis_id: str) -> dict[str, Any] | None:
        with reader(self.db_path) as connection:
            row = connection.execute(
                "SELECT result_json FROM analysis_results WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["result_json"])

    def rules_document(self, analysis_id: str) -> dict[str, Any] | None:
        """rules 阶段产物：report 未提交时也返回（部分结果可观测）。"""

        document = self.result_document(analysis_id)
        if document is not None:
            return document
        return self.load_stage_output(analysis_id, "rules")

    def coverage_summary(self, analysis_id: str) -> dict[str, Any] | None:
        document = self.result_document(analysis_id)
        if document is None:
            return None
        return document.get("selection_summary")

    def get_map(self, analysis_id: str, side: str) -> dict[str, Any] | None:
        with reader(self.db_path) as connection:
            row = connection.execute(
                "SELECT map_json FROM maps WHERE analysis_id = ? AND side = ?",
                (analysis_id, side),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["map_json"])

    def list_decisions(
        self,
        analysis_id: str,
        *,
        status: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[int, list[sqlite3.Row]]:
        with reader(self.db_path) as connection:
            if status is None:
                total = connection.execute(
                    "SELECT COUNT(*) AS n FROM decisions WHERE analysis_id = ?",
                    (analysis_id,),
                ).fetchone()["n"]
                rows = connection.execute(
                    "SELECT snapshot_id, path, status, reason, rule_id, language, bytes"
                    " FROM decisions WHERE analysis_id = ?"
                    " ORDER BY path LIMIT ? OFFSET ?",
                    (analysis_id, limit, offset),
                ).fetchall()
            else:
                total = connection.execute(
                    "SELECT COUNT(*) AS n FROM decisions"
                    " WHERE analysis_id = ? AND status = ?",
                    (analysis_id, status),
                ).fetchone()["n"]
                rows = connection.execute(
                    "SELECT snapshot_id, path, status, reason, rule_id, language, bytes"
                    " FROM decisions WHERE analysis_id = ? AND status = ?"
                    " ORDER BY path LIMIT ? OFFSET ?",
                    (analysis_id, status, limit, offset),
                ).fetchall()
        return int(total), list(rows)

    def findings_count(self, analysis_id: str) -> int:
        with reader(self.db_path) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM findings WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        return int(row["n"])

    def evidence_count(self, analysis_id: str) -> int:
        with reader(self.db_path) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM evidence WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        return int(row["n"])

    def list_findings(
        self,
        analysis_id: str,
        *,
        category: str | None = None,
        rule_id: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[int, list[sqlite3.Row]]:
        # 过滤组合固定为四种查询文本；值一律参数绑定，SQL 文本不做拼接。
        with reader(self.db_path) as connection:
            if category is None and rule_id is None:
                total = connection.execute(
                    "SELECT COUNT(*) AS n FROM findings WHERE analysis_id = ?",
                    (analysis_id,),
                ).fetchone()["n"]
                rows = connection.execute(
                    "SELECT f.analysis_id, f.finding_id, f.snapshot_id, f.category,"
                    " f.kind, f.impact, f.reliability, f.rule_id, f.unresolved_reason,"
                    " f.evidence_ids_json, COALESCE(r.state, 'unreviewed') AS review_state"
                    " FROM findings f LEFT JOIN reviews r"
                    " ON r.analysis_id = f.analysis_id AND r.finding_id = f.finding_id"
                    " WHERE f.analysis_id = ? ORDER BY f.finding_id LIMIT ? OFFSET ?",
                    (analysis_id, limit, offset),
                ).fetchall()
            elif category is not None and rule_id is None:
                total = connection.execute(
                    "SELECT COUNT(*) AS n FROM findings"
                    " WHERE analysis_id = ? AND category = ?",
                    (analysis_id, category),
                ).fetchone()["n"]
                rows = connection.execute(
                    "SELECT f.analysis_id, f.finding_id, f.snapshot_id, f.category,"
                    " f.kind, f.impact, f.reliability, f.rule_id, f.unresolved_reason,"
                    " f.evidence_ids_json, COALESCE(r.state, 'unreviewed') AS review_state"
                    " FROM findings f LEFT JOIN reviews r"
                    " ON r.analysis_id = f.analysis_id AND r.finding_id = f.finding_id"
                    " WHERE f.analysis_id = ? AND f.category = ?"
                    " ORDER BY f.finding_id LIMIT ? OFFSET ?",
                    (analysis_id, category, limit, offset),
                ).fetchall()
            elif category is None and rule_id is not None:
                total = connection.execute(
                    "SELECT COUNT(*) AS n FROM findings"
                    " WHERE analysis_id = ? AND rule_id = ?",
                    (analysis_id, rule_id),
                ).fetchone()["n"]
                rows = connection.execute(
                    "SELECT f.analysis_id, f.finding_id, f.snapshot_id, f.category,"
                    " f.kind, f.impact, f.reliability, f.rule_id, f.unresolved_reason,"
                    " f.evidence_ids_json, COALESCE(r.state, 'unreviewed') AS review_state"
                    " FROM findings f LEFT JOIN reviews r"
                    " ON r.analysis_id = f.analysis_id AND r.finding_id = f.finding_id"
                    " WHERE f.analysis_id = ? AND f.rule_id = ?"
                    " ORDER BY f.finding_id LIMIT ? OFFSET ?",
                    (analysis_id, rule_id, limit, offset),
                ).fetchall()
            else:
                total = connection.execute(
                    "SELECT COUNT(*) AS n FROM findings"
                    " WHERE analysis_id = ? AND category = ? AND rule_id = ?",
                    (analysis_id, category, rule_id),
                ).fetchone()["n"]
                rows = connection.execute(
                    "SELECT f.analysis_id, f.finding_id, f.snapshot_id, f.category,"
                    " f.kind, f.impact, f.reliability, f.rule_id, f.unresolved_reason,"
                    " f.evidence_ids_json, COALESCE(r.state, 'unreviewed') AS review_state"
                    " FROM findings f LEFT JOIN reviews r"
                    " ON r.analysis_id = f.analysis_id AND r.finding_id = f.finding_id"
                    " WHERE f.analysis_id = ? AND f.category = ? AND f.rule_id = ?"
                    " ORDER BY f.finding_id LIMIT ? OFFSET ?",
                    (analysis_id, category, rule_id, limit, offset),
                ).fetchall()
        return int(total), list(rows)

    def get_finding(self, analysis_id: str, finding_id: str) -> sqlite3.Row | None:
        with reader(self.db_path) as connection:
            return connection.execute(
                "SELECT f.analysis_id, f.finding_id, f.snapshot_id, f.category,"
                " f.kind, f.impact, f.reliability, f.rule_id, f.unresolved_reason,"
                " f.evidence_ids_json, COALESCE(r.state, 'unreviewed') AS review_state"
                " FROM findings f LEFT JOIN reviews r"
                " ON r.analysis_id = f.analysis_id AND r.finding_id = f.finding_id"
                " WHERE f.analysis_id = ? AND f.finding_id = ?",
                (analysis_id, finding_id),
            ).fetchone()

    def get_evidence(self, analysis_id: str, evidence_id: str) -> sqlite3.Row | None:
        with reader(self.db_path) as connection:
            return connection.execute(
                "SELECT analysis_id, evidence_id, snapshot_id, path, side,"
                " start_line, end_line, snippet, source_kind, rule_id, commit_sha"
                " FROM evidence WHERE analysis_id = ? AND evidence_id = ?",
                (analysis_id, evidence_id),
            ).fetchone()

    def evidence_for_finding(
        self, analysis_id: str, evidence_ids: Sequence[str]
    ) -> list[sqlite3.Row]:
        # 每个 ID 一条固定文本查询（证据数很小；避免动态 IN 列表拼接 SQL）。
        found: dict[str, sqlite3.Row] = {}
        with reader(self.db_path) as connection:
            for evidence_id in evidence_ids:
                row = connection.execute(
                    "SELECT analysis_id, evidence_id, snapshot_id, path, side,"
                    " start_line, end_line, snippet, source_kind, rule_id, commit_sha"
                    " FROM evidence WHERE analysis_id = ? AND evidence_id = ?",
                    (analysis_id, evidence_id),
                ).fetchone()
                if row is not None:
                    found[evidence_id] = row
        return [found[evidence_id] for evidence_id in evidence_ids if evidence_id in found]

    # ------------------------------------------------------------------
    # Reviews
    # ------------------------------------------------------------------

    def save_review(
        self,
        *,
        analysis_id: str,
        finding_id: str,
        state: str,
        note: str,
        updated_at: str,
    ) -> None:
        with writer(self.db_path) as connection:
            connection.execute(
                "INSERT INTO reviews (analysis_id, finding_id, state, note, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(analysis_id, finding_id) DO UPDATE SET"
                " state = excluded.state, note = excluded.note,"
                " updated_at = excluded.updated_at",
                (analysis_id, finding_id, state, note, updated_at),
            )

    def list_reviews(self, analysis_id: str) -> list[sqlite3.Row]:
        with reader(self.db_path) as connection:
            rows = connection.execute(
                "SELECT analysis_id, finding_id, state, note, updated_at"
                " FROM reviews WHERE analysis_id = ? ORDER BY finding_id",
                (analysis_id,),
            ).fetchall()
        return list(rows)


def is_terminal_status(status: str) -> bool:
    return status in _TERMINAL_STATUSES


def is_active_status(status: str) -> bool:
    return status in _ACTIVE_STATUSES
