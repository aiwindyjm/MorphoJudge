import { defineConfig } from '@playwright/test'

// Batch-05 浏览器回归配置。默认指向 Compose 内网的 web 服务；
// 宿主观察走回环发布端口（如 MORPHOJUDGE_PORT=3025）。
// 截图与 trace 写入 ARTIFACT_DIR（e2e 容器内 /artifacts 卷），
// 之后由宿主 `docker cp` 提取，不挂 PRIVATE。
export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: process.env.ARTIFACT_DIR ? `${process.env.ARTIFACT_DIR}/test-results` : './test-results',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://web:3000',
    trace: 'retain-on-failure',
    screenshot: 'off',
  },
})
