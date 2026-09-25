export type Kind = '页面' | '功能' | '方法' | '契约' | '数据' | '外部服务'
// WEB-002：resolution 无损容纳服务契约的三态；unresolved 不并入 candidate。
export type Resolution = 'resolved' | 'candidate' | 'unresolved'
// 源码定位可缺：真实服务不提供节点源码；行号缺失时为 null，不得默认 0/1。
export type Source = { file: string; line: number | null; code: string | null }
export type MethodFacts = {
  declaration?: { params: string[]; required: string[]; effects: string[] }
  actual: { params: string[]; required: string[]; effects: string[] }
  complete: boolean
}
export type MapNode = {
  id: string
  label: string
  kind: Kind
  source: Source
  x: number
  y: number
  resolution?: Resolution
  note?: string | null
  facts?: MethodFacts
}
export type MapEdge = {
  id: string
  from: string
  to: string
  relation: string
  resolution: Resolution
  source: Source
}
export type SoftwareMap = { nodes: MapNode[]; edges: MapEdge[]; limits: string[] }
export type Trace = { nodeId: string; path: string[]; candidate: boolean }
export type ExplanationClaim = { text: string; evidenceIds: string[]; certainty: 'fact_restatement' | 'model_inference' | 'unknown' }
export type Explanation = { id: string; provider: 'ollama' | 'fake' | 'remote'; model: string; status: 'completed' | 'failed'; summary: string; claims: ExplanationClaim[]; uncertainty: string[]; errors?: string[] }

export function traceGraph(graph: { edges: MapEdge[] }, start: string, reverse = false): Trace[] {
  const adjacency = new Map<string, MapEdge[]>()
  for (const edge of graph.edges) {
    const key = reverse ? edge.to : edge.from
    const entries = adjacency.get(key)
    if (entries) entries.push(edge)
    else adjacency.set(key, [edge])
  }
  const results = new Map<string, Trace>()
  // 第一轮只走 resolved 边；第二轮放开 candidate/unresolved（标记 candidate
  // 语义＝非确定），用 visited 防环，避免无限递归。
  for (const resolvedOnly of [true, false]) {
    const queue: Trace[] = [{ nodeId: start, path: [start], candidate: false }]
    const visited = new Set([start])
    for (let cursor = 0; cursor < queue.length; cursor++) {
      const current = queue[cursor]
      for (const edge of adjacency.get(current.nodeId) ?? []) {
        if (resolvedOnly && edge.resolution !== 'resolved') continue
        const next = reverse ? edge.from : edge.to
        if (visited.has(next)) continue
        visited.add(next)
        const entry = { nodeId: next, path: [...current.path, next], candidate: current.candidate || edge.resolution !== 'resolved' }
        queue.push(entry)
        if (!results.has(next)) results.set(next, entry)
      }
    }
  }
  return [...results.values()]
}

export function checkMethod(node: MapNode) {
  const facts = node.facts
  if (!facts?.declaration) return { state: '无法判定', reasons: ['缺少结构化注释声明，不能判断与实现是否一致。'] }
  const { declaration, actual } = facts
  const reasons: string[] = []
  const same = (left: string[], right: string[]) => left.length === right.length && left.every((value) => right.includes(value))
  if (!same(declaration.params, actual.params)) reasons.push(`PARAM-01：注释参数 ${declaration.params.join(', ')} 与签名 ${actual.params.join(', ')} 不一致。`)
  const missing = declaration.required.filter((field) => !actual.required.includes(field))
  if (missing.length) reasons.push(`RETURN-01：声明的必填返回字段 ${missing.join(', ')} 未被当前返回结构保证。`)
  const extra = actual.effects.filter((effect) => !declaration.effects.includes(effect))
  if (extra.length) reasons.push(`EFFECT-01：发现未声明的直接副作用 ${extra.join(', ')}。`)
  if (!facts.complete) reasons.push('存在无法解析的动态行为；未覆盖部分仍需人工核实。')
  return { state: reasons.length ? (missing.length || extra.length || !same(declaration.params, actual.params) ? '差异线索' : '无法判定') : '所检字段一致', reasons: reasons.length ? reasons : ['参数名、必填返回字段和直接副作用通过当前规则；不代表完整逻辑等价。'] }
}
