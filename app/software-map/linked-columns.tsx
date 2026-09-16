'use client'

import { useEffect, useState } from 'react'
import { fixture } from './fixture'
import { traceGraph, type MapNode } from './model'

const nodeById = new Map(fixture.nodes.map((node) => [node.id, node]))

export default function LinkedColumns({ selected, pageFilter, onSelect }: { selected: MapNode; pageFilter: string; onSelect: (node: MapNode) => void }) {
  const ancestors = traceGraph(fixture, selected.id, true)
  const initialPage = pageFilter !== 'all' ? pageFilter : ancestors.find((trace) => nodeById.get(trace.nodeId)?.kind === '页面')?.nodeId ?? (selected.kind === '页面' ? selected.id : 'page-settings')
  const initialFeature = fixture.edges.find((edge) => edge.from === initialPage)?.to ?? ''
  const nearestMethod = ancestors.find((trace) => nodeById.get(trace.nodeId)?.kind === '方法')?.nodeId
  const candidateMethod = selected.kind === '方法' ? selected.id : nearestMethod ?? ''
  const initialMethod = traceGraph(fixture, initialFeature).some((trace) => trace.nodeId === candidateMethod) ? candidateMethod : ''
  const [path, setPath] = useState<string[]>([initialPage, initialFeature, initialMethod, ...(initialMethod && ['数据', '契约'].includes(selected.kind) ? [selected.id] : [])])
  const [entry, setEntry] = useState<'页面' | '方法'>('页面')
  const [expandedColumn, setExpandedColumn] = useState(0)
  const [compact, setCompact] = useState(false)
  useEffect(() => {
    const media = window.matchMedia('(max-width: 640px)')
    const update = () => setCompact(media.matches)
    update(); media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  const direct = (id: string) => fixture.edges.filter((edge) => edge.from === id).map((edge) => ({ node: nodeById.get(edge.to)!, relation: edge.relation, candidate: edge.resolution === 'candidate', path: [edge.from, edge.to] }))
  const pageEntries = fixture.nodes.filter((node) => node.kind === entry && (entry !== '页面' || pageFilter === 'all' || node.id === pageFilter)).map((node) => ({ node, relation: entry === '页面' ? '页面入口' : '无页面 / 直接从方法进入', candidate: false, path: [node.id] }))
  const reachableMethods = traceGraph(fixture, path[1] ?? '').filter((trace) => nodeById.get(trace.nodeId)?.kind === '方法').map((trace) => ({ node: nodeById.get(trace.nodeId)!, relation: trace.path.length === 2 ? '功能处理器' : `${trace.path.length - 2} 层间接调用`, candidate: trace.candidate, path: trace.path }))
  const columns = entry === '页面'
    ? [{ title: '01 页面', items: pageEntries }, { title: '02 功能', items: direct(path[0] ?? '') }, { title: '03 关联方法', items: reachableMethods }, { title: '04 调用 / 契约 / 数据操作', items: direct(path[2] ?? '') }, { title: '05 数据表 / 字段 / 下游', items: direct(path[3] ?? '') }]
    : [{ title: '01 方法入口', items: pageEntries }, ...[0, 1, 2, 3].map((index) => ({ title: `0${index + 2} 下游调用 / 数据契约`, items: direct(path[index] ?? '') }))]

  const pick = (index: number, node: MapNode) => {
    setPath([...path.slice(0, index), node.id])
    setExpandedColumn(Math.min(index + 1, 4))
    onSelect(node)
  }
  const feature = nodeById.get(path[1])
  const featurePurpose = feature?.kind === '功能' ? ({
    'F-01 生成诊断': '生成诊断记录，并交给遥测流程处理。',
    'F-02 保存设置': '保存用户偏好，同时触发相关的文件、数据库和遥测更新。',
  } as Record<string, string>)[feature.label] : undefined
  return <div className="linked-browser">
    <div className="linked-header"><div><h2>从入口追到数据落点</h2><p>选择一项，下一栏只显示它能直接或间接到达的对象。</p></div><label>起始对象 <select aria-label="联动入口" value={entry} onChange={(event) => { setEntry(event.target.value as '页面' | '方法'); setPath([]); setExpandedColumn(0) }}><option>页面</option><option>方法</option></select></label></div>
    {feature && featurePurpose && <div className="feature-purpose"><strong>{feature.label} 的作用</strong><span>{featurePurpose}</span><small>来源：示例功能映射，需在真实仓库中由需求或人工确认。</small></div>}
    <div className="linked-breadcrumb" aria-label="当前浏览路径">{path.filter(Boolean).length ? path.map((id, index) => id && <button key={`${id}-${index}`} onClick={() => pick(index, nodeById.get(id)!)}>{nodeById.get(id)?.label} <span>›</span></button>) : '选择一个入口开始；无需先操作网络图。'}</div>
    <div className="linked-columns">{columns.map((column, index) => <section key={column.title} className={`linked-column ${compact && expandedColumn !== index ? 'is-collapsed' : ''}`}><button className="linked-column-toggle" aria-expanded={!compact || expandedColumn === index} onClick={() => compact && setExpandedColumn(index)}><span>{column.title}</span><span>{column.items.length}</span></button><div className="linked-column-body" hidden={compact && expandedColumn !== index}>{column.items.length ? column.items.map((item) => <button className={path[index] === item.node.id ? 'chosen' : ''} key={item.node.id} onClick={() => pick(index, item.node)} aria-pressed={path[index] === item.node.id}><span className="linked-kind">{item.node.kind} · {item.candidate ? '候选' : item.relation}</span><strong>{item.node.label}</strong><small>{item.node.source.file}:{item.node.source.line}</small>{item.path.length > 2 && <small>经 {item.path.slice(0, -1).map((id) => nodeById.get(id)?.label).join(' → ')}</small>}<span className="linked-arrow">›</span></button>) : <div className="linked-empty">{index > 0 && !path[index - 1] ? '先选择左侧条目' : '当前图没有更多下游关系。选中数据节点可在右侧查看表结构与字段；无关系不代表无影响。'}</div>}</div></section>)}</div>
    <div className="linked-footnote">操作示例：设置页面 → F-02 保存设置 → saveSettings → UPDATE preferences → preferences 表。右侧同步显示 SQL、字段、来源与受影响页面。</div>
  </div>
}
