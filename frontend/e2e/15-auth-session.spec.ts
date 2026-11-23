/**
 * 四期专项 F：登录态档案（storageState 只存本机，执行前过期预检）。
 * 覆盖：导入可用登录态 → 列表可见 → 预检通过 → 已过期登录态被拦 → 清除后消失。
 */
import { expect, test } from '@playwright/test'
import { api, assertHealthy, data, getSeed, open } from './helpers'

const HOUR = 3600

function jwt(expSeconds: number): string {
  const b64 = (value: unknown): string =>
    Buffer.from(JSON.stringify(value)).toString('base64url')
  return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ sub: 'e2e', exp: expSeconds })}.sig`
}

function storageState(expiresIn: number, withExpiredJwt = false): Record<string, unknown> {
  const now = Math.floor(Date.now() / 1000)
  return {
    cookies: [{
      name: 'sid', value: 'e2e-cookie', domain: '127.0.0.1', path: '/',
      expires: now + expiresIn, httpOnly: true, secure: false, sameSite: 'Lax',
    }],
    origins: [{
      origin: 'http://127.0.0.1:8762',
      localStorage: [{
        name: 'token',
        value: jwt(withExpiredJwt ? now - 60 : now + HOUR),
      }],
    }],
  }
}

test('导入登录态 → 预检可用 → 过期被拦 → 清除', async ({ page }) => {
  const seed = await getSeed()
  const role = `admin-${Date.now()}`
  const created = await api('POST', `/auth-sessions?projectId=${seed.projectId}`, {
    role,
    envId: null,
    note: 'E2E 导入',
    storageState: storageState(HOUR),
  })
  expect(created.code, `导入登录态失败：${created.message}`).toBe(200000)
  const session = data(created)
  expect(session.role).toBe(role)
  expect(session.expired).toBe(false)
  expect(session.cookieCount).toBe(1)

  const health = await open(page, '/settings/environments')
  const panel = page.locator('.panel', { hasText: '登录态档案' })
  await expect(panel).toBeVisible()
  const row = panel.locator('.el-table__row', { hasText: role })
  await expect(row).toBeVisible()
  await expect(row).toContainText('可用')

  await row.getByRole('button', { name: '预检' }).click()
  await expect(page.locator('.el-message', { hasText: '登录态可用' })).toBeVisible({ timeout: 10_000 })

  await row.getByRole('button', { name: '清除' }).click()
  const confirmBox = page.locator('.el-message-box')
  await expect(confirmBox).toBeVisible()
  await confirmBox.getByRole('button', { name: '确定' }).click()
  await expect(panel.locator('.el-table__row', { hasText: role })).toHaveCount(0, { timeout: 10_000 })

  const listed = data(await api('GET', `/auth-sessions?projectId=${seed.projectId}`))
  expect(listed.list.some((item: any) => item.role === role)).toBe(false)
  assertHealthy(health)
})

test('已过期登录态保存即标记过期，预检明确报错', async () => {
  const seed = await getSeed()
  const role = `expired-${Date.now()}`
  const created = await api('POST', `/auth-sessions?projectId=${seed.projectId}`, {
    role,
    storageState: storageState(-60),
  })
  expect(created.code, `保存过期登录态失败：${created.message}`).toBe(200000)
  const session = data(created)
  expect(session.expired, '过期 cookie 应被标记为已过期').toBe(true)

  const precheck = await api('POST', `/auth-sessions/${session.id}/precheck?projectId=${seed.projectId}`, {})
  expect(precheck.code, '过期登录态预检必须失败').not.toBe(200000)
  expect(String(precheck.message)).toContain('登录态已过期')

  const removed = await api('DELETE', `/auth-sessions/${session.id}?projectId=${seed.projectId}`)
  expect(removed.code).toBe(200000)
})

test('跨项目不可见登录态档案', async () => {
  const seed = await getSeed()
  const other = data(await api('POST', '/projects', {
    code: `E2E-AUTH-${Date.now()}`, name: 'E2E 他人项目',
  }))
  const created = await api('POST', `/auth-sessions?projectId=${seed.projectId}`, {
    role: `scope-${Date.now()}`,
    storageState: storageState(HOUR),
  })
  expect(created.code).toBe(200000)
  const detail = await api('GET', `/auth-sessions/${data(created).id}?projectId=${other.id}`)
  expect(detail.code, '跨项目读取必须被拒').not.toBe(200000)
})
