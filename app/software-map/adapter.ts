// WEB-002 服务 DTO → 展示模型适配层。页面边界一次性转换：
// - 保留原始节点/边 ID、kind、resolution（含 unresolved，无损）与来源定位；
// - 节点源码在后端结果中不存在：code 置 null，行号缺失置 null，绝不用
//   默认值冒充已定位；
// - 图位置是确定性展示布局（按类别分列、列内按 ID 稳定排序），不代表
//   执行次序；
// - 不丢节点：服务契约的全部实体种类（含外部服务）都映射到展示 Kind。

import type { MapEdge as ServiceEdge, MapNode as ServiceNode, SoftwareMap as ServiceMap, ImpactPathItem, Finding } from '../lib/contracts'
import type { MapEdge, MapNode, SoftwareMap, Source } from './model'

const KIND_BY_SERVICE: Record<ServiceNode['kind'], MapNode['kind']> = {
  page: '页面',
  feature: '功能',
  method: '方法',
  contract: '契约',
  data: '数据',
  external_service: '外部服务',
}

export const RELATION_LABEL: Record<string, string> = {
  triggers: '触发',
  implements: '实现映射',
  calls: '调用',
  accepts: '接受',
  returns: '返回',
  reads: '读取',
  writes: '写入',
  sends: '发送',
}

const LANE_ORDER: MapNode['kind'][] = ['页面', '功能', '方法', '契约', '数据', '外部服务']
const LANE_X: Record<MapNode['kind'], number> = { 页面: 110, 功能: 265, 方法: 430, 契约: 595, 数据: 745, 外部服务: 840 }
const LANE_GAP_Y = 105
const LANE_START_Y = 110

export interface AdaptedGraph extends SoftwareMap {
  /** 绑定的快照身份与侧别：所有选择/范围都以 analysis+side 区分 */
  snapshotId: string
  side: 'base' | 'target'
  truncated: boolean
  fileReports: ServiceMap['file_reports']
}

const nodeSource = (node: ServiceNode): Source => ({
  file: node.file_path ?? '(无文件来源)',
  line: node.start_line ?? null,
  code: null, // 后端结果不提供节点源码；详情片段只来自 Evidence 端点
})

const edgeSource = (edge: ServiceEdge): Source => ({
  file: edge.file_path ?? '(无来源文件)',
  line: edge.line ?? null,
  code: null,
})

/** 确定性布局：类别分列，列内按 ID 稳定排序、自上而下排布。 */
function layout(nodes: MapNode[]): void {
  const counters = new Map<MapNode['kind'], number>()
  for (const node of [...nodes].sort((a, b) => (a.id < b.id ? -1 : 1))) {
    const lane = LANE_ORDER.includes(node.kind) ? node.kind : '方法'
    const index = counters.get(lane) ?? 0
    counters.set(lane, index + 1)
    node.x = LANE_X[lane]
    node.y = LANE_START_Y + index * LANE_GAP_Y
  }
}

export function adaptSoftwareMap(service: ServiceMap, side: 'base' | 'target'): AdaptedGraph {
  const nodes: MapNode[] = service.nodes.map((node) => ({
    id: node.id,
    label: node.label,
    kind: KIND_BY_SERVICE[node.kind] ?? '方法',
    source: nodeSource(node),
    resolution: node.resolution,
    note: node.note,
    x: 0,
    y: 0,
  }))
  const known = new Set(nodes.map((node) => node.id))
  // 防御：引用缺失端点的服务边在图中孤立悬挂，按原始数据保留为指向空；
  // 当前后端不会产生这种边，出现时宁可少画线不造节点。
  const edges: MapEdge[] = service.edges
    .filter((edge) => known.has(edge.source_id) && known.has(edge.target_id))
    .map((edge) => ({
      id: edge.id,
      from: edge.source_id,
      to: edge.target_id,
      relation: RELATION_LABEL[edge.relation] ?? edge.relation,
      resolution: edge.resolution,
      source: edgeSource(edge),
    }))
  layout(nodes)
  return {
    nodes,
    edges,
    limits: [...service.limits],
    snapshotId: service.snapshot_id,
    side,
    truncated: service.truncated,
    fileReports: service.file_reports,
  }
}

// --- WEB-003：注释视图 / 影响面板的后端投影 ---

export interface ConsistencyItem {
  findingId: string
  ruleId: string | null
  file: string
  line: number | null
  detail: string
}

export function consistencyItemsFromFindings(findings: readonly Finding[]): ConsistencyItem[] {
  // 只显示后端已提供的一致性发现；没有发现≠所检字段一致。
  return findings
    .filter((finding) => finding.category === 'consistency')
    .map((finding) => ({
      findingId: finding.id,
      ruleId: finding.rule_id,
      file: '(见证据定位)',
      line: null,
      detail: finding.unresolved_reason
        ? `${finding.rule_id ?? 'CONS'}：证据未能精确定位（${finding.unresolved_reason}）`
        : `${finding.rule_id ?? 'CONS'}：声明与实现存在可验证差异；详见证据 ${finding.evidence_ids.join('、') || '（无证据关联）'}`,
    }))
}

export interface ImpactSummary {
  originId: string
  resolution: 'resolved' | 'candidate' | 'unresolved'
  nodeIds: string[]
  truncated: boolean
  stopReason: string | null
  limitNote: string | null
}

/** node_ids 是集合投影（无顺序）；展示为关联节点集合，不连成顺序调用链。 */
export function impactSummariesFor(paths: readonly ImpactPathItem[], nodeId: string): ImpactSummary[] {
  return paths
    .filter((path) => path.origin_id === nodeId)
    .map((path) => {
      // 服务契约里 resolution 声明为 string；在边界收窄到已知三态，
      // 未知值归为 unresolved，不冒充确定影响。
      const resolution: ImpactSummary['resolution'] =
        path.resolution === 'resolved' || path.resolution === 'candidate' ? path.resolution : 'unresolved'
      return {
        originId: path.origin_id,
        resolution,
        nodeIds: [...path.node_ids],
        truncated: path.truncated,
        stopReason: path.stop_reason,
        limitNote: path.limit_note,
      }
    })
}
