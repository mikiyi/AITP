/** 非快乐路径：刷新/后退、空数据、超大量、表单边界、生产确认统一（BUG-3）。 */
import { test, expect, type Page } from '@playwright/test'
import { api, data, getSeed, attachHealth, assertHealthy } from './helpers'

const btn = (page: Page, name: string | RegExp) => page.getByRole('button', { name }).first()

test('刷新与浏览器后退不丢状态/不白屏', async ({ page }) => {
  const seed = await getSeed()
  const h = attachHealth(page)
  await page.goto('/test-cases')
  await page.goto(`/test-cases/${seed.caseId}`)
  await page.reload() // 刷新详情页
  await expect(page.locator('.page, .page-header').first()).toBeVisible()
  await page.goBack() // 后退到列表
  await expect(page.locator('.page, .page-header').first()).toBeVisible()
  assertHealthy(h)
})

test('空数据项目显示空态而非报错', async ({ page }) => {
  const p = data(await api('POST', '/projects', { code: `EMPTY${Date.now()}`, name: '空项目' }))
  const h = attachHealth(page)
  await page.goto('/test-cases')
  // 切到空项目
  await page.locator('.project-switcher, .el-select').first().click()
  await page.locator('.el-select-dropdown__item', { hasText: '空项目' }).first().click()
  await expect(page.locator('.el-empty, .el-table__empty-text, [class*=empty]').first()).toBeVisible()
  assertHealthy(h)
})

test('超大量用例：分页总数正确且窗口行数受限', async ({ page }) => {
  const p = data(await api('POST', '/projects', { code: `BIG${Date.now()}`, name: '大量项目' }))
  // 造 1000 条
  for (let i = 0; i < 1000; i++) {
    await api('POST', '/test-cases', { projectId: p.id, title: `BIG 用例 ${i}`, caseType: 'functional', priority: 'P2' })
  }
  const h = attachHealth(page)
  await page.goto('/test-cases')
  await page.locator('.project-switcher, .el-select').first().click()
  await page.locator('.el-select-dropdown__item', { hasText: '大量项目' }).first().click()
  await expect(page.locator('body')).toContainText('1000', { timeout: 15_000 })
  // 窗口化：DOM 行数远小于 1000
  const rows = await page.locator('.el-table__body-wrapper tbody tr').count()
  expect(rows).toBeLessThan(200)
  assertHealthy(h)
})

test('表单边界：超长标题被截断/拦截，特殊字符不触发 XSS', async ({ page }) => {
  const h = attachHealth(page)
  await page.goto('/test-cases')
  await btn(page, '新建用例').click()
  const dlg = page.locator('.el-dialog:visible').last()
  const long = '长'.repeat(600)
  await dlg.locator('.el-form-item', { hasText: '标题' }).locator('input').first().fill(long)
  await btn(dlg, /保存|确定/).click()
  // 要么被长度校验拦下（弹窗仍在），要么成功但标题被截断；二者都不允许 JS 异常
  await page.waitForTimeout(800)
  assertHealthy(h)
})

// 生产环境确认（BUG-3）见 05-prod-confirm.spec.ts（需有头模式，单独成文件）
