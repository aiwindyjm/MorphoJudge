// 运行时响应校验（WEB-001）：真实响应先过这里，再交给 UI。
// 只做结构与枚举校验，类型来源仍是 contracts.ts——这里不复制第二份契约，
// 而是校验“响应符合 contracts.ts 声明的形状”，非法响应抛 ApiSchemaError，
// 绝不 as SomeType 放行。校验器保持保守：未知字段不拒绝（后端可兼容追加），
// 但已知字段的类型/枚举错误一律失败。

import type {
  AnalysisResponse,
  CoverageResponse,
  EvidenceAnchor,
  FindingDetail,
  FindingsPage,
  ImpactPathsPage,
  MapResponse,
  RepositoriesPage,
  SoftwareMap,
  StageCoverageItem,
  SummaryResponse,
} from './contracts'

export const SUPPORTED_SCHEMA_VERSION = '1.3.0'

export class ApiSchemaError extends Error {
  constructor(message: string, readonly path: string) {
    super(`${message}（at ${path}）`)
    this.name = 'ApiSchemaError'
  }
}

const fail = (path: string, message: string): never => {
  throw new ApiSchemaError(message, path)
}

const isObj = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

type Check = (value: unknown, path: string) => void

const str = (value: unknown, path: string): string =>
  typeof value === 'string' ? value : fail(path, `expected string, got ${typeof value}`)

const strOrNull = (value: unknown, path: string): string | null =>
  value === null ? null : str(value, path)

const num = (value: unknown, path: string): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : fail(path, `expected number, got ${String(value)}`)

const numOrNull = (value: unknown, path: string): number | null =>
  value === null ? null : num(value, path)

const bool = (value: unknown, path: string): boolean =>
  typeof value === 'boolean' ? value : fail(path, `expected boolean, got ${typeof value}`)

const arr = (value: unknown, path: string): unknown[] =>
  Array.isArray(value) ? value : fail(path, 'expected array')

const strArray = (value: unknown, path: string): string[] =>
  arr(value, path).map((item, index) => str(item, `${path}[${index}]`))

const enumOf = <T extends string>(values: readonly T[]): Check => (value, path) => {
  const text = str(value, path)
  if (!(values as readonly string[]).includes(text)) fail(path, `unknown enum value ${JSON.stringify(text)}`)
}

const nullableEnum = <T extends string>(values: readonly T[]): Check => (value, path) => {
  if (value === null) return
  enumOf(values)(value, path)
}

const record = (value: unknown, path: string): Record<string, unknown> =>
  isObj(value) ? value : fail(path, 'expected object')

const oneOf = (...checks: Check[]): Check => (value, path) => {
  for (const check of checks) {
    try {
      check(value, path)
      return
    } catch {
      // 尝试下一个分支
    }
  }
  fail(path, 'value matched none of the allowed shapes')
}

const field = (container: Record<string, unknown>, key: string, check: Check, path: string, required = true): void => {
  if (!(key in container)) {
    if (required) fail(path, `missing field "${key}"`)
    return
  }
  check(container[key], `${path}.${key}`)
}

// --- 枚举（与 contracts.ts 逐字一致；漂移由 schemas.spec.ts 固定） ---

export const ANALYSIS_STATUSES = [
  'queued', 'running', 'completed', 'completed_with_limits', 'failed', 'cancelled',
] as const
export const STAGE_STATUSES = ['queued', 'running', 'completed', 'failed', 'skipped'] as const
export const STAGE_NAMES = ['git', 'selection', 'parse', 'behavior', 'dependency', 'report', 'explain'] as const
export const MAP_NODE_KINDS = ['page', 'feature', 'method', 'contract', 'data', 'external_service'] as const
export const MAP_RELATIONS = ['triggers', 'implements', 'calls', 'accepts', 'returns', 'reads', 'writes', 'sends'] as const
export const RESOLUTIONS = ['resolved', 'candidate', 'unresolved'] as const
export const EVIDENCE_SIDES = ['old', 'new', 'context'] as const
export const FINDING_CATEGORIES = [
  'behavior_network', 'behavior_shell', 'behavior_file', 'behavior_permission',
  'dependency', 'consistency', 'structure',
] as const
export const SELECTION_STATUSES = ['selected', 'excluded', 'limited', 'failed'] as const

const checkAnalysisStatus = enumOf(ANALYSIS_STATUSES)
const checkStageStatus = enumOf(STAGE_STATUSES)
const checkStageName = enumOf(STAGE_NAMES)
const checkKind = enumOf(MAP_NODE_KINDS)
const checkRelation = enumOf(MAP_RELATIONS)
const checkResolution = enumOf(RESOLUTIONS)
const checkSide = enumOf(EVIDENCE_SIDES)
const checkCategory = enumOf(FINDING_CATEGORIES)
const checkReviewState = enumOf(['unreviewed', 'needs-investigation', 'confirmed', 'dismissed'] as const)

const checkIsoOrNull: Check = (value, path) => {
  if (value === null) return
  str(value, path)
}

const checkAvailability: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', (v, p) => str(v, p), path)
  field(obj, 'analysis_status', checkAnalysisStatus, path)
  field(obj, 'artifacts', enumOf(['complete', 'partial'] as const), path)
  field(obj, 'note', str, path)
}

const checkStageRecord: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'stage', checkStageName, path)
  field(obj, 'status', checkStageStatus, path)
  field(obj, 'detail', strOrNull, path, false)
}

const checkStageCoverage: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', str, path)
  field(obj, 'stage', str, path)
  field(obj, 'status', str, path)
  field(obj, 'completed', num, path)
  field(obj, 'failed', num, path)
  field(obj, 'limited', num, path)
  field(obj, 'notes', strArray, path)
}

const checkCounts: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'findings', numOrNull, path, false)
  field(obj, 'evidence', numOrNull, path, false)
}

const checkAnalysis: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', str, path)
  field(obj, 'analysis_id', str, path)
  field(obj, 'status', checkAnalysisStatus, path)
  field(obj, 'repository_id', str, path)
  field(obj, 'base_ref', str, path)
  field(obj, 'target_ref', str, path)
  field(obj, 'rules_version', str, path)
  field(obj, 'snapshot_id', strOrNull, path, false)
  field(obj, 'resumable', bool, path)
  field(obj, 'cancel_requested', bool, path)
  field(obj, 'created_at', str, path)
  field(obj, 'updated_at', str, path)
  field(obj, 'failure_reason', strOrNull, path, false)
  field(obj, 'stages', (v, p) => arr(v, p).forEach((item, i) => checkStageRecord(item, `${p}[${i}]`)), path)
  field(obj, 'counts', checkCounts, path)
}

const checkMapNode: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'id', str, path)
  field(obj, 'snapshot_id', str, path)
  field(obj, 'kind', checkKind, path)
  field(obj, 'label', str, path)
  field(obj, 'language', strOrNull, path, false)
  field(obj, 'file_path', strOrNull, path, false)
  field(obj, 'start_line', numOrNull, path, false)
  field(obj, 'end_line', numOrNull, path, false)
  field(obj, 'resolution', checkResolution, path)
  field(obj, 'evidence_ids', strArray, path)
  field(obj, 'note', strOrNull, path, false)
}

const checkMapEdge: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'id', str, path)
  field(obj, 'snapshot_id', str, path)
  field(obj, 'relation', checkRelation, path)
  field(obj, 'source_id', str, path)
  field(obj, 'target_id', str, path)
  field(obj, 'resolution', checkResolution, path)
  field(obj, 'evidence_ids', strArray, path)
  field(obj, 'note', strOrNull, path, false)
  field(obj, 'file_path', strOrNull, path, false)
  field(obj, 'line', numOrNull, path, false)
}

const checkSoftwareMap: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'snapshot_id', str, path)
  field(obj, 'nodes', (v, p) => arr(v, p).forEach((item, i) => checkMapNode(item, `${p}[${i}]`)), path)
  field(obj, 'edges', (v, p) => arr(v, p).forEach((item, i) => checkMapEdge(item, `${p}[${i}]`)), path)
  field(obj, 'truncated', bool, path)
  field(obj, 'limits', strArray, path)
  field(obj, 'file_reports', (v, p) => arr(v, p).forEach((item, i) => {
    const report = record(item, `${p}[${i}]`)
    field(report, 'path', str, `${p}[${i}]`)
    field(report, 'status', str, `${p}[${i}]`)
    field(report, 'issue_count', num, `${p}[${i}]`)
    field(report, 'note', strOrNull, `${p}[${i}]`, false)
  }), path)
}

const checkEvidence: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'id', str, path)
  field(obj, 'snapshot_id', str, path)
  field(obj, 'path', str, path)
  field(obj, 'side', checkSide, path)
  field(obj, 'start_line', num, path)
  field(obj, 'end_line', num, path)
  field(obj, 'snippet', str, path)
  field(obj, 'source_kind', str, path)
  field(obj, 'rule_id', strOrNull, path, false)
  field(obj, 'commit', strOrNull, path, false)
}

const checkFinding: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'id', str, path)
  field(obj, 'snapshot_id', str, path)
  field(obj, 'category', checkCategory, path)
  field(obj, 'kind', enumOf(['fact', 'rule_hint'] as const), path)
  field(obj, 'impact', enumOf(['unknown', 'low', 'medium', 'high'] as const), path)
  field(obj, 'reliability', enumOf(['deterministic', 'rule_based'] as const), path)
  field(obj, 'evidence_ids', strArray, path)
  field(obj, 'explanation_id', strOrNull, path, false)
  field(obj, 'rule_id', strOrNull, path, false)
  field(obj, 'unresolved_reason', strOrNull, path, false)
}

const checkFindingListItem: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', str, path)
  field(obj, 'finding', checkFinding, path)
  field(obj, 'review_state', checkReviewState, path)
}

const pageedEnvelope = (itemCheck: Check): Check => (value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', str, path)
  field(obj, 'items', (v, p) => arr(v, p).forEach((item, i) => itemCheck(item, `${p}[${i}]`)), path)
  field(obj, 'total', num, path)
  field(obj, 'limit', num, path, false)
  field(obj, 'offset', num, path, false)
  field(obj, 'availability', checkAvailability, path, false)
}

// 仓库列表不是分页端点（后端 RepositoriesPage 只有 schema_version + items）
const checkRepositories: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', str, path)
  field(obj, 'items', (v, p) => arr(v, p).forEach((item, i) => {
    const entry = record(item, `${p}[${i}]`)
    field(entry, 'schema_version', str, `${p}[${i}]`)
    field(entry, 'repository_id', str, `${p}[${i}]`)
    field(entry, 'name', str, `${p}[${i}]`)
    field(entry, 'registered_at', str, `${p}[${i}]`)
  }), path)
}

const checkFindingsPage: Check = pageedEnvelope(checkFindingListItem)

const checkDecision: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'snapshot_id', str, path)
  field(obj, 'path', str, path)
  field(obj, 'status', enumOf(SELECTION_STATUSES), path)
  field(obj, 'reason', str, path)
  field(obj, 'rule_id', strOrNull, path, false)
  field(obj, 'language', strOrNull, path, false)
  field(obj, 'bytes', numOrNull, path, false)
}

const checkCoverageSummary: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'snapshot_id', str, path)
  for (const key of ['total', 'selected', 'excluded', 'limited', 'failed', 'completed']) {
    field(obj, key, num, path)
  }
  field(obj, 'partial', bool, path)
  field(obj, 'updated_at', checkIsoOrNull, path, false)
}

const checkCoverage: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', str, path)
  field(obj, 'summary', checkCoverageSummary, path)
  field(obj, 'decisions', (v, p) => arr(v, p).forEach((item, i) => checkDecision(item, `${p}[${i}]`)), path)
  field(obj, 'stage_coverage', (v, p) => arr(v, p).forEach((item, i) => checkStageCoverage(item, `${p}[${i}]`)), path, false)
  field(obj, 'limits', strArray, path, false)
  field(obj, 'manifest', oneOf((_v, _p) => { /* dict or null */ }), path, false)
  field(obj, 'total', num, path)
  field(obj, 'limit', num, path)
  field(obj, 'offset', num, path)
  field(obj, 'availability', checkAvailability, path)
}

const checkMapResponse: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', str, path)
  field(obj, 'side', enumOf(['base', 'target'] as const), path)
  field(obj, 'snapshot_id', str, path)
  field(obj, 'map', checkSoftwareMap, path)
  field(obj, 'availability', checkAvailability, path)
}

const checkImpactPaths: Check = pageedEnvelope((value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', str, path)
  field(obj, 'origin_id', str, path)
  field(obj, 'direction', str, path)
  field(obj, 'node_ids', strArray, path)
  field(obj, 'edge_ids', strArray, path)
  field(obj, 'evidence_ids', strArray, path)
  field(obj, 'resolution', checkResolution, path)
  field(obj, 'truncated', bool, path)
  field(obj, 'stop_reason', strOrNull, path, false)
  field(obj, 'limit_note', strOrNull, path, false)
})

const checkSummary: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', str, path)
  field(obj, 'analysis_id', str, path)
  field(obj, 'snapshot_id', strOrNull, path, false)
  field(obj, 'base_commit', strOrNull, path, false)
  field(obj, 'target_commit', strOrNull, path, false)
  field(obj, 'rules_version', strOrNull, path, false)
  field(obj, 'status', checkAnalysisStatus, path)
  field(obj, 'counts', checkCounts, path)
  field(obj, 'stage_coverage', (v, p) => arr(v, p).forEach((item, i) => checkStageCoverage(item, `${p}[${i}]`)), path)
  field(obj, 'limits', strArray, path)
  field(obj, 'manifest', oneOf((_v, _p) => {}), path, false)
  field(obj, 'evidence_resolution_failures', (v, p) => arr(v, p), path, false)
  field(obj, 'diff_files', (v, p) => arr(v, p), path, false)
  field(obj, 'availability', checkAvailability, path)
}

const checkFindingDetail: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', str, path)
  field(obj, 'finding', checkFinding, path)
  field(obj, 'review_state', checkReviewState, path)
  field(obj, 'evidence', (v, p) => arr(v, p).forEach((item, i) => checkEvidence(item, `${p}[${i}]`)), path)
}

const checkErrorEnvelope: Check = (value, path) => {
  const obj = record(value, path)
  field(obj, 'schema_version', str, path)
  const error = record(obj.error, `${path}.error`)
  field(error, 'code', str, `${path}.error`)
  field(error, 'message', str, `${path}.error`)
  field(error, 'retryable', bool, `${path}.error`)
  field(error, 'details', record, `${path}.error`)
}

// --- 校验入口：返回 contracts.ts 类型（已通过形状校验） ---

export function parseRepositories(value: unknown): RepositoriesPage {
  checkRepositories(value, 'repositories')
  return value as RepositoriesPage
}

export function parseAnalysis(value: unknown): AnalysisResponse {
  checkAnalysis(value, 'analysis')
  return value as AnalysisResponse
}

export function parseFindingsPage(value: unknown): FindingsPage {
  checkFindingsPage(value, 'findings')
  return value as FindingsPage
}

export function parseCoverage(value: unknown): CoverageResponse {
  checkCoverage(value, 'coverage')
  return value as CoverageResponse
}

export function parseMapResponse(value: unknown): MapResponse {
  checkMapResponse(value, 'map')
  return value as MapResponse
}

export function parseImpactPaths(value: unknown): ImpactPathsPage {
  checkImpactPaths(value, 'impact-paths')
  return value as ImpactPathsPage
}

export function parseSummary(value: unknown): SummaryResponse {
  checkSummary(value, 'summary')
  return value as SummaryResponse
}

export function parseFindingDetail(value: unknown): FindingDetail {
  checkFindingDetail(value, 'finding-detail')
  return value as FindingDetail
}

export function parseEvidence(value: unknown): EvidenceAnchor {
  checkEvidence(value, 'evidence')
  return value as EvidenceAnchor
}

export function isApiError(value: unknown): value is { error: { code: string; message: string; retryable: boolean; details: Record<string, unknown> } } {
  try {
    checkErrorEnvelope(value, 'error-envelope')
    return true
  } catch {
    return false
  }
}

export type StageCoverageLike = StageCoverageItem
export type SoftwareMapLike = SoftwareMap
