// WEB-002 适配层不变量（纯逻辑）：无损转换、空图安全、同端点多边、
// 缺源码可空、unresolved 保留、确定性布局。

import { expect, test } from '@playwright/test'
import { adaptSoftwareMap, consistencyItemsFromFindings, impactSummariesFor } from '../../app/software-map/adapter'
import type { SoftwareMap } from '../../app/lib/contracts'
import { traceGraph } from '../../app/software-map/model'

const SID = 'a'.repeat(64)

const serviceMap = (overrides: Partial<SoftwareMap> = {}): SoftwareMap => ({
  snapshot_id: SID,
  nodes: [
    { id: 'page:/x', snapshot_id: SID, kind: 'page', label: '/x', language: null, file_path: 'src/x/page.tsx', start_line: 1, end_line: 2, resolution: 'resolved', evidence_ids: [], note: 'route:file_convention' },
    { id: 'method:a', snapshot_id: SID, kind: 'method', label: 'a', language: 'typescript', file_path: 'src/a.ts', start_line: 10, end_line: 20, resolution: 'resolved', evidence_ids: [] },
    { id: 'method:b', snapshot_id: SID, kind: 'method', label: 'b', language: null, file_path: null, start_line: null, end_line: null, resolution: 'unresolved', evidence_ids: [], note: null },
    { id: 'svc:telemetry', snapshot_id: SID, kind: 'external_service', label: 'svc:telemetry', language: null, file_path: null, start_line: null, end_line: null, resolution: 'resolved', evidence_ids: [] },
    { id: 'data:unknown:dyn', snapshot_id: SID, kind: 'data', label: 'unknown:dynamic', language: null, file_path: null, start_line: null, end_line: null, resolution: 'unresolved', evidence_ids: [] },
  ],
  edges: [
    { id: 'edge:1', snapshot_id: SID, relation: 'calls', source_id: 'method:a', target_id: 'method:b', resolution: 'resolved', evidence_ids: [], note: null, file_path: 'src/a.ts', line: 12 },
    { id: 'edge:2', snapshot_id: SID, relation: 'sends', source_id: 'method:b', target_id: 'svc:telemetry', resolution: 'candidate', evidence_ids: [], note: null, file_path: 'src/a.ts', line: 14 },
    // 同端点第二条边（不同关系与 ID）必须保留
    { id: 'edge:3', snapshot_id: SID, relation: 'calls', source_id: 'method:a', target_id: 'method:b', resolution: 'candidate', evidence_ids: [], note: null, file_path: 'src/a.ts', line: 18 },
    // 自环
    { id: 'edge:4', snapshot_id: SID, relation: 'calls', source_id: 'method:a', target_id: 'method:a', resolution: 'resolved', evidence_ids: [], note: null, file_path: 'src/a.ts', line: 19 },
  ],
  truncated: false,
  limits: ['no_lockfile:not_checked'],
  file_reports: [],
  ...overrides,
})

test('无损转换：kind/ID/resolution 全保留，外部服务不丢弃', () => {
  const graph = adaptSoftwareMap(serviceMap(), 'target')
  expect(graph.nodes.map((node) => node.id)).toEqual(['page:/x', 'method:a', 'method:b', 'svc:telemetry', 'data:unknown:dyn'])
  expect(graph.nodes.find((node) => node.id === 'svc:telemetry')?.kind).toBe('外部服务')
  expect(graph.nodes.find((node) => node.id === 'method:b')?.resolution).toBe('unresolved')
  expect(graph.edges.map((edge) => edge.id)).toHaveLength(4)
  expect(graph.snapshotId).toBe(SID)
  expect(graph.side).toBe('target')
})

test('同端点多边与自环保留；图 key 使用真实边 ID', () => {
  const graph = adaptSoftwareMap(serviceMap(), 'target')
  const pairEdges = graph.edges.filter((edge) => edge.from === 'method:a' && edge.to === 'method:b')
  expect(pairEdges).toHaveLength(2)
  expect(new Set(graph.edges.map((edge) => edge.id)).size).toBe(4)
  const selfLoop = graph.edges.find((edge) => edge.from === edge.to)
  expect(selfLoop?.id).toBe('edge:4')
})

test('缺源码/行号用 null 表示，不用默认值冒充定位', () => {
  const graph = adaptSoftwareMap(serviceMap(), 'target')
  const noSource = graph.nodes.find((node) => node.id === 'svc:telemetry')!
  expect(noSource.source.code).toBeNull()
  expect(noSource.source.line).toBeNull()
  const located = graph.nodes.find((node) => node.id === 'method:a')!
  expect(located.source.line).toBe(10)
})

test('空图不崩溃：0 节点 0 边', () => {
  const graph = adaptSoftwareMap({ snapshot_id: SID, nodes: [], edges: [], truncated: false, limits: ['empty'], file_reports: [] }, 'base')
  expect(graph.nodes).toEqual([])
  expect(traceGraph(graph, 'any').length).toBe(0)
})

test('确定性布局：同输入两次结果一致', () => {
  const first = adaptSoftwareMap(serviceMap(), 'target')
  const second = adaptSoftwareMap(serviceMap(), 'target')
  expect(first.nodes.map((node) => [node.id, node.x, node.y])).toEqual(second.nodes.map((node) => [node.id, node.x, node.y]))
})

test('traceGraph：unresolved 不进入第一轮确定路径，环可终止', () => {
  const graph = adaptSoftwareMap(serviceMap(), 'target')
  const upstream = traceGraph(graph, 'svc:telemetry', true)
  expect(upstream.map((trace) => trace.nodeId)).toContain('method:a') // 经 resolved+candidate 边可达
  const loopGraph = { nodes: graph.nodes, edges: [...graph.edges,
    { id: 'edge:loop', from: 'method:b', to: 'method:a', relation: '调用', resolution: 'resolved', source: { file: 'x', line: 1, code: null } }] }
  expect(() => traceGraph(loopGraph, 'svc:telemetry', true)).not.toThrow()
})

test('一致性条目：无发现时不编造“一致”结论', () => {
  expect(consistencyItemsFromFindings([])).toEqual([])
  const items = consistencyItemsFromFindings([
    { id: 'f1', snapshot_id: SID, category: 'consistency', kind: 'rule_hint', impact: 'low', reliability: 'rule_based', evidence_ids: ['e1'], explanation_id: null, rule_id: 'CONS-PARAM-EXTRA', unresolved_reason: null },
  ])
  expect(items[0]?.ruleId).toBe('CONS-PARAM-EXTRA')
  expect(items[0]?.detail).toContain('e1')
})

test('影响摘要：集合投影、不排序、保留截断原因', () => {
  const paths = [
    { schema_version: '1.3.0', origin_id: 'method:a', direction: 'up', node_ids: ['method:a', 'page:/x'], edge_ids: ['edge:1'], evidence_ids: [], resolution: 'candidate' as const, truncated: true, stop_reason: 'max_depth', limit_note: 'truncated:max_depth' },
  ]
  const summaries = impactSummariesFor(paths, 'method:a')
  expect(summaries).toHaveLength(1)
  expect(summaries[0]?.nodeIds).toEqual(['method:a', 'page:/x'])
  expect(summaries[0]?.truncated).toBe(true)
  expect(impactSummariesFor(paths, 'page:/x')).toHaveLength(0)
})
