/** 批量发起测试轮次 + 数据池引用拦截：界面动作必须落到真实业务记录。 */
import { test, expect, type Page } from '@playwright/test'
import { api, data, getSeed, attachHealth, assertHealthy } from './helpers'

const btn = (page: Page, name: string | RegExp) => page.getByRole('button', { name }).first()
const dialog = (page: Page) => page.locator('.el-dialog:visible').last()

test('用例批量勾选发起轮次：取消不落库，确认后快照条数一致', async ({ page }) => {
  const seed = await getSeed()
  const h = attachHealth(page)
  await page.goto('/test-cases')
  const rows = page.locator('.base-pro-table__body .el-table__row')
  await rows.first().waitFor({ state: 'visible' })
  await rows.nth(0).locator('.el-checkbox').click()
  await rows.nth(1).locator('.el-checkbox').click()
  await expect(btn(page, '发起测试轮次')).toBeVisible()

  // 非快乐路径：打开后取消，不应产生轮次
  await btn(page, '发起测试轮次').click()
  const dlg = dialog(page)
  await expect(dlg).toContainText('已选 2 条用例')
  await btn(dlg, '取消').click()
  await expect(dlg).toBeHidden()

  const name = `E2E 批量轮次 ${Date.now()}`
  await btn(page, '发起测试轮次').click()
  await dialog(page).locator('.el-form-item', { hasText: '轮次名称' }).locator('input').first().fill(name)
  await btn(dialog(page), '发起轮次').click()
  await expect(page).toHaveURL(/\/test-runs\/\d+/, { timeout: 15_000 })

  const runs = data(await api('GET', `/test-runs?projectId=${seed.projectId}&keyword=${encodeURIComponent(name)}&pageSize=5`))
  expect(runs.list.length, '同名轮次只应有一条').toBe(1)
  expect(runs.list[0].total, '轮次快照应包含勾选的两条用例').toBe(2)
  assertHealthy(h)
})

test('被 pool 引用的数据池无法删除，错误提示带引用方', async ({ page }) => {
  const seed = await getSeed()
  const name = `E2E引用池${Date.now()}`
  data(await api('POST', '/data-pools', { projectId: seed.projectId, name, content: '[{"u":"a"}]' }))
  data(await api('POST', '/test-cases', {
    projectId: seed.projectId,
    title: `E2E 引用数据池 ${name}`,
    caseType: 'functional',
    extra: { 测试数据: `pool:${name}` },
  }))
  const h = attachHealth(page)
  await page.goto('/lab/test_data_generation')
  await page.locator('.cap-mode').getByText('数据池', { exact: true }).click()
  const row = page.locator('.el-table__row', { hasText: `pool:${name}` }).first()
  await row.waitFor({ state: 'visible' })

  await row.getByRole('button', { name: '删除' }).click()
  await btn(page.locator('.el-message-box'), /确定|确认/).click()
  await expect(page.locator('.el-message')).toContainText('仍被')
  await expect(row, '删除被拦截后数据池仍然存在').toBeVisible()
  assertHealthy(h)
})