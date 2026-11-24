/**
 * V2.6.x：AI 入口统一支持「文本 + 文件 + 图片」。
 * 覆盖：需求拆分仅图片、知识问答带图/带文档、AI 修订参考截图、模型档案视觉开关。
 * 全部走隔离后端 8762 + 临时库 + 本地 mock 模型，不碰正式服务与正式库。
 */
import { expect, test } from '@playwright/test'
import { api, assertHealthy, attachHealth, data, open } from './helpers'

/** 120x90、左上角带亮色覆盖层的 PNG，上传后应识别为「已去水印」。 */
const WATERMARK_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAHgAAABaCAIAAAD8YgW4AAAA6klEQVR4nO3SsQ3AIBAEQXDV5NRM7i6GgJ0G7rX6ec4ZN+y9x0u+2we8otBIoZFCI4VGCo0UGik0Umik0EihkUIjhUYKjRQaKTRSaKTQSKGRudZSW0/ro5FCI4VGCo0UGik0Umik0EihkUIjhUYKjRQaKTRSaKTQSKGRQiOFRgqNFBopNFJopNBIoZFCI4VGCo0UGik0Umik0EihkUIjhUYKjRQaKTRSaKTQSKGRQiOFRgqNFBopNFJopNBIoZFCI4VGCo0UGik0Umik0EihkUIjhUYKjRQaKTRSaKTQSKGRQiOFRgqNFHoYPykaBoO4vUAFAAAAAElFTkSuQmCC',
  'base64',
)

const IMAGE_FILE = { name: '需求截图.png', mimeType: 'image/png', buffer: WATERMARK_PNG }

async function newProject(name: string): Promise<number> {
  const created = data(await api('POST', '/projects', {
    code: `E2E-ATT-${Date.now()}`,
    name,
  }))
  expect(created?.id, '创建隔离项目失败').toBeGreaterThan(0)
  return created.id
}

async function switchProject(page: any, name: string): Promise<void> {
  await page.locator('.project-switcher__select').click()
  await page.locator('.el-select-dropdown__item', { hasText: name }).click()
}

test('需求拆分支持仅上传截图（自动去水印）', async ({ page }) => {
  const health = await open(page, '/requirements')
  await page.getByRole('button', { name: '文档批量导入' }).click()
  const dialog = page.locator('.req-import-dialog')
  await expect(dialog).toBeVisible()

  await dialog.locator('input[accept*=".png"]').setInputFiles(IMAGE_FILE)
  await expect(dialog.locator('.ai-attach__badge')).toHaveText('已去水印')
  await expect(page.locator('.el-message', { hasText: '图片已去水印' })).toBeVisible()

  // 不填文本，仅靠附件也要能发起拆分
  await dialog.getByRole('button', { name: '开始拆分' }).click()
  const table = dialog.locator('.req-import-table')
  await expect(table).toBeVisible({ timeout: 20_000 })
  await expect(table.locator('.el-table__body-wrapper tbody tr')).toHaveCount(3)
  assertHealthy(health)
})

test('知识问答可带图片提问，发送后附件清空', async ({ page }) => {
  const health = attachHealth(page)
  await page.goto('/knowledge/assistant')
  // 全量串行跑时首个动态路由可能编译较慢，单独放宽到 30s
  await page.locator('.assistant').first().waitFor({ state: 'visible', timeout: 30_000 })

  await page
    .locator('.assistant__modes .el-radio-button__inner', { hasText: '基于大模型回答' })
    .click()
  await page.locator('.assistant__attachments input[accept*=".png"]').setInputFiles(IMAGE_FILE)
  await expect(page.locator('.assistant__attachments .ai-attach__badge')).toHaveText('已去水印')

  const question = `请总结这张截图里的需求（E2E 附件）${Date.now()}`
  await page.locator('.assistant__input textarea').fill(question)
  await page.getByRole('button', { name: '提问' }).click()

  await expect(page.locator('.assistant__msg.is-user').last()).toContainText(question)
  await expect(page.locator('.assistant__msg.is-assistant').last()).not.toBeEmpty({
    timeout: 30_000,
  })
  // 发送后附件随输入一起复位，避免下一条问题误带旧截图
  await expect(page.locator('.assistant__attachments .ai-attach__badge')).toHaveCount(0)
  assertHealthy(health)
})

test('知识问答可上传文档，文档编号随请求提交', async ({ page }) => {
  const health = attachHealth(page)
  await page.goto('/knowledge/assistant')
  // 全量串行跑时首个动态路由可能编译较慢，单独放宽到 30s
  await page.locator('.assistant').first().waitFor({ state: 'visible', timeout: 30_000 })

  await page
    .locator('.assistant__modes .el-radio-button__inner', { hasText: '基于大模型回答' })
    .click()
  await page.locator('.assistant__attachments input[accept*=".docx"]').setInputFiles({
    name: '登录需求.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('登录需求：手机号 + 验证码登录，验证码 5 分钟有效。'),
  })
  await expect(page.locator('.assistant__attachments .ai-attach__docs')).toContainText('登录需求.md')

  const streamed = page.waitForResponse(
    (resp) =>
      resp.url().includes('/api/v1/knowledge/assistant/ask-stream') &&
      resp.request().method() === 'POST',
  )

  await page.locator('.assistant__input textarea').fill(`请总结文档里的需求 ${Date.now()}`)
  await page.getByRole('button', { name: '提问' }).click()
  await expect(page.locator('.assistant__msg.is-assistant').last()).not.toBeEmpty({
    timeout: 30_000,
  })

  // 流式请求的 postData 在拦截层可能为空，改从 SSE 的 done 事件核对后端解析结果
  const response = await streamed
  const sse = await response.text()
  const doneChunk = sse
    .split('\n\n')
    .find((chunk) => chunk.startsWith('event: done'))
  const doneLine = doneChunk?.split('\n').find((line) => line.startsWith('data: '))
  const donePayload = doneLine ? JSON.parse(doneLine.slice(6)) : null
  expect(donePayload?.attachments?.documents ?? [], '文档应被后端解析并回传').toHaveLength(1)
  assertHealthy(health)
})

test('AI 修订可上传参考截图并随请求提交', async ({ page }) => {
  const projectName = `E2E 修订附件 ${Date.now()}`
  const projectId = await newProject(projectName)
  const created = data(await api('POST', '/test-cases', {
    projectId,
    title: '登录-验证码错误提示',
    caseType: 'functional',
    priority: 'P1',
    expectedResult: '提示验证码错误',
    steps: [{ action: '提交错误验证码', expected: '' }],
  }))
  expect(created?.id, '创建用例失败').toBeGreaterThan(0)

  const health = await open(page, '/test-cases')
  await switchProject(page, projectName)
  const row = page.locator('.el-table__body-wrapper tbody tr', { hasText: '登录-验证码错误提示' })
  await expect(row).toBeVisible()
  await row.locator('.el-checkbox').click()

  await page.getByRole('button', { name: 'AI 修订' }).click()
  const dialog = page.locator('.case-patch-form')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('参考附件')

  await dialog.locator('input[accept*=".png"]').setInputFiles(IMAGE_FILE)
  await expect(dialog.locator('.ai-attach__badge')).toHaveText('已去水印')

  let payload: any = null
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/test-cases/patches')) {
      payload = request.postDataJSON()
    }
  })
  await dialog.getByRole('button', { name: '生成修订' }).click()

  // mock 模型不产出 patch 结构，这里只要确认请求真的带上了附件与默认指令
  await expect(page.locator('.el-message', { hasText: /模型输出不是合法|暂无可用 AI 模型/ }))
    .toBeVisible({ timeout: 20_000 })
  expect(payload, '未捕获到 AI 修订请求').toBeTruthy()
  expect(payload.caseIds).toEqual([created.id])
  expect(payload.instruction).toContain('请根据所附附件修改这批用例')
  expect(payload.attachmentIds).toHaveLength(1)
  assertHealthy(health)
})

test('智能路由：截图先转文本再匹配', async ({ page }) => {
  const health = await open(page, '/lab')
  const entry = page.locator('.smart-entry')
  await expect(entry).toBeVisible()

  await entry.locator('input[accept*=".png"]').setInputFiles(IMAGE_FILE)
  await expect(entry.locator('.ai-attach__badge')).toHaveText('已去水印')

  let payload: any = null
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/lab/route')) {
      payload = request.postDataJSON()
    }
  })
  // 不填文本，仅靠截图也要能匹配
  await entry.getByRole('button', { name: '智能匹配' }).click()

  const transcript = entry.locator('.smart-entry__transcript')
  await expect(transcript).toBeVisible({ timeout: 30_000 })
  await expect(transcript).toContainText('附件已转文本参与匹配')
  await expect(transcript).toContainText('图片转写')
  await expect(entry.locator('.smart-entry__card').first()).toBeVisible()
  expect(payload?.attachmentIds, '匹配请求应带上附件编号').toHaveLength(1)
  assertHealthy(health)
})

test('AI 档案可关闭图片输入，回读状态一致', async ({ page }) => {
  const health = await open(page, '/settings/ai')
  const row = page.locator('.el-table__row', { hasText: 'E2E 本地 mock' })
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: '编辑' }).click()

  const dialog = page.locator('.el-dialog', { hasText: '编辑 AI 档案' })
  await expect(dialog).toBeVisible()
  const item = dialog.locator('.el-form-item', { hasText: '支持图片输入' })
  const visionSwitch = item.locator('.el-switch')
  await expect(visionSwitch).toHaveClass(/is-checked/)

  await visionSwitch.click()
  await dialog.getByRole('button', { name: '保存' }).click()
  await expect(page.locator('.el-message', { hasText: /保存成功|已保存/ }).last()).toBeVisible()

  const profiles = data(await api('GET', '/settings/ai/profiles'))
  const saved = (profiles ?? []).find((profile: any) => profile.name === 'E2E 本地 mock')
  expect(saved?.supportsVision, '关闭后档案应回读为 false').toBe(false)

  // 还原，避免影响后续用例的图片链路
  await row.getByRole('button', { name: '编辑' }).click()
  await expect(dialog.locator('.el-form-item', { hasText: '支持图片输入' }).locator('.el-switch'))
    .not.toHaveClass(/is-checked/)
  await dialog.locator('.el-form-item', { hasText: '支持图片输入' }).locator('.el-switch').click()
  await dialog.getByRole('button', { name: '保存' }).click()
  await expect(page.locator('.el-message', { hasText: /保存成功|已保存/ }).last()).toBeVisible()
  assertHealthy(health)
})
