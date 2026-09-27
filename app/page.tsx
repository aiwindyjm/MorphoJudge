'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import SoftwareWorkspace, { views, type ViewId } from './software-map/workspace'
import { adaptSoftwareMap, consistencyItemsFromFindings } from './software-map/adapter'
import { fixture } from './software-map/fixture'
import type { Finding, FindingDetail, FindingsPage, ImpactPathsPage, SoftwareMap } from './lib/contracts'
import {
  ApiConnectionError, ApiError, cancelAnalysis, createAnalysis, getAnalysis,
  getCoverage, getExplainProviders, getFindingDetail, getFindings, getImpactPaths, getRepositories, getSoftwareMap, getSummary, isTerminal,
} from './lib/api'
import { ApiSchemaError } from './lib/schemas'
import { RealArtifacts, RealReport, RealRunning, RealSetup, type FindingListItem } from './real-analysis'
import { Icon } from './icons'

type Status = '待复核' | '需调查' | '已确认' | '误报'
type FindingSeed = { id: string; title: string; category: string; impact: string; reliability: string; file: string; line: number; status: Status; detail: string }

const seed: FindingSeed[] = [
  { id: 'E-001', title: '新增外部网络请求', category: '网络访问', impact: '高影响', reliability: '事实', file: 'app/diagnostics.py', line: 24, status: '需调查', detail: '检测到向外部地址发起请求。需要确认请求目的、数据字段与用户授权。' },
  { id: 'E-002', title: 'Shell 命令调用', category: '系统命令', impact: '高影响', reliability: '事实', file: 'app/diagnostics.py', line: 39, status: '待复核', detail: '发现 subprocess 调用。需要确认命令内容、输入来源和调用可达性。' },
  { id: 'E-003', title: '新增 Git 来源依赖', category: '依赖变化', impact: '中影响', reliability: '规则提示', file: 'requirements.txt', line: 8, status: '待复核', detail: '依赖来自 Git URL，版本固定方式和来源可信度需要人工核实。' },
]

const EMPTY_ARTIFACTS: RealArtifacts = { summary: null, coverage: null, findings: null, map: null, impact: null, consistency: [], notReady: {} }

// http://web:3000 非安全上下文时 crypto.randomUUID 不可用；用 getRandomValues 回退
const newIdempotencyKey = (): string => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function formatApiError(error: unknown): string {
  if (error instanceof ApiError) return `${error.message}（${error.code}）`
  if (error instanceof ApiConnectionError) return error.message
  if (error instanceof ApiSchemaError) return `服务响应不符合契约：${error.message}`
  return error instanceof Error ? error.message : String(error)
}

export default function Home() {
  const [mode, setMode] = useState<'sample' | 'real'>('sample')
  const [step, setStep] = useState<'setup' | 'running' | 'report' | 'map'>('map')
  const [findings, setFindings] = useState(seed)
  const [selected, setSelected] = useState(seed[0])
  const [filter, setFilter] = useState('全部')
  const [model, setModel] = useState(true)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [view, setView] = useState<ViewId>('trace')
  const visible = useMemo(() => filter === '全部' ? findings : findings.filter((item) => item.category === filter), [filter, findings])
  const updateStatus = (status: Status) => { setFindings((items) => items.map((item) => item.id === selected.id ? { ...item, status } : item)); setSelected({ ...selected, status }) }

  // --- 真实模式状态（WEB-001/003） ---
  const [repositories, setRepositories] = useState<Awaited<ReturnType<typeof getRepositories>> | null>(null)
  const [repositoriesError, setRepositoriesError] = useState<string | null>(null)
  const [repositoryId, setRepositoryId] = useState('')
  const [baseRef, setBaseRef] = useState('HEAD~1')
  const [targetRef, setTargetRef] = useState('HEAD')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [analysisId, setAnalysisId] = useState<string | null>(null)
  const [analysis, setAnalysis] = useState<Awaited<ReturnType<typeof getAnalysis>> | null>(null)
  const [pollError, setPollError] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [artifacts, setArtifacts] = useState<RealArtifacts>(EMPTY_ARTIFACTS)
  const [findingsCategory, setFindingsCategory] = useState('')
  const [findingsOffset, setFindingsOffset] = useState(0)
  const [coverageStatus, setCoverageStatus] = useState('')
  const [coverageOffset, setCoverageOffset] = useState(0)
  const [detail, setDetail] = useState<FindingDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [mapSide, setMapSide] = useState<'target' | 'base'>('target')
  const [serviceMap, setServiceMap] = useState<SoftwareMap | null>(null)
  const [explainProviders, setExplainProviders] = useState<import('./lib/contracts').ExplainProvidersResponse | null>(null)
  const [impactPage, setImpactPage] = useState<ImpactPathsPage | null>(null)
  const [consistencyFindings, setConsistencyFindings] = useState<Finding[]>([])
  const artifactsLoadedFor = useRef<string | null>(null)
  const repositoryName = repositories?.items.find((item) => item.repository_id === repositoryId)?.name ?? analysis?.repository_id.slice(0, 12) ?? ''

  // 刷新恢复：URL ?analysis=<id> 只读取/轮询，绝不重新 POST。
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get('analysis')
    if (fromUrl) {
      setMode('real')
      setAnalysisId(fromUrl)
      setStep('running')
    }
  }, [])

  const loadRepositories = useCallback(async () => {
    setRepositoriesError(null)
    try {
      const page = await getRepositories()
      setRepositories(page)
      setRepositoryId((current) => current || page.items[0]?.repository_id || '')
    } catch (error) {
      setRepositoriesError(formatApiError(error))
    }
  }, [])

  useEffect(() => {
    if (mode === 'real' && repositories === null && repositoriesError === null) void loadRepositories()
  }, [mode, repositories, repositoriesError, loadRepositories])

  const resetArtifacts = useCallback(() => {
    artifactsLoadedFor.current = null
    setArtifacts(EMPTY_ARTIFACTS)
    setServiceMap(null)
    setImpactPage(null)
    setConsistencyFindings([])
    setDetail(null)
    setFindingsCategory('')
    setFindingsOffset(0)
    setCoverageStatus('')
    setCoverageOffset(0)
    setMapSide('target')
  }, [])

  const loadArtifacts = useCallback(async (targetId: string) => {
    const notReady: Record<string, string> = {}
    const next: RealArtifacts = { summary: null, coverage: null, findings: null, map: null, impact: null, consistency: [], notReady }
    const summary = await getSummary(targetId).catch((error: unknown) => {
      if (error instanceof ApiError && error.code === 'ANALYSIS_NOT_READY') notReady.summary = `${error.message}（当前状态：${STATUS_SAFE(error)}）`
      else notReady.summary = formatApiError(error)
      return null
    })
    next.summary = summary
    const [coverage, findingsPage, mapResponse, impact, consistency] = await Promise.all([
      getCoverage(targetId, { limit: 20, offset: 0 }).catch((error: unknown) => { notReady.coverage = formatApiError(error); return null }),
      getFindings(targetId, { limit: 20, offset: 0 }).catch((error: unknown) => { notReady.findings = formatApiError(error); return null as FindingsPage | null }),
      getSoftwareMap(targetId, 'target').catch((error: unknown) => { notReady.map = formatApiError(error); return null }),
      getImpactPaths(targetId).catch((error: unknown) => { notReady.impact = formatApiError(error); return null }),
      getFindings(targetId, { category: 'consistency', limit: 100 }).then((page) => page.items.map((item) => item.finding)).catch(() => [] as Finding[]),
    ])
    next.coverage = coverage
    next.findings = findingsPage
    next.impact = impact
    next.consistency = consistency
    if (targetId !== artifactsLoadedFor.current && artifactsLoadedFor.current !== null) return // 过期结果丢弃
    setArtifacts(next)
    if (mapResponse) setServiceMap(mapResponse.map)
    setImpactPage(impact)
    setConsistencyFindings(consistency)
  }, [])

  // 状态轮询：只轮询未终态（含刷新恢复时的首拍，analysis 尚为 null）；
  // 终态停止；连接错误保留最后已知状态并可重试。
  useEffect(() => {
    if (mode !== 'real' || !analysisId) return
    if (analysis && isTerminal(analysis.status)) return
    let stopped = false
    const tick = async () => {
      try {
        const fresh = await getAnalysis(analysisId)
        if (stopped) return
        setAnalysis(fresh)
        setPollError(null)
      } catch (error) {
        if (stopped || (error instanceof DOMException && error.name === 'AbortError')) return
        setPollError(formatApiError(error))
      }
    }
    void tick()
    const timer = window.setInterval(tick, 1500)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [mode, analysisId, analysis?.status])

  // 终态 → 加载产物（每个分析只加载一次基础产物）。
  useEffect(() => {
    if (mode !== 'real' || !analysisId || !analysis || !isTerminal(analysis.status)) return
    if (artifactsLoadedFor.current === analysisId) return
    artifactsLoadedFor.current = analysisId
    void loadArtifacts(analysisId)
  }, [mode, analysisId, analysis?.status, loadArtifacts])

  // 终态后自动从“运行中”切到报告视图（刷新恢复走同一路径）。
  useEffect(() => {
    if (mode !== 'real' || !analysis || !isTerminal(analysis.status)) return
    setStep((current) => (current === 'running' ? 'report' : current))
  }, [mode, analysis])

  // 终态后拉取解释 Provider 状态（失败静默：面板显示未知配置）。
  useEffect(() => {
    if (mode !== 'real' || !analysisId || !analysis || !isTerminal(analysis.status)) return
    if (artifactsLoadedFor.current !== analysisId) return
    let stale = false
    void getExplainProviders(analysisId)
      .then((providers) => { if (!stale) setExplainProviders(providers) })
      .catch(() => { if (!stale) setExplainProviders(null) })
    return () => { stale = true }
  }, [mode, analysisId, analysis?.status])

  // 发现分页/过滤（服务端分页；过滤变更重置 offset；防迟到响应串页）。
  useEffect(() => {
    if (mode !== 'real' || !analysisId || !analysis || !isTerminal(analysis.status)) return
    const requestKey = `${analysisId}|${findingsCategory}|${findingsOffset}`
    let stale = false
    void getFindings(analysisId, { limit: 20, offset: findingsOffset, category: findingsCategory || undefined })
      .then((page) => { if (!stale) setArtifacts((current) => ({ ...current, findings: page, notReady: { ...current.notReady, findings: '' } })) })
      .catch((error: unknown) => { if (!stale) setArtifacts((current) => ({ ...current, findings: null, notReady: { ...current.notReady, findings: formatApiError(error) } })) })
    return () => { stale = true; void requestKey }
  }, [mode, analysisId, analysis?.status, findingsCategory, findingsOffset])

  // 覆盖决策分页/过滤。
  useEffect(() => {
    if (mode !== 'real' || !analysisId || !analysis || !isTerminal(analysis.status)) return
    let stale = false
    void getCoverage(analysisId, { limit: 20, offset: coverageOffset, status: coverageStatus || undefined })
      .then((page) => { if (!stale) setArtifacts((current) => ({ ...current, coverage: page, notReady: { ...current.notReady, coverage: '' } })) })
      .catch((error: unknown) => { if (!stale) setArtifacts((current) => ({ ...current, coverage: null, notReady: { ...current.notReady, coverage: formatApiError(error) } })) })
    return () => { stale = true }
  }, [mode, analysisId, analysis?.status, coverageStatus, coverageOffset])

  // 基线/目标侧切换（真实模式图）。
  useEffect(() => {
    if (mode !== 'real' || !analysisId || !analysis || !isTerminal(analysis.status)) return
    if (artifactsLoadedFor.current !== analysisId) return
    let stale = false
    if (mapSide === 'target' && serviceMap !== null) return
    void getSoftwareMap(analysisId, mapSide)
      .then((response) => { if (!stale) setServiceMap(response.map) })
      .catch(() => { if (!stale) setServiceMap(null) })
    return () => { stale = true }
  }, [mode, analysisId, analysis?.status, mapSide, serviceMap])

  const selectFinding = useCallback(async (item: FindingListItem) => {
    if (!analysisId) return
    setDetailLoading(true)
    try {
      setDetail(await getFindingDetail(analysisId, item.finding.id))
    } catch (error) {
      setDetail(null)
      setPollError(formatApiError(error))
    } finally {
      setDetailLoading(false)
    }
  }, [analysisId])

  const startRealAnalysis = useCallback(async () => {
    if (!repositoryId) return
    setSubmitting(true)
    setFormError(null)
    const key = newIdempotencyKey() // 每次明确“开始/重新分析”都是新任务；网络重试在 api.ts 内复用此 key
    try {
      const created = await createAnalysis({ repository_id: repositoryId, base_ref: baseRef.trim(), target_ref: targetRef.trim() }, key)
      resetArtifacts()
      setAnalysisId(created.analysis_id)
      setAnalysis(created)
      window.history.replaceState(null, '', `?analysis=${encodeURIComponent(created.analysis_id)}`)
      setStep('running')
    } catch (error) {
      setFormError(formatApiError(error))
    } finally {
      setSubmitting(false)
    }
  }, [repositoryId, baseRef, targetRef, resetArtifacts])

  const doCancel = useCallback(async () => {
    if (!analysisId) return
    setCancelling(true)
    try {
      const result = await cancelAnalysis(analysisId)
      if (result.analysis) setAnalysis(result.analysis)
      if (!result.accepted) setPollError(null) // 409：已按真实终态刷新
    } catch (error) {
      setPollError(formatApiError(error))
    } finally {
      setCancelling(false)
    }
  }, [analysisId])

  const switchMode = (next: 'sample' | 'real') => {
    setMode(next)
    setMobileOpen(false)
    if (next === 'sample') {
      setAnalysis(null)
      setAnalysisId(null)
      setPollError(null)
      resetArtifacts()
      window.history.replaceState(null, '', window.location.pathname)
      setStep('map')
    } else {
      setStep('setup')
    }
  }

  const navigate = (next: 'setup' | 'report' | 'map', nextView?: ViewId) => {
    setStep(next)
    if (nextView) setView(nextView)
    setMobileOpen(false)
  }

  const realGraph = useMemo(() => {
    if (mode !== 'real') return null
    if (serviceMap) return adaptSoftwareMap(serviceMap, mapSide)
    return { nodes: [], edges: [], limits: artifacts.summary?.limits ?? [], snapshotId: artifacts.summary?.snapshot_id ?? '', side: mapSide, truncated: false, fileReports: [] }
  }, [mode, serviceMap, mapSide, artifacts.summary])
  const consistencyItems = useMemo(() => consistencyItemsFromFindings(consistencyFindings), [consistencyFindings])
  const terminal = analysis !== null && isTerminal(analysis.status)

  return <main className="shell">
    <header className="topbar">
      <div className="brand"><button className="mobile-sidebar-toggle" aria-label="打开工作栏" aria-expanded={mobileOpen} aria-controls="workspace-sidebar" onClick={() => setMobileOpen(true)}>☰</button><img className="mark" src="/logo-96.png" alt="" width="32" height="32" /><div><strong>MorphoJudge</strong><small>闪蝶判官</small></div></div>
      <div className="top-actions"><span className="local-pill"><i /> 本地模式</span><button className="ghost">文档</button><button className="avatar">M</button></div>
    </header>
    <div className={`workspace ${sidebarOpen ? '' : 'sidebar-collapsed'} ${mobileOpen ? 'mobile-sidebar-open' : ''}`}>
      {mobileOpen && <button className="sidebar-backdrop" aria-label="关闭导航遮罩" onClick={() => setMobileOpen(false)} />}
      <aside id="workspace-sidebar" className="sidebar" aria-label="工作栏" onKeyDown={(event) => { if (event.key === 'Escape') { setMobileOpen(false); document.querySelector<HTMLButtonElement>('.mobile-sidebar-toggle')?.focus() } }}>
        <details className="workspace-picker"><summary><span className="folder"><Icon name="folder" /></span><span><strong>{mode === 'real' ? (repositoryName || '未选择仓库') : 'demo-service'}</strong><small>{mode === 'real' ? '真实分析工作区' : '当前工作空间'}</small></span><span>⌄</span></summary><div><code>{mode === 'real' ? 'daemon 登记仓库（只读挂载）' : '~/Projects/demo-service'}</code><p>{mode === 'real' ? '真实分析通过本地 daemon 读取已登记仓库。' : '当前只有此示例工作空间。'}</p></div></details>
        <button className="dock-toggle" aria-label={sidebarOpen ? '收起左侧栏' : '展开左侧栏'} aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(!sidebarOpen)}>{sidebarOpen ? '‹' : '›'}</button>
        <button className="mobile-sidebar-close" aria-label="关闭工作栏" onClick={() => { setMobileOpen(false); document.querySelector<HTMLButtonElement>('.mobile-sidebar-toggle')?.focus() }}>关闭 ×</button>
        <nav className="work-nav" aria-label="工作台导航">
          <button className={`nav ${step === 'setup' ? 'active' : ''}`} aria-current={step === 'setup' ? 'page' : undefined} onClick={() => navigate('setup')}><span className="nav-icon" aria-hidden="true"><Icon name="filePlus" /></span><span className="nav-label">新建分析</span></button>
          <div className="workspace-views" role="group" aria-label="软件理解视图">{views.map((item) => <button className={`nav view-nav ${step === 'map' && view === item.id ? 'active' : ''}`} id={`view-${item.id}`} key={item.id} aria-current={step === 'map' && view === item.id ? 'page' : undefined} onClick={() => navigate('map', item.id)}><span className="nav-icon" aria-hidden="true"><Icon name={item.id} /></span><span className="nav-label">{item.label}</span></button>)}</div>
          <button className={`nav report-nav ${step === 'report' ? 'active' : ''}`} aria-current={step === 'report' ? 'page' : undefined} onClick={() => navigate('report')}><span className="nav-icon" aria-hidden="true"><Icon name="report" /></span><span className="nav-label">分析报告</span>{mode === 'real' && terminal && analysis?.counts.findings != null && <span className="nav-count" aria-label={`${analysis.counts.findings} 个发现`}>{analysis.counts.findings}</span>}{mode === 'sample' && <span className="nav-count" aria-label="1 个报告">1</span>}</button>
        </nav>
        <div className="side-footer">
          <div className="mode-switch" role="group" aria-label="数据模式切换">
            <button className={mode === 'sample' ? 'mode-active' : ''} aria-pressed={mode === 'sample'} onClick={() => switchMode('sample')}>示例</button>
            <button className={mode === 'real' ? 'mode-active' : ''} aria-pressed={mode === 'real'} onClick={() => switchMode('real')}>真实</button>
          </div>
          <span className="status-dot" /> {mode === 'sample' ? '示例模式' : '真实模式'}<small>{mode === 'sample' ? '未连接真实分析服务' : pollError ? 'daemon 连接异常' : '连接本地分析服务'}</small>
        </div>
      </aside>
      <section className="content">
        <div hidden={step !== 'map'}>
          {mode === 'sample' && <SoftwareWorkspace tab={view} setTab={setView} graph={fixture} snapshotLabel="示例快照 · 未接入仓库" mode="sample" />}
          {mode === 'real' && <>
            {terminal && artifacts.summary && <div className="side-switch" role="group" aria-label="图侧别切换">
              <span>快照侧别：</span>
              <button className={mapSide === 'target' ? 'mode-active' : ''} aria-pressed={mapSide === 'target'} onClick={() => setMapSide('target')}>检查版本（新）</button>
              <button className={mapSide === 'base' ? 'mode-active' : ''} aria-pressed={mapSide === 'base'} onClick={() => setMapSide('base')}>对比起点（旧）</button>
            </div>}
            {realGraph && <SoftwareWorkspace key={`${analysisId}-${mapSide}`} tab={view} setTab={setView} graph={realGraph} snapshotLabel={`真实快照 ${(realGraph.snapshotId || '').slice(0, 12)}… · ${mapSide === 'target' ? '新侧' : '旧侧'}`} mode="real" consistencyItems={consistencyItems} impactPaths={impactPage?.items} />}
          </>}
        </div>
        {step === 'setup' && (mode === 'sample'
          ? <Setup model={model} setModel={setModel} onStart={() => { setStep('running'); setTimeout(() => setStep('report'), 900) }} />
          : <RealSetup repositories={repositories} repositoriesError={repositoriesError} repositoryId={repositoryId} baseRef={baseRef} targetRef={targetRef} submitting={submitting} formError={formError}
              onRepositoryChange={setRepositoryId} onBaseChange={setBaseRef} onTargetChange={setTargetRef} onStart={() => void startRealAnalysis()} onRetryRepositories={() => { setRepositories(null); void loadRepositories() }} />)}
        {step === 'running' && (mode === 'sample' ? <Running /> : <RealRunning analysis={analysis} pollError={pollError} cancelling={cancelling} onCancel={() => void doCancel()} onRetry={async () => { if (analysisId) { try { setAnalysis(await getAnalysis(analysisId)); setPollError(null) } catch (error) { setPollError(formatApiError(error)) } } }} />)}
        {step === 'report' && (mode === 'sample'
          ? <Report findings={findings} visible={visible} selected={selected} setSelected={setSelected} filter={filter} setFilter={setFilter} updateStatus={updateStatus} />
          : analysis ? <RealReport analysis={analysis} repositoryName={repositoryName} artifacts={artifacts}
              findingsCategory={findingsCategory} findingsOffset={findingsOffset} coverageStatus={coverageStatus} coverageOffset={coverageOffset}
              detail={detail} detailLoading={detailLoading} explainProviders={explainProviders}
              onEvidenceHighlight={(evidenceId) => { const node = document.querySelector(`[data-evidence-id="${CSS.escape(evidenceId)}"]`); node?.scrollIntoView({ block: 'center' }); (node as HTMLElement | null)?.classList.add('evidence-flash'); window.setTimeout(() => node?.classList.remove('evidence-flash'), 1600) }}
              onFindingsCategoryChange={(value) => { setFindingsCategory(value); setFindingsOffset(0); setDetail(null) }}
              onFindingsPage={(delta) => setFindingsOffset((value) => Math.max(0, value + delta * 20))}
              onCoverageStatusChange={(value) => { setCoverageStatus(value); setCoverageOffset(0) }}
              onCoveragePage={(delta) => setCoverageOffset((value) => Math.max(0, value + delta * 20))}
              onFindingSelect={(item) => void selectFinding(item)}
              onReviewChange={async (findingId, state, note) => {
                if (!analysisId) return
                const response = await fetch(`/api/daemon/v1/analyses/${encodeURIComponent(analysisId)}/reviews`, {
                  method: 'POST',
                  headers: { 'content-type': 'application/json' },
                  body: JSON.stringify({ finding_id: findingId, state, note }),
                })
                if (!response.ok) throw new Error(`复核保存失败：${response.status}`)
                setDetail((current) => current && current.finding.id === findingId ? { ...current, review_state: state as typeof current.review_state } : current)
              }}
              onReanalyze={() => void startRealAnalysis()}
              onOpenMap={() => navigate('map', 'graph')} />
          : <div className="linked-empty" role="status">没有可显示的分析。请先在“新建分析”创建，或切换回示例模式。</div>)}
      </section>
    </div>
  </main>
}

const STATUS_SAFE = (error: unknown): string => {
  if (error instanceof ApiError) return String(error.details?.status ?? '')
  return ''
}

function Setup({ model, setModel, onStart }: { model: boolean; setModel: (value: boolean) => void; onStart: () => void }) {
  return <div className="setup">
    <div className="card form-card">
      <label htmlFor="repository-path">项目所在文件夹</label><div className="path-input"><span aria-hidden="true"><Icon name="folder" size={14} /></span><input id="repository-path" defaultValue="/Users/you/Projects/demo-service" readOnly /><span className="tag">示例项目</span></div>
      <div className="grid two">
        <div><label htmlFor="base-version">对比起点（修改前）</label><select id="base-version" defaultValue="main"><option value="main">main 分支 · a31f90c</option></select></div>
        <div><label htmlFor="target-version">检查版本（修改后）</label><select id="target-version" defaultValue="head"><option value="head">当前检出的提交（HEAD）· f09c2de</option></select></div>
      </div>
      <div className="divider" /><label>检查内容</label>
      <label className="check"><input type="checkbox" defaultChecked /> 文件改动与代码差异（Diff）</label>
      <label className="check"><input type="checkbox" defaultChecked /> 网络、文件操作与依赖变化</label>
      <label className="check"><input type="checkbox" checked={model} onChange={(event) => setModel(event.target.checked)} /> 附加 AI 解释（可选） <span className="tag">示例</span></label>
      <button className="primary" onClick={onStart}>预览分析流程 <span>→</span></button>
    </div>
    <div className="recent"><span>示例分析</span><div><b>demo-service</b><small>3 个发现 · 模拟结果</small><button onClick={onStart}>查看分析流程 →</button></div></div>
  </div>
}
function Running() { return <div className="running"><div className="spinner"><img src="/logo-96.png" alt="" /></div><div className="eyebrow">正在本地分析</div><h1>理解这次变更…</h1><p>代码留在本机。分析器不会执行仓库中的脚本或命令。</p><div className="progress"><span /></div><div className="stages"><div className="done">✓ <span>读取 Git 变化</span><small>完成</small></div><div className="done">✓ <span>解析项目结构</span><small>完成</small></div><div className="active-stage">◌ <span>提取行为与依赖线索</span><small>进行中</small></div><div>○ <span>生成本地模型解释</span><small>等待</small></div></div></div> }
function Report({ findings, visible, selected, setSelected, filter, setFilter, updateStatus }: { findings: FindingSeed[]; visible: FindingSeed[]; selected: FindingSeed; setSelected: (f: FindingSeed) => void; filter: string; setFilter: (v: string) => void; updateStatus: (s: Status) => void }) { return <div className="report"><div className="report-head"><div><div className="eyebrow">分析报告 · 完成于今天 19:42</div><h2>demo-service <span>↔</span> f09c2de</h2><p>对比 a31f90c → f09c2de · 12 个文件变更 · 3 个发现 · 覆盖限制 1 项</p></div><button className="outline"><Icon name="download" size={14} /> 导出报告</button></div><div className="summary"><div><small>可信状态</small><strong className="amber">需要人工复核</strong></div><div><small>变更文件</small><strong>12</strong></div><div><small>高影响关注项</small><strong className="red">2</strong></div><div><small>模型解释</small><strong>已生成</strong></div></div><div className="report-grid"><div className="findings"><div className="section-title"><h3>发现 <span>{findings.length}</span></h3><select value={filter} onChange={(e) => setFilter(e.target.value)}><option>全部</option><option>网络访问</option><option>系统命令</option><option>依赖变化</option></select></div>{visible.map((item) => <button className={`finding ${selected.id === item.id ? 'selected' : ''}`} key={item.id} onClick={() => setSelected(item)}><div className="finding-icon"><Icon name={item.category === '依赖变化' ? 'package' : item.category === '系统命令' ? 'terminal' : 'globe'} /></div><div className="finding-body"><strong>{item.title}</strong><small>{item.file}:{item.line} · {item.reliability}</small><div><span className={item.impact === '高影响' ? 'impact high' : 'impact medium'}>{item.impact}</span><span className="state">{item.status}</span></div></div><span className="chevron">›</span></button>)}</div><div className="evidence"><div className="evidence-head"><span className="evidence-id">{selected.id}</span><span className={selected.impact === '高影响' ? 'impact high' : 'impact medium'}>{selected.impact}</span><span className="evidence-file">{selected.file}:{selected.line}</span></div><h3>{selected.title}</h3><p>{selected.detail}</p><div className="code"><div className="code-head"><span>新侧 · {selected.file}</span><span>行 {selected.line}</span></div><pre><code><i>{selected.line - 1}</i>  def collect_diagnostics():{`\n`}<i>{selected.line}</i>  <mark>requests.post(endpoint, json=payload)</mark>{`\n`}<i>{selected.line + 1}</i>  return {"{'ok': True}"}</code></pre></div><div className="explain"><div className="explain-title"><span aria-hidden="true"><Icon name="cpu" size={15} /></span><strong>本地模型解释</strong><small>Qwen Coder · Ollama</small></div><p>这段变更可能将诊断数据发送到外部服务。当前证据只能证明存在请求调用，无法确认请求是否实际执行或数据是否敏感。</p><a>查看引用证据 E-{selected.id.slice(2)} →</a></div><div className="review"><small>人工复核</small><div>{(['需调查', '已确认', '误报'] as Status[]).map((status) => <button className={selected.status === status ? 'review-active' : ''} key={status} onClick={() => updateStatus(status)}>{status}</button>)}</div></div></div></div></div> }
