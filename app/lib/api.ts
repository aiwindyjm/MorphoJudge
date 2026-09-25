// 类型化 daemon 客户端（WEB-001）。浏览器只访问本站 /api/daemon/v1/...
// （Next 服务端白名单代理转发到 daemon），绝不直连容器服务名。
// 每个响应先经 schemas.ts 运行时校验；失败抛 ApiSchemaError，不静默降级。

import type { AnalysisResponse, CoverageResponse, FindingDetail, FindingsPage, ImpactPathsPage, MapResponse, RepositoriesPage, SummaryResponse } from './contracts'
import {
  ApiSchemaError,
  isApiError,
  parseAnalysis,
  parseCoverage,
  parseFindingDetail,
  parseFindingsPage,
  parseImpactPaths,
  parseMapResponse,
  parseRepositories,
  parseSummary,
} from './schemas'

const BASE = '/api/daemon/v1'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
    readonly retryable: boolean,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export class ApiConnectionError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ApiConnectionError'
  }
}

export const TERMINAL_STATUSES = ['completed', 'completed_with_limits', 'failed', 'cancelled'] as const
export const isTerminal = (status: string): boolean =>
  (TERMINAL_STATUSES as readonly string[]).includes(status)

async function request(path: string, init: RequestInit & { signal?: AbortSignal } = {}): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        ...(init.body !== undefined ? { 'content-type': 'application/json' } : {}),
        ...(init.headers ?? {}),
      },
      signal: init.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiConnectionError('无法连接本地分析服务（代理不可达或已超时）')
  }
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    if (isApiError(payload)) {
      const error = payload as { error: { code: string; message: string; retryable: boolean; details?: Record<string, unknown> } }
      throw new ApiError(error.error.message, error.error.code, response.status, error.error.retryable, error.error.details ?? {})
    }
    throw new ApiConnectionError(`分析服务返回 ${response.status} 且无统一错误体`)
  }
  if (payload === null) {
    // 上游必须始终返回 JSON；空体等价于结构错误，不当作空集合。
    throw new ApiSchemaError('响应不是 JSON 对象', path)
  }
  return payload
}

// --- 仓库 ---

export async function getRepositories(signal?: AbortSignal): Promise<RepositoriesPage> {
  return parseRepositories(await request('/repositories', { signal }))
}

// --- 分析生命周期 ---

export interface CreateAnalysisInput {
  repository_id: string
  base_ref: string
  target_ref: string
  rules_version?: string
}

export async function createAnalysis(
  input: CreateAnalysisInput,
  idempotencyKey: string,
  signal?: AbortSignal,
  networkRetries = 2,
): Promise<AnalysisResponse> {
  let lastError: unknown
  // 网络层失败用同一个 Idempotency-Key 重试，防止重试创建重复任务；
  // 4xx/5xx 业务响应不重试（交给用户）。
  for (let attempt = 0; attempt <= networkRetries; attempt += 1) {
    try {
      return parseAnalysis(await request('/analyses', {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify(input),
        signal,
      }))
    } catch (error) {
      if (error instanceof ApiConnectionError && attempt < networkRetries) {
        lastError = error
        continue
      }
      throw error
    }
  }
  throw lastError
}

export async function getAnalysis(analysisId: string, signal?: AbortSignal): Promise<AnalysisResponse> {
  return parseAnalysis(await request(`/analyses/${encodeURIComponent(analysisId)}`, { signal }))
}

export async function cancelAnalysis(
  analysisId: string,
  signal?: AbortSignal,
): Promise<{ accepted: boolean; status: number; analysis: AnalysisResponse | null }> {
  try {
    const payload = await request(`/analyses/${encodeURIComponent(analysisId)}/cancel`, { method: 'POST', signal })
    return { accepted: true, status: 202, analysis: parseAnalysis(payload) }
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      // 分析已到终态：取消未被接受，重新读取真实状态。
      const analysis = await getAnalysis(analysisId, signal)
      return { accepted: false, status: 409, analysis }
    }
    throw error
  }
}

// --- 结果（产物分层：parse→map/coverage、rules→summary/impact、report→findings/evidence） ---

export interface PageParams {
  limit?: number
  offset?: number
}

const queryString = (params: Record<string, string | number | undefined>): string => {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export async function getCoverage(
  analysisId: string,
  params: PageParams & { status?: string } = {},
  signal?: AbortSignal,
): Promise<CoverageResponse> {
  return parseCoverage(await request(`/analyses/${encodeURIComponent(analysisId)}/coverage${queryString({ limit: params.limit, offset: params.offset, status: params.status })}`, { signal }))
}

export async function getSoftwareMap(
  analysisId: string,
  side: 'base' | 'target' = 'target',
  signal?: AbortSignal,
): Promise<MapResponse> {
  return parseMapResponse(await request(`/analyses/${encodeURIComponent(analysisId)}/software-map${queryString({ side })}`, { signal }))
}

export async function getImpactPaths(analysisId: string, signal?: AbortSignal): Promise<ImpactPathsPage> {
  return parseImpactPaths(await request(`/analyses/${encodeURIComponent(analysisId)}/impact-paths`, { signal }))
}

export async function getSummary(analysisId: string, signal?: AbortSignal): Promise<SummaryResponse> {
  return parseSummary(await request(`/analyses/${encodeURIComponent(analysisId)}/summary`, { signal }))
}

export async function getFindings(
  analysisId: string,
  params: PageParams & { category?: string; rule_id?: string } = {},
  signal?: AbortSignal,
): Promise<FindingsPage> {
  return parseFindingsPage(await request(`/analyses/${encodeURIComponent(analysisId)}/findings${queryString({ limit: params.limit, offset: params.offset, category: params.category, rule_id: params.rule_id })}`, { signal }))
}

export async function getFindingDetail(
  analysisId: string,
  findingId: string,
  signal?: AbortSignal,
): Promise<FindingDetail> {
  return parseFindingDetail(await request(`/analyses/${encodeURIComponent(analysisId)}/findings/${encodeURIComponent(findingId)}`, { signal }))
}
