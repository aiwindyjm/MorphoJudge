// Batch-06 浏览器回归：真实模式下按需生成本地解释（Fake Provider 全闭环）。
// 覆盖：四态（idle/loading/done/failed）、引用证据点击定位、失败重试、
// 模型解释不覆盖确定性状态、示例模式不受影响、Provider 面板状态。

import { expect, test, type Page } from '@playwright/test'

async function startAnalysis(page: Page): Promise<void> {
  await page.goto('/')
  await page.getByRole('group', { name: '数据模式切换' }).getByRole('button', { name: '真实' }).click()
  await expect(page.locator('#repository-id')).toContainText('ts-web', { timeout: 30_000 })
  await page.getByRole('button', { name: /开始本地分析/ }).click()
  await expect(page.locator('.report-head')).toBeVisible({ timeout: 60_000 })
}

async function selectFindingWithEvidence(page: Page): Promise<void> {
  const findings = page.locator('.findings .finding')
  await expect(findings.first()).toBeVisible({ timeout: 30_000 })
  const count = await findings.count()
  for (let index = 0; index < count; index += 1) {
    await findings.nth(index).click()
    const anchors = page.locator('.evidence-anchor')
    if (await anchors.count()) return
  }
  test.fail(true, 'fixture 必须存在带证据锚点的发现')
}

test('解释闭环：Fake 生成 → 分层显示 → 引用点击定位证据', async ({ page }) => {
  await startAnalysis(page)
  await selectFindingWithEvidence(page)
  const panel = page.locator('.explain-panel')
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('本地模型解释')
  // idle：无证据不可解释的按钮或生成按钮
  await panel.getByRole('button', { name: /生成本地解释/ }).click()
  // done：claims 分层出现
  await expect(panel.locator('.model-claim').first()).toBeVisible({ timeout: 30_000 })
  await expect(panel).toContainText('证据复述')
  await expect(panel).toContainText('模型推断')
  // 引用点击 → 页面滚动定位到对应证据锚点（同页 data-evidence-id 存在）
  const claimRef = panel.locator('.model-claim .text-action').first()
  await expect(claimRef).toBeVisible()
  const evidenceId = (await claimRef.textContent())?.replace('引用 ', '').trim() ?? ''
  expect(evidenceId).toMatch(/^evidence:/)
  await claimRef.click()
  await expect(page.locator(`[data-evidence-id="${evidenceId.replace(/[:]/g, '\\:')}"], [data-evidence-id="${evidenceId}"]`).first()).toBeVisible()
  // 确定性状态未被模型覆盖：报告头部仍为终态而非解释状态
  await expect(page.locator('.report-head .eyebrow')).toContainText(/已完成|受限完成/)
  await expect(panel).toContainText(/上下文 [0-9a-f]{4,}/)
})

test('解释失败重试：拦截 503 → failed 可重试 → 恢复成功', async ({ page }) => {
  await startAnalysis(page)
  await selectFindingWithEvidence(page)
  const panel = page.locator('.explain-panel')
  let failOnce = true
  await page.route(/\/api\/daemon\/v1\/analyses\/[^/]+\/explain$/, async (route) => {
    if (failOnce) {
      failOnce = false
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          schema_version: '1.4.0',
          error: { code: 'EXPLAIN_PROVIDER_UNAVAILABLE', message: '本地 Ollama 不可达', retryable: true, details: {} },
        }),
      })
    } else {
      await route.continue()
    }
  })
  await panel.getByRole('button', { name: /生成本地解释/ }).click()
  await expect(panel.locator('.unknown-reason')).toContainText('不可达', { timeout: 30_000 })
  await expect(panel.getByRole('button', { name: '重试' })).toBeVisible()
  await panel.getByRole('button', { name: '重试' }).click()
  await expect(panel.locator('.model-claim').first()).toBeVisible({ timeout: 30_000 })
})

test('Provider 面板：fake 可用、ollama 未配置如实禁用', async ({ page }) => {
  await startAnalysis(page)
  await selectFindingWithEvidence(page)
  const select = page.locator('.explain-panel select')
  await expect(select).toHaveValue('fake')
  const ollamaOption = select.locator('option', { hasText: 'Ollama' })
  await expect(ollamaOption).toBeDisabled()
})

test('无证据发现：解释按钮禁用并说明原因', async ({ page }) => {
  await startAnalysis(page)
  const findings = page.locator('.findings .finding')
  await expect(findings.first()).toBeVisible({ timeout: 30_000 })
  const count = await findings.count()
  for (let index = 0; index < count; index += 1) {
    await findings.nth(index).click()
    const panel = page.locator('.explain-panel')
    const button = panel.getByRole('button', { name: /无证据关联/ })
    if (await button.count()) {
      await expect(button).toBeDisabled()
      return
    }
  }
  test.skip(true, 'fixture 当前所有选中发现均带证据；无证据路径已由引擎测试覆盖')
})

test('示例模式不受影响：Fake 演示仍走 /api/explanations', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.linked-columns')).toBeVisible()
  await page.locator('.map-inspector .model-action').click()
  await expect(page.locator('.model-claim').first()).toBeVisible({ timeout: 15_000 })
  // 示例解释与真实解释面板不共存
  await expect(page.locator('.explain-panel')).toHaveCount(0)
})

test('错误契约响应被拒绝：拦截返回畸形 payload 显示失败而非崩溃', async ({ page }) => {
  await startAnalysis(page)
  await selectFindingWithEvidence(page)
  await page.route(/\/api\/daemon\/v1\/analyses\/[^/]+\/explain$/, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"bogus":true}' })
  })
  const panel = page.locator('.explain-panel')
  await panel.getByRole('button', { name: /生成本地解释/ }).click()
  await expect(panel.locator('.unknown-reason')).toContainText('契约', { timeout: 30_000 })
})
