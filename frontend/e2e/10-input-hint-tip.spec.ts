/** 占位提示浮显 + 多行文本框可拉大：全站统一行为的真实界面断言。 */
import { test, expect } from '@playwright/test'
import { getSeed, attachHealth, assertHealthy } from './helpers'

test('表单 textarea 全站可纵向拉大（智能入口与实验室造数页）', async ({ page }) => {
  await getSeed()
  const h = attachHealth(page)
  await page.goto('/lab')
  const smartBox = page.locator('.smart-entry textarea').first()
  await smartBox.waitFor({ state: 'visible' })
  expect(await smartBox.evaluate((el) => getComputedStyle(el).resize), '智能入口描述框要可纵向拉大').toBe('vertical')

  await page.goto('/lab/test_data_generation')
  const needBox = page.locator('textarea').first()
  await needBox.waitFor({ state: 'visible' })
  expect(await needBox.evaluate((el) => getComputedStyle(el).resize), '造数需求框要可纵向拉大').toBe('vertical')
  assertHealthy(h)
})

test('占位提示被截断的文本框悬停出浮显，短提示不打扰', async ({ page }) => {
  await getSeed()
  const h = attachHealth(page)
  await page.goto('/automation/scenario')
  await page.getByRole('button', { name: '新建场景' }).click()
  const dialog = page.locator('.el-dialog:visible').last()
  await dialog.waitFor()

  // 测的是「截断→浮显」机制而非某处布局：用测试样式把 URL 框压窄，保证占位必被截断，
  // 布局演进（步骤两行化把 URL 框加宽）不会把这个用例晃挂
  await page.addStyleTag({ content: '.scenario-step__target .el-input { width: 120px !important; flex: none !important; }' })
  const urlBox = dialog.getByPlaceholder('/path 或 {{baseUrl}}/path，可用 {{var}}')
  const fullPlaceholder = (await urlBox.getAttribute('placeholder'))!.trim()
  await urlBox.hover()
  const tip = page.locator('.input-hint-tip:visible')
  await expect(tip, '长占位被截断时悬停要出浮显').toBeVisible()
  await expect(tip).toHaveText(fullPlaceholder)

  // 步骤名输入框（3 字短占位不截断）：悬停后浮显必须消失
  await dialog.getByPlaceholder('步骤名').hover()
  await expect(page.locator('.input-hint-tip:visible'), '短占位不截断不出浮显').toHaveCount(0)

  await dialog.getByRole('button', { name: '取消' }).click()
  assertHealthy(h)
})

test('场景步骤编辑器：步骤头单行不溢出，大文本框有外置字段标签', async ({ page }) => {
  await getSeed()
  const h = attachHealth(page)
  await page.goto('/automation/scenario')
  await page.getByRole('button', { name: '新建场景' }).click()
  const dialog = page.locator('.el-dialog:visible').last()
  await dialog.waitFor()

  // 第一行：序号/步骤名/类型/操作按钮必须同一行（垂直中线一致），不再挤压换行
  const head = dialog.locator('.scenario-step__head').first()
  const centers = await head.evaluate((el) =>
    [...el.children].map((c) => { const r = c.getBoundingClientRect(); return Math.round(r.top + r.height / 2) }))
  expect(new Set(centers).size, '步骤头所有控件同一行').toBe(1)
  await expect(head.getByRole('button', { name: '删除' })).toBeVisible()

  // 接口步骤：第二行是方法+URL，请求体/断言规则带外置标签
  const target = dialog.locator('.scenario-step__target').first()
  await expect(target.locator('.el-select')).toBeVisible()
  await expect(dialog.locator('.scenario-step__label').nth(0)).toHaveText('请求体')
  await expect(dialog.locator('.scenario-step__label').nth(1)).toHaveText('断言规则')

  // 切数据库步骤：第二行变脚本引用下拉，标签变 查询语句/断言规则
  await head.locator('.el-select').click()
  await page.locator('.el-select-dropdown__item:visible', { hasText: '数据库' }).first().click()
  await expect(target.locator('.el-select')).toBeVisible()
  await expect(dialog.locator('.scenario-step__label').nth(0)).toHaveText('查询语句')
  await expect(dialog.locator('.scenario-step__label').nth(1)).toHaveText('断言规则')

  await dialog.getByRole('button', { name: '取消' }).click()
  assertHealthy(h)
})
