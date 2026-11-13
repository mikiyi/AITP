import { defineConfig } from '@playwright/test'

/**
 * AITP 端到端测试配置。
 * 隔离工装：独立端口后端(8762)+临时库+mock 模型(18790/18791)+vite(5199)，
 * 绝不触碰正式 8760 与 data/aitp.db。global-setup 负责拉起与播种，teardown 负责清理。
 * 串行执行（workers=1）：共享同一个隔离后端，避免并发写库互相干扰。
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['json', { outputFile: 'e2e/results.json' }]],
  use: {
    baseURL: 'http://127.0.0.1:5199',
    headless: true,
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
  },
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
})
