'use client'

// WEB-003/004 真实模式组件：创建表单 / 运行状态 / 真实报告 / 解释面板。
// 全部数据来自 daemon 已提交产物；409 未就绪、连接失败、部分产物
// （failed/cancelled 的 availability=partial）都有明确状态，不造空结论。
// 解释（Batch-06）：四态（idle/loading/failed/completed）、失败重试、
// 引用证据 ID 可点击定位、模型解释与确定性事实视觉分层；示例模式
// 的 Fake 演示不经此组件。

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Icon } from './icons'
import type { AnalysisResponse, CoverageResponse, EvidenceAnchor, ExplainProvidersResponse, ExplanationPayload, Finding, FindingDetail, FindingsPage, ImpactPathsPage, RepositoriesPage, StageRecord, SummaryResponse } from './lib/contracts'

export const CATEGORY_LABEL: Record<string, string> = {
  behavior_network: '网络访问',
  behavior_shell: '系统命令',
  behavior_file: '文件操作',
  behavior_permission: '权限线索',
  dependency: '依赖变化',
  consistency: '一致性',
  structure: '结构',
}
export const STATUS_LABEL: Record<string, string> = {
  queued: '排队中', running: '分析中', completed: '已完成', completed_with_limits: '受限完成',
  failed: '失败', cancelled: '已取消',
}
const STAGE_LABEL: Record<string, string> = {
  git: '读取 Git 变化', selection: '文件选择', parse: '解析结构', behavior: '行为规则',
  dependency: '依赖规则', report: '结果落库', explain: '模型解释',
}
const categoryIcon = (category: string): string =>
  category === 'behavior_network' ? 'globe' : category === 'behavior_shell' ? 'terminal' : category === 'dependency' ? 'package' : 'filePlus'

export function RealSetup({
  repositories, repositoriesError, repositoryId, baseRef, targetRef, submitting, formError,
  onRepositoryChange, onBaseChange, onTargetChange, onStart, onRetryRepositories,
}: {
  repositories: RepositoriesPage | null
  repositoriesError: string | null
  repositoryId: string
  baseRef: string
  targetRef: string
  submitting: boolean
  formError: string | null
  onRepositoryChange: (value: string) => void
  onBaseChange: (value: string) => void
  onTargetChange: (value: string) => void
  onStart: () => void
  onRetryRepositories: () => void
}) {
  const ready = repositories !== null && repositories.items.length > 0 && repositoryId !== '' && baseRef.trim() !== '' && targetRef.trim() !== '' && !submitting
  return <div className="setup">
    <div className="card form-card">
      <label htmlFor="repository-id">登记仓库（daemon 只读挂载）</label>
      {repositories === null && !repositoriesError && <div className="path-input"><span>载入仓库列表…</span></div>}
      {repositoriesError && <div className="banner-error" role="alert">{repositoriesError} <button className="text-action" onClick={onRetryRepositories}>重试</button></div>}
      {repositories !== null && (repositories.items.length === 0
        ? <div className="banner-error" role="alert">daemon 没有已登记仓库；请先按 README 挂载并恢复 fixture。</div>
        : <select id="repository-id" value={repositoryId} onChange={(event) => onRepositoryChange(event.target.value)}>
            {repositories.items.map((item) => <option value={item.repository_id} key={item.repository_id}>{item.name}</option>)}
          </select>)}
      <div className="grid two">
        <div><label htmlFor="base-ref">对比起点（修改前 · 旧提交）</label><input id="base-ref" value={baseRef} placeholder="如 HEAD~1 或提交 SHA" onChange={(event) => onBaseChange(event.target.value)} /></div>
        <div><label htmlFor="target-ref">检查版本（修改后 · 新提交）</label><input id="target-ref" value={targetRef} placeholder="如 HEAD 或提交 SHA" onChange={(event) => onTargetChange(event.target.value)} /></div>
      </div>
      <p className="form-hint">方向：旧提交 → 新提交。ref 由本地分析服务校验；无效 ref 会按实际原因显示错误。不发送路径、命令或模型配置。</p>
      <div className="divider" /><label>检查内容</label>
      <label className="check"><input type="checkbox" checked disabled /> 文件改动与确定性分析（固定）</label>
      <label className="check"><input type="checkbox" disabled /> 高级分析选项（后续批次提供）</label>
      <label className="check"><input type="checkbox" disabled /> 附加 AI 解释（后续批次接入）</label>
      {formError && <div className="banner-error" role="alert">{formError}</div>}
      <button className="primary" disabled={!ready} onClick={onStart}>{submitting ? '正在创建…' : '开始本地分析'} <span>→</span></button>
    </div>
    <div className="recent"><span>说明</span><div><b>本地优先</b><small>代码留在本机；分析器不执行仓库脚本。每次点击“开始”生成新的幂等键；网络重试不会创建重复任务。</small></div></div>
  </div>
}

const StageRow = ({ stage }: { stage: StageRecord }) => {
  const label = STAGE_LABEL[stage.stage] ?? stage.stage
  const cls = stage.status === 'completed' ? 'done' : stage.status === 'running' ? 'active-stage' : stage.status === 'failed' ? 'failed-stage' : stage.status === 'skipped' ? 'skipped-stage' : ''
  const mark = stage.status === 'completed' ? '✓' : stage.status === 'running' ? '◌' : stage.status === 'failed' ? '✕' : stage.status === 'skipped' ? '−' : '○'
  const stateText = stage.status === 'completed' ? '完成' : stage.status === 'running' ? '进行中' : stage.status === 'failed' ? '失败' : stage.status === 'skipped' ? '跳过' : '等待'
  return <div className={cls}>{mark} <span>{label}</span><small>{stateText}{stage.detail ? ` · ${stage.detail.slice(0, 60)}` : ''}</small></div>
}

export function RealRunning({ analysis, pollError, cancelling, onCancel, onRetry }: {
  analysis: AnalysisResponse | null
  pollError: string | null
  cancelling: boolean
  onCancel: () => void
  onRetry: () => void
}) {
  const stages = analysis?.stages ?? []
  return <div className="running">
    <div className="spinner"><img src="/logo-96.png" alt="" /></div>
    <div className="eyebrow">正在本地分析 · {analysis ? STATUS_LABEL[analysis.status] : '连接中'}</div>
    <h1>理解这次变更…</h1>
    <p>代码留在本机。分析器不会执行仓库中的脚本或命令。</p>
    {pollError && <div className="banner-error" role="alert">{pollError} <button className="text-action" onClick={onRetry}>立即重试</button></div>}
    <div className="progress"><span /></div>
    <div className="stages">{stages.length ? stages.map((stage) => <StageRow key={stage.stage} stage={stage} />) : <div>等待阶段报告…</div>}</div>
    <div className="running-actions">
      <button className="outline" disabled={cancelling || !analysis} onClick={onCancel}>{cancelling ? '正在取消…' : '取消分析'}</button>
      {analysis && <small>分析 {analysis.analysis_id}</small>}
    </div>
  </div>
}

export interface RealArtifacts {
  summary: SummaryResponse | null
  coverage: CoverageResponse | null
  findings: FindingsPage | null
  map: { nodes: unknown[]; edges: unknown[] } | null
  impact: ImpactPathsPage | null
  consistency: Finding[]
  notReady: Record<string, string>
}

const Section = ({ title, notReady, children }: { title: string; notReady?: string; children: ReactNode }) => (
  <section className="report-section">
    <div className="section-title"><h3>{title}</h3></div>
    {notReady ? <div className="linked-empty" role="status">{notReady}</div> : children}
  </section>
)

const EvidenceBlock = ({ anchor, onHighlight }: { anchor: EvidenceAnchor; onHighlight?: (evidenceId: string) => void }) => (
  <div className="code evidence-anchor" data-evidence-id={anchor.id}>
    <div className="code-head">
      <span>{anchor.side === 'old' ? '旧侧' : anchor.side === 'new' ? '新侧' : '上下文'} · {anchor.path}</span>
      <span>行 {anchor.start_line}–{anchor.end_line}{anchor.commit ? ` · ${anchor.commit.slice(0, 8)}` : ''}</span>
    </div>
    <pre><code>{anchor.snippet}</code></pre>
    <small>证据 {anchor.id}{anchor.rule_id ? ` · 规则 ${anchor.rule_id}` : ''} · 这是片段，不是完整方法。{onHighlight && <button className="text-action" onClick={() => onHighlight(anchor.id)}>解释此证据 →</button>}</small>
  </div>
)

const REVIEW_STATES: Array<{ value: 'unreviewed' | 'needs-investigation' | 'confirmed' | 'dismissed'; label: string }> = [
  { value: 'unreviewed', label: '待复核' },
  { value: 'needs-investigation', label: '需调查' },
  { value: 'confirmed', label: '已确认' },
  { value: 'dismissed', label: '误报' },
]

const FindingDetailBlock = ({ detail, analysisId, onEvidenceClick, onReviewChange }: {
  detail: FindingDetail
  analysisId: string
  onEvidenceClick?: (evidenceId: string) => void
  onReviewChange?: (findingId: string, state: string, note: string) => Promise<void>
}) => {
  const finding = detail.finding
  const [reviewState, setReviewState] = useState(detail.review_state)
  const [reviewNote, setReviewNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState('')

  useEffect(() => { setReviewState(detail.review_state) }, [detail.review_state, detail.finding.id])

  const submitReview = async (state: typeof detail.review_state) => {
    const previous = reviewState
    setReviewState(state)
    if (!onReviewChange) return
    setSaving(true)
    try {
      await onReviewChange(finding.id, state, reviewNote)
      setSavedAt(new Date().toLocaleTimeString('zh-CN'))
    } catch {
      setReviewState(previous)
    } finally {
      setSaving(false)
    }
  }

  return <div className="evidence">
    <div className="evidence-head">
      <span className="evidence-id">{finding.id}</span>
      <span className="impact medium">{CATEGORY_LABEL[finding.category] ?? finding.category}</span>
      <span className="evidence-file">{finding.evidence_ids.length ? `${finding.evidence_ids.length} 条证据` : '无精确定位'}</span>
    </div>
    <h3>{finding.rule_id ?? CATEGORY_LABEL[finding.category] ?? finding.category} · {finding.kind === 'fact' ? '事实' : '规则提示'}</h3>
    {finding.unresolved_reason
      ? <p className="unknown-reason">证据未能精确定位：{finding.unresolved_reason}。未定位不使用近似行号。</p>
      : finding.evidence_ids.length === 0
        ? <p className="unknown-reason">该发现无证据关联。</p>
        : detail.evidence.length === 0
          ? <p className="unknown-reason">证据详情暂不可用。</p>
          : detail.evidence.map((anchor) => <EvidenceBlock key={anchor.id} anchor={anchor} onHighlight={onEvidenceClick} />)}
    <div className="review">
      <small>人工复核 {savedAt && <span className="tag">已保存 {savedAt}</span>}</small>
      <div>
        {REVIEW_STATES.map(({ value, label }) =>
          <button key={value} className={reviewState === value ? 'review-active' : ''} disabled={saving} onClick={() => void submitReview(value)}>{label}</button>)}
      </div>
      {reviewState === 'confirmed' && <p className="form-hint">该发现描述成立，<strong>不代表软件整体安全</strong>。</p>}
      <input aria-label="复核备注" placeholder="备注（仅本地保存）" value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} onBlur={() => void submitReview(reviewState)} />
    </div>
  </div>
}

// --- WEB-004：解释面板（真实模式） ---

export type ExplainPanelHandle = {
  subjectType: 'finding' | 'node'
  subjectId: string
  evidenceIds: string[]
}

const CLAIM_KIND_LABEL: Record<string, string> = {
  restatement: '证据复述',
  inference: '模型推断',
  unknown: '未知项',
}

// 解释错误统一格式化：Schema 错误加"契约"前缀，连接错误区分于业务错误
const formatExplainError = (error: unknown): string => {
  if (error instanceof Error && error.name === 'ApiSchemaError') return `服务响应不符合契约：${error.message}`
  if (error instanceof Error && error.name === 'ApiConnectionError') return error.message
  return error instanceof Error ? error.message : '解释请求失败'
}

export function ExplainPanel({
  analysisId, subject, providers, onEvidenceClick,
}: {
  analysisId: string
  subject: ExplainPanelHandle
  providers: ExplainProvidersResponse | null
  onEvidenceClick?: (evidenceId: string) => void
}) {
  const [state, setState] = useState<'idle' | 'loading' | 'failed' | 'done'>('idle')
  const [explanation, setExplanation] = useState<ExplanationPayload | null>(null)
  const [errorText, setErrorText] = useState('')
  const [provider, setProvider] = useState<'fake' | 'ollama' | 'remote'>('fake')
  const canExplain = subject.evidenceIds.length > 0
  const ollamaInfo = providers?.providers.find((item) => item.provider === 'ollama')
  const ollamaAvailable = ollamaInfo?.available === true
  const ollamaModels = ollamaInfo?.models ?? []

  const run = async () => {
    setState('loading')
    setErrorText('')
    try {
      const { explainSubject, ApiError } = await import('./lib/api')
      const payload = await explainSubject(analysisId, {
        subject_type: subject.subjectType,
        subject_id: subject.subjectId,
        evidence_ids: subject.evidenceIds,
        provider,
      })
      setExplanation(payload)
      setState(payload.status === 'completed' ? 'done' : 'failed')
      if (payload.status === 'failed') setErrorText(payload.errors.join('；') || '模型输出未通过校验')
    } catch (error) {
      setState('failed')
      setErrorText(formatExplainError(error))
    }
  }

  return <section className="explain-panel" aria-live="polite">
    <div className="model-explanation-head">
      <strong>本地模型解释</strong>
      <span>{provider === 'fake' ? 'Fake Provider（离线演示）' : provider === 'ollama' ? (ollamaAvailable ? `Ollama · ${ollamaInfo?.model ?? '本地'}${ollamaModels.length > 0 ? ` · ${ollamaModels.length} 个模型` : ''}` : `Ollama · 不可用（${ollamaInfo?.note?.slice(0, 40) || '未配置'}）`) : '远程 · 需按次授权'}</span>
    </div>
    <p className="form-hint">解释只引用当前证据，不改确定性图；失败不影响报告。</p>
    <div className="toolbar-row">
      <select aria-label="解释 Provider" value={provider} onChange={(event) => setProvider(event.target.value as 'fake' | 'ollama' | 'remote')}>
        <option value="fake">Fake（离线）</option>
        <option value="ollama" disabled={!ollamaAvailable}>Ollama{ollamaAvailable ? '' : '（未配置/不可达）'}</option>
        <option value="remote" disabled>远程（按次授权，待接入确认界面）</option>
      </select>
      {ollamaAvailable && ollamaModels.length > 0 && (
        <span className="chip">已发现模型：{ollamaModels.slice(0, 3).join('、')}{ollamaModels.length > 3 ? ` 等 ${ollamaModels.length} 个` : ''}</span>
      )}
      {!ollamaAvailable && provider !== 'ollama' && (
        <span className="page-status">提示：设置 MORPHOJUDGE_OLLAMA_URL 后可使用本地模型</span>
      )}
      {state === 'idle' && <button className="outline" disabled={!canExplain} onClick={() => void run()}>{canExplain ? '生成本地解释' : '无证据关联，不能解释'}</button>}
      {state === 'loading' && <span className="page-status">生成中…</span>}
      {state === 'failed' && <button className="outline" onClick={() => void run()}>重试</button>}
      {state === 'done' && explanation && <button className="outline" onClick={() => void run()}>重新生成</button>}
    </div>
    {state === 'failed' && <p className="unknown-reason">{errorText || '解释失败；这是模型层失败，不是分析失败。'}</p>}
    {state === 'done' && explanation && <>
      {explanation.claims.map((claim, index) => <div className="model-claim" key={`${claim.text.slice(0, 24)}-${index}`}>
        <span>{CLAIM_KIND_LABEL[claim.kind] ?? claim.kind}</span>
        <p>{claim.text}</p>
        {claim.evidence_ids.map((evidenceId) => <button key={evidenceId} className="text-action" onClick={() => onEvidenceClick?.(evidenceId)}>引用 {evidenceId}</button>)}
      </div>)}
      {explanation.uncertainty && <small>仍需确认：{explanation.uncertainty}</small>}
      <small>Provider {explanation.provider} · {explanation.model} · 上下文 {explanation.context_hash.slice(0, 12)}…{explanation.duration_ms != null ? ` · ${explanation.duration_ms}ms` : ''}</small>
    </>}
  </section>
}

export function RealReport({
  analysis, repositoryName, artifacts, findingsCategory, findingsOffset,
  coverageStatus, coverageOffset, detail, detailLoading, explainProviders, onEvidenceHighlight,
  onFindingsCategoryChange, onFindingsPage, onCoverageStatusChange, onCoveragePage, onFindingSelect, onReanalyze, onOpenMap, onReviewChange,
}: {
  analysis: AnalysisResponse
  repositoryName: string
  artifacts: RealArtifacts
  findingsCategory: string
  findingsOffset: number
  coverageStatus: string
  coverageOffset: number
  detail: FindingDetail | null
  detailLoading: boolean
  explainProviders: ExplainProvidersResponse | null
  onEvidenceHighlight: (evidenceId: string) => void
  onFindingsCategoryChange: (value: string) => void
  onFindingsPage: (delta: number) => void
  onCoverageStatusChange: (value: string) => void
  onCoveragePage: (delta: number) => void
  onFindingSelect: (finding: FindingListItem) => void
  onReanalyze: () => void
  onOpenMap: () => void
  onReviewChange: (findingId: string, state: string, note: string) => Promise<void>
}) {
  const summary = artifacts.summary
  const statusText = STATUS_LABEL[analysis.status] ?? analysis.status
  const partial = summary?.availability.artifacts === 'partial'
  const stageCoverage = useMemo(() => summary?.stage_coverage ?? [], [summary])
  return <div className="report">
    <div className="report-head">
      <div>
        <div className="eyebrow">真实分析 · {statusText}{partial ? ' · 已提交产物（partial）' : ''}</div>
        <h2>{repositoryName} <span>↔</span> {analysis.target_ref}</h2>
        <p>{analysis.base_ref} → {analysis.target_ref} · 分析 {analysis.analysis_id}{analysis.snapshot_id ? ` · 快照 ${analysis.snapshot_id.slice(0, 12)}…` : ''}</p>
        {analysis.failure_reason && <p className="banner-error" role="alert">失败原因：{analysis.failure_reason}</p>}
        {analysis.cancel_requested && analysis.status !== 'cancelled' && <p className="form-hint">已请求取消；分析在阶段边界停止。</p>}
      </div>
      <div className="report-actions">
        <button className="outline" onClick={() => { window.open(`/api/daemon/v1/analyses/${encodeURIComponent(analysis.analysis_id)}/report?format=markdown`, '_blank') }}>导出 Markdown</button>
        <button className="outline" onClick={() => { window.open(`/api/daemon/v1/analyses/${encodeURIComponent(analysis.analysis_id)}/report?format=json`, '_blank') }}>导出 JSON</button>
        <button className="outline" onClick={onOpenMap}>查看关系图 →</button>
        <button className="outline" onClick={onReanalyze}>重新分析（新任务）</button>
      </div>
    </div>
    <div className="summary">
      <div><small>状态</small><strong className={analysis.status === 'completed' ? '' : 'amber'}>{statusText}</strong></div>
      <div><small>发现</small><strong>{analysis.counts.findings ?? '—'}</strong></div>
      <div><small>证据</small><strong>{analysis.counts.evidence ?? '—'}</strong></div>
      <div><small>模型解释</small><strong>未接入</strong></div>
    </div>

    {summary && <Section title="阶段覆盖">
      <table className="map-table stage-table"><thead><tr><th>阶段</th><th>状态</th><th>完成</th><th>失败</th><th>受限</th><th>说明</th></tr></thead><tbody>
        {stageCoverage.map((item) => <tr key={item.stage}><td>{item.stage}</td><td>{item.status}</td><td>{item.completed}</td><td>{item.failed}</td><td>{item.limited}</td><td>{item.notes.join('；') || '—'}</td></tr>)}
      </tbody></table>
    </Section>}

    {summary && summary.limits.length > 0 && <Section title="覆盖限制与未检查范围">
      <ul className="limit-list">{summary.limits.map((limit) => <li key={limit}>{limit}</li>)}</ul>
    </Section>}

    <Section title="文件覆盖（选择决策）" notReady={artifacts.notReady.coverage}>
      {artifacts.coverage && <>
        <div className="chip-row">
          <span className="chip">选中 {artifacts.coverage.summary.selected}</span>
          <span className="chip">排除 {artifacts.coverage.summary.excluded}</span>
          <span className="chip">受限 {artifacts.coverage.summary.limited}</span>
          <span className="chip">失败 {artifacts.coverage.summary.failed}</span>
          {artifacts.coverage.summary.partial && <span className="chip warn">部分覆盖</span>}
        </div>
        <div className="section-title toolbar-row">
          <select aria-label="覆盖状态过滤" value={coverageStatus} onChange={(event) => onCoverageStatusChange(event.target.value)}>
            <option value="">全部状态</option>
            <option value="selected">已选中</option><option value="excluded">已排除</option><option value="limited">受限</option><option value="failed">失败</option>
          </select>
          <span className="page-status">共 {artifacts.coverage.total} 项 · 第 {coverageOffset + 1}–{offsetEnd(coverageOffset, artifacts.coverage.decisions.length)} 项</span>
          <button className="outline" disabled={coverageOffset === 0} onClick={() => onCoveragePage(-1)}>上一页</button>
          <button className="outline" disabled={coverageOffset + artifacts.coverage.decisions.length >= artifacts.coverage.total} onClick={() => onCoveragePage(1)}>下一页</button>
        </div>
        <table className="map-table"><thead><tr><th>文件</th><th>状态</th><th>原因</th><th>规则</th></tr></thead><tbody>
          {artifacts.coverage.decisions.map((decision) => <tr key={decision.path}><td>{decision.path}</td><td>{decision.status}</td><td>{decision.reason}</td><td>{decision.rule_id ?? '—'}</td></tr>)}
        </tbody></table>
      </>}
    </Section>

    <Section title="发现（确定性结果）" notReady={artifacts.notReady.findings}>
      {artifacts.findings && <>
        <div className="section-title toolbar-row">
          <select aria-label="发现类别过滤" value={findingsCategory} onChange={(event) => onFindingsCategoryChange(event.target.value)}>
            <option value="">全部类别</option>
            {Object.entries(CATEGORY_LABEL).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          </select>
          <span className="page-status">共 {artifacts.findings.total} 条 · 已加载 {artifacts.findings.items.length} 条（服务端分页，非完整集合）</span>
          <button className="outline" disabled={findingsOffset === 0} onClick={() => onFindingsPage(-1)}>上一页</button>
          <button className="outline" disabled={findingsOffset + artifacts.findings.items.length >= artifacts.findings.total} onClick={() => onFindingsPage(1)}>下一页</button>
        </div>
        <div className="report-grid">
          <div className="findings">
            {artifacts.findings.items.length === 0
              ? <div className="linked-empty" role="status">本页没有发现。未发现不等于安全；请结合覆盖限制与未检查范围判断。</div>
              : artifacts.findings.items.map((item) => <button className={`finding ${detail?.finding.id === item.finding.id ? 'selected' : ''}`} key={item.finding.id} onClick={() => onFindingSelect(item)}>
                <div className="finding-icon"><Icon name={categoryIcon(item.finding.category)} /></div>
                <div className="finding-body">
                  <strong>{item.finding.rule_id ?? CATEGORY_LABEL[item.finding.category] ?? item.finding.category}</strong>
                  <small>{CATEGORY_LABEL[item.finding.category] ?? item.finding.category} · {item.finding.kind === 'fact' ? '事实' : '规则提示'} · {item.finding.impact === 'high' ? '高影响' : item.finding.impact === 'medium' ? '中影响' : '低影响'}</small>
                  <div><span className="state">{item.finding.evidence_ids.length ? `${item.finding.evidence_ids.length} 条证据` : '无精确定位'}</span><span className="state">{item.review_state === 'unreviewed' ? '待复核' : '已有复核'}</span></div>
                </div>
                <span className="chevron">›</span>
              </button>)}
          </div>
          <div>{detailLoading ? <div className="linked-empty">正在读取证据…</div> : detail ? <>
            <FindingDetailBlock detail={detail} analysisId={analysis.analysis_id} onEvidenceClick={onEvidenceHighlight} onReviewChange={onReviewChange} />
            <ExplainPanel
              analysisId={analysis.analysis_id}
              subject={{ subjectType: 'finding', subjectId: detail.finding.id, evidenceIds: detail.finding.evidence_ids }}
              providers={explainProviders}
              onEvidenceClick={onEvidenceHighlight}
            />
          </> : <div className="linked-empty">左侧选择一条发现查看证据定位。</div>}</div>
        </div>
      </>}
    </Section>
  </div>
}

const offsetEnd = (offset: number, loaded: number): number => offset + loaded

export interface FindingListItem { finding: Finding; review_state: string }
