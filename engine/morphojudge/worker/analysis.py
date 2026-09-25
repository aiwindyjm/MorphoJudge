"""ANL-003: resumable analysis worker with checkpoints, cancel and recovery.

Stage model (frozen stage names, docs/contract-freezes.md F1.2):
``git → selection → parse → behavior → dependency → report``

The deterministic core (Batch-03) is exposed as composable stage functions
(``collect_map_inputs`` → ``evaluate_rules`` → ``assemble_analysis_result``);
the worker persists FOUR crash-safe checkpoints, each in exactly one SQLite
transaction, and skips stages whose checkpoint output already exists:

1. ``git``    — resolve_snapshot → SnapshotReport (+ insert-only snapshot cache)
2. ``parse``  — diff + both-side selection/parse/SoftwareMap state (+ maps and
   decisions rows, manifest binding: status + content digest + the exact
   permission_modules used)
3. ``rules``  — behavior/dependency/consistency → findings, evidence, impact
   paths, stage coverage, limits (the full result document)
4. ``report`` — findings/evidence rows + result document + final status

Resume uses the persisted stage outputs verbatim (rules re-reads nothing from
the manifest — its inputs come from the parse checkpoint, so recovery cannot
silently switch configuration). Terminal analyses are never re-run. Cancel
is cooperative at stage boundaries AND linearized inside the report
transaction (a cancel flag persisted before the report commit wins).

All SQL lives in db/; this module only orchestrates.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Sequence

from ..analyzer.service import (
    MapInputs,
    analyze_snapshot,  # noqa: F401 — re-exported for backward imports
    assemble_analysis_result,
    collect_map_inputs,
    evaluate_rules,
)
from ..contracts.domain import (
    SCHEMA_VERSION,
    DiffResult,
    SnapshotReport,
)
from ..contracts.errors import ErrorCode, MorphoJudgeError
from ..db.repository import AnalysisRepository, is_terminal_status
from ..git.snapshot import resolve_snapshot
from ..pipeline import dump_build_state, load_build_state

STAGE_ORDER: tuple[str, ...] = (
    "git",
    "selection",
    "parse",
    "behavior",
    "dependency",
    "report",
)
_SELECTION_STAGES: tuple[str, ...] = ("selection", "parse")
_RULE_STAGES: tuple[str, ...] = ("behavior", "dependency")

EMPTY_MANIFEST: dict[str, Any] = {"human_feature_mapping": []}

# result.stage_coverage 的内部阶段名 → 会话冻结阶段名（F1.2）
_COVERAGE_STAGE_MAP = {
    "selection": "selection",
    "parse": "parse",
    "behavior_rules": "behavior",
    "dependency": "dependency",
}

# 会话冻结 StageStatus 没有 completed_with_limits；该语义进入 detail 与分析级状态
_MAX_REASON_LENGTH = 500
_ABSOLUTE_PATH_RE = re.compile(r"[A-Za-z]:(?:[\\/][\w.\-]+)+|(?:/[\w.\-]+)+")


def sanitize_reason(text: str) -> str:
    """脱敏失败原因：绝对路径形态的片段替换为 <path>，并截断长度。"""

    masked = _ABSOLUTE_PATH_RE.sub("<path>", text)
    if len(masked) > _MAX_REASON_LENGTH:
        masked = masked[:_MAX_REASON_LENGTH] + "…"
    return masked


class WorkerCancelled(Exception):
    """Raised internally when a cooperative cancel takes effect."""


class _StageFailed(Exception):
    """Raised internally after a stage failed and was persisted as `failed`."""


# ---------------------------------------------------------------------------
# 执行所有权（B04-R2-02）：单进程、单 daemon 支持范围内的跨 Runner/Worker
# 互斥。键 = (规范化数据库路径, analysis_id)；只有正在执行的分析占用登记，
# 所有返回/异常路径在 finally 释放——不是持有全部历史 ID 的无界注册表，
# 重启后为空，不影响恢复。非持有者不写入任何阶段/checkpoint/失败或终态。
# ---------------------------------------------------------------------------

_EXECUTION_CLAIMS: set[tuple[str, str]] = set()
_EXECUTION_CLAIMS_LOCK = threading.Lock()


def _claim_execution(key: tuple[str, str]) -> bool:
    with _EXECUTION_CLAIMS_LOCK:
        if key in _EXECUTION_CLAIMS:
            return False
        _EXECUTION_CLAIMS.add(key)
        return True


def _release_execution(key: tuple[str, str]) -> None:
    with _EXECUTION_CLAIMS_LOCK:
        _EXECUTION_CLAIMS.discard(key)


# ---------------------------------------------------------------------------
# Manifest loading (B04-R1-07)
# ---------------------------------------------------------------------------


class ManifestStatus:
    ABSENT = "absent"
    LOADED = "loaded"
    READ_FAILED = "read_failed"
    PARSE_FAILED = "parse_failed"
    SCHEMA_INVALID = "schema_invalid"


class ManifestOutcome:
    """映射配置的加载结果：缺失/损坏/错类型绝不静默变成“空映射”。

    note 只含文件名等非敏感信息；digest 绑定内容，恢复时不换输入。
    """

    def __init__(self, status: str, manifest: dict | None, digest: str | None, note: str) -> None:
        self.status = status
        self.manifest = manifest
        self.digest = digest
        self.note = note

    @property
    def is_loaded(self) -> bool:
        return self.status == ManifestStatus.LOADED


def _manifest_shape_ok(manifest: Any) -> bool:
    """单一、显式的已消费配置校验入口（B04-R2-04）。

    按 parser/extract.load_feature_mapping 与 permission_modules 的实际消费
    结构校验子项：human_feature_mapping 每项必须是 dict，含字符串
    page/feature_id/feature、可选 events（字符串列表）且 confirmed_by ==
    "human"；permission_modules 每项必须是字符串（禁止 str() 强转错型）。
    未消费的描述性元数据（fixture 的 author/commits/description 等）不受
    影响——不擅自收窄整个 manifest 格式。
    """

    if not isinstance(manifest, dict):
        return False
    # B04-R3-01：缺字段 ≠ 显式 JSON null。只有键不存在时才走可选缺省；
    # 键存在就必须校验真实类型——isinstance(None, ...) 恒假，四种显式
    # null（mapping/entities/permission_modules/events）一律 schema_invalid。
    if "human_feature_mapping" in manifest:
        mapping = manifest["human_feature_mapping"]
        if not isinstance(mapping, list):
            return False
        for entry in mapping:
            if not isinstance(entry, dict):
                return False
            if entry.get("confirmed_by") != "human":
                return False
            for field in ("page", "feature_id", "feature"):
                value = entry.get(field)
                if not isinstance(value, str) or not value:
                    return False
            if "events" in entry:
                events = entry["events"]
                if not isinstance(events, list):
                    return False
                if any(not isinstance(event, str) for event in events):
                    return False
    if "required_entities" in manifest:
        entities = manifest["required_entities"]
        if not isinstance(entities, dict):
            return False
        if "permission_modules" in entities:
            modules = entities["permission_modules"]
            if not isinstance(modules, list):
                return False
            if any(not isinstance(module, str) for module in modules):
                return False
    return True


def load_manifest(manifest_dir: Path | None, repo_path: Path) -> ManifestOutcome:
    """Deterministic manifest lookup: <repo-dirname>-manifest.json, data only.

    A missing manifest is NOT an error: the analysis proceeds with an empty
    human feature mapping (features stay unmapped instead of guessed). A
    present-but-unusable manifest is an explicit, persisted limitation.
    """

    if manifest_dir is None:
        return ManifestOutcome(ManifestStatus.ABSENT, None, None, "manifest_dir_not_configured")
    manifest_path = manifest_dir / f"{repo_path.name}-manifest.json"
    if not manifest_path.is_file():
        return ManifestOutcome(ManifestStatus.ABSENT, None, None, manifest_path.name)
    try:
        raw = manifest_path.read_bytes()
    except OSError:
        return ManifestOutcome(ManifestStatus.READ_FAILED, None, None, manifest_path.name)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ManifestOutcome(ManifestStatus.PARSE_FAILED, None, digest, manifest_path.name)
    if not _manifest_shape_ok(manifest):
        return ManifestOutcome(ManifestStatus.SCHEMA_INVALID, None, digest, manifest_path.name)
    return ManifestOutcome(ManifestStatus.LOADED, manifest, digest, manifest_path.name)


def permission_modules_of(manifest: dict | None) -> list[str]:
    if not isinstance(manifest, dict):
        return []
    entities = manifest.get("required_entities")
    if not isinstance(entities, dict):
        return []
    modules = entities.get("permission_modules")
    if not isinstance(modules, list):
        return []
    # 不做强转：非字符串元素只可能来自未校验路径，过滤而非 str()（B04-R2-04）
    return [module for module in modules if isinstance(module, str)]


# ---------------------------------------------------------------------------
# Result document
# ---------------------------------------------------------------------------


def build_result_document(result) -> dict[str, Any]:
    """Serialize the internal analysis result into the persisted document."""

    from dataclasses import asdict, is_dataclass

    def _plain(value: Any) -> Any:
        if is_dataclass(value) and not isinstance(value, type):
            return asdict(value)
        return value

    dependency_section: dict[str, Any] | None = None
    if result.dependency_report is not None:
        report = result.dependency_report
        dependency_section = {
            "status": report.status,
            "changes": [_plain(change) for change in report.changes],
            "notes": list(report.notes),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot": result.snapshot.model_dump(mode="json"),
        "diff": result.diff.model_dump(mode="json"),
        "selection_decisions": [
            decision.model_dump(mode="json") for decision in result.selection_decisions
        ],
        "selection_summary": result.selection_summary.model_dump(mode="json"),
        "maps": {
            "base_map": result.base_map.model_dump(mode="json"),
            "target_map": result.target_map.model_dump(mode="json"),
        },
        "findings": [finding.model_dump(mode="json") for finding in result.findings],
        "evidence": [anchor.model_dump(mode="json") for anchor in result.evidence],
        "evidence_resolutions": [
            {
                "request": asdict(resolution.request),
                "status": resolution.status.value,
                "reason": resolution.reason,
                "anchor_id": resolution.anchor_id,
            }
            for resolution in result.evidence_resolutions
        ],
        "impact_paths": [
            {
                "origin_id": path.origin_id,
                "direction": path.direction,
                "node_ids": sorted(path.node_ids),
                "edge_ids": sorted(path.edge_ids),
                "evidence_ids": list(path.evidence_ids),
                "resolution": path.resolution,
                "truncated": path.truncated,
                "stop_reason": path.stop_reason,
                "limit_note": path.limit_note,
            }
            for path in result.impact_paths
        ],
        "stage_coverage": [
            {
                "stage": coverage.stage,
                "status": coverage.status,
                "completed": coverage.completed,
                "failed": coverage.failed,
                "limited": coverage.limited,
                "notes": list(coverage.notes),
            }
            for coverage in result.stage_coverage
        ],
        "limits": list(result.limits),
        "analyzer_errors": list(result.analyzer_errors),
        "behavior_notes": list(result.behavior_notes),
        "dependency": dependency_section,
        "consistency_records": [_plain(record) for record in result.consistency_records],
    }


def _normalize_stage_status(raw: str) -> tuple[str, str | None]:
    """内部 completed_with_limits → 会话冻结枚举 + detail 说明。"""

    if raw == "completed":
        return "completed", None
    if raw == "completed_with_limits":
        return "completed", "coverage_status=completed_with_limits"
    return "failed", f"coverage_status={raw}"


def _coverage_entry(document: dict[str, Any], stage: str) -> dict[str, Any]:
    for item in document.get("stage_coverage", []):
        if item["stage"] == stage:
            return item
    return {}


def _selection_stage_statuses(target_state: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    """parse checkpoint 的会话阶段记录（来自 target 侧确定性覆盖）。"""

    coverage = target_state["coverage"]
    selection = "completed_with_limits" if coverage.get("partial") else "completed"
    parse_reports = target_state.get("parse_reports") or []
    with_errors = sum(1 for r in parse_reports if r["status"] == "parsed_with_errors")
    errored = sum(1 for r in parse_reports if r["status"] in ("error", "limited"))
    parse = "completed_with_limits" if (with_errors or errored) else "completed"
    return {
        "selection": _normalize_stage_status(selection),
        "parse": _normalize_stage_status(parse),
    }


def _rules_stage_statuses(document: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    """rules checkpoint 的会话阶段记录（behavior/dependency）。"""

    behavior_raw = _coverage_entry(document, "behavior_rules").get("status", "completed")
    consistency = _coverage_entry(document, "consistency").get("status", "completed")
    impact = _coverage_entry(document, "impact").get("status", "completed")
    behavior_status, behavior_detail = _normalize_stage_status(behavior_raw)
    detail = (
        f"{behavior_detail or ''};consistency={consistency};impact={impact}".lstrip(";")
    )
    dependency_raw = _coverage_entry(document, "dependency").get("status", "completed")
    return {
        "behavior": (behavior_status, detail),
        "dependency": _normalize_stage_status(dependency_raw),
    }


def derive_final_status(document: dict[str, Any]) -> str:
    """completed_with_limits whenever the deterministic flow hit any
    limit/failed stage/unresolved finding — never disguised as completed."""

    for item in document["stage_coverage"]:
        if item["status"] != "completed":
            return "completed_with_limits"
    if document["limits"]:
        return "completed_with_limits"
    for finding in document["findings"]:
        if finding.get("unresolved_reason"):
            return "completed_with_limits"
    return "completed"


def _impact_options(options_json: str | None) -> dict[str, int]:
    if not options_json:
        return {"impact_max_depth": 10, "impact_max_nodes": 1000}
    try:
        parsed = json.loads(options_json)
    except ValueError:
        return {"impact_max_depth": 10, "impact_max_nodes": 1000}
    return {
        "impact_max_depth": int(parsed.get("impact_max_depth", 10)),
        "impact_max_nodes": int(parsed.get("impact_max_nodes", 1000)),
    }


class AnalysisWorker:
    """Runs one analysis through its checkpoints; safe to re-run after a crash."""

    def __init__(
        self,
        repository: AnalysisRepository,
        *,
        allowed_roots: Sequence[Path],
        manifest_dir: Path | None = None,
        on_stage_start: Callable[[str, str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.allowed_roots = tuple(allowed_roots)
        self.manifest_dir = manifest_dir
        self.on_stage_start = on_stage_start

    # -- helpers ---------------------------------------------------------

    def _repo_path_for(self, analysis_id: str) -> tuple[Path, dict[str, Any]]:
        row = self.repository.get_analysis(analysis_id)
        if row is None:
            raise MorphoJudgeError(
                ErrorCode.ANALYSIS_NOT_FOUND, f"analysis not found: {analysis_id}"
            )
        repo_row = self.repository.get_repository(row["repository_id"])
        if repo_row is None:
            raise MorphoJudgeError(
                ErrorCode.REPOSITORY_NOT_REGISTERED,
                f"repository no longer registered: {row['repository_id']}",
            )
        return Path(str(repo_row["canonical_path"])), dict(row)

    def _check_cancel(self, analysis_id: str) -> None:
        if self.repository.is_cancel_requested(analysis_id):
            self.repository.apply_cancel(analysis_id)
            raise WorkerCancelled(analysis_id)

    # -- main ------------------------------------------------------------

    def run(self, analysis_id: str) -> str:
        """Execute (or resume) the analysis; returns the final status.

        同一数据库的同一分析在单进程内只有一个执行者（B04-R2-02）：
        非持有者直接返回当前状态，不产生任何写入。
        """

        key = (str(self.repository.db_path.resolve()), analysis_id)
        if not _claim_execution(key):
            existing = self.repository.get_analysis(analysis_id)
            if existing is None:
                raise MorphoJudgeError(
                    ErrorCode.ANALYSIS_NOT_FOUND, f"analysis not found: {analysis_id}"
                )
            return str(existing["status"])
        try:
            return self._run_owned(analysis_id)
        finally:
            _release_execution(key)

    def _run_owned(self, analysis_id: str) -> str:
        row = self.repository.get_analysis(analysis_id)
        if row is None:
            raise MorphoJudgeError(
                ErrorCode.ANALYSIS_NOT_FOUND, f"analysis not found: {analysis_id}"
            )
        status = str(row["status"])
        if is_terminal_status(status):
            return status  # 终态分析永不重跑：旧结果不被覆盖
        if not bool(row["resumable"]):
            self.repository.fail_analysis(
                analysis_id, "not_resumable", running_stages=STAGE_ORDER[1:]
            )
            return "failed"

        self.repository.set_status(analysis_id, "running")
        self.repository.ensure_stage_rows(analysis_id, STAGE_ORDER)

        try:
            snapshot = self._run_git_stage(analysis_id, row)
            parse_state = self._run_parse_stage(analysis_id, row, snapshot)
            document = self._run_rules_stage(analysis_id, row, snapshot, parse_state)
            self._run_report_stage(analysis_id, snapshot, document)
        except (WorkerCancelled, _StageFailed):
            # 终态（cancelled/failed）已由对应 checkpoint 写入 SQLite。
            pass
        return str(self.repository.analysis_status(analysis_id) or "failed")

    def _run_git_stage(self, analysis_id: str, row: dict[str, Any]) -> SnapshotReport:
        cached = self.repository.load_stage_output(analysis_id, "git")
        if cached is not None:
            return SnapshotReport.model_validate(cached)

        if self.on_stage_start is not None:
            self.on_stage_start(analysis_id, "git")
        self._check_cancel(analysis_id)
        self.repository.upsert_stage(analysis_id, "git", "running")

        repo_path = Path(str(self._repo_path_for(analysis_id)[0]))
        try:
            snapshot = resolve_snapshot(
                repo_path,
                base_ref=str(row["base_ref"]),
                target_ref=str(row["target_ref"]),
                rules_version=str(row["rules_version"]),
                allowed_roots=self.allowed_roots,
            )
        except MorphoJudgeError as error:
            reason = f"{error.code.value}:{sanitize_reason(error.message)}"
            self.repository.fail_analysis(analysis_id, reason, running_stages=["git"])
            raise _StageFailed(analysis_id) from error

        self.repository.commit_git_checkpoint(
            analysis_id=analysis_id,
            repository_id=str(row["repository_id"]),
            snapshot_id=snapshot.identity.snapshot_id,
            snapshot_report=snapshot.model_dump(mode="json"),
        )
        return snapshot

    def _run_parse_stage(
        self, analysis_id: str, row: dict[str, Any], snapshot: SnapshotReport
    ) -> dict[str, Any]:
        cached = self.repository.load_stage_output(analysis_id, "parse")
        if cached is not None:
            return cached

        if self.on_stage_start is not None:
            self.on_stage_start(analysis_id, "parse")
        self._check_cancel(analysis_id)
        for stage in _SELECTION_STAGES:
            self.repository.upsert_stage(analysis_id, stage, "running")

        repo_path, _ = self._repo_path_for(analysis_id)
        outcome = load_manifest(self.manifest_dir, repo_path)
        manifest_for_maps = outcome.manifest if outcome.is_loaded else EMPTY_MANIFEST

        try:
            inputs = collect_map_inputs(repo_path, snapshot, manifest_for_maps)
        except Exception as error:  # noqa: BLE001 — 阶段不可恢复错误 → failed
            reason = f"internal_error:{type(error).__name__}"
            self.repository.fail_analysis(analysis_id, reason)
            raise _StageFailed(analysis_id) from error

        output = {
            "snapshot": snapshot.model_dump(mode="json"),
            "diff": inputs.diff.model_dump(mode="json"),
            "maps_state": {
                "base_state": dump_build_state(inputs.base_res),
                "target_state": dump_build_state(inputs.target_res),
            },
            "selection_decisions": [
                decision.model_dump(mode="json")
                for decision in inputs.target_res.decisions
            ],
            "selection_summary": inputs.target_res.coverage.model_dump(mode="json"),
            "manifest": {
                "status": outcome.status,
                "digest": outcome.digest,
                "permission_modules": permission_modules_of(
                    outcome.manifest if outcome.is_loaded else None
                ),
            },
        }
        self.repository.commit_parse_checkpoint(
            analysis_id=analysis_id,
            snapshot_id=snapshot.identity.snapshot_id,
            output=output,
            stage_statuses=_selection_stage_statuses(output["maps_state"]["target_state"]),
            manifest_status=outcome.status,
            manifest_digest=outcome.digest,
        )
        return output

    def _run_rules_stage(
        self,
        analysis_id: str,
        row: dict[str, Any],
        snapshot: SnapshotReport,
        parse_state: dict[str, Any],
    ) -> dict[str, Any]:
        cached = self.repository.load_stage_output(analysis_id, "rules")
        if cached is not None:
            return cached

        if self.on_stage_start is not None:
            self.on_stage_start(analysis_id, "rules")
        self._check_cancel(analysis_id)
        for stage in _RULE_STAGES:
            self.repository.upsert_stage(analysis_id, stage, "running")

        repo_path, _ = self._repo_path_for(analysis_id)
        # 恢复输入全部来自 parse checkpoint：不重新读取 manifest，输入不漂移。
        options = _impact_options(
            row["options_json"] if "options_json" in row.keys() else None
        )
        try:
            inputs = MapInputs(
                diff=DiffResult.model_validate(parse_state["diff"]),
                base_res=load_build_state(parse_state["maps_state"]["base_state"]),
                target_res=load_build_state(parse_state["maps_state"]["target_state"]),
            )
            outputs = evaluate_rules(
                repo_path,
                snapshot,
                inputs,
                list(parse_state["manifest"]["permission_modules"]),
                impact_max_depth=options["impact_max_depth"],
                impact_max_nodes=options["impact_max_nodes"],
            )
            result = assemble_analysis_result(snapshot, inputs, outputs)
        except Exception as error:  # noqa: BLE001 — 阶段不可恢复错误 → failed
            reason = f"internal_error:{type(error).__name__}"
            self.repository.fail_analysis(analysis_id, reason)
            raise _StageFailed(analysis_id) from error

        document = build_result_document(result)
        manifest_meta = parse_state["manifest"]
        document["manifest"] = {
            "status": manifest_meta["status"],
            "digest": manifest_meta["digest"],
        }
        if manifest_meta["status"] != ManifestStatus.LOADED:
            # 映射配置缺失/损坏是显式覆盖限制：功能与权限映射不可用。
            document["limits"] = sorted(
                set(document["limits"] + [f"manifest:{manifest_meta['status']}"])
            )
        self.repository.commit_rules_checkpoint(
            analysis_id=analysis_id,
            document=document,
            stage_statuses=_rules_stage_statuses(document),
        )
        return document

    def _run_report_stage(
        self, analysis_id: str, snapshot: SnapshotReport, document: dict[str, Any]
    ) -> None:
        if self.repository.result_document(analysis_id) is not None:
            return  # report checkpoint 已提交（恢复路径）

        if self.on_stage_start is not None:
            self.on_stage_start(analysis_id, "report")
        self._check_cancel(analysis_id)
        self.repository.upsert_stage(analysis_id, "report", "running")

        try:
            self.repository.commit_report_checkpoint(
                analysis_id=analysis_id,
                snapshot_id=snapshot.identity.snapshot_id,
                document=document,
                final_status=derive_final_status(document),
            )
        except Exception as error:  # noqa: BLE001 — report 写入失败关闭阶段
            reason = f"report_write_failed:{type(error).__name__}"
            self.repository.fail_analysis(analysis_id, reason)
            raise _StageFailed(analysis_id) from error


class AnalysisRunner:
    """Thread-pool runner with an in-memory task registry and recovery.

    Registry updates and pool submission happen under ONE lock, so two
    concurrent ``submit`` calls for the same analysis cannot both win; the
    done-callback only removes its own Future. Durability comes from the
    SQLite status (queued/running analyses are re-enqueued by ``recover()``
    after a daemon restart), not from process memory. Single-daemon scope:
    no distributed queue, but invariants hold for re-entry and a second
    Runner instance over the same repository.
    """

    def __init__(
        self,
        worker: AnalysisWorker,
        *,
        concurrency: int = 1,
    ) -> None:
        self._worker = worker
        self._pool = ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="morphojudge-worker"
        )
        self._tasks: dict[str, Future[str]] = {}
        self._lock = threading.Lock()

    @property
    def worker(self) -> AnalysisWorker:
        return self._worker

    def submit(self, analysis_id: str) -> bool:
        """Enqueue unless a live task is already registered for this id."""

        def _safe_run() -> str:
            try:
                return self._worker.run(analysis_id)
            except Exception as error:  # noqa: BLE001 — worker 自身崩溃也要落终态
                try:
                    self._worker.repository.fail_analysis(
                        analysis_id, f"internal_error:{type(error).__name__}"
                    )
                except Exception:  # noqa: BLE001 — 记录失败也失败时只能放行异常
                    pass
                raise

        with self._lock:
            existing = self._tasks.get(analysis_id)
            if existing is not None and not existing.done():
                return False
            future = self._pool.submit(_safe_run)
            self._tasks[analysis_id] = future
        future.add_done_callback(
            lambda done, key=analysis_id: self._forget_and_consume(key, done)
        )
        return True

    def cancel_pending(self, analysis_id: str) -> bool:
        """Best-effort cancel of a queued-but-not-started task.

        True when the task never started (caller marks the analysis
        cancelled); False when it is running (worker observes the flag at
        the next stage boundary) or unknown.
        """

        with self._lock:
            future = self._tasks.get(analysis_id)
        if future is None:
            return False
        return future.cancel()

    def recover(self) -> list[str]:
        """Re-enqueue non-terminal analyses after a restart."""

        resumed: list[str] = []
        for row in self._worker.repository.recoverable_analyses():
            analysis_id = str(row["analysis_id"])
            if self.submit(analysis_id):
                resumed.append(analysis_id)
        return resumed

    def _forget_and_consume(self, analysis_id: str, future: Future[str]) -> None:
        try:
            future.exception()  # 消费异常，避免解释器退出时的未读警告
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            current = self._tasks.get(analysis_id)
            if current is future:  # 只移除自己的登记，不碰重提交的新 Future
                self._tasks.pop(analysis_id, None)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
