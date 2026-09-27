// Batch-07 浏览器回归：报告/复核/导出完整用户闭环（TEST-002）。
// 覆盖：导出 Markdown（六段标题）、导出 JSON（schema_version）、复核四状态
// +持久化+刷新保留、confirmed 措辞、既有回归无破坏。

import { expect, test, type Page } from '@playwright/test'

const artifacts = process.env.ARTIFACT_DIR ?? 'test-results'

async function startAnalysis(page: Page): Promise<void> {
  await page.goto('/')
  await page.getByRole('group', { name: '数据模式切换' }).getByRole('button', { name: '真实' }).click()
  await expect(page.locator('#repository-id')).toContainText('ts-web', { timeout: 30_000 })
  await page.getByRole('button', { name: /开始本地分析/ }).click()
}

async function waitForReport(page: Page): Promise<void> {
  await expect(page).toHaveURL(/analysis=analysis%3A/, { timeout: 10_000 })
  await expect(page.locator('.report-head h2')).toContainText('ts-web', { timeout: 60_000 })
}

test('完整闭环：创建→报告→发现→证据→解释→复核→导出→刷新→复核持久化', async ({ page }) => {
  await startAnalysis(page)
  await waitForReport(page)

  // 选择一条有证据的发现
  const findings = page.locator('.findings .finding')
  await expect(findings.first()).toBeVisible({ timeout: 30_000 })
  await findings.first().click()

  // 证据可见
  const evidenceOrUnknown = page.locator('.evidence-anchor, .unknown-reason').first()
  await expect(evidenceOrUnknown).toBeVisible({ timeout: 15_000 })

  // 生成本地解释（Fake Provider）
  const explainButton = page.getByRole('button', { name: /生成本地解释/ })
  if (await explainButton.isVisible()) {
    await explainButton.click()
    await expect(page.locator('.model-claim').first()).toBeVisible({ timeout: 30_000 })
  }

  // 复核：切换状态 → 已确认
  const confirmedButton = page.locator('.review button', { hasText: '已确认' })
  await expect(confirmedButton).toBeVisible()
  await confirmedButton.click()
  await expect(confirmedButton).toHaveClass(/review-active/, { timeout: 10_000 })

  // confirmed 措辞检查（PRD §3.4）
  await expect(page.locator('.review .form-hint')).toContainText('不代表软件整体安全')

  // 刷新 → 复核状态保留
  await page.reload()
  await waitForReport(page)
  await findings.first().click()
  await expect(page.locator('.evidence-anchor, .unknown-reason').first()).toBeVisible({ timeout: 15_000 })
  // 复核状态已恢复
  await expect(page.locator('.review button.review-active')).toHaveText(/已确认/, { timeout: 10_000 })

  // 截图
  await page.screenshot({ path: `${artifacts}/batch07-review-confirmed.png` })
})

test('导出 Markdown：六段标题结构', async ({ page, request }) => {
  await startAnalysis(page)
  await waitForReport(page)

  const analysisUrl = page.url()
  const analysisId = decodeURIComponent(analysisUrl.split('analysis=')[1]?.split('&')[0] ?? '')
  expect(analysisId).toMatch(/^analysis:/)

  const response = await request.get(`/api/daemon/v1/analyses/${encodeURIComponent(analysisId)}/report?format=markdown`)
  expect(response.status()).toBe(200)
  const text = await response.text()

  // PRD §3.3 六段结构
  for (const section of ['## 1. 变更摘要', '## 2. 高影响关注项', '## 3. 证据', '## 4. 模型解释', '## 5. 人工复核', '## 6. 覆盖限制']) {
    expect(text).toContain(section)
  }

  // 无误导措辞（PRD §7 术语）
  const misleading = ['安全证明', '无风险', '已执行', '漏洞']
  for (const word of misleading) {
    expect(text).not.toContain(word)
  }

  // 有 fenced code block（源码片段）
  expect(text).toContain('```')
})

test('导出 JSON：schema_version 存在且可解析', async ({ page, request }) => {
  await startAnalysis(page)
  await waitForReport(page)

  const analysisUrl = page.url()
  const analysisId = decodeURIComponent(analysisUrl.split('analysis=')[1]?.split('&')[0] ?? '')

  const response = await request.get(`/api/daemon/v1/analyses/${encodeURIComponent(analysisId)}/report?format=json`)
  expect(response.status()).toBe(200)
  const report = await response.json()

  expect(report.schema_version).toBeTruthy()
  expect(report.analysis_id).toBe(analysisId)
  expect(report.identity).toBeTruthy()
  expect(report.identity.snapshot_id).toBeTruthy()
  expect(Array.isArray(report.findings)).toBe(true)
  expect(Array.isArray(report.stage_coverage)).toBe(true)
  expect(Array.isArray(report.limits)).toBe(true)
  expect(report.counts).toBeTruthy()
})

test('复核持久化：切换到需调查→刷新→状态保留', async ({ page }) => {
  await startAnalysis(page)
  await waitForReport(page)

  const findings = page.locator('.findings .finding')
  await findings.first().click()
  await expect(page.locator('.evidence-anchor, .unknown-reason').first()).toBeVisible({ timeout: 15_000 })

  // 切换到需调查
  const needsButton = page.locator('.review button', { hasText: '需调查' })
  await needsButton.click()
  await expect(needsButton).toHaveClass(/review-active/, { timeout: 10_000 })

  // 刷新
  await page.reload()
  await waitForReport(page)
  await findings.first().click()
  await expect(page.locator('.evidence-anchor, .unknown-reason').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.review button.review-active')).toHaveText(/需调查/, { timeout: 10_000 })
})

test('导出按钮可见且可点击', async ({ page }) => {
  await startAnalysis(page)
  await waitForReport(page)

  const mdButton = page.getByRole('button', { name: /导出 Markdown/ })
  const jsonButton = page.getByRole('button', { name: /导出 JSON/ })
  await expect(mdButton).toBeVisible()
  await expect(jsonButton).toBeVisible()
  await expect(mdButton).toBeEnabled()
  await expect(jsonButton).toBeEnabled()
})
