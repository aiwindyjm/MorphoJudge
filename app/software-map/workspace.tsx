'use client'

import { useMemo, useRef, useState } from 'react'
import { fixture } from './fixture'
import { checkMethod, traceGraph, type Explanation, type Kind, type MapNode } from './model'
import './workspace.css'
import LinkedColumns from './linked-columns'

const colors: Record<Kind, string> = { 页面: '#7d8aff', 功能: '#cf9afc', 方法: '#52c6c0', 契约: '#e7b65c', 数据: '#ef8f92' }
const byId = new Map(fixture.nodes.map((node) => [node.id, node]))
const nameOf = (id: string) => byId.get(id)?.label ?? id
const codeTokens = (code: string) => code.split(/(\/\/[^\n]*|\/\*[\s\S]*?\*\/|'[^']*'|"[^"]*"|\b(?:export|async|function|return|const|await|if|new|type)\b)/g).map((part, index) => {
  const kind = /^(\/\/|\/\*)/.test(part) ? 'code-comment' : /^['"]/.test(part) ? 'code-string' : /^(export|async|function|return|const|await|if|new|type)$/.test(part) ? 'code-keyword' : ''
  return <span className={kind} key={index}>{part}</span>
})
const CodePreview = ({ node, compact = false }: { node: MapNode; compact?: boolean }) => <pre className={compact ? 'code-preview' : 'source-code'} aria-label={`${node.label} 源码`}>{codeTokens(node.source.code)}</pre>

export const views = [
  { id: 'trace', label: '功能追踪' },
  { id: 'graph', label: '依赖关系图' },
  { id: 'methods', label: '方法清单' },
  { id: 'comments', label: '注释与实现' },
] as const
export type ViewId = typeof views[number]['id']

export default function SoftwareWorkspace({ tab, setTab }: { tab: ViewId; setTab: (view: ViewId) => void }) {
  const [selectedId, setSelectedId] = useState('save')
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
  const selected = byId.get(selectedId)!
  const upstream = useMemo(() => traceGraph(fixture, selectedId, true), [selectedId])
  const downstream = useMemo(() => traceGraph(fixture, selectedId), [selectedId])
  const related = new Set([selectedId, ...upstream.map((trace) => trace.nodeId), ...downstream.map((trace) => trace.nodeId)])
  const scope = new Set(pageId === 'all' ? fixture.nodes.map((node) => node.id) : [pageId, ...traceGraph(fixture, pageId).map((trace) => trace.nodeId)])
  const nodes = fixture.nodes.filter((node) => scope.has(node.id))
  const edges = fixture.edges.filter((edge) => scope.has(edge.from) && scope.has(edge.to))
  const matches = nodes.filter((node) => `${node.label} ${node.source.file}`.toLowerCase().includes(query.toLowerCase()))
  const methods = nodes.filter((node) => node.kind === '方法')
  const affectedPages = upstream.filter((trace) => byId.get(trace.nodeId)?.kind === '页面')
  const activeCheck = selected.kind === '方法' ? checkMethod(selected) : null
  const selectNode = (node: MapNode) => { setSelectedId(node.id); setFocusVersion((value) => value + 1); setExplanation(null); setExplanationState('idle'); setExplanationError('') }
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

  return <div className="software-workspace">
    <div className="map-toolbar"><label>页面范围 <select aria-label="页面范围" value={pageId} onChange={(event) => { const next = event.target.value; setPageId(next); setQuery(''); if (next !== 'all') setSelectedId(next); resetView() }}><option value="all">整个示例项目</option>{fixture.nodes.filter((node) => node.kind === '页面').map((node) => <option value={node.id} key={node.id}>{node.label}</option>)}</select></label></div>
    <div className={`map-layout ${inspectorOpen ? '' : 'inspector-collapsed'}`}>
      <section className="map-main" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`view-${tab}`}>
        <div hidden={tab !== 'trace'}><LinkedColumns key={`${pageId}-${focusVersion}`} selected={selected} pageFilter={pageId} onSelect={(node) => setSelectedId(node.id)} /></div>
        {tab === 'graph' && <>
          <div className="graph-tools"><label><span className="sr-only">搜索节点</span><input aria-label="搜索节点" value={query} placeholder="搜索页面、方法或契约…" onChange={(event) => setQuery(event.target.value)} /></label><div><button aria-label="缩小" onClick={() => setZoom((value) => Math.max(0.5, value - 0.2))}>−</button><span>{Math.round(zoom * 100)}%</span><button aria-label="放大" onClick={() => setZoom((value) => Math.min(2, value + 0.2))}>+</button><button onClick={resetView}>复位</button></div></div>
          {query && <div className="graph-search" aria-live="polite">{matches.length ? matches.map((node) => <button key={node.id} onClick={() => selectNode(node)}>{node.label}</button>) : '没有匹配节点；图保留当前范围，修改关键词继续查找。'}</div>}
          <div className="graph-canvas">
            <svg viewBox="0 0 900 610" aria-label="软件关系网络，节点可通过 Tab 和 Enter 选择" onPointerDown={(event) => { if ((event.target as Element).closest('[data-node]')) return; drag.current = { x: event.clientX, y: event.clientY, originX: offset.x, originY: offset.y }; event.currentTarget.setPointerCapture(event.pointerId) }} onPointerMove={(event) => { if (!drag.current) return; const factor = 900 / event.currentTarget.getBoundingClientRect().width; setOffset({ x: drag.current.originX + (event.clientX - drag.current.x) * factor, y: drag.current.originY + (event.clientY - drag.current.y) * factor }) }} onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }}>
              <defs><pattern id="map-grid" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="0.7" fill="#39455e" /></pattern><marker id="map-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8496b3" /></marker></defs>
              <rect width="900" height="610" fill="url(#map-grid)" />
              <g transform={`translate(${450 + offset.x}, ${305 + offset.y}) scale(${zoom}) translate(-450,-305)`}>
                {edges.map((edge) => { const from = byId.get(edge.from)!; const to = byId.get(edge.to)!; const length = Math.hypot(to.x - from.x, to.y - from.y); const endX = to.x - (to.x - from.x) / length * 14; const endY = to.y - (to.y - from.y) / length * 14; return <g key={`${edge.from}-${edge.to}`} opacity={related.has(edge.from) && related.has(edge.to) ? 0.9 : 0.22}><line x1={from.x} y1={from.y} x2={endX} y2={endY} stroke={edge.resolution === 'candidate' ? '#c09bed' : '#687e9d'} strokeWidth="1.4" strokeDasharray={edge.resolution === 'candidate' ? '5 5' : undefined} markerEnd="url(#map-arrow)" /><text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 6} textAnchor="middle" className="graph-edge-label">{edge.relation}</text></g> })}
                {nodes.map((node) => <g key={node.id} data-node={node.id} role="button" tabIndex={0} aria-label={`${node.kind} ${node.label}`} aria-pressed={selectedId === node.id} onClick={() => selectNode(node)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(node) } }} className="graph-node" opacity={query && !matches.includes(node) ? 0.2 : related.has(node.id) ? 1 : 0.45}>
                  <rect x={node.x - 75} y={node.y - 24} width="150" height="65" fill="transparent" pointerEvents="all" />
                  {selectedId === node.id && <circle cx={node.x} cy={node.y} r="23" fill="none" stroke={colors[node.kind]} strokeOpacity="0.55" strokeWidth="2" />}
                  <circle cx={node.x} cy={node.y} r={node.kind === '方法' ? 11 : 8} fill={colors[node.kind]} />
                  <text x={node.x} y={node.y + 32} textAnchor="middle" fill="#e8eef9">{node.label}</text>
                </g>)}
              </g>
            </svg>
          </div>
          <div className="graph-legend">{Object.entries(colors).map(([kind, color]) => <span key={kind}><i style={{ background: color }} />{kind}</span>)}<span>虚线：候选关系</span></div>
          <p className="graph-caption">拖动画布 · 点选节点追溯 · 当前范围 {nodes.length} 节点 / {edges.length} 关系。位置不代表执行次序。</p>
        </>}
        {tab === 'methods' && <div className="map-table-wrap"><h2>从用户入口追溯到数据</h2><p>显式功能映射 + 静态关系示例。同一方法可以服务多个页面。悬停方法名可快速预览代码，点击进入完整证据。</p><table className="map-table"><thead><tr><th>方法 / 来源</th><th>上游页面 · 功能</th><th>直接契约</th><th>直接调用 / 数据</th></tr></thead><tbody>{methods.map((method) => { const parents = traceGraph(fixture, method.id, true).filter((entry) => ['页面', '功能'].includes(byId.get(entry.nodeId)!.kind)); const direct = fixture.edges.filter((edge) => edge.from === method.id); return <tr key={method.id}><td className="method-cell"><button onClick={() => jump(method)}>{method.label}</button><small>{method.source.file}:{method.source.line}</small><div className="method-preview"><strong>{method.source.file}:{method.source.line}</strong><CodePreview node={method} compact /></div></td><td>{parents.length ? parents.map((entry) => <small key={entry.nodeId}>{nameOf(entry.nodeId)}{entry.candidate ? '（候选路径）' : ''}</small>) : '未映射 / 无页面入口'}</td><td>{direct.filter((edge) => byId.get(edge.to)?.kind === '契约').map((edge) => <button key={edge.to} onClick={() => jump(byId.get(edge.to)!)}>{edge.relation} {nameOf(edge.to)}</button>)}{!direct.some((edge) => byId.get(edge.to)?.kind === '契约') && '未建立直接契约边'}</td><td>{direct.filter((edge) => byId.get(edge.to)?.kind !== '契约').map((edge) => <small key={edge.to}>{edge.relation} → {nameOf(edge.to)}</small>)}</td></tr> })}</tbody></table></div>}
        {tab === 'comments' && <div className="map-checks"><h2>声明与实现，逐项对照</h2><p>根据示例结构化事实运行 3 类规则；自然语言业务逻辑不在自动判定范围。</p>{methods.map((method) => { const check = checkMethod(method); return <article key={method.id} className="map-check"><div><button onClick={() => jump(method)}>{method.label} ↗</button><span className={`check-state ${check.state === '差异线索' ? 'warning' : ''}`}>{check.state}</span></div><small>{method.source.file}:{method.source.line}</small><div className="check-columns"><section><h3>注释声明（示例元数据）</h3><pre>{method.facts?.declaration ? JSON.stringify(method.facts.declaration, null, 2) : '没有结构化声明'}</pre></section><section><h3>实现事实（示例元数据）</h3><pre>{JSON.stringify(method.facts?.actual, null, 2)}</pre></section></div>{check.reasons.map((reason) => <p key={reason}>{reason}</p>)}</article> })}</div>}
      </section>
      {inspectorOpen && <aside className="map-inspector" aria-label="节点与变更影响"><button className="inspector-close" aria-label="收起详情面板" onClick={() => setInspectorOpen(false)}>›</button><button className={`entity-flip ${cardFlipped ? 'is-flipped' : ''}`} aria-label={`${cardFlipped ? '返回' : '查看'} ${selected.label} 摘要`} aria-pressed={cardFlipped} onClick={() => setCardFlipped((value) => !value)}><span className="entity-face entity-front"><small>{selected.kind}</small><strong>{selected.label}</strong><em>{selected.source.file}:{selected.source.line}</em><i>点击查看关系与证据</i></span><span className="entity-face entity-back"><small>当前对象</small><strong>{upstream.length} 个上游 · {downstream.length} 个下游</strong><em>{selected.kind === '方法' ? '可追溯代码与调用链' : '可追溯来源与关联节点'}</em><i>点击返回对象卡</i></span></button><details open><summary>来源证据 · 示例快照</summary><CodePreview node={selected} /></details>
        <section className="model-explanation" aria-live="polite"><div className="model-explanation-head"><strong>本地模型解释</strong><span>Qwen Coder · Ollama</span></div>{explanationState === 'loading' ? <p>正在基于当前证据生成解释…</p> : explanation ? <><p>{explanation.summary}</p>{explanation.claims.map((claim, index) => <div className="model-claim" key={`${claim.text}-${index}`}><span>{claim.certainty === 'model_inference' ? '模型推断' : claim.certainty === 'unknown' ? '未知项' : '证据复述'}</span><p>{claim.text}</p><small>引用：{claim.evidenceIds.join('、')}</small></div>)}{explanation.uncertainty.length > 0 && <small>仍需确认：{explanation.uncertainty.join('；')}</small>}<button className="text-action" onClick={generateExplanation}>重新生成</button></> : <><p>模型只解释当前已提取的代码和关系证据，不改变关系图事实。</p><button className="outline model-action" onClick={generateExplanation}>生成本地解释</button>{explanationState === 'error' && <p className="model-error">{explanationError} <button className="text-action" onClick={generateExplanation}>重试</button></p>}</>}</section>
        {activeCheck && <div className="selected-check"><strong>{activeCheck.state}</strong>{activeCheck.reasons.map((reason) => <p key={reason}>{reason}</p>)}</div>}
        <section className="impact-section"><div className="impact-title"><h3>如果修改这里</h3><span>{upstream.length} 个上游节点</span></div><p>对整个示例图回溯，不受左侧页面筛选限制。静态依赖成立不代表业务必然改变。</p><div className="impact-count"><b>{affectedPages.length}</b><span>关联页面</span><b>{upstream.filter((trace) => trace.candidate).length}</b><span>候选影响</span></div>
          {upstream.length ? upstream.map((trace) => <div className="impact-path" key={trace.nodeId}><button onClick={() => selectNode(byId.get(trace.nodeId)!)}>{nameOf(trace.nodeId)}</button><span>{trace.candidate ? '候选依赖' : '确定依赖'}</span><p>{[...trace.path].reverse().map(nameOf).join(' → ')}</p></div>) : <p>当前图未发现上游依赖；不等于无影响。</p>}
        </section>
        <details open><summary>直接关系与调用证据</summary>{fixture.edges.filter((edge) => edge.from === selectedId || edge.to === selectedId).map((edge) => <div className="edge-proof" key={`${edge.from}-${edge.to}`}><button onClick={() => selectNode(byId.get(edge.from === selectedId ? edge.to : edge.from)!)}>{nameOf(edge.from)} → {nameOf(edge.to)}</button><small>{edge.relation} · {edge.resolution === 'resolved' ? '示例已解析' : '候选'} · {edge.source.file}:{edge.source.line}</small></div>)}</details>
        <details><summary>下游可达关系 · {downstream.length}</summary>{downstream.map((trace) => <div className="impact-path" key={trace.nodeId}><p>{trace.path.map(nameOf).join(' → ')}{trace.candidate ? '（候选）' : ''}</p></div>)}<p>包含契约与数据关系，不是运行时执行链。</p></details>
      </aside>}{!inspectorOpen && <button className="inspector-restore" aria-label="展开详情面板" onClick={() => setInspectorOpen(true)}>‹</button>}
    </div>
    <details className="map-limits" open><summary>覆盖范围与无法判定的部分</summary><ul>{fixture.limits.map((limit) => <li key={limit}>{limit}</li>)}</ul></details>
  </div>
}
