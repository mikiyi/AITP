/**
 * V2.6.0：执行可信度与资产效率。
 * 覆盖：用例质量体检、执行前置自检、失败归因/并发执行、发布就绪结论。
 * 全部使用隔离后端 8762 与临时项目，不碰正式库。
 */
import { expect, test } from '@playwright/test'
import { API, api, data, open, assertHealthy } from './helpers'

const BACKEND_ORIGIN = API.replace(/\/api\/v1$/, '')

async function newProject(name: string): Promise<number> {
  const created = data(await api('POST', '/projects', {
    code: `E2E-V26-${Date.now()}`,
    name,
  }))
  expect(created?.id, '创建隔离项目失败').toBeGreaterThan(0)
  return created.id
}

async function switchProject(page: any, name: string): Promise<void> {
  await page.locator('.project-switcher__select').click()
  await page.locator('.el-select-dropdown__item', { hasText: name }).click()
}

test('用例质量体检：重复标题/缺预期被检出，抽屉可见评分与问题', async ({ page }) => {
  const projectName = `E2E 质量体检 ${Date.now()}`
  const projectId = await newProject(projectName)

  for (const [no, expected] of [['IT-001', ''], ['IT-002', '']] as const) {
    await api('POST', '/test-cases', {
      projectId,
      title: '登录-验证码错误',
      caseType: 'functional',
      priority: 'P1',
      expectedResult: expected,
      steps: [{ action: '提交错误验证码', expected: '' }],
      caseNo: no,
    })
  }
  await api('POST', '/test-cases', {
    projectId, title: '下单-缺少步骤', caseType: 'functional', priority: 'P0',
  })

  const quality = data(await api('GET', `/test-cases/quality?projectId=${projectId}`))
  expect(quality.score, '存在高危问题时应扣分').toBeLessThan(100)
  const codes = quality.issues.map((item: any) => item.code)
  expect(codes).toContain('title_duplicate')
  expect(codes).toContain('expected_empty')
  expect(codes).toContain('steps_empty')

  const health = await open(page, '/test-cases')
  await switchProject(page, projectName)
  await page.getByRole('button', { name: '质量体检' }).click()
  const drawer = page.locator('.case-quality-drawer')
  await expect(drawer).toBeVisible()
  await expect(drawer).toContainText('用例质量')
  await expect(drawer).toContainText('标题重复')
  await expect(drawer).toContainText('缺少预期结果')
  assertHealthy(health)
})

test('执行前置自检 + 并发执行归因：环境类失败不误判为断言错误', async ({ page }) => {
  const projectName = `E2E 执行可信度 ${Date.now()}`
  const projectId = await newProject(projectName)
  const env = data(await api('POST', '/test-envs', {
    name: `V26 环境 ${Date.now()}`,
    kind: 'test',
    app: { baseUrl: BACKEND_ORIGIN },
  }))

  const normal = data(await api('POST', '/test-cases', {
    projectId,
    title: '健康检查-版本断言',
    caseType: 'interface',
    priority: 'P1',
    extra: {
      接口URL: '/api/v1/health',
      请求方式: 'GET',
      预期状态码: '200',
      断言规则: 'json:$.appVersion = 9.9.9',
    },
  }))
  const unreachable = data(await api('POST', '/test-cases', {
    projectId,
    title: '不可达地址-环境问题',
    caseType: 'interface',
    priority: 'P1',
    extra: {
      接口URL: 'http://127.0.0.1:1/api/health',
      请求方式: 'GET',
      预期状态码: '200',
    },
  }))
  const run = data(await api('POST', '/test-runs', {
    projectId,
    name: `V26 轮次 ${Date.now()}`,
    caseIds: [normal.id, unreachable.id],
    envId: env.id,
  }))

  const precheckRaw = await api(
    'POST', `/test-runs/${run.id}/precheck?projectId=${projectId}`, { envId: env.id },
  )
  const precheck = data(precheckRaw)
  expect(precheck, `自检接口失败：${JSON.stringify(precheckRaw)}`).toBeTruthy()
  expect(precheck.canRun, `自检被阻断：${precheck.blockers?.join('；')}`).toBe(true)
  expect(precheck.items.some((item: any) => item.key === 'definition')).toBe(true)

  const health = await open(page, '/test-cases')
  await switchProject(page, projectName)
  const runHealth = await open(page, `/test-runs/${run.id}`)
  await page.getByRole('button', { name: '执行前自检' }).click()
  const dialog = page.locator('.execution-precheck-dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('用例定义')
  await expect(dialog).toContainText('登录态')
  await page.keyboard.press('Escape')

  const batch = data(await api('POST', `/test-runs/${run.id}/interface-run?projectId=${projectId}`, {
    envId: env.id, sync: true, retry: 1, workers: 2,
  }))
  expect(batch.total).toBe(2)
  expect(batch.details).toHaveLength(2)
  const unreachableDetail = batch.details.find((item: any) => item.caseId === unreachable.id)
  expect(unreachableDetail.attribution.kind, '连接失败应归因为环境问题').toBe('environment')
  expect(
    unreachableDetail.attribution.reason,
    JSON.stringify(unreachableDetail.attribution),
  ).toContain('环境')
  const normalDetail = batch.details.find((item: any) => item.caseId === normal.id)
  expect(
    ['bug', 'assertion'],
    JSON.stringify(normalDetail),
  ).toContain(normalDetail.attribution.kind)
  expect(normalDetail.retryCount, '应记录重试次数').toBeGreaterThanOrEqual(0)
  assertHealthy(health)
  assertHealthy(runHealth)
})

test('发布准入页展示发布就绪结论与风险项', async ({ page }) => {
  const projectName = `E2E 发布就绪 ${Date.now()}`
  const projectId = await newProject(projectName)
  const env = data(await api('POST', '/test-envs', {
    name: `V26 就绪环境 ${Date.now()}`,
    kind: 'test',
    app: { baseUrl: BACKEND_ORIGIN },
  }))
  const item = data(await api('POST', '/test-cases', {
    projectId,
    title: '就绪-失败用例',
    caseType: 'interface',
    priority: 'P0',
    extra: {
      接口URL: '/api/v1/health',
      请求方式: 'GET',
      预期状态码: '200',
      断言规则: 'json:$.appVersion = 9.9.9',
    },
  }))
  const run = data(await api('POST', '/test-runs', {
    projectId, name: `V26 就绪轮次 ${Date.now()}`, caseIds: [item.id], envId: env.id,
  }))
  await api('POST', `/test-runs/${run.id}/interface-run?projectId=${projectId}`, {
    envId: env.id, sync: true, workers: 1,
  })
  await api('POST', `/test-runs/${run.id}/complete?projectId=${projectId}`, {})

  const preview = data(await api('POST', '/release-gates/preview', {
    projectId, runId: run.id,
  }))
  expect(preview.readiness, '发布准入应带发布就绪结论').toBeTruthy()
  expect(['ready', 'risk', 'blocked']).toContain(preview.readiness.verdict)
  expect(preview.readiness.risks.length, '失败用例应形成风险项').toBeGreaterThan(0)

  const health = await open(page, '/traceability')
  await switchProject(page, projectName)
  await page.getByRole('tab', { name: '发布准入' }).click()
  await page.locator('.gate-panel .el-select').click()
  const runOption = page.getByRole('option', { name: run.name })
  await expect(runOption).toBeVisible({ timeout: 15_000 })
  await runOption.click()
  await page.getByRole('button', { name: '预览结论' }).click()
  const card = page.locator('.readiness-card')
  await expect(card).toBeVisible({ timeout: 15_000 })
  await expect(card.locator('.readiness-label')).toContainText(/可发布|可发|不可发/)
  await expect(card).toContainText('风险')
  assertHealthy(health)
})
