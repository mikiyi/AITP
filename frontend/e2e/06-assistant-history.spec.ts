/**
 * 问答助手历史（用户报的第 3 条）：问过的题目换个菜单再回来还在。
 *
 * 原来对话只活在组件里，`<keep-alive :max="6">` 把它挤掉就全丢；现在每次问答按会话
 * 落库，页面挂载时回放。这里三段断言各管一摊：
 *   走够淘汰半径 → 组件实例确实没了（证明不是 keep-alive 帮我们记着）；
 *   回到助手页 → 历史回来了（store + 后端回放）；
 *   整页刷新 → 还在（真在库里，不在内存里）；
 *   清空对话 → 当前线程空了，但旧会话在「会话记录」里能查到。
 */
import { test, expect, type Page } from '@playwright/test'
import { api, data, getSeed, attachHealth, assertHealthy } from './helpers'

const ASSISTANT = '/knowledge/assistant'
const QUESTION = '支付回调重复推送怎么排查？（E2E 历史）'

const pathOf = (page: Page) => new URL(page.url()).pathname

/** 展开所有分组，让子菜单项可点。 */
async function expandGroups(page: Page) {
  const groups = page.locator('.aitp-menu > li.el-sub-menu')
  const n = await groups.count()
  for (let i = 0; i < n; i++) {
    const group = groups.nth(i)
    const panel = group.locator('> ul.el-menu')
    if (await panel.isVisible().catch(() => false)) continue
    await group.locator('> .el-sub-menu__title').click()
    await panel.waitFor({ state: 'visible', timeout: 3_000 }).catch(() => {})
  }
}

/** 挨个点击侧边菜单，收集走过的不同路由；助手页本身不算。 */
async function hopAroundMenu(page: Page, need: number): Promise<string[]> {
  await expandGroups(page)
  const items = page.locator('.aitp-menu .el-menu-item')
  const seen: string[] = []
  for (let i = 0; i < (await items.count()) && seen.length < need; i++) {
    const item = items.nth(i)
    if (!(await item.isVisible().catch(() => false))) continue
    await item.click()
    await page.waitForTimeout(150)
    const path = pathOf(page)
    if (path !== ASSISTANT && !seen.includes(path)) seen.push(path)
  }
  return seen
}

/** 按菜单文案回到某个页面（SPA 跳转，不重载文档）。 */
async function backViaMenu(page: Page, text: string) {
  await expandGroups(page)
  await page.locator('.aitp-menu .el-menu-item', { hasText: text }).first().click()
  await page.waitForURL((u) => new URL(u).pathname === ASSISTANT)
}

async function askInKbMode(page: Page, question: string) {
  // 知识库直答不调模型：历史断言不该依赖模型档案，也不烧额度
  await page.locator('.assistant__modes .el-radio-button__inner', { hasText: '基于知识库回答' }).click()
  await page.locator('.assistant__input textarea').fill(question)
  await page.getByRole('button', { name: '提问' }).click()
  await expect(page.locator('.assistant__msg.is-user').last()).toContainText(question)
}

test('问答助手：换菜单与刷新都不丢历史，清空后旧会话仍在会话记录', async ({ page }) => {
  test.setTimeout(180_000)
  const h = attachHealth(page)
  const seed = await getSeed()

  await page.goto(ASSISTANT)
  await page.locator('.assistant').first().waitFor({ state: 'visible' })
  await askInKbMode(page, QUESTION)

  // keep-alive 上限 6：走够 8 个不同页面，助手实例必然被淘汰销毁
  const visited = await hopAroundMenu(page, 8)
  expect(visited.length, `走过的页面不足淘汰半径: ${visited.join(' ')}`).toBeGreaterThanOrEqual(8)

  await backViaMenu(page, '问答助手')
  await expect(page.locator('.assistant__msg.is-user').last()).toContainText(QUESTION)
  await expect(page.locator('.assistant__msg')).toHaveCount(2)

  await page.reload()
  await page.locator('.assistant').first().waitFor({ state: 'visible' })
  await expect(page.locator('.assistant__msg.is-user').last()).toContainText(QUESTION)
  // 直答的正文也一起回来了，不只是问题气泡
  await expect(page.locator('.assistant__msg.is-assistant').last()).toContainText(
    /摘自知识库|没有检索到/,
  )

  // 服务端确实留了会话：不靠页面内存
  const sessions = data(await api('GET', '/lab/sessions?module=knowledge-assistant&pageSize=50'))
  expect(sessions?.list?.length ?? 0, '问答会话未落库').toBeGreaterThan(0)
  const detail = data(await api('GET', `/lab/sessions/${sessions.list[0].id}?projectId=${seed.projectId}`))
  expect(JSON.stringify(detail?.messages ?? [])).toContain(QUESTION)

  // 清空对话 = 另起一条会话，当前线程空了
  await page.getByRole('button', { name: '清空对话' }).click()
  await expect(page.locator('.assistant__empty')).toBeVisible()

  // 问过的内容在「会话记录」页按「知识库问答」继续可查
  await page.goto('/lab/sessions')
  await page.locator('.el-table').first().waitFor({ state: 'visible' })
  await expect(page.locator('.el-table__body')).toContainText('知识库问答')
  assertHealthy(h)
})
