/**
 * 四期专项 H：用例脑图视图（只读，纯前端 ECharts tree）。
 * 断言：视图切换 / 画布渲染 / 地址栏视图态可刷新还原 / 空项目空态 / 切回列表。
 */
import { expect, test } from '@playwright/test'
import { api, assertHealthy, data, open } from './helpers'

test('切换脑图 → 渲染画布 → 刷新还原视图态 → 切回列表', async ({ page }) => {
  const health = await open(page, '/test-cases')
  const listTable = page.locator('.el-table')
  await expect(listTable.first()).toBeVisible()

  await page.locator('.el-radio-button', { hasText: '脑图' }).click()
  const canvas = page.getByTestId('case-mindmap')
  await expect(canvas).toBeVisible()
  await expect(canvas.locator('canvas')).toHaveCount(1, { timeout: 15_000 })
  await expect(page.locator('.mindmap-bar')).toContainText('以功能点树为骨架、用例为叶子')
  expect(page.url()).toContain('view=mindmap')

  await page.reload()
  await expect(page.getByTestId('case-mindmap')).toBeVisible()
  await expect(page.getByTestId('case-mindmap').locator('canvas')).toHaveCount(1)

  await page.locator('.el-radio-button', { hasText: '列表' }).click()
  await expect(page.getByTestId('case-mindmap')).toHaveCount(0)
  await expect(page.locator('.el-table').first()).toBeVisible()
  expect(page.url()).not.toContain('view=mindmap')
  assertHealthy(health)
})

test('空项目脑图给出空态且不报错', async ({ page }) => {
  const code = `E2E-MIND-${Date.now()}`
  const created = data(await api('POST', '/projects', { code, name: 'E2E 空脑图项目' }))
  expect(created?.id, '创建空项目失败').toBeGreaterThan(0)

  const health = await open(page, '/test-cases?view=mindmap')
  await page.locator('.project-switcher__select').click()
  await page.locator('.el-select-dropdown__item', { hasText: 'E2E 空脑图项目' }).click()

  await expect(page.locator('.mindmap-empty')).toBeVisible()
  await expect(page.locator('.mindmap-empty')).toContainText('没有可展示的功能点或用例')
  assertHealthy(health)
})
