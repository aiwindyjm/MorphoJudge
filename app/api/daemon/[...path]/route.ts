// Next 服务端同源代理（WEB-001）：浏览器只访问本站 /api/daemon/v1/...，
// 由这里转发到 Compose 内网的 daemon。URL 完全由白名单决定，用户输入
// 只出现在路径参数与受限查询键中；写请求（POST）强制同源 Origin 校验，
// 不转发浏览器 Cookie/任意头。上游超时/不可达返回明确错误状态，绝不
// 伪装 200 空结果。

import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

const DAEMON_URL = process.env.MORPHOJUDGE_DAEMON_URL ?? 'http://daemon:8000'
const UPSTREAM_TIMEOUT_MS = 60_000

type Rule = { method: 'GET' | 'POST'; pattern: RegExp }

// 固定白名单：只放行本批需要的 repositories、创建/状态/取消与结果 GET。
const RULES: Rule[] = [
  { method: 'GET', pattern: /^v1\/repositories$/ },
  { method: 'POST', pattern: /^v1\/analyses$/ },
  { method: 'GET', pattern: /^v1\/analyses\/[^/]+$/ },
  { method: 'POST', pattern: /^v1\/analyses\/[^/]+\/cancel$/ },
  { method: 'GET', pattern: /^v1\/analyses\/[^/]+\/(coverage|software-map|impact-paths|summary)$/ },
  { method: 'GET', pattern: /^v1\/analyses\/[^/]+\/findings$/ },
  { method: 'GET', pattern: /^v1\/analyses\/[^/]+\/findings\/[^/]+$/ },
  { method: 'GET', pattern: /^v1\/analyses\/[^/]+\/evidence\/[^/]+$/ },
]

const ALLOWED_QUERY_KEYS = new Set(['side', 'status', 'category', 'rule_id', 'limit', 'offset'])
const SEGMENT_RE = /^[A-Za-z0-9:_@.-]{1,200}$/

const errorBody = (code: string, message: string, retryable: boolean) => ({
  schema_version: '1.3.0',
  error: { code, message, retryable, details: {} },
})

function sameOrigin(request: Request): boolean {
  const host = request.headers.get('host')
  const origin = request.headers.get('origin') ?? request.headers.get('referer')
  if (!host || !origin) return false
  try {
    return new URL(origin).host === host
  } catch {
    return false
  }
}

async function forward(request: Request, path: string, method: 'GET' | 'POST'): Promise<Response> {
  const url = new URL(request.url)
  const upstreamQuery = new URLSearchParams()
  for (const [key, value] of url.searchParams.entries()) {
    if (!ALLOWED_QUERY_KEYS.has(key)) continue
    if (key === 'limit' || key === 'offset') {
      if (!/^\d{1,6}$/.test(value)) continue
    }
    upstreamQuery.set(key, value)
  }
  // segments 已含 "v1/..."：上游地址 = DAEMON_URL + / + path（不重复 v1）
  const target = `${DAEMON_URL}/${path}${upstreamQuery.toString() ? `?${upstreamQuery}` : ''}`

  const headers: Record<string, string> = {}
  if (method === 'POST') {
    headers['content-type'] = 'application/json'
    const idempotency = request.headers.get('idempotency-key')
    if (idempotency) headers['idempotency-key'] = idempotency
  }

  let upstream: Response
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body: method === 'POST' ? await request.text() : undefined,
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      cache: 'no-store',
    })
  } catch (error) {
    const timedOut = error instanceof Error && error.name === 'TimeoutError'
    return NextResponse.json(
      errorBody('INTERNAL_ERROR', timedOut ? '本地分析服务响应超时' : '本地分析服务不可达', true),
      { status: 502 },
    )
  }

  const payload = await upstream.text()
  const response = new NextResponse(payload, { status: upstream.status })
  response.headers.set('content-type', 'application/json')
  const replayed = upstream.headers.get('idempotency-replayed')
  if (replayed) response.headers.set('idempotency-replayed', replayed)
  return response
}

async function handle(request: Request, context: { params: Promise<{ path?: string[] }> }, method: 'GET' | 'POST'): Promise<Response> {
  const segments = (await context.params).path ?? []
  if (segments.length === 0 || !segments.every((segment) => SEGMENT_RE.test(segment))) {
    return NextResponse.json(errorBody('ROUTE_NOT_FOUND', '代理路径不在白名单内', false), { status: 404 })
  }
  const path = segments.join('/')
  const matched = RULES.some((rule) => rule.method === method && rule.pattern.test(path))
  if (!matched) {
    return NextResponse.json(errorBody('ROUTE_NOT_FOUND', '请求的端点或方法不在代理白名单内', false), { status: 404 })
  }
  if (method === 'POST' && !sameOrigin(request)) {
    return NextResponse.json(errorBody('INVALID_INPUT', '写请求必须来自本站（同源校验失败）', false), { status: 403 })
  }
  return forward(request, path, method)
}

export async function GET(request: Request, context: { params: Promise<{ path?: string[] }> }): Promise<Response> {
  return handle(request, context, 'GET')
}

export async function POST(request: Request, context: { params: Promise<{ path?: string[] }> }): Promise<Response> {
  return handle(request, context, 'POST')
}
