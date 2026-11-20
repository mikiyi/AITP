/** 资产编号 / 场景状态筛选 / 已被引用悬停浮显引用编号：界面动作必须落到真实业务记录。 */
import { test, expect, type Page } from '@playwright/test'
import { api, data, getSeed, attachHealth, assertHealthy } from './helpers'

const btn = (page: Page, name: string | RegExp) => page.getByRole('button', { name }).first()
const dialog = (page: Page) => page.locator('.el-dialog:visible').last()
const rows = (page: Page) => page.locator('.base-pro-table__body .el-table__row')
const option = (page: Page, text: string) =>
  page.locator('.el-select-dropdown:visible .el-select-dropdown__item', { hasText: text }).last()

test('Web 自动化脚本编号列与按编号检索', async ({ page }) => {
  const seed = await getSeed()
  const name = `E2E编号脚本${Date.now()}`
  const created = data(await api('POST', '/automation/scripts', {
    projectId: seed.projectId, name, category: 'web', content: 'print("ok")',
  }))
  expect(created.scriptNo, '新建脚本应带 AUTO-W 编号').toMatch(/^AUTO-W-\d+$/)

  const h = attachHealth(page)
  await page.goto('/automation/web')
  await rows(page).first().waitFor({ state: 'visible' })
  await expect(page.locator('.base-pro-table__body th', { hasText: '编号' }).first()).toBeVisible()
  const row = rows(page).filter({ hasText: name }).first()
  await expect(row, '列表行应展示脚本编号').toContainText(created.scriptNo)

  await page.getByPlaceholder('脚本名称 / 编号').fill(created.scriptNo)
  await expect(rows(page), '按编号检索只命中一条').toHaveCount(1)
  await expect(rows(page).first()).toContainText(name)

  // 非快乐路径：检索不存在的编号应给筛选空态而不是报错
  await page.getByPlaceholder('脚本名称 / 编号').fill('E2E不存在编号XYZ')
  await expect(page.locator('.base-pro-table__body .el-table__empty-text')).toContainText('没有符合当前筛选的记录')
  assertHealthy(h)
})

test('场景状态字段：列表展示、筛选与编辑落库', async ({ page }) => {
  const seed = await getSeed()
  const ts = Date.now()
  const step = { name: 's1', type: 'interface', extra: { 请求方式: 'GET', 接口URL: '/api/v1/projects' } }
  const ready = data(await api('POST', '/api-scenarios', {
    projectId: seed.projectId, name: `E2E状态启用${ts}`, scenarioType: 'api', steps: [step],
  }))
  const disabled = data(await api('POST', '/api-scenarios', {
    projectId: seed.projectId, name: `E2E状态停用${ts}`, scenarioType: 'api', status: 'disabled', steps: [step],
  }))
  expect(disabled.status, '新建时指定停用应落库').toBe('disabled')

  const h = attachHealth(page)
  await page.goto('/automation/scenario')
  const scenarioRows = page.locator('.panel .el-table__row')
  await scenarioRows.first().waitFor({ state: 'visible' })
  await expect(scenarioRows.filter({ hasText: `E2E状态启用${ts}` })).toContainText('启用')
  await expect(scenarioRows.filter({ hasText: `E2E状态停用${ts}` })).toContainText('停用')

  // 关键词 + 状态双筛选：先按名称锁定停用场景，再切「启用」应查无此场景
  await page.getByPlaceholder('场景名称 / 编号').fill(`E2E状态停用${ts}`)
  await page.getByPlaceholder('场景名称 / 编号').press('Enter')
  await expect(scenarioRows.filter({ hasText: `E2E状态停用${ts}` })).toHaveCount(1)
  await page.locator('.scenario-filter .el-select').click()
  await option(page, '启用').click()
  await expect(scenarioRows.filter({ hasText: `E2E状态停用${ts}` })).toHaveCount(0)
  // 筛选空态与全站口径一致，且可一键重置
  await expect(page.locator('.panel .el-table__empty-text')).toContainText('没有符合当前筛选的记录')
  await page.locator('.panel .el-empty').getByRole('button', { name: '重置筛选' }).click()
  await expect(scenarioRows.filter({ hasText: `E2E状态停用${ts}` })).toHaveCount(1)
  await page.getByPlaceholder('场景名称 / 编号').fill(`E2E状态停用${ts}`)
  await page.getByPlaceholder('场景名称 / 编号').press('Enter')
  await page.locator('.scenario-filter .el-select').click()
  await option(page, '停用').click()
  await expect(scenarioRows.filter({ hasText: `E2E状态停用${ts}` })).toHaveCount(1)

  // 编辑对话框里把启用场景改成停用，保存后列表与数据库都要变（先把状态筛选还原为全部）
  await page.locator('.scenario-filter .el-select').click()
  await option(page, '全部状态').click()
  await page.getByPlaceholder('场景名称 / 编号').fill(`E2E状态启用${ts}`)
  await page.getByPlaceholder('场景名称 / 编号').press('Enter')
  await scenarioRows.filter({ hasText: `E2E状态启用${ts}` }).getByRole('button', { name: '编辑' }).click()
  const dlg = dialog(page)
  await expect(dlg.locator('.el-form-item', { hasText: '状态' })).toBeVisible()
  await dlg.locator('.el-form-item', { hasText: '状态' }).locator('.el-select').click()
  await option(page, '停用').click()
  await btn(dlg, '保存').click()
  await expect(dlg).toBeHidden()
  await expect(scenarioRows.filter({ hasText: `E2E状态启用${ts}` })).toContainText('停用')
  const after = data(await api('GET', `/api-scenarios/${ready.id}?projectId=${seed.projectId}`))
  expect(after.status, '编辑状态应真实落库').toBe('disabled')
  assertHealthy(h)
})

test('已被引用悬停浮显场景编号，超过 10 个走查看更多弹框', async ({ page }) => {
  const seed = await getSeed()
  const ts = Date.now()
  const used = data(await api('POST', '/automation/scripts', {
    projectId: seed.projectId, name: `E2E被引用脚本${ts}`, category: 'web', content: 'print("ok")',
  }))
  const idle = data(await api('POST', '/automation/scripts', {
    projectId: seed.projectId, name: `E2E未引用脚本${ts}`, category: 'web', content: 'print("ok")',
  }))
  const scenarioNos: string[] = []
  const interfaceStep = { name: 'iface', type: 'interface', extra: { 请求方式: 'GET', 接口URL: '/api/v1/projects' } }
  for (let i = 0; i < 11; i += 1) {
    const scn = data(await api('POST', '/api-scenarios', {
      projectId: seed.projectId, name: `E2E引用场景${ts}-${i}`, scenarioType: 'web',
      steps: [
        interfaceStep,
        { name: 'script-step', type: 'automation', scriptNo: used.scriptNo },
      ],
    }))
    expect(scn.scenarioNo, '场景应带 SCN 编号').toMatch(/^SCN-\d+$/)
    scenarioNos.push(scn.scenarioNo)
  }
  // 反向对照：只有定时调度引用的脚本不算「已被引用」
  data(await api('POST', '/schedules', {
    projectId: seed.projectId, name: `E2E调度${ts}`,
    targetType: 'script', targetId: idle.id, intervalMinutes: 60,
  }))

  const h = attachHealth(page)
  await page.goto('/automation/web')
  await rows(page).first().waitFor({ state: 'visible' })
  const usedRow = rows(page).filter({ hasText: `E2E被引用脚本${ts}` }).first()
  const idleRow = rows(page).filter({ hasText: `E2E未引用脚本${ts}` }).first()
  await expect(usedRow).toContainText('已被引用')
  await expect(idleRow).toContainText('未被引用')

  await usedRow.locator('.ref-refs__tag').hover()
  const tip = page.locator('.ref-refs__tip')
  await expect(tip).toBeVisible()
  const tipText = await tip.innerText()
  expect((tipText.match(/SCN-\d+/g) ?? []).length, '浮显最多展示 10 个编号').toBe(10)
  await expect(tip.locator('.ref-refs__more')).toContainText('查看更多')

  await tip.locator('.ref-refs__more').click()
  const dlg = dialog(page)
  await expect(dlg).toContainText('被引用的编号')
  await expect(dlg.locator('.ref-refs__item'), '弹框应列出全部 11 个引用编号').toHaveCount(11)
  for (const no of scenarioNos) {
    await expect(dlg).toContainText(no)
  }
  await expect(dlg.locator('.ref-refs__item').first()).toContainText('场景')
  await dlg.locator('.el-dialog__headerbtn').click()
  await expect(dlg).toBeHidden()

  // 未被引用的脚本悬停不应出现编号浮显内容（先等上一条浮显收起，避免两条并存）
  await expect(page.locator('.ref-refs__tip')).toHaveCount(0)
  await idleRow.locator('.ref-refs__tag').hover()
  await expect(page.locator('.ref-refs__tip')).toContainText('暂无被引用的编号')
  assertHealthy(h)
})

test('场景 automation 步骤保存 scriptNo，缺编号被拒绝', async () => {
  const seed = await getSeed()
  const ts = Date.now()
  const script = data(await api('POST', '/automation/scripts', {
    projectId: seed.projectId, name: `E2E场景步骤脚本${ts}`, category: 'web', content: 'print("ok")',
  }))
  const scn = data(await api('POST', '/api-scenarios', {
    projectId: seed.projectId, name: `E2E自动化步骤场景${ts}`, scenarioType: 'web',
    steps: [{ name: '脚本步', type: 'automation', scriptNo: script.scriptNo }],
  }))
  const detail = data(await api('GET', `/api-scenarios/${scn.id}?projectId=${seed.projectId}`))
  const saved = detail.steps.find((s: { type?: string }) => s.type === 'automation')
  expect(saved, 'automation 步骤应原样读回').toBeTruthy()
  expect(saved.scriptNo).toBe(script.scriptNo)

  // 非快乐路径：automation 步骤缺 scriptNo 必须被后端拒绝，不能落脏数据
  const bad = await api('POST', '/api-scenarios', {
    projectId: seed.projectId, name: `E2E非法步骤${ts}`, scenarioType: 'web',
    steps: [{ name: '空脚本步', type: 'automation' }],
  })
  expect(bad.code, '缺脚本编号应返回业务错误码').not.toBe(200000)
})