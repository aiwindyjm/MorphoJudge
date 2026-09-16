"""Frozen domain contracts (Freeze 1 / V0.1.0-alpha, docs/contract-freezes.md).

This module is the single source of truth for MorphoJudge data contracts.
``packages/contracts/schema.json`` is generated from here, and
``app/lib/contracts.ts`` must stay field- and enum-identical with it.

Design rules enforced here:
- Deterministic relation fields (MapNode / MapEdge / DiffFileEntry /
  SelectionDecision / SnapshotIdentity ...) never contain free-text model
  output; model explanations live only in ``Explanation``.
- All IDs are strings. Commit SHAs are 40-char lowercase hex. Line numbers
  are positive integers. Byte counts are non-negative integers or null.
- Repo-relative paths are POSIX style, never absolute, never ``..``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 契约版本历史（F1.4）：1.0.0 = Batch-01 首发；1.1.0 = Batch-02 累计向后兼容追加
# （MapNode.note、SoftwareMap、MapEdge.file_path/line、FileParseReport、ParseStatus）。
SCHEMA_VERSION = "1.1.0"

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$")
_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


class ContractModel(BaseModel):
    """Base config for every MorphoJudge contract: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def validate_repo_relative_path(value: str) -> str:
    """Repo-relative POSIX path: non-empty, no leading slash, no ``..``/``.``/empty parts."""
    if not value or "\x00" in value:
        raise ValueError("path must be a non-empty string without NUL")
    if value.startswith(("/", "\\")):
        raise ValueError(f"path must be repo-relative, got absolute path: {value!r}")
    if "\\" in value:
        raise ValueError(f"path must use POSIX separators: {value!r}")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"path contains empty/'.'/'..' component: {value!r}")
    return value


def _validate_commit(value: str) -> str:
    if not _COMMIT_RE.fullmatch(value):
        raise ValueError(f"commit must be a 40-char lowercase hex SHA: {value!r}")
    return value


def _validate_rule_id(value: str) -> str:
    if not _RULE_ID_RE.fullmatch(value):
        raise ValueError(f"rule_id must look like 'SEL-PRIVATE-PATH': {value!r}")
    return value


# ---------------------------------------------------------------------------
# Enums (values must match app/lib/contracts.ts literally)
# ---------------------------------------------------------------------------


class AnalysisStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_LIMITS = "completed_with_limits"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StageName(StrEnum):
    GIT = "git"
    SELECTION = "selection"
    PARSE = "parse"
    BEHAVIOR = "behavior"
    DEPENDENCY = "dependency"
    REPORT = "report"
    EXPLAIN = "explain"


class DiffFileStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"
    UNMERGED = "unmerged"


class SelectionStatus(StrEnum):
    SELECTED = "selected"
    EXCLUDED = "excluded"
    LIMITED = "limited"
    FAILED = "failed"


class MapNodeKind(StrEnum):
    PAGE = "page"
    FEATURE = "feature"
    METHOD = "method"
    CONTRACT = "contract"
    DATA = "data"
    EXTERNAL_SERVICE = "external_service"


class MapRelation(StrEnum):
    TRIGGERS = "triggers"
    IMPLEMENTS = "implements"
    CALLS = "calls"
    ACCEPTS = "accepts"
    RETURNS = "returns"
    READS = "reads"
    WRITES = "writes"
    SENDS = "sends"


class Resolution(StrEnum):
    RESOLVED = "resolved"
    CANDIDATE = "candidate"
    UNRESOLVED = "unresolved"


class ParseStatus(StrEnum):
    """File-level parse outcome; every relation edge must be traceable to
    the parse status of the file its provenance points at."""

    PARSED = "parsed"
    PARSED_WITH_ERRORS = "parsed_with_errors"
    LIMITED = "limited"
    ERROR = "error"


class EvidenceSide(StrEnum):
    OLD = "old"
    NEW = "new"
    CONTEXT = "context"


class FindingCategory(StrEnum):
    BEHAVIOR_NETWORK = "behavior_network"
    BEHAVIOR_SHELL = "behavior_shell"
    BEHAVIOR_FILE = "behavior_file"
    BEHAVIOR_PERMISSION = "behavior_permission"
    DEPENDENCY = "dependency"
    CONSISTENCY = "consistency"
    STRUCTURE = "structure"


class FindingKind(StrEnum):
    """Deterministic finding provenance. Model output never becomes a Finding kind."""

    FACT = "fact"
    RULE_HINT = "rule_hint"


class FindingImpact(StrEnum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingReliability(StrEnum):
    DETERMINISTIC = "deterministic"
    RULE_BASED = "rule_based"


class ReviewState(StrEnum):
    UNREVIEWED = "unreviewed"
    NEEDS_INVESTIGATION = "needs-investigation"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


class ExplanationClaimKind(StrEnum):
    RESTATEMENT = "restatement"
    INFERENCE = "inference"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Snapshot identity (GIT-001)
# ---------------------------------------------------------------------------


class SnapshotIdentity(ContractModel):
    """Immutable identity of the analyzed input.

    snapshot_id = sha256("morphojudge-snapshot-v1\\0" + repository_id + "\\0"
                         + base_commit + "\\0" + target_commit + "\\0"
                         + rules_version)  — see docs/contract-freezes.md F1.5.
    """

    repository_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    canonical_path: str = Field(min_length=1)
    base_commit: str
    target_commit: str
    snapshot_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    rules_version: str = Field(min_length=1)

    _commits = field_validator("base_commit", "target_commit", mode="before")(_validate_commit)


class WorkingTreeEntry(ContractModel):
    """One `git status --porcelain` entry, informational only (never analyzed)."""

    path: str
    old_path: Optional[str] = None
    x: str = Field(min_length=1, max_length=1)
    y: str = Field(min_length=1, max_length=1)

    @field_validator("path", "old_path")
    @classmethod
    def _path(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return validate_repo_relative_path(value)


class SnapshotReport(ContractModel):
    """Snapshot resolution result: identity plus boundary observations.

    workspace_dirty does not change snapshot_id (identity binds commits).
    notes carry explicit limitation flags such as submodule_present,
    lfs_filter_present, base_equals_target.
    """

    identity: SnapshotIdentity
    workspace_dirty: bool
    working_tree: List[WorkingTreeEntry] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Diff (GIT-002)
# ---------------------------------------------------------------------------


class LineMapHunk(ContractModel):
    """Old↔new line ranges of one diff hunk (zero-context hunks, U0)."""

    old_start: int = Field(ge=0)
    old_count: int = Field(ge=0)
    new_start: int = Field(ge=0)
    new_count: int = Field(ge=0)


class DiffFileEntry(ContractModel):
    """One changed file between base and target commits.

    path is the target-side path when it exists there, otherwise the
    base-side path (deleted files). old_path is set only for renames/copies
    where the base-side name differs.
    """

    path: str
    old_path: Optional[str] = None
    status: DiffFileStatus
    additions: Optional[int] = Field(default=None, ge=0)
    deletions: Optional[int] = Field(default=None, ge=0)
    binary: bool = False
    is_symlink: bool = False
    is_submodule: bool = False
    old_bytes: Optional[int] = Field(default=None, ge=0)
    new_bytes: Optional[int] = Field(default=None, ge=0)
    line_map: Optional[List[LineMapHunk]] = None
    line_map_unavailable_reason: Optional[str] = None

    _paths = field_validator("path", "old_path", mode="before")(
        lambda v: None if v is None else validate_repo_relative_path(v)
    )

    @model_validator(mode="after")
    def _binary_implies_unmapped(self) -> "DiffFileEntry":
        if self.binary:
            if self.additions is not None or self.deletions is not None:
                raise ValueError("binary file must have null additions/deletions")
            if self.line_map is not None:
                raise ValueError("binary file must have null line_map")
            if not self.line_map_unavailable_reason:
                self.line_map_unavailable_reason = "binary_file"
        if self.line_map is None and not self.line_map_unavailable_reason and not self.binary:
            # Non-binary entries produced by the engine always carry a hunk map
            # (possibly empty for pure renames); a null map without a reason is
            # accepted only for synthetic inputs, so no hard failure here.
            pass
        return self


class DiffResult(ContractModel):
    """Commit-to-commit diff. Untracked files never appear in files; they are
    reported separately per the frozen untracked policy (F1.5)."""

    snapshot_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    base_commit: str
    target_commit: str
    files: List[DiffFileEntry] = Field(default_factory=list)
    untracked: List[str] = Field(default_factory=list)
    policy_note: str = (
        "commit-to-commit diff only; working-tree untracked files are recorded "
        "informationally and never analyzed"
    )

    _commits = field_validator("base_commit", "target_commit", mode="before")(_validate_commit)
    _untracked = field_validator("untracked", mode="before")(
        lambda v: [validate_repo_relative_path(p) for p in v]
    )


# ---------------------------------------------------------------------------
# Selection / coverage (SEL-001)
# ---------------------------------------------------------------------------


class SelectionDecision(ContractModel):
    """Deterministic per-file selection decision.

    rule_id/language/bytes are REQUIRED (non-null) whenever status is
    excluded/limited/failed so every coverage gap is attributable.
    path is informational and may carry the offending path of a rejected
    entry (e.g. traversal attempts); only DiffFileEntry enforces the strict
    repo-relative shape.
    """

    snapshot_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    path: str
    status: SelectionStatus
    reason: str = Field(min_length=1)
    rule_id: Optional[str] = None
    language: Optional[str] = None
    bytes: Optional[int] = Field(default=None, ge=0)

    @field_validator("path")
    @classmethod
    def _display_path(cls, value: str) -> str:
        if not value or "\x00" in value or len(value) > 1024:
            raise ValueError("path must be 1..1024 chars without NUL")
        return value

    _rule = field_validator("rule_id", mode="before")(
        lambda v: None if v is None else _validate_rule_id(v)
    )

    @model_validator(mode="after")
    def _require_rule_fields(self) -> "SelectionDecision":
        if self.status == SelectionStatus.SELECTED:
            return self
        missing = [
            name
            for name, value in (
                ("rule_id", self.rule_id),
                ("language", self.language),
                ("bytes", self.bytes),
            )
            if value is None
        ]
        # language may legitimately be null for path violations where no
        # language detection applies; bytes may be null when size is unknown.
        if self.rule_id is None:
            raise ValueError(
                f"non-selected decision requires rule_id; missing: {missing}"
            )
        return self


class CoverageSummary(ContractModel):
    """Counts over SelectionDecisions. `completed` counts files whose analysis
    finished (always 0 at selection time, reserved for later stages).
    `skipped` is the display alias of `excluded`. `updated_at` stays null for
    deterministic pure runs (FR-010 reproducibility); callers may stamp it."""

    snapshot_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    total: int = Field(ge=0)
    selected: int = Field(ge=0)
    excluded: int = Field(ge=0)
    limited: int = Field(ge=0)
    failed: int = Field(ge=0)
    completed: int = Field(default=0, ge=0)
    partial: bool
    updated_at: Optional[str] = None

    @field_validator("updated_at")
    @classmethod
    def _iso(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _ISO8601_RE.fullmatch(value):
            raise ValueError(f"updated_at must be ISO-8601: {value!r}")
        return value

    @model_validator(mode="after")
    def _counts_consistent(self) -> "CoverageSummary":
        if self.selected + self.excluded + self.limited + self.failed != self.total:
            raise ValueError("status counts must sum to total")
        if self.completed > self.selected:
            raise ValueError("completed cannot exceed selected")
        return self


# ---------------------------------------------------------------------------
# Software map (IR placeholders; populated from Batch-02)
# ---------------------------------------------------------------------------


class MapNode(ContractModel):
    """Deterministic graph node. No model free-text: explanations attach via ids.

    `note` carries deterministic provenance only (e.g. "feature_mapping:human"),
    never model output.
    """

    id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    kind: MapNodeKind
    label: str = Field(min_length=1)
    language: Optional[str] = None
    file_path: Optional[str] = None
    start_line: Optional[int] = Field(default=None, ge=1)
    end_line: Optional[int] = Field(default=None, ge=1)
    resolution: Resolution
    evidence_ids: List[str] = Field(default_factory=list)
    note: Optional[str] = None

    @field_validator("file_path")
    @classmethod
    def _path(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_repo_relative_path(value)

    @model_validator(mode="after")
    def _lines(self) -> "MapNode":
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must be >= start_line")
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("start_line and end_line must be set together")
        return self


class MapEdge(ContractModel):
    """Deterministic relation edge with provenance. Candidate edges must keep
    resolution=candidate; they never upgrade without new deterministic evidence.

    Provenance (non-lossy source location):
    - file_path/line point at the call/reference site (1-based line);
    - manifest-derived edges (triggers/implements) carry file_path of the
      mapping target; line is null only when no single line applies;
    - the file's parse status is reachable via SoftwareMap.file_reports
      keyed by file_path, and the edge is bound to its snapshot_id.
    """

    id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    relation: MapRelation
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    resolution: Resolution
    evidence_ids: List[str] = Field(default_factory=list)
    note: Optional[str] = None
    file_path: Optional[str] = None
    line: Optional[int] = Field(default=None, ge=1)

    @field_validator("file_path")
    @classmethod
    def _edge_path(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_repo_relative_path(value)


class FileParseReport(ContractModel):
    """Parse status of one analyzed file inside a snapshot (contract-level)."""

    path: str
    status: ParseStatus
    issue_count: int = Field(ge=0)
    note: Optional[str] = None

    _path = field_validator("path", mode="before")(validate_repo_relative_path)


class SoftwareMap(ContractModel):
    """Deterministic software map for one snapshot (REL-001).

    nodes/edges are the graph; truncated means a build/query budget was hit
    (limits lists the named budgets). Bad edges stay in the list while the
    adjacency layer records them as errors. file_reports makes every edge's
    provenance traceable to the parse status of its source file.
    """

    snapshot_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    nodes: List[MapNode] = Field(default_factory=list)
    edges: List[MapEdge] = Field(default_factory=list)
    truncated: bool = False
    limits: List[str] = Field(default_factory=list)
    file_reports: List[FileParseReport] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evidence / findings / explanation / review
# ---------------------------------------------------------------------------


class EvidenceAnchor(ContractModel):
    """Traceable evidence location inside one snapshot."""

    id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    path: str
    side: EvidenceSide
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    snippet: str
    source_kind: str = Field(min_length=1)
    rule_id: Optional[str] = None
    commit: Optional[str] = None

    _path = field_validator("path", mode="before")(validate_repo_relative_path)
    _rule = field_validator("rule_id", mode="before")(
        lambda v: None if v is None else _validate_rule_id(v)
    )
    _commit = field_validator("commit", mode="before")(
        lambda v: None if v is None else _validate_commit(v)
    )

    @model_validator(mode="after")
    def _lines(self) -> "EvidenceAnchor":
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        return self


class Finding(ContractModel):
    """A deterministic finding backed by evidence, or an explicitly unresolved one."""

    id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    category: FindingCategory
    kind: FindingKind
    impact: FindingImpact
    reliability: FindingReliability
    evidence_ids: List[str] = Field(default_factory=list)
    explanation_id: Optional[str] = None
    rule_id: Optional[str] = None
    unresolved_reason: Optional[str] = None

    _rule = field_validator("rule_id", mode="before")(
        lambda v: None if v is None else _validate_rule_id(v)
    )

    @model_validator(mode="after")
    def _evidence_or_reason(self) -> "Finding":
        if not self.evidence_ids and not self.unresolved_reason:
            raise ValueError("finding requires evidence_ids or an explicit unresolved_reason")
        if self.evidence_ids and self.unresolved_reason:
            raise ValueError("finding cannot have both evidence_ids and unresolved_reason")
        return self


class ExplanationClaim(ContractModel):
    text: str = Field(min_length=1)
    evidence_ids: List[str] = Field(min_length=1)
    kind: ExplanationClaimKind


class Explanation(ContractModel):
    """Model output, always separate from deterministic facts. Claims must
    reference existing evidence ids (validated at persistence time)."""

    id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    claims: List[ExplanationClaim] = Field(min_length=1)
    uncertainty: str
    errors: Optional[List[str]] = None


class Review(ContractModel):
    finding_id: str = Field(min_length=1)
    state: ReviewState
    note: str = ""
    updated_at: str

    @field_validator("updated_at")
    @classmethod
    def _updated(cls, value: str) -> str:
        if not _ISO8601_RE.fullmatch(value):
            raise ValueError(f"updated_at must be ISO-8601: {value!r}")
        return value


# ---------------------------------------------------------------------------
# Analysis session
# ---------------------------------------------------------------------------


class StageRecord(ContractModel):
    stage: StageName
    status: StageStatus
    detail: Optional[str] = None


class ImmutableAnalysisInput(ContractModel):
    repository_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    base_ref: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    rules_version: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")


class AnalysisSession(ContractModel):
    id: str = Field(min_length=1)
    status: AnalysisStatus
    immutable_input: ImmutableAnalysisInput
    stages: List[StageRecord] = Field(default_factory=list)
    resumable: bool = True
    budget: Optional[Dict[str, int]] = None
    failure_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Registry used for JSON Schema export (packages/contracts/schema.json)
# ---------------------------------------------------------------------------

CONTRACT_MODELS: List[type[ContractModel]] = [
    AnalysisSession,
    AnalysisStatus,
    CoverageSummary,
    DiffFileEntry,
    DiffFileStatus,
    DiffResult,
    EvidenceAnchor,
    EvidenceSide,
    Explanation,
    ExplanationClaim,
    ExplanationClaimKind,
    FileParseReport,
    Finding,
    FindingCategory,
    FindingImpact,
    FindingKind,
    FindingReliability,
    ImmutableAnalysisInput,
    LineMapHunk,
    MapEdge,
    MapNode,
    MapNodeKind,
    MapRelation,
    ParseStatus,
    Resolution,
    Review,
    ReviewState,
    SelectionDecision,
    SelectionStatus,
    SnapshotIdentity,
    SnapshotReport,
    SoftwareMap,
    StageName,
    StageRecord,
    StageStatus,
    WorkingTreeEntry,
]

__all__ = [
    "SCHEMA_VERSION",
    "ContractModel",
    "CONTRACT_MODELS",
    "validate_repo_relative_path",
    # enums
    "AnalysisStatus",
    "StageStatus",
    "StageName",
    "DiffFileStatus",
    "SelectionStatus",
    "MapNodeKind",
    "MapRelation",
    "Resolution",
    "EvidenceSide",
    "FindingCategory",
    "FindingKind",
    "FindingImpact",
    "FindingReliability",
    "ReviewState",
    "ExplanationClaimKind",
    # models
    "SnapshotIdentity",
    "WorkingTreeEntry",
    "SnapshotReport",
    "LineMapHunk",
    "DiffFileEntry",
    "DiffResult",
    "SelectionDecision",
    "CoverageSummary",
    "MapNode",
    "MapEdge",
    "SoftwareMap",
    "FileParseReport",
    "ParseStatus",
    "EvidenceAnchor",
    "Finding",
    "ExplanationClaim",
    "Explanation",
    "Review",
    "StageRecord",
    "ImmutableAnalysisInput",
    "AnalysisSession",
]
