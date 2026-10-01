/**
 * MorphoJudge core contracts (Freeze 1 / V0.1.0-alpha).
 *
 * Mirror of engine/morphojudge/contracts/domain.py + errors.py.
 * The JSON Schema in packages/contracts/schema.json is generated from the
 * Python models; a pytest guard asserts that this file stays field- and
 * enum-identical with it. Semantic rules:
 * - Deterministic relation types (SnapshotIdentity, DiffFileEntry,
 *   SelectionDecision, MapNode, MapEdge...) never carry model free-text;
 *   model output lives only in Explanation.
 * - All ids are strings; commits are 40-char lowercase hex; line numbers are
 *   positive integers; repo paths are POSIX-relative (never absolute, never '..').
 * - Excluded/limited/failed selection decisions always carry rule_id.
 */

// 契约版本历史（F1.4）：1.0.0 = Batch-01 首发；1.1.0 = Batch-02 累计向后兼容追加
// （MapNode.note、SoftwareMap、MapEdge.file_path/line、FileParseReport、ParseStatus）；
// 1.2.0 = Batch-04 兼容追加（ErrorCode 新增 6 个 API 错误码）；
// 1.3.0 = Batch-04-R1 兼容追加（API DTO 入公开契约、ROUTE_NOT_FOUND、AnalysisOptions）；
// 1.4.0 = Batch-06 兼容追加（explain 端点 DTO 与 EXPLAIN_* 错误码）。
export const SCHEMA_VERSION = '1.4.0';

// ---------------------------------------------------------------------------
// Enums (single-line literal unions; keep values byte-identical with Python)
// ---------------------------------------------------------------------------

export type AnalysisStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'completed_with_limits'
  | 'failed'
  | 'cancelled';

export type StageStatus = 'queued' | 'running' | 'completed' | 'failed' | 'skipped';

export type StageName =
  | 'git'
  | 'selection'
  | 'parse'
  | 'behavior'
  | 'dependency'
  | 'report'
  | 'explain';

export type DiffFileStatus =
  | 'added'
  | 'modified'
  | 'deleted'
  | 'renamed'
  | 'copied'
  | 'type_changed'
  | 'unmerged';

export type SelectionStatus = 'selected' | 'excluded' | 'limited' | 'failed';

export type MapNodeKind =
  | 'page'
  | 'feature'
  | 'method'
  | 'contract'
  | 'data'
  | 'external_service';

export type MapRelation =
  | 'triggers'
  | 'implements'
  | 'calls'
  | 'accepts'
  | 'returns'
  | 'reads'
  | 'writes'
  | 'sends';

export type Resolution = 'resolved' | 'candidate' | 'unresolved';

export type ParseStatus = 'parsed' | 'parsed_with_errors' | 'limited' | 'error';

export type EvidenceSide = 'old' | 'new' | 'context';

export type FindingCategory =
  | 'behavior_network'
  | 'behavior_shell'
  | 'behavior_file'
  | 'behavior_permission'
  | 'dependency'
  | 'consistency'
  | 'structure';

export type FindingKind = 'fact' | 'rule_hint';

export type FindingImpact = 'unknown' | 'low' | 'medium' | 'high';

export type FindingReliability = 'deterministic' | 'rule_based';

export type ReviewState =
  | 'unreviewed'
  | 'needs-investigation'
  | 'confirmed'
  | 'dismissed';

export type ExplanationClaimKind = 'restatement' | 'inference' | 'unknown';

export type ErrorCode =
  | 'REPOSITORY_NOT_FOUND'
  | 'NOT_A_GIT_REPOSITORY'
  | 'LINKED_WORKTREE_NOT_SUPPORTED'
  | 'REF_NOT_FOUND'
  | 'PATH_OUT_OF_ROOTS'
  | 'PATH_TRAVERSAL_DETECTED'
  | 'SYMLINK_ESCAPE'
  | 'GIT_COMMAND_FAILED'
  | 'INVALID_INPUT'
  | 'INTERNAL_ERROR'
  | 'ANALYSIS_NOT_FOUND'
  | 'ANALYSIS_NOT_READY'
  | 'ANALYSIS_CONFLICT'
  | 'REPOSITORY_NOT_REGISTERED'
  | 'FINDING_NOT_FOUND'
  | 'EVIDENCE_NOT_FOUND'
  | 'ROUTE_NOT_FOUND'
  | 'EXPLAIN_SUBJECT_NOT_FOUND'
  | 'EXPLAIN_EVIDENCE_NOT_FOUND'
  | 'EXPLAIN_PROVIDER_UNAVAILABLE'
  | 'EXPLAIN_CONSENT_REQUIRED';

// ---------------------------------------------------------------------------
// Snapshot identity (GIT-001)
// ---------------------------------------------------------------------------

export interface SnapshotIdentity {
  readonly repository_id: string;
  readonly canonical_path: string;
  readonly base_commit: string;
  readonly target_commit: string;
  readonly snapshot_id: string;
  readonly rules_version: string;
}

export interface WorkingTreeEntry {
  readonly path: string;
  readonly old_path: string | null;
  readonly x: string;
  readonly y: string;
}

export interface SnapshotReport {
  readonly identity: SnapshotIdentity;
  readonly workspace_dirty: boolean;
  readonly working_tree: WorkingTreeEntry[];
  readonly notes: string[];
}

// ---------------------------------------------------------------------------
// Diff (GIT-002)
// ---------------------------------------------------------------------------

export interface LineMapHunk {
  readonly old_start: number;
  readonly old_count: number;
  readonly new_start: number;
  readonly new_count: number;
}

export interface DiffFileEntry {
  readonly path: string;
  readonly old_path: string | null;
  readonly status: DiffFileStatus;
  readonly additions: number | null;
  readonly deletions: number | null;
  readonly binary: boolean;
  readonly is_symlink: boolean;
  readonly is_submodule: boolean;
  readonly old_bytes: number | null;
  readonly new_bytes: number | null;
  readonly line_map: LineMapHunk[] | null;
  readonly line_map_unavailable_reason: string | null;
}

export interface DiffResult {
  readonly snapshot_id: string;
  readonly base_commit: string;
  readonly target_commit: string;
  readonly files: DiffFileEntry[];
  readonly untracked: string[];
  readonly policy_note: string;
}

// ---------------------------------------------------------------------------
// Selection / coverage (SEL-001)
// ---------------------------------------------------------------------------

export interface SelectionDecision {
  readonly snapshot_id: string;
  readonly path: string;
  readonly status: SelectionStatus;
  readonly reason: string;
  readonly rule_id: string | null;
  readonly language: string | null;
  readonly bytes: number | null;
}

export interface CoverageSummary {
  readonly snapshot_id: string;
  readonly total: number;
  readonly selected: number;
  readonly excluded: number;
  readonly limited: number;
  readonly failed: number;
  readonly completed: number;
  readonly partial: boolean;
  readonly updated_at: string | null;
}

// ---------------------------------------------------------------------------
// Software map (IR)
// ---------------------------------------------------------------------------

export interface MapNode {
  readonly id: string;
  readonly snapshot_id: string;
  readonly kind: MapNodeKind;
  readonly label: string;
  readonly language: string | null;
  readonly file_path: string | null;
  readonly start_line: number | null;
  readonly end_line: number | null;
  readonly resolution: Resolution;
  readonly evidence_ids: string[];
  readonly note: string | null;
}

export interface MapEdge {
  readonly id: string;
  readonly snapshot_id: string;
  readonly relation: MapRelation;
  readonly source_id: string;
  readonly target_id: string;
  readonly resolution: Resolution;
  readonly evidence_ids: string[];
  readonly note: string | null;
  readonly file_path: string | null;
  readonly line: number | null;
}

export interface FileParseReport {
  readonly path: string;
  readonly status: ParseStatus;
  readonly issue_count: number;
  readonly note: string | null;
}

export interface SoftwareMap {
  readonly snapshot_id: string;
  readonly nodes: MapNode[];
  readonly edges: MapEdge[];
  readonly truncated: boolean;
  readonly limits: string[];
  readonly file_reports: FileParseReport[];
}

// ---------------------------------------------------------------------------
// Evidence / findings / explanation / review
// ---------------------------------------------------------------------------

export interface EvidenceAnchor {
  readonly id: string;
  readonly snapshot_id: string;
  readonly path: string;
  readonly side: EvidenceSide;
  readonly start_line: number;
  readonly end_line: number;
  readonly snippet: string;
  readonly source_kind: string;
  readonly rule_id: string | null;
  readonly commit: string | null;
}

export interface Finding {
  readonly id: string;
  readonly snapshot_id: string;
  readonly category: FindingCategory;
  readonly kind: FindingKind;
  readonly impact: FindingImpact;
  readonly reliability: FindingReliability;
  readonly evidence_ids: string[];
  readonly explanation_id: string | null;
  readonly rule_id: string | null;
  readonly unresolved_reason: string | null;
}

export interface ExplanationClaim {
  readonly text: string;
  readonly evidence_ids: string[];
  readonly kind: ExplanationClaimKind;
}

export interface Explanation {
  readonly id: string;
  readonly provider: string;
  readonly model: string;
  readonly claims: ExplanationClaim[];
  readonly uncertainty: string;
  readonly errors: string[] | null;
}

export interface Review {
  readonly finding_id: string;
  readonly state: ReviewState;
  readonly note: string;
  readonly updated_at: string;
}

// ---------------------------------------------------------------------------
// Analysis session
// ---------------------------------------------------------------------------

export interface StageRecord {
  readonly stage: StageName;
  readonly status: StageStatus;
  readonly detail: string | null;
}

export interface ImmutableAnalysisInput {
  readonly repository_id: string;
  readonly base_ref: string;
  readonly target_ref: string;
  readonly rules_version: string;
  readonly snapshot_id: string;
}

export interface AnalysisSession {
  readonly id: string;
  readonly status: AnalysisStatus;
  readonly immutable_input: ImmutableAnalysisInput;
  readonly stages: StageRecord[];
  readonly resumable: boolean;
  readonly budget: Record<string, number> | null;
  readonly failure_reason: string | null;
}

// ---------------------------------------------------------------------------
// Unified error object (ARC-001 / Freeze 1 §F1.3)
// ---------------------------------------------------------------------------

export interface ErrorObject {
  readonly code: ErrorCode;
  readonly message: string;
  readonly retryable: boolean;
  readonly details: Record<string, unknown>;
}

export interface ErrorResponse {
  readonly schema_version: string;
  readonly error: ErrorObject;
}

// ---------------------------------------------------------------------------
// API transport contracts (Batch-04-R1 / Freeze 4 登记)
// 每个响应携带 schema_version；可用性显式标注 partial，无产物不是空结论。
// ---------------------------------------------------------------------------

export interface Availability {
  readonly schema_version: string;
  readonly analysis_status: AnalysisStatus;
  readonly artifacts: 'complete' | 'partial';
  readonly note: string;
}

export interface AnalysisOptions {
  readonly impact_max_depth: number;
  readonly impact_max_nodes: number;
}

export interface CreateAnalysisRequest {
  readonly repository_id: string;
  readonly base_ref: string;
  readonly target_ref: string;
  readonly rules_version: string;
  readonly options: AnalysisOptions | null;
}

export interface RepositoryInfo {
  readonly schema_version: string;
  readonly repository_id: string;
  readonly name: string;
  readonly registered_at: string;
}

export interface RepositoriesPage {
  readonly schema_version: string;
  readonly items: RepositoryInfo[];
}

export interface AnalysisCounts {
  readonly findings: number | null;
  readonly evidence: number | null;
}

export interface AnalysisResponse {
  readonly schema_version: string;
  readonly analysis_id: string;
  readonly status: AnalysisStatus;
  readonly repository_id: string;
  readonly base_ref: string;
  readonly target_ref: string;
  readonly rules_version: string;
  readonly snapshot_id: string | null;
  readonly resumable: boolean;
  readonly cancel_requested: boolean;
  readonly created_at: string;
  readonly updated_at: string;
  readonly failure_reason: string | null;
  readonly stages: StageRecord[];
  readonly counts: AnalysisCounts;
}

export interface StageCoverageItem {
  readonly schema_version: string;
  readonly stage: string;
  readonly status: string;
  readonly completed: number;
  readonly failed: number;
  readonly limited: number;
  readonly notes: string[];
}

export interface FindingListItem {
  readonly schema_version: string;
  readonly finding: Finding;
  readonly review_state: ReviewState;
}

export interface FindingDetail {
  readonly schema_version: string;
  readonly finding: Finding;
  readonly review_state: ReviewState;
  readonly evidence: EvidenceAnchor[];
}

export interface FindingsPage {
  readonly schema_version: string;
  readonly items: FindingListItem[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
  readonly availability: Availability;
}

export interface CoverageResponse {
  readonly schema_version: string;
  readonly summary: CoverageSummary;
  readonly decisions: SelectionDecision[];
  readonly stage_coverage: StageCoverageItem[];
  readonly limits: string[];
  readonly manifest: Record<string, unknown> | null;
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
  readonly availability: Availability;
}

export interface MapResponse {
  readonly schema_version: string;
  readonly side: string;
  readonly snapshot_id: string;
  readonly map: SoftwareMap;
  readonly availability: Availability;
}

export interface ImpactPathItem {
  readonly schema_version: string;
  readonly origin_id: string;
  readonly direction: string;
  readonly node_ids: string[];
  readonly edge_ids: string[];
  readonly evidence_ids: string[];
  readonly resolution: string;
  readonly truncated: boolean;
  readonly stop_reason: string | null;
  readonly limit_note: string | null;
}

export interface ImpactPathsPage {
  readonly schema_version: string;
  readonly items: ImpactPathItem[];
  readonly total: number;
  readonly availability: Availability;
}

export interface SummaryResponse {
  readonly schema_version: string;
  readonly analysis_id: string;
  readonly snapshot_id: string | null;
  readonly base_commit: string | null;
  readonly target_commit: string | null;
  readonly rules_version: string | null;
  readonly status: AnalysisStatus;
  readonly counts: AnalysisCounts;
  readonly stage_coverage: StageCoverageItem[];
  readonly limits: string[];
  readonly manifest: Record<string, unknown> | null;
  readonly evidence_resolution_failures: Record<string, unknown>[];
  readonly diff_files: DiffFileEntry[];
  readonly availability: Availability;
}

export interface ReviewRequest {
  readonly finding_id: string;
  readonly state: ReviewState;
  readonly note: string;
}

export interface ReviewsPage {
  readonly schema_version: string;
  readonly items: Review[];
  readonly total: number;
  readonly availability: Availability;
}

// ---------------------------------------------------------------------------
// Explain transport contracts (Batch-06 / Freeze 5 登记)
// 模型输出永不改变确定性图；failed 不冒充 completed。
// ---------------------------------------------------------------------------

export interface ExplainConsentInput {
  readonly schema_version: string;
  readonly endpoint: string;
  readonly acknowledged: boolean;
}

export interface ExplainRequest {
  readonly schema_version: string;
  readonly subject_type: 'finding' | 'node';
  readonly subject_id: string;
  readonly evidence_ids: string[] | null;
  readonly provider: 'fake' | 'ollama' | 'remote';
  readonly remote_consent: ExplainConsentInput | null;
}

export interface ExplainClaimItem {
  readonly schema_version: string;
  readonly text: string;
  readonly evidence_ids: string[];
  readonly kind: 'restatement' | 'inference' | 'unknown';
}

export interface ExplanationPayload {
  readonly schema_version: string;
  readonly explanation_id: string;
  readonly analysis_id: string;
  readonly subject_type: string;
  readonly subject_id: string;
  readonly status: 'completed' | 'failed';
  readonly provider: string;
  readonly model: string;
  readonly claims: ExplainClaimItem[];
  readonly uncertainty: string;
  readonly errors: string[];
  readonly context_hash: string;
  readonly duration_ms: number | null;
  readonly authorization_id: number | null;
  readonly created_at: string;
}

export interface ExplainProviderInfo {
  readonly provider: string;
  readonly model: string | null;
  readonly available: boolean;
  readonly note: string;
  readonly models?: readonly string[];
}

export interface ExplainProvidersResponse {
  readonly schema_version: string;
  readonly providers: ExplainProviderInfo[];
}

export interface RemoteAuthorizationItem {
  readonly schema_version: string;
  readonly authorization_id: number;
  readonly provider: string;
  readonly endpoint_host: string;
  readonly scope_evidence_ids: string[];
  readonly context_hash: string;
  readonly granted_at: string;
  readonly used_at: string | null;
}

export interface RemoteAuthorizationsPage {
  readonly schema_version: string;
  readonly items: RemoteAuthorizationItem[];
}
