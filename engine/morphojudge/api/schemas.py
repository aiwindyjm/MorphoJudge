"""API request/response schemas (Freeze 4 登记，docs/contract-freezes.md).

Transport contracts on top of the frozen domain models. Every response
carries ``schema_version``; the frozen entities (Finding, EvidenceAnchor,
SelectionDecision, CoverageSummary, SoftwareMap, StageRecord, Review,
DiffFileEntry) are reused verbatim so the API can never drift from the
persistence layer. All DTOs are exported into packages/contracts/schema.json
(``api_models``) and mirrored in app/lib/contracts.ts with drift tests.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, List, Optional

from pydantic import Field, field_validator

from ..contracts.domain import (
    SCHEMA_VERSION,
    AnalysisStatus,
    ContractModel,
    CoverageSummary,
    DiffFileEntry,
    EvidenceAnchor,
    Finding,
    Review,
    ReviewState,
    SelectionDecision,
    SoftwareMap,
    StageRecord,
)

# 未显式指定时绑定的规则版本（进入 snapshot_id，变更需按 F1.5 重新推导语义）
DEFAULT_RULES_VERSION = "morphojudge-v0.1"

_REPOSITORY_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class ApiContractModel(ContractModel):
    """API DTO 基类：未知字段拒绝 + 每个响应携带 schema_version。"""

    schema_version: str = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class AnalysisOptions(ContractModel):
    """本批支持的显式分析选项（超出范围的选项一律被拒绝）。

    两个预算直接进入确定性影响遍历；持久化在 analyses.options_json，
    是恢复输入的一部分。
    """

    impact_max_depth: int = Field(default=10, ge=1, le=50)
    impact_max_nodes: int = Field(default=1000, ge=1, le=5000)


class CreateAnalysisRequest(ContractModel):
    """POST /v1/analyses body — registered repository ids only, never paths."""

    repository_id: str = Field(min_length=64, max_length=64)
    base_ref: str = Field(min_length=1, max_length=200)
    target_ref: str = Field(min_length=1, max_length=200)
    rules_version: str = Field(default=DEFAULT_RULES_VERSION, min_length=1, max_length=100)
    options: Optional[AnalysisOptions] = None

    @field_validator("repository_id")
    @classmethod
    def _hex64(cls, value: str) -> str:
        if not _REPOSITORY_ID_RE.fullmatch(value):
            raise ValueError("repository_id must be 64 lowercase hex chars")
        return value

    def canonical_payload(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)


class ReviewRequest(ContractModel):
    finding_id: str = Field(min_length=1)
    state: ReviewState
    note: str = ""


# ---------------------------------------------------------------------------
# Common response pieces
# ---------------------------------------------------------------------------


class Availability(ApiContractModel):
    """结果可用性：产物是否完整、分析处于什么状态。

    artifacts=complete 表示该端点所需产物已全部提交；partial 表示分析
    未到完成态（failed/cancelled/running）但已提交产物可读。无产物不是
    空的安全结论——没有产物时端点返回 409，不返回空集合。
    """

    analysis_status: AnalysisStatus
    artifacts: str = Field(pattern="^(complete|partial)$")
    note: str = ""


class AnalysisCounts(ContractModel):
    findings: Optional[int] = None
    evidence: Optional[int] = None


class RepositoryInfo(ApiContractModel):
    """仓库登记信息——内部 canonical_path 不出 API（B04-R1-06）。"""

    repository_id: str
    name: str
    registered_at: str


class RepositoriesPage(ApiContractModel):
    items: List[RepositoryInfo] = Field(default_factory=list)


class AnalysisResponse(ApiContractModel):
    analysis_id: str
    status: AnalysisStatus
    repository_id: str
    base_ref: str
    target_ref: str
    rules_version: str
    snapshot_id: Optional[str] = None
    resumable: bool = True
    cancel_requested: bool = False
    created_at: str
    updated_at: str
    failure_reason: Optional[str] = None
    stages: List[StageRecord] = Field(default_factory=list)
    counts: AnalysisCounts = Field(default_factory=AnalysisCounts)


# ---------------------------------------------------------------------------
# Result payloads (API-002)
# ---------------------------------------------------------------------------


class StageCoverageItem(ApiContractModel):
    stage: str
    status: str
    completed: int
    failed: int
    limited: int
    notes: List[str] = Field(default_factory=list)


class FindingListItem(ApiContractModel):
    finding: Finding
    review_state: ReviewState = ReviewState.UNREVIEWED


class FindingDetail(ApiContractModel):
    finding: Finding
    review_state: ReviewState = ReviewState.UNREVIEWED
    evidence: List[EvidenceAnchor] = Field(default_factory=list)


class FindingsPage(ApiContractModel):
    items: List[FindingListItem] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    availability: Availability


class CoverageResponse(ApiContractModel):
    summary: CoverageSummary
    decisions: List[SelectionDecision] = Field(default_factory=list)
    stage_coverage: List[StageCoverageItem] = Field(default_factory=list)
    limits: List[str] = Field(default_factory=list)
    manifest: Optional[dict] = None
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    availability: Availability


class MapResponse(ApiContractModel):
    side: str  # "base" | "target"
    snapshot_id: str
    map: SoftwareMap
    availability: Availability


class ImpactPathItem(ApiContractModel):
    origin_id: str
    direction: str
    node_ids: List[str] = Field(default_factory=list)
    edge_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    resolution: str
    truncated: bool
    stop_reason: Optional[str] = None
    limit_note: Optional[str] = None


class ImpactPathsPage(ApiContractModel):
    items: List[ImpactPathItem] = Field(default_factory=list)
    total: int = Field(ge=0)
    availability: Availability


class SummaryResponse(ApiContractModel):
    analysis_id: str
    snapshot_id: Optional[str] = None
    base_commit: Optional[str] = None
    target_commit: Optional[str] = None
    rules_version: Optional[str] = None
    status: AnalysisStatus
    counts: AnalysisCounts = Field(default_factory=AnalysisCounts)
    stage_coverage: List[StageCoverageItem] = Field(default_factory=list)
    limits: List[str] = Field(default_factory=list)
    manifest: Optional[dict] = None
    evidence_resolution_failures: List[dict] = Field(default_factory=list)
    diff_files: List[DiffFileEntry] = Field(default_factory=list)
    availability: Availability


class ReviewsPage(ApiContractModel):
    items: List[Review] = Field(default_factory=list)
    total: int = Field(ge=0)
    availability: Availability


# 注册进 packages/contracts/schema.json 的 API DTO（export.py 消费）
API_CONTRACT_MODELS: list[type[ContractModel]] = [
    AnalysisCounts,
    AnalysisOptions,
    AnalysisResponse,
    Availability,
    CoverageResponse,
    CreateAnalysisRequest,
    FindingDetail,
    FindingListItem,
    FindingsPage,
    ImpactPathItem,
    ImpactPathsPage,
    MapResponse,
    RepositoriesPage,
    RepositoryInfo,
    ReviewRequest,
    ReviewsPage,
    StageCoverageItem,
    SummaryResponse,
]


# ---------------------------------------------------------------------------
# Idempotency (Freeze 4)
# ---------------------------------------------------------------------------

_IDEMPOTENCY_DOMAIN = "morphojudge-analysis-v1"


def validate_idempotency_header(value: str) -> str:
    if not value or len(value) > 200 or any(ch in value for ch in "\x00\n\r"):
        raise ValueError(
            "Idempotency-Key must be 1..200 chars without control characters"
        )
    return value


def derive_idempotency_key(header_value: str | None, request: CreateAnalysisRequest) -> str:
    if header_value is not None:
        return validate_idempotency_header(header_value)
    digest = hashlib.sha256(request.canonical_payload().encode("utf-8")).hexdigest()
    return f"{_IDEMPOTENCY_DOMAIN}:auto:{digest}"


def request_hash(request: CreateAnalysisRequest) -> str:
    return hashlib.sha256(request.canonical_payload().encode("utf-8")).hexdigest()


def analysis_id_for(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"analysis:{digest[:24]}"


def availability_for(status: str, artifacts_complete: bool) -> Availability:
    if status in ("completed", "completed_with_limits"):
        return Availability(
            analysis_status=AnalysisStatus(status), artifacts="complete"
        )
    return Availability(
        analysis_status=AnalysisStatus(status),
        artifacts="partial",
        note="analysis did not reach a completed state; committed artifacts are readable as-is",
    )
