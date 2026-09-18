"""API-002: result queries — coverage, software map, findings, evidence,
impact paths, summary and reviews.

Readiness is artifact-based (B04-R1-04): an endpoint answers as soon as the
artifacts it serves are committed — a failed or cancelled analysis with a
persisted rules checkpoint still exposes its committed coverage/map/findings,
each response carrying an explicit ``availability`` marker. Missing artifacts
return 409 ANALYSIS_NOT_READY — never an empty collection that could be
mistaken for "nothing found". All reads come from SQLite; the repository
working tree is never touched.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Query, Request

from ...contracts.domain import (
    AnalysisStatus,
    CoverageSummary,
    DiffFileEntry,
    EvidenceAnchor,
    EvidenceSide,
    Finding,
    FindingCategory,
    Review,
    ReviewState,
    SelectionDecision,
    SelectionStatus,
    SoftwareMap,
)
from ...contracts.errors import ErrorCode, MorphoJudgeError
from ...db.repository import AnalysisRepository, utc_now
from ..schemas import (
    AnalysisCounts,
    Availability,
    CoverageResponse,
    FindingDetail,
    FindingListItem,
    FindingsPage,
    ImpactPathItem,
    ImpactPathsPage,
    MapResponse,
    ReviewRequest,
    ReviewsPage,
    StageCoverageItem,
    SummaryResponse,
    availability_for,
)

router = APIRouter(prefix="/v1/analyses", tags=["results"])


def _get_analysis_row(repository: AnalysisRepository, analysis_id: str):
    row = repository.get_analysis(analysis_id)
    if row is None:
        raise MorphoJudgeError(
            ErrorCode.ANALYSIS_NOT_FOUND, f"analysis not found: {analysis_id}"
        )
    return row


def _require_artifact(
    repository: AnalysisRepository, analysis_id: str, kind: str
) -> tuple[dict | None, str]:
    """按端点所需产物判定可读性；无产物 → 409，不返回空集合。

    产物分层（B04-R1-04）：
    - ``parse``：maps 行 + 选择决策（map/coverage 最低要求）；
    - ``rules``：rules 阶段文档（summary/impact-paths/coverage 全量信息；
      report 未提交或失败时仍可读，availability 标 partial）；
    - ``report``：findings/evidence 行（findings/evidence/reviews 端点）。
    """

    row = _get_analysis_row(repository, analysis_id)
    status = str(row["status"])
    document = None
    if kind == "parse":
        document = repository.rules_document(analysis_id)
        if document is None:
            document = repository.load_stage_output(analysis_id, "parse")
    elif kind == "rules":
        document = repository.rules_document(analysis_id)
    elif kind == "report":
        document = repository.result_document(analysis_id)
    else:
        raise ValueError(f"unknown artifact kind: {kind}")
    if document is None:
        raise MorphoJudgeError(
            ErrorCode.ANALYSIS_NOT_READY,
            "analysis artifacts are not persisted yet",
            details={"status": status, "missing": f"{kind}_artifact"},
        )
    return document, status


def _finding_from_row(row) -> Finding:
    return Finding(
        id=str(row["finding_id"]),
        snapshot_id=str(row["snapshot_id"]),
        category=str(row["category"]),
        kind=str(row["kind"]),
        impact=str(row["impact"]),
        reliability=str(row["reliability"]),
        evidence_ids=json.loads(str(row["evidence_ids_json"])),
        rule_id=row["rule_id"],
        unresolved_reason=row["unresolved_reason"],
    )


def _evidence_from_row(row) -> EvidenceAnchor:
    return EvidenceAnchor(
        id=str(row["evidence_id"]),
        snapshot_id=str(row["snapshot_id"]),
        path=str(row["path"]),
        side=EvidenceSide(str(row["side"])),
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        snippet=str(row["snippet"]),
        source_kind=str(row["source_kind"]),
        rule_id=row["rule_id"],
        commit=row["commit_sha"],
    )


def _stage_coverage_items(document: dict) -> list[StageCoverageItem]:
    return [
        StageCoverageItem(
            stage=item["stage"],
            status=item["status"],
            completed=item["completed"],
            failed=item["failed"],
            limited=item["limited"],
            notes=list(item.get("notes") or []),
        )
        for item in document.get("stage_coverage") or []
    ]


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


@router.get("/{analysis_id}/coverage", response_model=CoverageResponse)
def coverage(
    analysis_id: str,
    request: Request,
    status: Annotated[
        str | None,
        Query(description="filter decisions by selection status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CoverageResponse:
    repository: AnalysisRepository = request.app.state.repository
    document, analysis_status = _require_artifact(repository, analysis_id, "parse")

    if status is not None and status not in {item.value for item in SelectionStatus}:
        raise MorphoJudgeError(
            ErrorCode.INVALID_INPUT,
            f"unknown selection status filter: {status}",
            details={"status": status},
        )

    rules_document = repository.rules_document(analysis_id)
    source = rules_document if rules_document is not None else document
    summary_dict = source.get("selection_summary")
    if summary_dict is None:
        raise MorphoJudgeError(
            ErrorCode.ANALYSIS_NOT_READY,
            "coverage summary is not persisted for this analysis",
            details={"analysis_id": analysis_id},
        )
    total, rows = repository.list_decisions(
        analysis_id, status=status, limit=limit, offset=offset
    )
    decisions = [
        SelectionDecision(
            snapshot_id=str(row["snapshot_id"]),
            path=str(row["path"]),
            status=SelectionStatus(str(row["status"])),
            reason=str(row["reason"]),
            rule_id=row["rule_id"],
            language=row["language"],
            bytes=row["bytes"],
        )
        for row in rows
    ]
    raw_manifest = source.get("manifest") or {}
    manifest_meta = (
        {"status": raw_manifest.get("status"), "digest": raw_manifest.get("digest")}
        if raw_manifest
        else None
    )
    return CoverageResponse(
        summary=CoverageSummary.model_validate(summary_dict),
        decisions=decisions,
        stage_coverage=_stage_coverage_items(source) if rules_document else [],
        limits=list(source.get("limits") or []),
        manifest=manifest_meta,
        total=total,
        limit=limit,
        offset=offset,
        availability=availability_for(analysis_status, rules_document is not None),
    )


# ---------------------------------------------------------------------------
# Software map（正式端点 + /map 兼容别名）
# ---------------------------------------------------------------------------


def _software_map_response(
    analysis_id: str, side: str, request: Request
) -> MapResponse:
    repository: AnalysisRepository = request.app.state.repository
    row = _get_analysis_row(repository, analysis_id)
    analysis_status = str(row["status"])
    map_dict = repository.get_map(analysis_id, side)
    if map_dict is None:
        raise MorphoJudgeError(
            ErrorCode.ANALYSIS_NOT_READY,
            "software map is not persisted for this analysis",
            details={"analysis_id": analysis_id, "side": side, "status": analysis_status},
        )
    return MapResponse(
        side=side,
        snapshot_id=str(row["snapshot_id"] or ""),
        map=SoftwareMap.model_validate(map_dict),
        availability=availability_for(analysis_status, True),
    )


@router.get("/{analysis_id}/software-map", response_model=MapResponse)
def software_map(
    analysis_id: str,
    request: Request,
    side: Annotated[str, Query(pattern="^(base|target)$")] = "target",
) -> MapResponse:
    return _software_map_response(analysis_id, side, request)


@router.get("/{analysis_id}/map", response_model=MapResponse, include_in_schema=False)
def software_map_alias(
    analysis_id: str,
    request: Request,
    side: Annotated[str, Query(pattern="^(base|target)$")] = "target",
) -> MapResponse:
    """Batch-04 兼容别名；正式端点为 /software-map（B04-R1-04）。"""

    return _software_map_response(analysis_id, side, request)


# ---------------------------------------------------------------------------
# Findings / evidence
# ---------------------------------------------------------------------------


@router.get("/{analysis_id}/findings", response_model=FindingsPage)
def findings(
    analysis_id: str,
    request: Request,
    category: Annotated[str | None, Query()] = None,
    rule_id: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FindingsPage:
    repository: AnalysisRepository = request.app.state.repository
    _, analysis_status = _require_artifact(repository, analysis_id, "report")

    if category is not None and category not in {
        item.value for item in FindingCategory
    }:
        raise MorphoJudgeError(
            ErrorCode.INVALID_INPUT,
            f"unknown finding category filter: {category}",
            details={"category": category},
        )

    total, rows = repository.list_findings(
        analysis_id, category=category, rule_id=rule_id, limit=limit, offset=offset
    )
    items = [
        FindingListItem(
            finding=_finding_from_row(row),
            review_state=ReviewState(str(row["review_state"])),
        )
        for row in rows
    ]
    return FindingsPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        availability=availability_for(analysis_status, True),
    )


@router.get("/{analysis_id}/findings/{finding_id}", response_model=FindingDetail)
def finding_detail(analysis_id: str, finding_id: str, request: Request) -> FindingDetail:
    repository: AnalysisRepository = request.app.state.repository
    _require_artifact(repository, analysis_id, "report")
    row = repository.get_finding(analysis_id, finding_id)
    if row is None:
        raise MorphoJudgeError(
            ErrorCode.FINDING_NOT_FOUND, f"finding not found: {finding_id}"
        )
    finding = _finding_from_row(row)
    evidence = [
        _evidence_from_row(evidence_row)
        for evidence_row in repository.evidence_for_finding(
            analysis_id, finding.evidence_ids
        )
    ]
    return FindingDetail(
        finding=finding,
        review_state=ReviewState(str(row["review_state"])),
        evidence=evidence,
    )


@router.get("/{analysis_id}/evidence/{evidence_id}", response_model=EvidenceAnchor)
def evidence_detail(analysis_id: str, evidence_id: str, request: Request) -> EvidenceAnchor:
    repository: AnalysisRepository = request.app.state.repository
    _require_artifact(repository, analysis_id, "report")
    row = repository.get_evidence(analysis_id, evidence_id)
    if row is None:
        raise MorphoJudgeError(
            ErrorCode.EVIDENCE_NOT_FOUND, f"evidence not found: {evidence_id}"
        )
    return _evidence_from_row(row)


# ---------------------------------------------------------------------------
# Impact paths + summary
# ---------------------------------------------------------------------------


@router.get("/{analysis_id}/impact-paths", response_model=ImpactPathsPage)
def impact_paths(analysis_id: str, request: Request) -> ImpactPathsPage:
    repository: AnalysisRepository = request.app.state.repository
    document, analysis_status = _require_artifact(repository, analysis_id, "rules")
    items = [
        ImpactPathItem(
            origin_id=item["origin_id"],
            direction=item["direction"],
            node_ids=list(item["node_ids"]),
            edge_ids=list(item["edge_ids"]),
            evidence_ids=list(item["evidence_ids"]),
            resolution=item["resolution"],
            truncated=item["truncated"],
            stop_reason=item.get("stop_reason"),
            limit_note=item.get("limit_note"),
        )
        for item in document.get("impact_paths") or []
    ]
    return ImpactPathsPage(
        items=items,
        total=len(items),
        availability=availability_for(analysis_status, True),
    )


@router.get("/{analysis_id}/summary", response_model=SummaryResponse)
def summary(analysis_id: str, request: Request) -> SummaryResponse:
    repository: AnalysisRepository = request.app.state.repository
    document, analysis_status = _require_artifact(repository, analysis_id, "rules")
    row = _get_analysis_row(repository, analysis_id)
    identity = document.get("snapshot", {}).get("identity", {})
    failures = [
        {
            "path": resolution["request"]["path"],
            "start_line": resolution["request"]["start_line"],
            "reason": resolution.get("reason"),
        }
        for resolution in document.get("evidence_resolutions") or []
        if resolution.get("status") != "resolved"
    ]
    diff = document.get("diff") or {}
    diff_files = [
        DiffFileEntry.model_validate(file) for file in diff.get("files") or []
    ]
    result_ready = repository.result_document(analysis_id) is not None
    return SummaryResponse(
        analysis_id=analysis_id,
        snapshot_id=identity.get("snapshot_id"),
        base_commit=identity.get("base_commit"),
        target_commit=identity.get("target_commit"),
        rules_version=identity.get("rules_version"),
        status=AnalysisStatus(analysis_status),
        counts=AnalysisCounts(
            findings=repository.findings_count(analysis_id) if result_ready else None,
            evidence=repository.evidence_count(analysis_id) if result_ready else None,
        ),
        stage_coverage=_stage_coverage_items(document),
        limits=list(document.get("limits") or []),
        manifest=document.get("manifest"),
        evidence_resolution_failures=failures,
        diff_files=diff_files,
        availability=availability_for(analysis_status, True),
    )


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


@router.get("/{analysis_id}/reviews", response_model=ReviewsPage)
def list_reviews(analysis_id: str, request: Request) -> ReviewsPage:
    repository: AnalysisRepository = request.app.state.repository
    _, analysis_status = _require_artifact(repository, analysis_id, "report")
    rows = repository.list_reviews(analysis_id)
    items = [
        Review(
            finding_id=str(row["finding_id"]),
            state=ReviewState(str(row["state"])),
            note=str(row["note"]),
            updated_at=str(row["updated_at"]),
        )
        for row in rows
    ]
    return ReviewsPage(
        items=items,
        total=len(items),
        availability=availability_for(analysis_status, True),
    )


@router.post("/{analysis_id}/reviews", response_model=Review, status_code=201)
def save_review(
    analysis_id: str, payload: ReviewRequest, request: Request
) -> Review:
    repository: AnalysisRepository = request.app.state.repository
    _require_artifact(repository, analysis_id, "report")
    if repository.get_finding(analysis_id, payload.finding_id) is None:
        raise MorphoJudgeError(
            ErrorCode.FINDING_NOT_FOUND,
            f"finding not found in this analysis: {payload.finding_id}",
        )
    updated_at = utc_now()
    repository.save_review(
        analysis_id=analysis_id,
        finding_id=payload.finding_id,
        state=payload.state.value,
        note=payload.note,
        updated_at=updated_at,
    )
    return Review(
        finding_id=payload.finding_id,
        state=payload.state,
        note=payload.note,
        updated_at=updated_at,
    )
