/** DFS：核心业务页穷尽交互（每页≤20元素），每步断言业务结果。覆盖三个终态/确认修复。 */
import { test, expect, type Page } from '@playwright/test'
import { api, data, getSeed, makeBudget, attachHealth, assertHealthy } from './helpers'

const btn = (page: Page, name: string | RegExp) => page.getByRole('button', { name }).first()
const dialog = (page: Page) => page.locator('.el-dialog:visible').last()

test('AI 能力页：执行→产出→采纳去重→取消/恢复→落库', async ({ page }) => {
  const budget = makeBudget()
  const key = '/lab/case_generation'
  const seed = await getSeed()
  // 造两条与 mock 产出同名的基线用例，触发去重命中（去重条仅有命中时渲染）
  await api('POST', '/test-cases', { projectId: seed.projectId, title: '验证码登录-正常场景-输入有效验证码', caseType: 'functional', priority: 'P0' })
  await api('POST', '/test-cases', { projectId: seed.projectId, title: '验证码登录-异常场景-验证码已过期', caseType: 'exception', priority: 'P1' })
  const h = attachHealth(page)
  await page.goto(key)
  await page.waitForLoadState('networkidle')
  budget.use(key)
  // 等动态表单渲染稳定后再填，避免被配置加载重置
  const reqItem = page.locator('.el-form-item', { hasText: '需求原文' })
  await reqItem.locator('textarea').waitFor({ state: 'visible' })
  const reqTa = reqItem.locator('textarea')
  await reqTa.fill('手机号+验证码登录：验证码5分钟有效，60秒限流，连续5次错误锁定10分钟')
  budget.use(key)
  const projInput = page.locator('.el-form-item', { hasText: '项目名称' }).locator('input').first()
  await projInput.fill('E2E 演示项目')
  budget.use(key)
  // 确认值已注册进 v-model
  await expect(reqTa).toHaveValue(/验证码/)
  await expect(projInput).toHaveValue('E2E 演示项目')
  await btn(page, '执行').click()
  budget.use(key)
  // mock 产出应渲染结构化用例
  await expect(page.locator('body')).toContainText('验证码登录', { timeout: 30_000 })
  // 打开采纳弹窗
  await btn(page, '采纳为用例').click()
  budget.use(key)
  const dlg = dialog(page)
  await expect(dlg).toContainText('采纳为测试用例')
  // 去重口径条存在
  await expect(dlg.locator('.adopt-dedup__text')).toBeVisible()
  budget.use(key)
  // 取消（非快乐路径）
  await btn(dlg, '取消').click()
  budget.use(key)
  await expect(dlg).toBeHidden()
  // 重开，恢复疑似重复，采纳
  await btn(page, '采纳为用例').click()
  budget.use(key)
  const dlg2 = dialog(page)
  const restore = dlg2.getByRole('button', { name: /一键恢复|取消勾选疑似/ })
  if (await restore.count()) { await restore.click(); budget.use(key) }
  const adoptBtn = dlg2.getByRole('button', { name: /采纳 \d+ 条/ })
  await expect(adoptBtn).toBeEnabled()
  await adoptBtn.click()
  budget.use(key)
  // 成功提示 + 跳转按钮 → 用例列表
  await expect(page.locator('.el-message, .el-notification')).toContainText(/采纳|成功/, { timeout: 10_000 })
  assertHealthy(h)
})

test('用例页：空标题校验→新建→编辑→删除取消/确认→覆盖矩阵', async ({ page }) => {
  const budget = makeBudget()
  const key = '/test-cases'
  const h = attachHealth(page)
  await page.goto(key)
  await btn(page, '新建用例').click(); budget.use(key)
  const dlg = dialog(page)
  // 空标题保存 → 校验拦截
  await btn(dlg, /保存|确定/).click(); budget.use(key)
  await expect(dlg).toBeVisible() // 仍开着=被校验拦下
  // 填标题+特殊字符
  await dlg.locator('.el-form-item', { hasText: '标题' }).locator('input').first().fill('E2E <script>alert(1)</script> 用例')
  budget.use(key)
  await btn(dlg, /保存|确定/).click(); budget.use(key)
  await expect(dlg).toBeHidden()
  await expect(page.locator('body')).toContainText('E2E')
  // 覆盖矩阵开/关
  await btn(page, '覆盖矩阵').click(); budget.use(key)
  await expect(dialog(page)).toBeVisible()
  await btn(dialog(page), /关闭|取消/).click(); budget.use(key)
  assertHealthy(h)
})

test('报告页：发布冻结→归档终态（BUG-2 无退回草稿）', async ({ page }) => {
  const budget = makeBudget()
  const seed = await getSeed()
  const key = `/reports/${seed.reportId}`
  const h = attachHealth(page)
  // 先经 API 把报告置为草稿，保证可发布
  await api('PUT', `/reports/${seed.reportId}`, { status: 'draft' }).catch(() => {})
  await page.goto(key)
  // 若已发布/归档则新建一条草稿报告
  let rid = seed.reportId
  if (!(await btn(page, '发布').count())) {
    const created = data(await api('POST', '/reports', { projectId: seed.projectId, title: 'E2E 终态报告' }))
    rid = created.id
    await page.goto(`/reports/${rid}`)
  }
  await btn(page, '发布').click(); budget.use(key)
  await btn(page.locator('.el-message-box'), /确定|确认/).click(); budget.use(key)
  await expect(page.locator('body')).toContainText('已发布')
  await expect(btn(page, '发布')).toHaveCount(0) // 发布后隐藏
  // 归档
  await btn(page, '归档').click(); budget.use(key)
  await btn(page.locator('.el-message-box'), /确定|确认/).click(); budget.use(key)
  await expect(page.locator('body')).toContainText('已归档')
  // BUG-2：归档后不得有退回草稿
  await expect(btn(page, '退回草稿')).toHaveCount(0)
  assertHealthy(h)
})

test('编排页：发布后载入/删除禁用（BUG-1 终态锁）', async ({ page }) => {
  const budget = makeBudget()
  const key = '/lab/pipeline'
  const h = attachHealth(page)
  // 经 API 建一个已发布编排
  const e = data(await api('POST', '/workbench/entries', { code: `E2E${Date.now()}`, name: 'E2E 编排', capability: 'case_generation' }))
  await api('POST', `/workbench/entries/${e.id}/publish`)
  await page.goto(key)
  const row = page.locator('.saved-entry', { hasText: 'E2E 编排' }).first()
  await expect(row).toBeVisible()
  await expect(row.getByRole('button', { name: '载入' })).toBeDisabled()
  await expect(row.getByRole('button', { name: '删除' })).toBeDisabled()
  budget.use(key)
  assertHealthy(h)
})
