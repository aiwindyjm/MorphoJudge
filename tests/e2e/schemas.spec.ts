// WEB-001 响应校验的真实非法输入测试（纯逻辑，不需要浏览器）：
// 非法响应必须抛 ApiSchemaError，绝不静默 as SomeType 放行。

import { expect } from '@playwright/test'
import { test } from '@playwright/test'
import {
  ApiSchemaError, SUPPORTED_SCHEMA_VERSION,
  parseAnalysis, parseCoverage, parseFindingsPage, parseImpactPaths, parseMapResponse, parseRepositories, parseSummary,
} from '../../app/lib/schemas'

const baseAnalysis = {
  schema_version: SUPPORTED_SCHEMA_VERSION,
  analysis_id: 'analysis:abc', status: 'queued', repository_id: 'r'.repeat(64),
  base_ref: 'HEAD~1', target_ref: 'HEAD', rules_version: 'v',
  snapshot_id: null, resumable: true, cancel_requested: false,
  created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-20T00:00:00Z',
  failure_reason: null, stages: [], counts: { findings: null, evidence: null },
}

const availability = {
  schema_version: SUPPORTED_SCHEMA_VERSION, analysis_status: 'completed', artifacts: 'complete', note: '',
}

test('合法 analysis 响应通过校验', () => {
  expect(parseAnalysis(baseAnalysis).analysis_id).toBe('analysis:abc')
})

test('未知 status 枚举被拒绝', () => {
  expect(() => parseAnalysis({ ...baseAnalysis, status: 'sort_of_done' })).toThrow(ApiSchemaError)
})

test('字段类型错误被拒绝（stages 不是数组）', () => {
  expect(() => parseAnalysis({ ...baseAnalysis, stages: 'none' })).toThrow(ApiSchemaError)
})

test('缺必填字段被拒绝', () => {
  const { repository_id, ...missing } = baseAnalysis
  void repository_id
  expect(() => parseAnalysis(missing)).toThrow(ApiSchemaError)
})

test('repositories 响应：非字符串 id 被拒绝', () => {
  expect(() => parseRepositories({ schema_version: '1.3.0', items: [{ schema_version: '1.3.0', repository_id: 7, name: 'x', registered_at: 'now' }] })).toThrow(ApiSchemaError)
})

test('findings 分页：非法类别枚举被拒绝', () => {
  const page = {
    schema_version: '1.3.0', total: 1, limit: 20, offset: 0, availability,
    items: [{ schema_version: '1.3.0', review_state: 'unreviewed', finding: {
      id: 'f', snapshot_id: 's'.repeat(64), category: 'made_up', kind: 'fact', impact: 'low',
      reliability: 'deterministic', evidence_ids: [], rule_id: null, unresolved_reason: null,
    } }],
  }
  expect(() => parseFindingsPage(page)).toThrow(ApiSchemaError)
})

test('map 响应：非枚举 kind 被拒绝', () => {
  const response = {
    schema_version: '1.3.0', side: 'target', snapshot_id: 's'.repeat(64), availability,
    map: {
      snapshot_id: 's'.repeat(64), nodes: [{ id: 'n', snapshot_id: 's'.repeat(64), kind: 'mystery', label: 'x', resolution: 'resolved', evidence_ids: [] }],
      edges: [], truncated: false, limits: [], file_reports: [],
    },
  }
  expect(() => parseMapResponse(response)).toThrow(ApiSchemaError)
})

test('impact-paths：非布尔 truncated 被拒绝', () => {
  const page = {
    schema_version: '1.3.0', total: 1, availability,
    items: [{ schema_version: '1.3.0', origin_id: 'm', direction: 'up', node_ids: [], edge_ids: [], evidence_ids: [], resolution: 'resolved', truncated: 'yes' }],
  }
  expect(() => parseImpactPaths(page)).toThrow(ApiSchemaError)
})

test('summary：缺必填 limits 被拒绝', () => {
  const { limits, ...withoutLimits } = {
    schema_version: '1.3.0', analysis_id: 'a', status: 'completed',
    counts: { findings: 1, evidence: 1 }, stage_coverage: [], limits: [],
    evidence_resolution_failures: [], diff_files: [], availability,
  }
  void limits
  expect(() => parseSummary(withoutLimits)).toThrow(ApiSchemaError)
})

test('coverage：decisions 非法状态枚举被拒绝', () => {
  const coverage = {
    schema_version: '1.3.0',
    summary: { snapshot_id: 's'.repeat(64), total: 1, selected: 1, excluded: 0, limited: 0, failed: 0, completed: 0, partial: false },
    decisions: [{ snapshot_id: 's'.repeat(64), path: 'a.ts', status: 'maybe', reason: 'r' }],
    stage_coverage: [], limits: [], total: 1, limit: 20, offset: 0, availability,
  }
  expect(() => parseCoverage(coverage)).toThrow(ApiSchemaError)
})

test('未解析 resolution 是合法枚举（unresolved 无损保留）', () => {
  const response = {
    schema_version: '1.3.0', side: 'target', snapshot_id: 's'.repeat(64), availability,
    map: {
      snapshot_id: 's'.repeat(64),
      nodes: [
        { id: 'n1', snapshot_id: 's'.repeat(64), kind: 'method', label: 'x', resolution: 'unresolved', evidence_ids: [], file_path: null, start_line: null, end_line: null, language: null, note: null },
        { id: 'n2', snapshot_id: 's'.repeat(64), kind: 'external_service', label: 'svc', resolution: 'resolved', evidence_ids: [], file_path: null, start_line: null, end_line: null, language: null, note: null },
      ],
      edges: [{ id: 'e1', snapshot_id: 's'.repeat(64), relation: 'sends', source_id: 'n1', target_id: 'n2', resolution: 'candidate', evidence_ids: [], note: null, file_path: 'a.ts', line: 3 }],
      truncated: false, limits: [], file_reports: [],
    },
  }
  const parsed = parseMapResponse(response)
  expect(parsed.map.nodes[0].resolution).toBe('unresolved')
  expect(parsed.map.nodes[1].kind).toBe('external_service')
})
