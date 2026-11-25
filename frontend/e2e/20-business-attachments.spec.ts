/** 业务对象持久附件：缺陷新建/编辑上传、详情展示与删除。 */
import { expect, test } from '@playwright/test'
import { api, assertHealthy, data, getSeed, open } from './helpers'

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

const IMAGE_FILE = { name: '失败截图.png', mimeType: 'image/png', buffer: PNG }
const LOG_FILE = {
  name: '执行日志.txt',
  mimeType: 'text/plain',
  buffer: Buffer.from('2026-11-24 ERROR NullPointerException at OrderService:88'),
}

test('缺陷详情可上传文档与图片附件，并支持预览和删除', async ({ page }) => {
  const seed = await getSeed()
  const title = `E2E 附件缺陷 ${Date.now()}`
  const bug = data(await api('POST', '/bugs', { projectId: seed.projectId, title }))
  expect(bug?.id, '创建缺陷失败').toBeGreaterThan(0)

  const health = await open(page, `/bugs/${bug.id}`)
  const manager = page.locator('.attachment-manager', { hasText: '缺陷附件' })
  await expect(manager).toBeVisible()
  await expect(manager.locator('.attachment-item')).toHaveCount(0)

  await manager.locator('input[type=file]').setInputFiles([LOG_FILE, IMAGE_FILE])
  await expect(page.locator('.el-message', { hasText: '已上传 2 个附件' })).toBeVisible({
    timeout: 15_000,
  })
  await expect(manager.locator('.attachment-item')).toHaveCount(2)
  await expect(manager.locator('.attachment-item__name', { hasText: '执行日志.txt' })).toBeVisible()
  await expect(manager.locator('.attachment-item__name', { hasText: '失败截图.png' })).toBeVisible()

  // 图片缩略图可打开预览
  await manager.locator('.attachment-item__thumb').first().click()
  await expect(page.locator('.el-image-viewer__wrapper')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('.el-image-viewer__wrapper')).toBeHidden()

  await manager
    .locator('.attachment-item', { hasText: '执行日志.txt' })
    .locator('.attachment-item__remove')
    .click()
  await expect(page.locator('.el-message', { hasText: '已删除' })).toBeVisible({ timeout: 10_000 })
  await expect(manager.locator('.attachment-item')).toHaveCount(1)

  const saved = data(
    await api('GET', `/attachments?ownerType=bug&ownerId=${bug.id}&projectId=${seed.projectId}`),
  )
  expect(saved.list.map((item: any) => item.fileName)).toEqual(['失败截图.png'])
  assertHealthy(health)
})

test('新建缺陷时可先选附件，保存后自动上传并展示', async ({ page }) => {
  const seed = await getSeed()
  const title = `E2E 新建附件缺陷 ${Date.now()}`
  const health = await open(page, '/bugs')

  await page.getByRole('button', { name: '新建缺陷' }).click()
  const dialog = page.locator('.el-dialog', { hasText: '新建缺陷' })
  await expect(dialog).toBeVisible()

  await dialog.locator('.el-form-item', { hasText: '标题' }).locator('input').first().fill(title)
  await dialog.locator('.attachment-manager input[type=file]').setInputFiles([LOG_FILE])
  await expect(page.locator('.el-message', { hasText: '保存后上传' })).toBeVisible({
    timeout: 10_000,
  })
  await expect(dialog.locator('.attachment-item', { hasText: '执行日志.txt' })).toBeVisible()

  await dialog.getByRole('button', { name: '保存' }).click()
  await expect(page.locator('.el-message', { hasText: '保存成功' })).toBeVisible({ timeout: 15_000 })
  await expect(dialog).toBeHidden()

  const list = data(await api('GET', `/bugs?projectId=${seed.projectId}&keyword=${encodeURIComponent(title)}`))
  const created = list.list?.find((item: any) => item.title === title)
  expect(created?.id, '新建缺陷未落库').toBeGreaterThan(0)

  const attached = data(
    await api('GET', `/attachments?ownerType=bug&ownerId=${created.id}&projectId=${seed.projectId}`),
  )
  expect(attached.list.map((item: any) => item.fileName)).toEqual(['执行日志.txt'])

  await page.locator('.el-table__body-wrapper tbody tr', { hasText: title }).first().click()
  await expect(page.locator('.attachment-manager', { hasText: '缺陷附件' })).toContainText('执行日志.txt')
  assertHealthy(health)
})
