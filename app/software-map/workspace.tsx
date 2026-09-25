'use client'

import { useMemo, useRef, useState } from 'react'
import { checkMethod, traceGraph, type Explanation, type Kind, type MapEdge, type MapNode } from './model'
import type { ImpactPathItem } from '../lib/contracts'
import { impactSummariesFor, type ConsistencyItem, type ImpactSummary } from './adapter'
import './workspace.css'
import LinkedColumns from './linked-columns'

const colors: Record<Kind, string> = { 页面: '#7d8aff', 功能: '#cf9afc', 方法: '#52c6c0', 契约: '#e7b65c', 数据: '#ef8f92', 外部服务: '#7fd3a8' }
const codeTokens = (code: string) => code.split(/(\/\/[^\n]*|\/\*[\s\S]*?\*\/|'[^']*'|"[^"]*"|\b(?:export|async|function|return|const|await|if|new|type)\b)/g).map((part, index) => {
  const kind = /^(\/\/|\/\*)/.test(part) ? 'code-comment' : /^['"]/.test(part) ? 'code-string' : /^(export|async|function|return|const|await|if|new|type)$/.test(part) ? 'code-keyword' : ''
  return <span className={kind} key={index}>{part}</span>
})
// WEB-003：真实结果不提供节点源码——如实说明，绝不用示例代码补齐。
const CodePreview = ({ node, compact = false }: { node: MapNode; compact?: boolean }) => node.source.code === null
  ? <div className={compact ? 'code-preview code-unavailable' : 'source-code code-unavailable'}>当前结果未提供代码片段；定位：{node.source.file}{node.source.line === null ? ' · 行号未提供' : `:${node.source.line}`}。片段仅在证据关联中提供。</div>
  : <pre className={compact ? 'code-preview' : 'source-code'} aria-label={`${node.label} 源码`}>{codeTokens(node.source.code)}</pre>

const resolutionText = (resolution: MapEdge['resolution']): string => resolution === 'candidate' ? '候选' : resolution === 'unresolved' ? '未解析' : '已解析'

export const views = [
  { id: 'trace', label: '功能追踪' },
  { id: 'graph', label: '依赖关系图' },
  { id: 'methods', label: '方法清单' },
  { id: 'comments', label: '注释与实现' },
] as const
export type ViewId = typeof views[number]['id']

export interface WorkspaceProps {
  tab: ViewId
  setTab: (view: ViewId) => void
  graph: { nodes: MapNode[]; edges: MapEdge[]; limits: string[] }
  /** 数据来源标识：示例快照 / 真实快照（analysis+side 绑定） */
  snapshotLabel: string
  mode: 'sample' | 'real'
  consistencyItems?: ConsistencyItem[]
  impactPaths?: readonly ImpactPathItem[]
}

export default function SoftwareWorkspace({ tab, setTab, graph, snapshotLabel, mode, consistencyItems = [], impactPaths }: WorkspaceProps) {
  const [selectedId, setSelectedId] = useState(graph.nodes[0]?.id ?? '')
  const [pageId, setPageId] = useState('all')
  const [query, setQuery] = useState('')
  const [zoom, setZoom] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [focusVersion, setFocusVersion] = useState(0)
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [cardFlipped, setCardFlipped] = useState(false)
  const [explanation, setExplanation] = useState<Explanation | null>(null)
  const [explanationState, setExplanationState] = useState<'idle' | 'loading' | 'error'>('idle')
  const [explanationError, setExplanationError] = useState('')
  const drag = useRef<{ x: number; y: number; originX: number; originY: number } | null>(null)
  const byId = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph])
  const selected = byId.get(selectedId) ?? graph.nodes[0]
  const upstream = useMemo(() => (selected ? traceGraph(graph, selected.id, true) : []), [graph, selected])
  const downstream = useMemo(() => (selected ? traceGraph(graph, selected.id) : []), [graph, selected])
  const related = new Set([selected?.id, ...upstream.map((trace) => trace.nodeId), ...downstream.map((trace) => trace.nodeId)])
  const scope = new Set(pageId === 'all' ? graph.nodes.map((node) => node.id) : [pageId, ...traceGraph(graph, pageId).map((trace) => trace.nodeId)])
  const nodes = graph.nodes.filter((node) => scope.has(node.id))
  const edges = graph.edges.filter((edge) => scope.has(edge.from) && scope.has(edge.to))
  const matches = nodes.filter((node) => `${node.label} ${node.source.file}`.toLowerCase().includes(query.toLowerCase()))
  const methods = nodes.filter((node) => node.kind === '方法')
  const affectedPages = upstream.filter((trace) => byId.get(trace.nodeId)?.kind === '页面')
  const backendImpact: ImpactSummary[] = selected && impactPaths ? impactSummariesFor(impactPaths, selected.id) : []
  const activeCheck = mode === 'sample' && selected?.kind === '方法' ? checkMethod(selected) : null
  const selectNode = (node: MapNode) => { setSelectedId(node.id); setFocusVersion((value) => value + 1); setCardFlipped(false); setExplanation(null); setExplanationState('idle'); setExplanationError('') }
  const generateExplanation = async () => {
    setExplanationState('loading'); setExplanationError('')
    try {
      const response = await fetch('/api/explanations', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ subject: selected, upstream: upstream.slice(0, 8), downstream: downstream.slice(0, 8), provider: 'fake', model: 'Qwen Coder · Ollama' }) })
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.error ?? '本地模型解释失败')
      setExplanation(await response.json())
      setExplanationState('idle')
    } catch (error) { setExplanationState('error'); setExplanationError(error instanceof Error ? error.message : '本地模型解释失败') }
  }
  const jump = (node: MapNode) => { selectNode(node); setTab('graph') }
  const resetView = () => { setZoom(1); setOffset({ x: 0, y: 0 }) }

  if (graph.nodes.length === 0) {
    return <div className="software-workspace"><div className="map-toolbar"><span className="tag">{snapshotLabel}</span></div>
      <div className="linked-empty" role="status">当前{mode === 'real' ? '分析结果' : '示例'}的图为空：没有可展示的节点。空图不代表安全结论；请在覆盖说明中查看选择与解析范围。</div>
      <details className="map-limits" open><summary>覆盖范围与无法判定的部分</summary><ul>{graph.limits.map((limit) => <li key={limit}>{limit}</li>)}</ul></details>
    </div>
  }
  if (!selected) return null

  return <div className="software-workspace">
    <div className="map-toolbar"><label>页面范围 <select aria-label="页面范围" value={pageId} onChange={(event) => { const next = event.target.value; setPageId(next); setQuery(''); if (next !== 'all') { const node = byId.get(next); if (node) selectNode(node) } resetView() }}><option value="all">整个{mode === 'real' ? '分析快照' : '示例项目'}</option>{graph.nodes.filter((node) => node.kind === '页面').map((node) => <option value={node.id} key={node.id}>{node.label}</option>)}</select></label><span className="tag">{snapshotLabel}</span></div>
    <div className={`map-layout ${inspectorOpen ? '' : 'inspector-collapsed'}`}>
      <section className="map-main" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`view-${tab}`}>
        <div hidden={tab !== 'trace'}><LinkedColumns key={`${pageId}-${focusVersion}`} graph={graph} selected={selected} pageFilter={pageId} onSelect={(node) => setSelectedId(node.id)} /></div>
        {tab === 'graph' && <>
          <div className="graph-tools"><label><span className="sr-only">搜索节点</span><input aria-label="搜索节点" value={query} placeholder="搜索页面、方法或契约…" onChange={(event) => setQuery(event.target.value)} /></label><div><button aria-label="缩小" onClick={() => setZoom((value) => Math.max(0.5, value - 0.2))}>−</button><span>{Math.round(zoom * 100)}%</span><button aria-label="放大" onClick={() => setZoom((value) => Math.min(2, value + 0.2))}>+</button><button onClick={resetView}>复位</button></div></div>
          {query && <div className="graph-search" aria-live="polite">{matches.length ? matches.map((node) => <button key={node.id} onClick={() => selectNode(node)}>{node.label}</button>) : '没有匹配节点；图保留当前范围，修改关键词继续查找。'}</div>}
          <div className="graph-canvas">
            <svg viewBox="0 0 900 610" aria-label="软件关系网络，节点可通过 Tab 和 Enter 选择" onPointerDown={(event) => { if ((event.target as Element).closest('[data-node]')) return; drag.current = { x: event.clientX, y: event.clientY, originX: offset.x, originY: offset.y }; event.currentTarget.setPointerCapture(event.pointerId) }} onPointerMove={(event) => { if (!drag.current) return; const factor = 900 / event.currentTarget.getBoundingClientRect().width; setOffset({ x: drag.current.originX + (event.clientX - drag.current.x) * factor, y: drag.current.originY + (event.clientY - drag.current.y) * factor }) }} onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }}>
              <defs><pattern id="map-grid" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="0.7" fill="#39455e" /></pattern><marker id="map-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8496b3" /></marker></defs>
              <rect width="900" height="610" fill="url(#map-grid)" />
              <g transform={`translate(${450 + offset.x}, ${305 + offset.y}) scale(${zoom}) translate(-450,-305)`}>
                {(() => {
                  // 同端点多边（不同边 ID）保留并做小幅平行偏移；key 用真实边 ID。
                  const seenPairs = new Map<string, number>()
                  return edges.map((edge) => {
                    const from = byId.get(edge.from); const to = byId.get(edge.to)
                    if (!from || !to) return null
                    const pairKey = `${edge.from}|${edge.to}`
                    const duplicateIndex = seenPairs.get(pairKey) ?? 0
                    seenPairs.set(pairKey, duplicateIndex + 1)
                    const length = Math.hypot(to.x - from.x, to.y - from.y) || 1
                    const shift = duplicateIndex * 5
                    const nx = -(to.y - from.y) / length * shift; const ny = (to.x - from.x) / length * shift
                    const endX = to.x + nx - (to.x - from.x) / length * 14; const endY = to.y + ny - (to.y - from.y) / length * 14
                    const stroke = edge.resolution === 'candidate' ? '#c09bed' : edge.resolution === 'unresolved' ? '#8a94a8' : '#687e9d'
                    const dash = edge.resolution === 'candidate' ? '5 5' : edge.resolution === 'unresolved' ? '2 6' : undefined
                    return <g key={edge.id} opacity={related.has(edge.from) && related.has(edge.to) ? 0.9 : 0.22}><line x1={from.x + nx} y1={from.y + ny} x2={endX} y2={endY} stroke={stroke} strokeWidth="1.4" strokeDasharray={dash} markerEnd="url(#map-arrow)" /><text x={(from.x + to.x) / 2 + nx} y={(from.y + to.y) / 2 + ny - 6} textAnchor="middle" className="graph-edge-label">{edge.relation}</text></g>
                  })
                })()}
                {nodes.map((node) => <g key={node.id} data-node={node.id} role="button" tabIndex={0} aria-label={`${node.kind} ${node.label}${node.resolution && node.resolution !== 'resolved' ? `（${resolutionText(node.resolution)}）` : ''}`} aria-pressed={selected.id === node.id} onClick={() => selectNode(node)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(node) } }} className="graph-node" opacity={query && !matches.includes(node) ? 0.2 : related.has(node.id) ? 1 : 0.45}>
                  <rect x={node.x - 75} y={node.y - 24} width="150" height="65" fill="transparent" pointerEvents="all" />
                  {selected.id === node.id && <circle cx={node.x} cy={node.y} r="23" fill="none" stroke={colors[node.kind]} strokeOpacity="0.55" strokeWidth="2" />}
                  <circle cx={node.x} cy={node.y} r={node.kind === '方法' ? 11 : 8} fill={colors[node.kind]} stroke={node.resolution === 'unresolved' ? '#8a94a8' : undefined} strokeDasharray={node.resolution === 'unresolved' ? '2 3' : undefined} />
                  <text x={node.x} y={node.y + 32} textAnchor="middle" fill="#e8eef9">{node.label}</text>
                </g>)}
              </g>
            </svg>
          </div>
          <div className="graph-legend">{Object.entries(colors).map(([kind, color]) => <span key={kind}><i style={{ background: color }} />{kind}</span>)}<span>虚线：候选关系</span><span>点线：未解析</span></div>
          <p className="graph-caption">拖动画布 · 点选节点追溯 · 当前范围 {nodes.length} 节点 / {edges.length} 关系。位置不代表执行次序。</p>
        </>}
        {tab === 'methods' && <div className="map-table-wrap"><h2>从用户入口追溯到数据</h2><p>{mode === 'real' ? '真实分析的关系图与人工确认的功能映射。' : '显式功能映射 + 静态关系示例。'}同一方法可以服务多个页面。点击进入完整证据。</p><table className="map-table"><thead><tr><th>方法 / 来源</th><th>上游页面 · 功能</th><th>直接契约</th><th>直接调用 / 数据</th></tr></thead><tbody>{methods.map((method) => { const parents = traceGraph(graph, method.id, true).filter((entry) => ['页面', '功能'].includes(byId.get(entry.nodeId)!.kind)); const direct = graph.edges.filter((edge) => edge.from === method.id); return <tr key={method.id}><td className="method-cell"><button onClick={() => jump(method)}>{method.label}</button><small>{method.source.file}{method.source.line === null ? '' : `:${method.source.line}`}</small>{method.source.code !== null && <div className="method-preview"><strong>{method.source.file}{method.source.line === null ? '' : `:${method.source.line}`}</strong><CodePreview node={method} compact /></div>}</td><td>{parents.length ? parents.map((entry) => <small key={entry.nodeId}>{byId.get(entry.nodeId)!.label}{entry.candidate ? '（候选路径）' : ''}</small>) : '未映射 / 无页面入口'}</td><td>{direct.filter((edge) => byId.get(edge.to)?.kind === '契约').map((edge) => <button key={edge.id} onClick={() => jump(byId.get(edge.to)!)}>{edge.relation} {byId.get(edge.to)!.label}</button>)}{!direct.some((edge) => byId.get(edge.to)?.kind === '契约') && '未建立直接契约边'}</td><td>{direct.filter((edge) => byId.get(edge.to)?.kind !== '契约').map((edge) => <small key={edge.id}>{edge.relation} → {byId.get(edge.to)?.label ?? edge.to}{edge.resolution !== 'resolved' ? `（${resolutionText(edge.resolution)}）` : ''}</small>)}</td></tr> })}</tbody></table></div>}
        {tab === 'comments' && (mode === 'sample'
          ? <div className="map-checks"><h2>声明与实现，逐项对照</h2><p>根据示例结构化事实运行 3 类规则；自然语言业务逻辑不在自动判定范围。</p>{methods.map((method) => { const check = checkMethod(method); return <article key={method.id} className="map-check"><div><button onClick={() => jump(method)}>{method.label} ↗</button><span className={`check-state ${check.state === '差异线索' ? 'warning' : ''}`}>{check.state}</span></div><small>{method.source.file}:{method.source.line}</small><div className="check-columns"><section><h3>注释声明（示例元数据）</h3><pre>{method.facts?.declaration ? JSON.stringify(method.facts.declaration, null, 2) : '没有结构化声明'}</pre></section><section><h3>实现事实（示例元数据）</h3><pre>{JSON.stringify(method.facts?.actual, null, 2)}</pre></section></div>{check.reasons.map((reason) => <p key={reason}>{reason}</p>)}</article> })}</div>
          : <div className="map-checks"><h2>声明与实现 · 后端一致性发现</h2><p>仅显示分析服务已提供的一致性发现与限制；当前 API 未提供完整逐方法一致性矩阵，未列出差异不代表全部一致。</p>{consistencyItems.length ? consistencyItems.map((item) => <article key={item.findingId} className="map-check"><div><span className="check-state warning">{item.ruleId ?? 'CONS'}</span><small>{item.findingId}</small></div><p>{item.detail}</p></article>) : <div className="linked-empty">本次分析没有一致性差异发现。API 未提供逐方法矩阵；“没有发现”不等于“所检字段一致”，请结合覆盖限制判断。</div>}{graph.limits.length > 0 && <p className="graph-caption">覆盖限制：{graph.limits.slice(0, 6).join('；')}</p>}</div>)}
      </section>
      {inspectorOpen && <aside className="map-inspector" aria-label="节点与变更影响"><button className="inspector-close" aria-label="收起详情面板" onClick={() => setInspectorOpen(false)}>›</button><button className={`entity-flip ${cardFlipped ? 'is-flipped' : ''}`} aria-label={`${cardFlipped ? '返回' : '查看'} ${selected.label} 摘要`} aria-pressed={cardFlipped} onClick={() => setCardFlipped((value) => !value)}><span className="entity-face entity-front"><small>{selected.kind}{selected.resolution && selected.resolution !== 'resolved' ? ` · ${resolutionText(selected.resolution)}` : ''}</small><strong>{selected.label}</strong><em>{selected.source.file}{selected.source.line === null ? '' : `:${selected.source.line}`}</em><i>点击查看关系与证据</i></span><span className="entity-face entity-back"><small>当前对象</small><strong>{upstream.length} 个上游 · {downstream.length} 个下游</strong><em>{selected.kind === '方法' ? '可追溯来源与关联节点' : '可追溯来源与关联节点'}</em><i>点击返回对象卡</i></span></button><details open><summary>来源证据{mode === 'real' ? ' · 定位' : ' · 示例快照'}</summary><CodePreview node={selected} /></details>
        {mode === 'sample'
          ? <section className="model-explanation" aria-live="polite"><div className="model-explanation-head"><strong>本地模型解释</strong><span>Qwen Coder · Ollama</span></div>{explanationState === 'loading' ? <p>正在基于当前证据生成解释…</p> : explanation ? <><p>{explanation.summary}</p>{explanation.claims.map((claim, index) => <div className="model-claim" key={`${claim.text}-${index}`}><span>{claim.certainty === 'model_inference' ? '模型推断' : claim.certainty === 'unknown' ? '未知项' : '证据复述'}</span><p>{claim.text}</p><small>引用：{claim.evidenceIds.join('、')}</small></div>)}{explanation.uncertainty.length > 0 && <small>仍需确认：{explanation.uncertainty.join('；')}</small>}<button className="text-action" onClick={generateExplanation}>重新生成</button></> : <><p>模型只解释当前已提取的代码和关系证据，不改变关系图事实。</p><button className="outline model-action" onClick={generateExplanation}>生成本地解释</button>{explanationState === 'error' && <p className="model-error">{explanationError} <button className="text-action" onClick={generateExplanation}>重试</button></p>}</>}</section>
          : <section className="model-explanation" aria-live="polite"><div className="model-explanation-head"><strong>本地模型解释</strong><span>尚未接入</span></div><p>真实分析的模型解释属于后续批次；此处不生成任何解释。当前视图中的全部事实、关系与限制均来自确定性分析结果。</p></section>}
        {activeCheck && <div className="selected-check"><strong>{activeCheck.state}</strong>{activeCheck.reasons.map((reason) => <p key={reason}>{reason}</p>)}</div>}
        <section className="impact-section"><div className="impact-title"><h3>如果修改这里</h3><span>{mode === 'real' && impactPaths ? `${backendImpact.length} 条影响路径` : `${upstream.length} 个上游节点`}</span></div>
          {mode === 'real' && impactPaths
            ? <>{backendImpact.length ? <><div className="impact-count"><b>{backendImpact.reduce((sum, item) => sum + item.nodeIds.length, 0)}</b><span>关联节点</span><b>{backendImpact.filter((item) => item.resolution !== 'resolved').length}</b><span>候选/未解析路径</span></div>{backendImpact.map((item, index) => <div className="impact-path" key={`${item.originId}-${index}`}><span>{item.resolution === 'resolved' ? '确定影响范围' : item.resolution === 'candidate' ? '候选影响（不构成确定结论）' : '未解析'}</span><p>{item.nodeIds.map((id) => byId.get(id)?.label ?? id).join(' · ')}（集合投影，非调用顺序）</p>{item.truncated && <small>已截断{item.stopReason ? `：${item.stopReason}` : ''}</small>}{item.limitNote && <small>{item.limitNote}</small>}</div>)}</> : <p>当前图未发现该节点的反向影响路径；不等于无影响。</p>}</>
            : <><p>对整个{mode === 'real' ? '分析图' : '示例图'}回溯，不受左侧页面筛选限制。静态依赖成立不代表业务必然改变。</p><div className="impact-count"><b>{affectedPages.length}</b><span>关联页面</span><b>{upstream.filter((trace) => trace.candidate).length}</b><span>候选影响</span></div>{upstream.length ? upstream.map((trace) => <div className="impact-path" key={trace.nodeId}><button onClick={() => selectNode(byId.get(trace.nodeId)!)}>{byId.get(trace.nodeId)?.label ?? trace.nodeId}</button><span>{trace.candidate ? '候选依赖' : '确定依赖'}</span><p>{[...trace.path].reverse().map((id) => byId.get(id)?.label ?? id).join(' → ')}</p></div>) : <p>当前图未发现上游依赖；不等于无影响。</p>}</>}
        </section>
        <details open><summary>直接关系与来源</summary>{graph.edges.filter((edge) => edge.from === selected.id || edge.to === selected.id).map((edge) => <div className="edge-proof" key={edge.id}><button onClick={() => selectNode(byId.get(edge.from === selected.id ? edge.to : edge.from)!)}>{(byId.get(edge.from)?.label ?? edge.from)} → {(byId.get(edge.to)?.label ?? edge.to)}</button><small>{edge.relation} · {resolutionText(edge.resolution)} · {edge.source.file}{edge.source.line === null ? '' : `:${edge.source.line}`}</small></div>)}</details>
        <details><summary>下游可达关系 · {downstream.length}</summary>{downstream.map((trace) => <div className="impact-path" key={trace.nodeId}><p>{trace.path.map((id) => byId.get(id)?.label ?? id).join(' → ')}{trace.candidate ? '（候选）' : ''}</p></div>)}<p>包含契约与数据关系，不是运行时执行链。</p></details>
      </aside>}{!inspectorOpen && <button className="inspector-restore" aria-label="展开详情面板" onClick={() => setInspectorOpen(true)}>‹</button>}
    </div>
    <details className="map-limits" open><summary>覆盖范围与无法判定的部分</summary><ul>{graph.limits.map((limit) => <li key={limit}>{limit}</li>)}</ul></details>
  </div>
}
