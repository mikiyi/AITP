/** 非快乐路径：跨项目直达被拒、后端断网可见错误（C2）。 */
import { expect, test } from '@playwright/test'
import { api, data, getSeed, attachHealth, assertHealthy } from './helpers'

test('跨项目直达详情 URL 不泄露别项目数据', async ({ page }) => {
  const seed = await getSeed()
  const other = data(await api('POST', '/projects', {
    code: `E2E-SCOPE-${Date.now()}`, name: 'E2E 跨项目项目',
  }))
  const title = `E2E 跨项目缺陷 ${Date.now()}`
  const bug = data(await api('POST', '/bugs', { projectId: other.id, title }))
  expect(bug.id).toBeTruthy()

  const health = attachHealth(page)
  await page.goto(`/bugs/${bug.id}`)
  await page.locator('.page, .page-header, .el-message').first().waitFor({ state: 'visible' })
  await expect(page.locator('.el-message', { hasText: /目标不存在|不存在|无法/ })).toBeVisible({
    timeout: 10_000,
  })
  await expect(page.locator('body')).not.toContainText(title)
  assertHealthy(health)
})

test('后端断网时页面给出可读错误而不是白屏', async ({ page }) => {
  await page.route('**/api/v1/**', (route) => route.abort())
  const health = attachHealth(page)
  await page.goto('/bugs')
  await expect(page.locator('body')).toContainText('无法连接后端')
  await expect(page.getByRole('button', { name: '重新连接' })).toBeVisible()
  assertHealthy(health)
})
