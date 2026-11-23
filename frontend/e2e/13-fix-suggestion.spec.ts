/**
 * 四期专项 A：AI 修复闭环（接口断言失败 → 生成建议 → 待审 → 采纳更新用例）。
 * 全链路走隔离后端 8762 + 本地 mock 模型，不烧云端额度、不碰正式库。
 * 目标接口用隔离后端自身的 /api/v1/health（同源可直接被 interface_runner 请求）。
 */
import { expect, test } from '@playwright/test'
import { API, api, data, getSeed, open, assertHealthy } from './helpers'

const BACKEND_ORIGIN = API.replace(/\/api\/v1$/, '')

async function failedExecution(projectId: number): Promise<{ caseId: number; executionId: number }> {
  const env = data(await api('POST', '/test-envs', {
    name: `E2E 修复环境 ${Date.now()}`,
    kind: 'test',
    app: { baseUrl: BACKEND_ORIGIN },
  }))
  const title = `E2E 修复建议用例 ${Date.now()}`
  const created = data(await api('POST', '/test-cases', {
    projectId,
    title,
    caseType: 'interface',
    priority: 'P1',
    extra: {
      接口URL: '/api/v1/health',
      请求方式: 'GET',
      预期状态码: '200',
      断言规则: 'json:$.appVersion = 9.9.9',
    },
  }))
  const run = data(await api('POST', `/test-cases/${created.id}/interface-run`, { envId: env.id }))
  expect(run.passed, '用例断言必须失败才能生成修复建议').toBe(false)
  expect(run.executionId, '执行记录应带回 executionId').toBeGreaterThan(0)
  return { caseId: created.id, executionId: run.executionId }
}

test('断言失败 → 生成建议 → 列表可见 → 采纳并更新用例', async ({ page }) => {
  const seed = await getSeed()
  const { caseId, executionId } = await failedExecution(seed.projectId)

  const generated = await api('POST', '/fix-suggestions', {
    executionId,
    projectId: seed.projectId,
  })
  expect(generated.code, `生成建议失败：${generated.message}`).toBe(200000)
  expect(generated.data.generated, 'mock 模型应返回结构化建议').toBe(true)
  const suggestion = generated.data.suggestion
  expect(suggestion.suggestionNo, '建议编号应为 AI_FIX- 前缀').toMatch(/^AI_FIX-\d{3,}$/)
  expect(suggestion.status).toBe('pending')
  expect(suggestion.diffPreview, '应有字段级 diff 预览').toContain('json:$.code')

  const health = await open(page, '/fix-suggestions')
  const table = page.locator('.fix-suggestion-table')
  await expect(table).toBeVisible()
  await expect(table).toContainText(suggestion.suggestionNo)

  await table.getByText(suggestion.suggestionNo).click()
  const drawer = page.locator('.el-drawer', { hasText: '修复建议详情' })
  await expect(drawer).toBeVisible()
  await expect(drawer).toContainText('变更预览')

  await drawer.getByRole('button', { name: '采纳并更新用例' }).click()
  const confirmBox = page.locator('.el-message-box')
  await expect(confirmBox).toBeVisible()
  await confirmBox.getByRole('button', { name: '确定' }).click()
  await expect(page.locator('.el-message', { hasText: '已采纳' })).toBeVisible({ timeout: 10_000 })

  const detail = data(await api('GET', `/test-cases/${caseId}?projectId=${seed.projectId}`))
  expect(detail.extra['断言规则'], '断言规则应已按建议更新').toContain('json:$.code = 1001')

  const listed = data(await api('GET', `/fix-suggestions?projectId=${seed.projectId}`))
  const row = listed.list.find((item: any) => item.id === suggestion.id)
  expect(row.status, '采纳后状态应为 applied').toBe('applied')
  assertHealthy(health)
})

test('忽略建议后进入终态且不可再采纳', async ({ page }) => {
  const seed = await getSeed()
  const { executionId } = await failedExecution(seed.projectId)
  const generated = await api('POST', '/fix-suggestions', { executionId, projectId: seed.projectId })
  expect(generated.code).toBe(200000)
  const suggestion = generated.data.suggestion

  const health = await open(page, '/fix-suggestions')
  const table = page.locator('.fix-suggestion-table')
  await table.getByText(suggestion.suggestionNo).click()
  const drawer = page.locator('.el-drawer', { hasText: '修复建议详情' })
  await expect(drawer).toBeVisible()
  await drawer.getByRole('button', { name: '忽略' }).click()
  await expect(page.locator('.el-message', { hasText: '已忽略' })).toBeVisible({ timeout: 10_000 })

  const again = await api('POST', `/fix-suggestions/${suggestion.id}/adopt`, { projectId: seed.projectId })
  expect(again.code, '已忽略的建议不可再采纳').not.toBe(200000)
  assertHealthy(health)
})

test('列表页展示状态过滤与口径说明', async ({ page }) => {
  const health = await open(page, '/fix-suggestions')
  await expect(page.locator('.fix-suggestion-table')).toBeVisible()
  await expect(page.locator('.el-radio-button', { hasText: '全部' })).toBeVisible()
  await expect(page.locator('.el-radio-button', { hasText: '待审' })).toBeVisible()
  await expect(page.locator('.hint')).toContainText('AI 不会直接改库')
  await expect(page.locator('.filter-row')).toContainText('采纳前请核对 diff')
  assertHealthy(health)
})
