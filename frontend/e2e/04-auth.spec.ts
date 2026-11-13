/** 无权限/未登录：启用访问口令→登录闸→错口令→正确口令→关闭。自清理。 */
import { test, expect } from '@playwright/test'

const PWD = 'e2e-pass-123'

test('访问口令：未登录拦截→错口令报错→正确进入→关闭', async ({ page }) => {
  await page.goto('/')
  // 启用口令（首次设置，返回会话 cookie）
  await page.evaluate(async (pwd) => {
    await fetch('/api/v1/auth/password', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwd, confirmPassword: pwd }),
    })
  }, PWD)
  // 清会话模拟未登录：重载后应出现登录闸
  await page.context().clearCookies()
  await page.reload()
  await expect(page.locator('.access-gate__title')).toBeVisible({ timeout: 10_000 })
  // 错口令
  await page.locator('input[type=password]').fill('wrong-pass')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.locator('.access-gate__error')).toBeVisible()
  // 正确口令
  await page.locator('input[type=password]').fill(PWD)
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.locator('.page, .page-header').first()).toBeVisible({ timeout: 10_000 })
  // 关闭口令（自清理，带当前会话 cookie）
  await page.evaluate(async (pwd) => {
    await fetch('/api/v1/auth/password', {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwd }),
    })
  }, PWD)
})
