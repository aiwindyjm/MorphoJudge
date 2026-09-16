'use client'

import { useMemo, useState } from 'react'
import SoftwareWorkspace, { views, type ViewId } from './software-map/workspace'
import { Icon } from './icons'

type Status = '待复核' | '需调查' | '已确认' | '误报'
type Finding = { id: string; title: string; category: string; impact: string; reliability: string; file: string; line: number; status: Status; detail: string }

const seed: Finding[] = [
  { id: 'E-001', title: '新增外部网络请求', category: '网络访问', impact: '高影响', reliability: '事实', file: 'app/diagnostics.py', line: 24, status: '需调查', detail: '检测到向外部地址发起请求。需要确认请求目的、数据字段与用户授权。' },
  { id: 'E-002', title: 'Shell 命令调用', category: '系统命令', impact: '高影响', reliability: '事实', file: 'app/diagnostics.py', line: 39, status: '待复核', detail: '发现 subprocess 调用。需要确认命令内容、输入来源和调用可达性。' },
  { id: 'E-003', title: '新增 Git 来源依赖', category: '依赖变化', impact: '中影响', reliability: '规则提示', file: 'requirements.txt', line: 8, status: '待复核', detail: '依赖来自 Git URL，版本固定方式和来源可信度需要人工核实。' },
]

export default function Home() {
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

  const navigate = (next: 'setup' | 'report' | 'map', nextView?: ViewId) => {
    setStep(next)
    if (nextView) setView(nextView)
    setMobileOpen(false)
  }

  return <main className="shell">
    <header className="topbar">
      <div className="brand"><button className="mobile-sidebar-toggle" aria-label="打开工作栏" aria-expanded={mobileOpen} aria-controls="workspace-sidebar" onClick={() => setMobileOpen(true)}>☰</button><img className="mark" src="/logo-96.png" alt="" width="32" height="32" /><div><strong>MorphoJudge</strong><small>闪蝶判官</small></div></div>
      <div className="top-actions"><span className="local-pill"><i /> 本地模式</span><button className="ghost">文档</button><button className="avatar">M</button></div>
    </header>
    <div className={`workspace ${sidebarOpen ? '' : 'sidebar-collapsed'} ${mobileOpen ? 'mobile-sidebar-open' : ''}`}>
      {mobileOpen && <button className="sidebar-backdrop" aria-label="关闭导航遮罩" onClick={() => setMobileOpen(false)} />}
      <aside id="workspace-sidebar" className="sidebar" aria-label="工作栏" onKeyDown={(event) => { if (event.key === 'Escape') { setMobileOpen(false); document.querySelector<HTMLButtonElement>('.mobile-sidebar-toggle')?.focus() } }}>
        <details className="workspace-picker"><summary><span className="folder"><Icon name="folder" /></span><span><strong>demo-service</strong><small>当前工作空间</small></span><span>⌄</span></summary><div><code>~/Projects/demo-service</code><p>当前只有此示例工作空间，尚未接入本地仓库。</p></div></details>
        <button className="dock-toggle" aria-label={sidebarOpen ? '收起左侧栏' : '展开左侧栏'} aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(!sidebarOpen)}>{sidebarOpen ? '‹' : '›'}</button>
        <button className="mobile-sidebar-close" aria-label="关闭工作栏" onClick={() => { setMobileOpen(false); document.querySelector<HTMLButtonElement>('.mobile-sidebar-toggle')?.focus() }}>关闭 ×</button>
        <nav className="work-nav" aria-label="工作台导航">
          <button className={`nav ${step === 'setup' ? 'active' : ''}`} aria-current={step === 'setup' ? 'page' : undefined} onClick={() => navigate('setup')}><span className="nav-icon" aria-hidden="true"><Icon name="filePlus" /></span><span className="nav-label">新建分析</span></button>
          <div className="workspace-views" role="group" aria-label="软件理解视图">{views.map((item) => <button className={`nav view-nav ${step === 'map' && view === item.id ? 'active' : ''}`} id={`view-${item.id}`} key={item.id} aria-current={step === 'map' && view === item.id ? 'page' : undefined} onClick={() => navigate('map', item.id)}><span className="nav-icon" aria-hidden="true"><Icon name={item.id} /></span><span className="nav-label">{item.label}</span></button>)}</div>
          <button className={`nav report-nav ${step === 'report' ? 'active' : ''}`} aria-current={step === 'report' ? 'page' : undefined} onClick={() => navigate('report')}><span className="nav-icon" aria-hidden="true"><Icon name="report" /></span><span className="nav-label">分析报告</span><span className="nav-count" aria-label="1 个报告">1</span></button>
        </nav>
        <div className="side-footer"><span className="status-dot" /> 示例模式<small>未连接真实分析服务</small></div>
      </aside>
      <section className="content">
        <div hidden={step !== 'map'}><SoftwareWorkspace tab={view} setTab={setView} /></div>
        {step === 'setup' && <Setup model={model} setModel={setModel} onStart={() => { setStep('running'); setTimeout(() => setStep('report'), 900) }} />}
        {step === 'running' && <Running />}
        {step === 'report' && <Report findings={findings} visible={visible} selected={selected} setSelected={setSelected} filter={filter} setFilter={setFilter} updateStatus={updateStatus} />}
      </section>
    </div>
  </main>
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
function Report({ findings, visible, selected, setSelected, filter, setFilter, updateStatus }: { findings: Finding[]; visible: Finding[]; selected: Finding; setSelected: (f: Finding) => void; filter: string; setFilter: (v: string) => void; updateStatus: (s: Status) => void }) { return <div className="report"><div className="report-head"><div><div className="eyebrow">分析报告 · 完成于今天 19:42</div><h2>demo-service <span>↔</span> f09c2de</h2><p>对比 a31f90c → f09c2de · 12 个文件变更 · 3 个发现 · 覆盖限制 1 项</p></div><button className="outline"><Icon name="download" size={14} /> 导出报告</button></div><div className="summary"><div><small>可信状态</small><strong className="amber">需要人工复核</strong></div><div><small>变更文件</small><strong>12</strong></div><div><small>高影响关注项</small><strong className="red">2</strong></div><div><small>模型解释</small><strong>已生成</strong></div></div><div className="report-grid"><div className="findings"><div className="section-title"><h3>发现 <span>{findings.length}</span></h3><select value={filter} onChange={(e) => setFilter(e.target.value)}><option>全部</option><option>网络访问</option><option>系统命令</option><option>依赖变化</option></select></div>{visible.map((item) => <button className={`finding ${selected.id === item.id ? 'selected' : ''}`} key={item.id} onClick={() => setSelected(item)}><div className="finding-icon"><Icon name={item.category === '依赖变化' ? 'package' : item.category === '系统命令' ? 'terminal' : 'globe'} /></div><div className="finding-body"><strong>{item.title}</strong><small>{item.file}:{item.line} · {item.reliability}</small><div><span className={item.impact === '高影响' ? 'impact high' : 'impact medium'}>{item.impact}</span><span className="state">{item.status}</span></div></div><span className="chevron">›</span></button>)}</div><div className="evidence"><div className="evidence-head"><span className="evidence-id">{selected.id}</span><span className={selected.impact === '高影响' ? 'impact high' : 'impact medium'}>{selected.impact}</span><span className="evidence-file">{selected.file}:{selected.line}</span></div><h3>{selected.title}</h3><p>{selected.detail}</p><div className="code"><div className="code-head"><span>新侧 · {selected.file}</span><span>行 {selected.line}</span></div><pre><code><i>{selected.line - 1}</i>  def collect_diagnostics():{`\n`}<i>{selected.line}</i>  <mark>requests.post(endpoint, json=payload)</mark>{`\n`}<i>{selected.line + 1}</i>  return {"{'ok': True}"}</code></pre></div><div className="explain"><div className="explain-title"><span aria-hidden="true"><Icon name="cpu" size={15} /></span><strong>本地模型解释</strong><small>Qwen Coder · Ollama</small></div><p>这段变更可能将诊断数据发送到外部服务。当前证据只能证明存在请求调用，无法确认请求是否实际执行或数据是否敏感。</p><a>查看引用证据 E-{selected.id.slice(2)} →</a></div><div className="review"><small>人工复核</small><div>{(['需调查', '已确认', '误报'] as Status[]).map((status) => <button className={selected.status === status ? 'review-active' : ''} key={status} onClick={() => updateStatus(status)}>{status}</button>)}</div></div></div></div></div> }
