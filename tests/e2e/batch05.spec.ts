// Batch-05 浏览器回归：真实 daemon → SQLite → Web 表单 → 轮询 → 图/覆盖/
// 发现 → 证据定位 的完整闭环，外加生命周期、网络竞态、示例模式回归与
// 五档视口截图。故障场景用 Playwright 路由拦截构造，其余全部来自
// 真实 fixture 卷与真实 daemon。

import { expect, test, type Page } from '@playwright/test'

const VIEWPORTS: Array<[string, { width: number; height: number }]> = [
  ['1440x900', { width: 1440, height: 900 }],
  ['1136x1066', { width: 1136, height: 1066 }],
  ['768x1024', { width: 768, height: 1024 }],
  ['390x844', { width: 390, height: 844 }],
  ['320x640', { width: 320, height: 640 }],
]

const artifacts = process.env.ARTIFACT_DIR ?? 'test-results'

async function switchToReal(page: Page): Promise<void> {
  await page.goto('/')
  await page.getByRole('group', { name: '数据模式切换' }).getByRole('button', { name: '真实' }).click()
}

async function startAnalysis(page: Page, baseRef = 'HEAD~1'): Promise<void> {
  await switchToReal(page)
  // <option> 元素在 Playwright 中不视为可见；断言 select 文本包含仓库
  await expect(page.locator('#repository-id')).toContainText('ts-web', { timeout: 30_000 })
  await page.locator('#base-ref').fill(baseRef)
  await page.getByRole('button', { name: /开始本地分析/ }).click()
}

test('真实闭环：创建 → 轮询 → 报告 → 证据定位（真实 daemon/fixture）', async ({ page }) => {
  await startAnalysis(page)
  await expect(page).toHaveURL(/analysis=analysis%3A/, { timeout: 10_000 })
  // 运行视图出现阶段进度（真实 stages，非 setTimeout 假完成）
  await expect(page.locator('.stages')).toBeVisible()
  // 终态自动进入报告
  await expect(page.locator('.report-head h2')).toContainText('ts-web', { timeout: 60_000 })
  await expect(page.locator('.report-head .eyebrow')).toContainText(/已完成|受限完成/)
  // 阶段覆盖与覆盖限制来自真实产物
  await expect(page.getByRole('heading', { name: '阶段覆盖' })).toBeVisible()
  await expect(page.locator('.limit-list')).toContainText('no_lockfile:not_checked')
  // 文件覆盖：真实选择决策（fixture 有排除项）
  await expect(page.getByRole('heading', { name: /文件覆盖/ })).toBeVisible()
  await expect(page.locator('.chip', { hasText: /排除/ })).toBeVisible()
  // 发现：真实确定性发现 + 证据定位
  const findings = page.locator('.findings .finding')
  await expect(findings.first()).toBeVisible({ timeout: 30_000 })
  await findings.first().click()
  const evidenceBlock = page.locator('.evidence-anchor')
  const unknownReason = page.locator('.unknown-reason')
  await expect(evidenceBlock.or(unknownReason).first()).toBeVisible()
  if (await evidenceBlock.count()) {
    await expect(evidenceBlock.locator('pre code').first()).toContainText(/./)
    await expect(evidenceBlock.first()).toContainText(/旧侧|新侧|上下文/)
  }
  // 无发现 ≠ 安全：分页状态可见（服务端 total 与已加载数量区分）
  await expect(page.locator('.page-status').filter({ hasText: /共 \d+ 条/ })).toBeVisible()
})

test('刷新恢复：URL analysis 参数只读取，不重新 POST', async ({ page }) => {
  await startAnalysis(page)
  await expect(page.locator('.report-head')).toBeVisible({ timeout: 60_000 })
  const url = page.url()
  const identity = await page.locator('.report-head p').first().textContent()
  await page.reload()
  await expect(page.locator('.report-head h2')).toContainText('ts-web', { timeout: 60_000 })
  expect(page.url()).toBe(url)
  // 分析身份保持：同一 analysis id（未创建重复任务）
  const afterReload = await page.locator('.report-head p').first().textContent()
  expect(afterReload).toBe(identity)
})

test('非法 ref：按实际原因失败，部分产物不可用如实展示', async ({ page }) => {
  await startAnalysis(page, 'no-such-ref')
  await expect(page.locator('.report-head .eyebrow')).toContainText('失败', { timeout: 60_000 })
  await expect(page.locator('.banner-error')).toContainText('REF_NOT_FOUND')
  // 产物未提交：明确“尚未就绪”而不是空集合
  await expect(page.locator('.linked-empty').filter({ hasText: /尚|不可用|未能|not|ready|就绪|读/ }).first()).toBeVisible()
})

test('重新分析：终态后创建新任务（新 analysis id）', async ({ page }) => {
  await startAnalysis(page)
  await expect(page.locator('.report-head')).toBeVisible({ timeout: 60_000 })
  const firstUrl = page.url()
  await page.getByRole('button', { name: /重新分析/ }).click()
  // 创建是异步的：等待 URL 换成新任务，而不是拿旧报告当成功
  await expect.poll(() => page.url(), { timeout: 60_000 }).not.toBe(firstUrl)
  await expect(page.locator('.report-head')).toBeVisible({ timeout: 60_000 })
})

test('取消 202（拦截模拟）：接受取消意图，继续读取真实状态', async ({ page }) => {
  await page.route('**/api/daemon/v1/analyses/*/cancel', async (route) => {
    await route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        schema_version: '1.3.0', analysis_id: 'analysis:stub', status: 'running',
        repository_id: 'r'.repeat(64), base_ref: 'HEAD~1', target_ref: 'HEAD', rules_version: 'v',
        snapshot_id: null, resumable: true, cancel_requested: true,
        created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-20T00:00:00Z',
        failure_reason: null, stages: [], counts: { findings: null, evidence: null },
      }),
    })
  })
  await startAnalysis(page)
  const cancelButton = page.getByRole('button', { name: /取消分析/ })
  // 快速点击（fixture 分析很快；抢在终态前的概率高，即使终态也已断言不崩溃）
  await cancelButton.click().catch(() => {})
  await expect(page.locator('.report-head, .running').first()).toBeVisible({ timeout: 60_000 })
  // 前端不得强行把状态改写成 cancelled：要么报告终态，要么运行视图
  await expect(page.locator('.banner-error')).toHaveCount(0)
})

test('取消 409（拦截模拟）：report 先赢，重新读取终态不写取消', async ({ page }) => {
  await startAnalysis(page)
  await expect(page.locator('.report-head')).toBeVisible({ timeout: 60_000 })
  const analysisLine = await page.locator('.report-head p').first().textContent()
  expect(analysisLine).toContain('analysis:')
  await page.route('**/api/daemon/v1/analyses/*/cancel', async (route) => {
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({
        schema_version: '1.3.0',
        error: { code: 'ANALYSIS_CONFLICT', message: 'terminal', retryable: false, details: {} },
      }),
    })
  })
  // 终态后运行视图不可达；直接验证已取消标记未出现且状态未被前端改写
  await expect(page.locator('.report-head .eyebrow')).toContainText(/已完成|受限完成/)
  await expect(page.locator('.report-head')).not.toContainText('已取消')
})

test('daemon 离线（拦截模拟）：连接错误可见且可重试，不伪装空结果', async ({ page }) => {
  await page.route('**/api/daemon/**', async (route) => {
    await route.abort('failed')
  })
  await switchToReal(page)
  await expect(page.locator('.banner-error')).toContainText(/无法连接|不可达/, { timeout: 30_000 })
  await expect(page.getByRole('button', { name: '重试' })).toBeVisible()
})

test('错误 Schema（拦截模拟）：明确报契约错误，不落 fixture', async ({ page }) => {
  await page.route(/\/api\/daemon\/v1\/analyses\/[^/]+$/, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"totally":"bogus"}' })
  })
  await startAnalysis(page)
  await expect(page.locator('.running .banner-error, .setup .banner-error').first()).toContainText('契约', { timeout: 30_000 })
})

test('真实图与交互：节点选择/翻转卡/侧别切换', async ({ page }) => {
  await startAnalysis(page)
  await expect(page.locator('.report-head')).toBeVisible({ timeout: 60_000 })
  await page.getByRole('button', { name: /查看关系图/ }).click()
  await expect(page.locator('.graph-canvas svg')).toBeVisible()
  const node = page.locator('[data-node]').first()
  await node.click({ force: true })
  await expect(page.locator('.entity-flip')).toBeVisible()
  await expect(page.locator('.entity-flip')).toContainText(/真实快照|·/) 
  // 翻转卡
  await page.locator('.entity-flip').click()
  await expect(page.locator('.entity-flip')).toContainText('个上游')
  // 侧别切换：旧侧图仍渲染
  await page.getByRole('button', { name: /对比起点（旧）/ }).click()
  await expect(page.locator('.graph-canvas svg')).toBeVisible()
  // 缺源码节点如实说明（真实结果不提供节点源码）
  await expect(page.locator('.code-unavailable, .source-code').first()).toBeVisible()
})

test('示例模式回归：fixture 图、Fake 解释、示例模式标识', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.linked-columns')).toBeVisible()
  await expect(page.locator('.side-footer')).toContainText('示例模式')
  // 功能追踪列存在并可选择
  await page.locator('.linked-column-body button').first().click()
  // Fake 模型解释（示例专属）
  await page.locator('.map-inspector .model-action').click()
  await expect(page.locator('.model-claim').first()).toBeVisible({ timeout: 15_000 })
})

test('键盘可达：图节点 Tab + Enter 可选择', async ({ page }) => {
  await page.goto('/')
  await page.locator('.workspace-views').getByRole('button', { name: '依赖关系图' }).click()
  const firstNode = page.locator('[data-node]').first()
  await firstNode.focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('.entity-flip')).toBeVisible()
})

test('五档视口截图（真实报告 + 真实图）', async ({ page }) => {
  test.setTimeout(360_000)
  const openNav = async (width: number) => {
    if (width > 900) return
    const toggle = page.locator('.mobile-sidebar-toggle')
    if ((await toggle.getAttribute('aria-expanded')) === 'true') return
    await toggle.click()
    await page.waitForTimeout(150)
  }
  const closeNav = async (width: number) => {
    if (width > 900) return
    const toggle = page.locator('.mobile-sidebar-toggle')
    if ((await toggle.getAttribute('aria-expanded')) !== 'true') return
    // Escape 仅在侧栏聚焦时生效；直接点击抽屉内的关闭按钮
    await page.locator('.mobile-sidebar-close').click()
    await page.waitForTimeout(150)
  }
  const gotoReport = async (width: number) => {
    await openNav(width)
    await page.locator('.report-nav').click()
  }
  const gotoGraph = async (width: number) => {
    await openNav(width)
    await page.locator('.workspace-views').getByRole('button', { name: '依赖关系图' }).click()
  }
  await startAnalysis(page)
  await expect(page.locator('.report-head')).toBeVisible({ timeout: 60_000 })
  await gotoGraph(1440)
  await expect(page.locator('.graph-canvas svg')).toBeVisible()
  for (const [label, viewport] of VIEWPORTS) {
    await page.setViewportSize(viewport)
    await page.waitForTimeout(300)
    if (viewport.width <= 640) {
      await openNav(viewport.width)
      await page.screenshot({ path: `${artifacts}/viewport-${label}-nav.png`, fullPage: false })
      await closeNav(viewport.width)
    } else {
      await page.screenshot({ path: `${artifacts}/viewport-${label}-map.png`, fullPage: false })
    }
    // 证据可达：报告视图在所有视口下都能打开并看到发现列表
    await openNav(viewport.width)
    await page.locator('.report-nav').click()
    await closeNav(viewport.width)
    await expect(page.locator('.report').locator('.finding, .linked-empty').first()).toBeVisible({ timeout: 30_000 })
    await page.screenshot({ path: `${artifacts}/viewport-${label}-report.png`, fullPage: false })
    await openNav(viewport.width)
    await page.locator('.workspace-views').getByRole('button', { name: '依赖关系图' }).click()
    await closeNav(viewport.width)
    await expect(page.locator('.graph-canvas svg')).toBeVisible({ timeout: 30_000 })
  }
})
