/** 缺陷编号三类前缀 + 截图上传/预览/删除 + 备份截图开关（页面级）。 */
import { expect, test } from '@playwright/test'
import { api, data, getSeed, open, assertHealthy } from './helpers'

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

test('手工缺陷编号 BUG- 前缀，截图按 pic_<编号>_001 递增并可预览删除', async ({ page }) => {
  const seed = await getSeed()
  const title = `E2E 截图缺陷 ${Date.now()}`
  const bug = data(await api('POST', '/bugs', { projectId: seed.projectId, title }))
  expect(bug.bugNo, '手工缺陷应带 BUG- 前缀').toMatch(/^BUG-\d{3,}$/)

  const health = await open(page, `/bugs/${bug.id}`)
  const panel = page.locator('.panel', { hasText: '缺陷截图' })
  await expect(panel).toBeVisible()
  await expect(panel.locator('.el-tag', { hasText: '0 张' })).toBeVisible()
  await expect(panel.getByText('data/bug-screenshots/')).toBeVisible()

  await panel.locator('input[type=file]').setInputFiles([
    { name: 'first.png', mimeType: 'image/png', buffer: PNG },
    { name: 'second.png', mimeType: 'image/png', buffer: PNG },
  ])
  await expect(page.locator('.el-message', { hasText: '已上传 2 张截图' })).toBeVisible({ timeout: 10_000 })
  await expect(panel.locator('.screenshot-item')).toHaveCount(2)
  const metas = panel.locator('.screenshot-item__meta')
  await expect(metas.nth(0)).toHaveText(`pic_${bug.bugNo}_001.png`)
  await expect(metas.nth(1)).toHaveText(`pic_${bug.bugNo}_002.png`)

  await panel.locator('.screenshot-item__img').first().click()
  await expect(page.locator('.el-image-viewer__wrapper')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('.el-image-viewer__wrapper')).toBeHidden()

  await panel.locator('.screenshot-item').first().getByRole('button', { name: '删除' }).click()
  await expect(page.locator('.el-message', { hasText: '已删除' })).toBeVisible({ timeout: 10_000 })
  await expect(panel.locator('.screenshot-item')).toHaveCount(1)
  await expect(metas.nth(0)).toHaveText(`pic_${bug.bugNo}_002.png`)

  const shots = data(await api('GET', `/bugs/${bug.id}/screenshots?projectId=${seed.projectId}`)).list
  expect(shots.map((item: any) => item.fileName)).toEqual([`pic_${bug.bugNo}_002.png`])
  assertHealthy(health)
})

test('非法文件被拒绝且不落库', async ({ page }) => {
  const seed = await getSeed()
  const bug = data(await api('POST', '/bugs', {
    projectId: seed.projectId, title: `E2E 非法截图 ${Date.now()}`,
  }))
  const health = await open(page, `/bugs/${bug.id}`)
  const panel = page.locator('.panel', { hasText: '缺陷截图' })
  await panel.locator('input[type=file]').setInputFiles([
    { name: 'evil.png', mimeType: 'image/png', buffer: Buffer.from('<html>not an image</html>') },
  ])
  await expect(page.locator('.el-message', { hasText: '只支持 PNG' })).toBeVisible({ timeout: 10_000 })
  await expect(panel.locator('.screenshot-item')).toHaveCount(0)
  const shots = data(await api('GET', `/bugs/${bug.id}/screenshots?projectId=${seed.projectId}`)).list
  expect(shots).toEqual([])
  assertHealthy(health)
})

test('备份设置可开关「备份包含缺陷截图」', async ({ page }) => {
  const health = await open(page, '/settings/backup')
  const panel = page.locator('.panel', { hasText: '备份包含缺陷截图' })
  await expect(panel).toBeVisible()
  const toggle = panel.getByRole('switch')
  const toggleBox = panel.locator('.el-switch')
  await expect(toggle).toHaveAttribute('aria-checked', 'false')

  await toggleBox.click()
  await panel.getByRole('button', { name: '保存' }).click()
  await expect(page.locator('.el-message', { hasText: '已开启' })).toBeVisible({ timeout: 10_000 })

  await toggleBox.click()
  await panel.getByRole('button', { name: '保存' }).click()
  await expect(page.locator('.el-message', { hasText: '已关闭' })).toBeVisible({ timeout: 10_000 })
  assertHealthy(health)
})
