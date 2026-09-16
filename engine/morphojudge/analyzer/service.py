"""ANL-001: analysis orchestration — one call, one slice of truth.

analyze_snapshot(repo, snapshot, manifest, rules) runs:
selection (both sides) → parse/IR (both sides) → behavior rules →
dependency rules → consistency rules → evidence resolution → findings →
impact paths → stage coverage & limits.

Internal aggregate only: AnalysisResult is NOT a frozen API/persistence
contract (that happens in later batches). Determinism: no clocks, no
randomness — identical inputs yield identical results and IDs.
Failures are isolated per stage/file/rule: a broken file, lockfile or rule
never silently swallows the other results.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts.domain import (
    EvidenceAnchor,
    EvidenceSide,
    Finding,
    FindingCategory,
    FindingImpact,
    FindingKind,
    FindingReliability,
    Resolution,
    SnapshotReport,
)
from ..evidence.resolver import (
    EvidenceRequest,
    EvidenceResolution,
    EvidenceResolver,
    EvidenceStatus,
)
from ..git.diff import build_diff_result
from ..pipeline import build_software_map
from ..relations.graph import build_graph
from ..rules import behavior as behavior_rules
from ..rules import dependency as dependency_rules
from ..rules import consistency as consistency_rules
from ..rules.dependency import DependencyReport
from ..selection.rules import SelectionRules

RULE_KINDS = {behavior_rules.RULE_PERM_GUARD_REMOVED: FindingKind.RULE_HINT}


@dataclass(frozen=True)
class ImpactPath:
    origin_id: str
    direction: str  # up | down
    node_ids: frozenset
    edge_ids: frozenset
    evidence_ids: tuple
    resolution: str  # resolved | candidate | unresolved
    truncated: bool
    stop_reason: str | None
    limit_note: str | None


@dataclass(frozen=True)
class StageCoverage:
    stage: str
    status: str  # completed | completed_with_limits | failed
    completed: int
    failed: int
    limited: int
    notes: tuple


@dataclass
class AnalysisResult:
    snapshot: SnapshotReport
    diff: object
    selection_decisions: list
    selection_summary: object
    base_map: object
    target_map: object
    findings: list[Finding] = field(default_factory=list)
    evidence: list[EvidenceAnchor] = field(default_factory=list)
    evidence_resolutions: list[EvidenceResolution] = field(default_factory=list)
    impact_paths: list[ImpactPath] = field(default_factory=list)
    dependency_report: DependencyReport | None = None
    consistency_records: list = field(default_factory=list)
    behavior_notes: list[str] = field(default_factory=list)
    stage_coverage: list[StageCoverage] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    analyzer_errors: list[str] = field(default_factory=list)


def _finding_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"finding:{digest}"


def _commit_for_side(snapshot: SnapshotReport, side: EvidenceSide) -> str:
    if side == EvidenceSide.OLD:
        return snapshot.identity.base_commit
    return snapshot.identity.target_commit


def analyze_snapshot(
    repo: Path,
    snapshot: SnapshotReport,
    manifest: dict,
    rules: SelectionRules | None = None,
    *,
    impact_max_depth: int = 10,
    impact_max_nodes: int = 1000,
) -> AnalysisResult:
    rules = rules or SelectionRules()
    identity = snapshot.identity
    sid = identity.snapshot_id

    diff = build_diff_result(repo, snapshot)
    # 行为对齐需要 base(old_path) -> target(new path) 方向
    rename_map = {
        entry.old_path: entry.path
        for entry in diff.files
        if entry.old_path and entry.status.value == "renamed"
    }

    target_res = build_software_map(repo, snapshot, manifest, rules, commit=identity.target_commit)
    base_res = build_software_map(repo, snapshot, manifest, rules, commit=identity.base_commit)

    permission_modules: list[str] = list(
        manifest.get("required_entities", {}).get("permission_modules", [])
    )

    signals: list[behavior_rules.BehaviorSignal] = []
    behavior_notes: list[str] = []
    analyzer_errors: list[str] = []
    evidence_resolutions: list[EvidenceResolution] = []
    anchors_by_id: dict[str, EvidenceAnchor] = {}

    # --- 行为规则 ---
    try:
        signals, behavior_notes = behavior_rules.collect_behavior_signals(
            base_res, target_res, rename_map, permission_modules
        )
    except Exception as exc:  # noqa: BLE001 — 单规则失败不吞掉其他阶段
        analyzer_errors.append(f"behavior_rules_failed:{type(exc).__name__}")

    dependency_report: DependencyReport | None = None
    try:
        dependency_report = dependency_rules.analyze_dependencies(
            repo, identity.base_commit, identity.target_commit, sid, rules
        )
    except Exception as exc:  # noqa: BLE001
        analyzer_errors.append(f"dependency_rules_failed:{type(exc).__name__}")

    consistency_records: list[consistency_rules.ConsistencyRecord] = []
    try:
        consistency_records = consistency_rules.analyze_consistency(target_res)
    except Exception as exc:  # noqa: BLE001
        analyzer_errors.append(f"consistency_rules_failed:{type(exc).__name__}")

    resolver = EvidenceResolver(repo, sid)
    findings: list[Finding] = []
    impact_paths: list[ImpactPath] = []

    def resolve_or_record(request: EvidenceRequest | None, unresolved_reason: str | None) -> EvidenceResolution:
        if request is None:
            resolution = EvidenceResolution(
                request=EvidenceRequest(
                    commit=identity.target_commit,
                    side=EvidenceSide.NEW,
                    path="unknown",
                    start_line=1,
                    end_line=1,
                    rule_id=None,
                ),
                status=EvidenceStatus.UNRESOLVED,
                reason=unresolved_reason or "no_evidence_request",
            )
        else:
            resolution = resolver.resolve(request)
        evidence_resolutions.append(resolution)
        if resolution.status == EvidenceStatus.RESOLVED and resolution.anchor is not None:
            anchors_by_id.setdefault(resolution.anchor.id, resolution.anchor)
        return resolution

    # --- 行为 findings ---
    for signal in signals:
        request = EvidenceRequest(
            commit=_commit_for_side(snapshot, signal.evidence_side),
            side=signal.evidence_side,
            path=signal.evidence_path,
            start_line=signal.evidence_line,
            end_line=signal.evidence_line,
            rule_id=signal.rule_id,
        )
        resolution = resolve_or_record(request, None)
        evidence_ids: list[str] = []
        unresolved_reason: str | None = None
        if resolution.status == EvidenceStatus.RESOLVED and resolution.anchor is not None:
            evidence_ids = [resolution.anchor.id]
        else:
            unresolved_reason = f"evidence_unresolved:{resolution.reason}"
        finding = Finding(
            id=_finding_id(sid, signal.rule_id, signal.change, signal.method_path, signal.method_name, str(signal.evidence_line), signal.target_label or ""),
            snapshot_id=sid,
            category=FindingCategory(signal.category),
            kind=RULE_KINDS.get(signal.rule_id, FindingKind.FACT),
            impact=FindingImpact(signal.impact),
            reliability=FindingReliability.RULE_BASED
            if RULE_KINDS.get(signal.rule_id) == FindingKind.RULE_HINT
            else FindingReliability.DETERMINISTIC,
            evidence_ids=evidence_ids,
            rule_id=signal.rule_id,
            unresolved_reason=unresolved_reason,
        )
        findings.append(finding)
        impact_paths.append(
            _build_impact_path(
                target_res=target_res,
                origin_id=signal.target_node_id,
                evidence_ids=evidence_ids,
                fallback_key=f"{finding.id}|{signal.method_path}|{signal.method_name}",
                max_depth=impact_max_depth,
                max_nodes=impact_max_nodes,
            )
        )

    # --- 依赖 findings ---
    if dependency_report is not None:
        for change in dependency_report.changes:
            side = EvidenceSide.OLD if change.change == "removed" else EvidenceSide.NEW
            commit = _commit_for_side(snapshot, side)
            if change.evidence_line is not None:
                request = EvidenceRequest(
                    commit=commit,
                    side=side,
                    path=change.evidence_path,
                    start_line=change.evidence_line,
                    end_line=change.evidence_line,
                    rule_id=change.rule_id,
                )
                resolution = resolve_or_record(request, None)
            else:
                resolution = resolve_or_record(None, "key_line_not_located")
            evidence_ids = []
            unresolved_reason = None
            if resolution.status == EvidenceStatus.RESOLVED and resolution.anchor is not None:
                evidence_ids = [resolution.anchor.id]
            else:
                unresolved_reason = f"evidence_unresolved:{resolution.reason}"
            findings.append(
                Finding(
                    id=_finding_id(sid, change.rule_id, change.change, change.source, change.name, str(change.evidence_line)),
                    snapshot_id=sid,
                    category=FindingCategory.DEPENDENCY,
                    kind=FindingKind.FACT,
                    impact=FindingImpact.LOW,
                    reliability=FindingReliability.DETERMINISTIC,
                    evidence_ids=evidence_ids,
                    rule_id=change.rule_id,
                    unresolved_reason=unresolved_reason,
                )
            )

    # --- 一致性 findings（仅 mismatch）---
    for record in consistency_records:
        if record.outcome != "mismatch":
            continue
        request = EvidenceRequest(
            commit=identity.target_commit,
            side=EvidenceSide.NEW,
            path=record.path,
            start_line=record.evidence_line,
            end_line=record.evidence_line,
            rule_id=record.rule_id,
        )
        resolution = resolve_or_record(request, None)
        evidence_ids = []
        unresolved_reason = None
        if resolution.status == EvidenceStatus.RESOLVED and resolution.anchor is not None:
            evidence_ids = [resolution.anchor.id]
        else:
            unresolved_reason = f"evidence_unresolved:{resolution.reason}"
        findings.append(
            Finding(
                id=_finding_id(sid, record.rule_id or "CONS", record.path, record.method_name, str(record.evidence_line)),
                snapshot_id=sid,
                category=FindingCategory.CONSISTENCY,
                kind=FindingKind.RULE_HINT,
                impact=FindingImpact.LOW,
                reliability=FindingReliability.RULE_BASED,
                evidence_ids=evidence_ids,
                rule_id=record.rule_id,
                unresolved_reason=unresolved_reason,
            )
        )

    # --- 阶段覆盖 ---
    stage_coverage = _build_stage_coverage(
        target_res=target_res,
        signals=signals,
        behavior_notes=behavior_notes,
        dependency_report=dependency_report,
        consistency_records=consistency_records,
        impact_paths=impact_paths,
        analyzer_errors=analyzer_errors,
    )

    limits: list[str] = []
    limits.extend(base_res.notes)
    limits.extend(target_res.notes)
    limits.extend(behavior_notes)
    if dependency_report is not None:
        limits.extend(dependency_report.notes)
    limits.extend(analyzer_errors)

    return AnalysisResult(
        snapshot=snapshot,
        diff=diff,
        selection_decisions=target_res.decisions,
        selection_summary=target_res.coverage,
        base_map=base_res.software_map,
        target_map=target_res.software_map,
        findings=sorted(findings, key=lambda f: f.id),
        evidence=sorted(anchors_by_id.values(), key=lambda a: a.id),
        evidence_resolutions=sorted(evidence_resolutions, key=lambda r: (r.request.path, r.request.start_line, r.status.value)),
        impact_paths=impact_paths,
        dependency_report=dependency_report,
        consistency_records=consistency_records,
        behavior_notes=behavior_notes,
        stage_coverage=stage_coverage,
        limits=sorted(set(limits)),
        analyzer_errors=analyzer_errors,
    )


def _build_impact_path(
    target_res,
    origin_id: str | None,
    evidence_ids: list[str],
    fallback_key: str,
    max_depth: int = 10,
    max_nodes: int = 1000,
) -> ImpactPath:
    """Upstream traversal on the target-side graph; candidate edges keep the
    path candidate; no path found is recorded honestly, never as 'safe'."""

    if origin_id is None:
        return ImpactPath(
            origin_id=fallback_key,
            direction="up",
            node_ids=frozenset(),
            edge_ids=frozenset(),
            evidence_ids=tuple(evidence_ids),
            resolution="unresolved",
            truncated=False,
            stop_reason=None,
            limit_note="origin_method_absent_on_target_side",
        )
    graph = build_graph(target_res.software_map)
    walk = graph.upstream(origin_id, max_depth=max_depth, max_nodes=max_nodes)
    edge_map = {edge.id: edge for edge in target_res.software_map.edges}
    traversed = [edge_map[edge_id] for edge_id in walk.edge_ids if edge_id in edge_map]
    if any(edge.resolution == Resolution.CANDIDATE for edge in traversed):
        resolution = "candidate"
    elif walk.node_ids - {origin_id}:
        resolution = "resolved"
    else:
        resolution = "unresolved"
    limit_note = None
    if not (walk.node_ids - {origin_id}):
        limit_note = "no_path_in_graph:not_a_safety_claim"
    elif walk.truncated:
        limit_note = f"truncated:{walk.stop_reason}"
    return ImpactPath(
        origin_id=origin_id,
        direction="up",
        node_ids=frozenset(walk.node_ids),
        edge_ids=frozenset(walk.edge_ids),
        evidence_ids=tuple(evidence_ids),
        resolution=resolution,
        truncated=walk.truncated,
        stop_reason=walk.stop_reason,
        limit_note=limit_note,
    )


def _build_stage_coverage(
    *,
    target_res,
    signals,
    behavior_notes,
    dependency_report,
    consistency_records,
    impact_paths,
    analyzer_errors,
) -> list[StageCoverage]:
    stages: list[StageCoverage] = []

    cov = target_res.coverage
    selection_notes = [f"excluded={cov.excluded}"]
    if cov.selected == 0:
        selection_notes.append("no_analyzable_source_files:not_a_safety_claim")
    stages.append(
        StageCoverage(
            stage="selection",
            status="completed_with_limits" if cov.partial else "completed",
            completed=cov.selected,
            failed=cov.failed,
            limited=cov.limited,
            notes=tuple(selection_notes),
        )
    )

    parsed = sum(1 for r in target_res.parse_reports if r.status.value == "parsed")
    with_errors = sum(1 for r in target_res.parse_reports if r.status.value == "parsed_with_errors")
    errored = sum(1 for r in target_res.parse_reports if r.status.value in ("error", "limited"))
    stages.append(
        StageCoverage(
            stage="parse",
            status="completed_with_limits" if (with_errors or errored) else "completed",
            completed=parsed,
            failed=errored,
            limited=with_errors,
            notes=(),
        )
    )

    behavior_failed = any(err.startswith("behavior_rules_failed") for err in analyzer_errors)
    stages.append(
        StageCoverage(
            stage="behavior_rules",
            status="failed" if behavior_failed else "completed",
            completed=len(signals),
            failed=1 if behavior_failed else 0,
            limited=0,
            notes=tuple(behavior_notes),
        )
    )

    if dependency_report is None:
        stages.append(StageCoverage("dependency", "failed", 0, 1, 0, ("dependency_report_unavailable",)))
    else:
        dep_limited = dependency_report.status == "limited"
        dep_failed = dependency_report.status == "failed"
        stages.append(
            StageCoverage(
                stage="dependency",
                status="failed" if dep_failed else ("completed_with_limits" if dep_limited else "completed"),
                completed=len(dependency_report.changes),
                failed=1 if dep_failed else 0,
                limited=1 if dep_limited else 0,
                notes=tuple(dependency_report.notes),
            )
        )

    mismatch = sum(1 for r in consistency_records if r.outcome == "mismatch")
    consistent = sum(1 for r in consistency_records if r.outcome == "consistent")
    unknown = sum(1 for r in consistency_records if r.outcome == "unknown")
    consistency_failed = any(err.startswith("consistency_rules_failed") for err in analyzer_errors)
    stages.append(
        StageCoverage(
            stage="consistency",
            status="failed" if consistency_failed else "completed",
            completed=mismatch + consistent,
            failed=1 if consistency_failed else 0,
            limited=0,
            notes=(f"unknown={unknown}",),
        )
    )

    truncated = sum(1 for p in impact_paths if p.truncated)
    stages.append(
        StageCoverage(
            stage="impact",
            status="completed_with_limits" if truncated else "completed",
            completed=len(impact_paths),
            failed=0,
            limited=truncated,
            notes=(),
        )
    )
    return stages
