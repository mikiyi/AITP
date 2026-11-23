/**
 * 四期专项 D：需求批量拆分导入（文档 → AI 拆多条 → 勾选批量采纳）。
 * 全链路走隔离后端 8762 + 本地 mock 模型（按提示词特征回吐三条需求），
 * 不烧云端额度、不碰正式库。
 */
import { expect, test } from '@playwright/test'
import { api, assertHealthy, data, getSeed, open } from './helpers'

const DOC = '登录需求：手机号 + 验证码登录；同一手机号 60 秒内只能发送一次验证码；连续 5 次错误锁定 15 分钟。'

test('文档批量拆分 → 勾选 → 采纳落库为草稿需求', async ({ page }) => {
  const seed = await getSeed()
  const unique = `E2E 拆分登录 ${Date.now()}`
  const health = await open(page, '/requirements')

  await page.getByRole('button', { name: '文档批量导入' }).click()
  const dialog = page.locator('.req-import-dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('AI 只产出拆分建议')

  await dialog.locator('.req-import-text textarea').fill(`${DOC}\n${unique}`)
  await dialog.getByRole('button', { name: '开始拆分' }).click()

  const table = dialog.locator('.req-import-table')
  await expect(table).toBeVisible({ timeout: 20_000 })
  const rows = table.locator('.el-table__body-wrapper tbody tr')
  await expect(rows).toHaveCount(3)

  // 逐行勾选（表头全选在 Element Plus 下点 label 不总是触发，逐行更稳）
  const rowChecks = table.locator('.el-table__body-wrapper tbody tr .el-checkbox')
  for (let index = 0; index < 3; index += 1) {
    await rowChecks.nth(index).click()
  }
  const adoptButton = dialog.getByRole('button', { name: /采纳 \d+ 条/ })
  await expect(adoptButton).toContainText('采纳 3 条')
  await adoptButton.click()

  await expect(page.locator('.el-message', { hasText: '已采纳 3 条需求' })).toBeVisible({
    timeout: 20_000,
  })

  const listed = data(await api(
    'GET',
    `/requirements?projectId=${seed.projectId}&pageSize=50&keyword=${encodeURIComponent('手机号验证码登录')}`,
  ))
  const hit = (listed?.list ?? []).find((item: any) => item.title === '手机号验证码登录')
  expect(hit, '拆分出的需求应已落库').toBeTruthy()
  expect(hit.source).toBe('ai')
  expect(hit.status).toBe('draft')
  expect(String(hit.reqNo)).toMatch(/^REQ-/)
  assertHealthy(health)
})

test('同一文档重复导入被识别，已采纳条目自动跳过', async ({ page }) => {
  const seed = await getSeed()
  const doc = `${DOC}\n重复导入校验 ${Date.now()}`
  const splitResponse = await api('POST', '/requirements/import/split', {
    projectId: seed.projectId,
    text: doc,
    fileName: '登录需求.md',
  })
  expect(splitResponse.code, `首次拆分失败：${splitResponse.message}`).toBe(200000)
  const first = data(splitResponse)
  expect(first.generated).toBe(true)
  expect(first.items).toHaveLength(3)

  const adopted = data(await api('POST', '/requirements/import/adopt', {
    projectId: seed.projectId,
    items: first.items,
    importId: first.importId,
    fingerprint: first.fingerprint,
  }))
  expect(adopted.created).toBe(3)

  const again = data(await api('POST', '/requirements/import/split', {
    projectId: seed.projectId,
    text: doc,
  }))
  expect(again.duplicated, '同一文档应标记为重复导入').toBe(true)
  expect(again.message).toContain('已导入过')

  const second = data(await api('POST', '/requirements/import/adopt', {
    projectId: seed.projectId,
    items: again.items,
    importId: again.importId,
    fingerprint: again.fingerprint,
  }))
  expect(second.created, '重复条目不应再次落库').toBe(0)
  expect(second.skipped).toBe(3)

  const health = await open(page, '/requirements')
  await page.getByRole('button', { name: '文档批量导入' }).click()
  await expect(page.locator('.req-import-dialog')).toContainText('已采纳的条目会自动跳过')
  assertHealthy(health)
})

test('空文档直接提示，不发起拆分', async ({ page }) => {
  const health = await open(page, '/requirements')
  await page.getByRole('button', { name: '文档批量导入' }).click()
  const dialog = page.locator('.req-import-dialog')
  await dialog.getByRole('button', { name: '开始拆分' }).click()
  await expect(page.locator('.el-message', { hasText: '请先粘贴或上传需求文档' })).toBeVisible()
  await expect(dialog.locator('.req-import-table')).toHaveCount(0)
  assertHealthy(health)
})
