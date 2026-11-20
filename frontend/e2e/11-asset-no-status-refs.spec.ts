/** 资产编号 / 场景状态筛选 / 已被引用悬停浮显引用编号：界面动作必须落到真实业务记录。 */
import { test, expect, type Locator, type Page } from '@playwright/test'
import { api, data, getSeed, attachHealth, assertHealthy } from './helpers'

const btn = (scope: Page | Locator, name: string | RegExp) => scope.getByRole('button', { name }).first()
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

test('复制出来的场景也计入被引用编号库', async ({ page }) => {
  const seed = await getSeed()
  const ts = Date.now()
  const script = data(await api('POST', '/automation/scripts', {
    projectId: seed.projectId, name: `E2E复制引用脚本${ts}`, category: 'web', content: 'print("ok")',
  }))
  const src = data(await api('POST', '/api-scenarios', {
    projectId: seed.projectId, name: `E2E复制引用场景${ts}`, scenarioType: 'web',
    steps: [{ name: '脚本步', type: 'automation', scriptNo: script.scriptNo }],
  }))
  const copy = data(await api('POST', `/api-scenarios/${src.id}/copy?projectId=${seed.projectId}`, {}))
  expect(copy.scenarioNo, '副本应有独立 SCN 编号').not.toBe(src.scenarioNo)

  const h = attachHealth(page)
  await page.goto('/automation/web')
  await rows(page).first().waitFor({ state: 'visible' })
  const row = rows(page).filter({ hasText: `E2E复制引用脚本${ts}` }).first()
  await expect(row).toContainText('已被引用')
  await row.locator('.ref-refs__tag').hover()
  const tip = page.locator('.ref-refs__tip')
  await expect(tip).toBeVisible()
  const tipText = await tip.innerText()
  expect(tipText, '原场景要计入引用库').toContain(src.scenarioNo)
  expect(tipText, '复制出来的场景也要计入引用库').toContain(copy.scenarioNo)
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

test('自动化脚本单条复制：新编号唯一、名称带副本前缀、其余字段一致', async ({ page }) => {
  const seed = await getSeed()
  const ts = Date.now()
  const src = data(await api('POST', '/automation/scripts', {
    projectId: seed.projectId, name: `E2E复制源${ts}`, category: 'web',
    content: 'print("copy-e2e")', framework: 'playwright', description: 'E2E复制描述',
  }))

  const h = attachHealth(page)
  await page.goto('/automation/web')
  await rows(page).first().waitFor({ state: 'visible' })
  const srcRow = rows(page).filter({ hasText: `E2E复制源${ts}` }).first()
  await srcRow.getByRole('button', { name: '复制' }).click()
  await expect(page.locator('.el-message--success').last()).toContainText('已复制')

  const copied = data(await api('GET', `/automation/scripts?projectId=${seed.projectId}&keyword=${encodeURIComponent(`E2E复制源${ts}`)}&pageSize=50`))
  const hit = (copied.list as { scriptNo: string; name: string; content: string; framework: string }[])
    .find((s) => s.name.startsWith('副本-') && s.name.includes(`E2E复制源${ts}`))
  expect(hit, '复制出的脚本应出现在列表').toBeTruthy()
  expect(hit!.scriptNo, '复制脚本编号是新 AUTO-W 号').toMatch(/^AUTO-W-\d+$/)
  expect(hit!.scriptNo).not.toBe(src.scriptNo)
  expect(hit!.content, '正文与源一致').toBe(src.content)
  expect(hit!.framework, '框架与源一致').toBe(src.framework)

  // 列表页能看到副本行
  await expect(rows(page).filter({ hasText: `副本-E2E复制源${ts}` }).first()).toBeVisible()
  assertHealthy(h)
})

test('数据库脚本与场景单条复制同样生效', async ({ page }) => {
  const seed = await getSeed()
  const ts = Date.now()
  const sql = data(await api('POST', '/sql-scripts', {
    projectId: seed.projectId, name: `E2E复制SQL${ts}`, content: 'SELECT 1', alias: 'e2e-copy-alias',
  }))
  const scn = data(await api('POST', '/api-scenarios', {
    projectId: seed.projectId, name: `E2E复制场景${ts}`, scenarioType: 'api',
    steps: [{ name: 's1', type: 'interface', extra: { 请求方式: 'GET', 接口URL: '/api/v1/projects' } }],
  }))

  const h = attachHealth(page)

  // 数据库脚本页复制
  await page.goto('/sql-scripts')
  await rows(page).first().waitFor({ state: 'visible' })
  const sqlRow = rows(page).filter({ hasText: `E2E复制SQL${ts}` }).first()
  await sqlRow.getByRole('button', { name: '复制' }).click()
  await expect(page.locator('.el-message--success').last()).toContainText('已复制')
  const sqlCopied = data(await api('GET', `/sql-scripts?projectId=${seed.projectId}&keyword=${encodeURIComponent(`E2E复制SQL${ts}`)}&pageSize=50`))
  const sqlHit = (sqlCopied.list as { scriptNo: string; name: string; content: string; alias: string }[])
    .find((s) => s.name.startsWith('副本-') && s.name.includes(`E2E复制SQL${ts}`))
  expect(sqlHit, '复制出的数据库脚本应出现在列表').toBeTruthy()
  expect(sqlHit!.scriptNo, '复制数据库脚本编号是新 SQL 号').toMatch(/^SQL-\d+$/)
  expect(sqlHit!.scriptNo).not.toBe(sql.scriptNo)
  expect(sqlHit!.content, 'SQL 正文与源一致').toBe(sql.content)

  // 场景页复制
  await page.goto('/automation/scenario')
  const scenarioRows = page.locator('.panel .el-table__row')
  await scenarioRows.first().waitFor({ state: 'visible' })
  const scnRow = scenarioRows.filter({ hasText: `E2E复制场景${ts}` }).first()
  await scnRow.getByRole('button', { name: '复制' }).click()
  await expect(page.locator('.el-message--success').last()).toContainText('已复制')
  const scnCopied = data(await api('GET', `/api-scenarios?projectId=${seed.projectId}&keyword=${encodeURIComponent(`E2E复制场景${ts}`)}&pageSize=50`))
  const scnHit = (scnCopied.list as { scenarioNo: string; name: string; steps: { type: string }[] }[])
    .find((s) => s.name.startsWith('副本-') && s.name.includes(`E2E复制场景${ts}`))
  expect(scnHit, '复制出的场景应出现在列表').toBeTruthy()
  expect(scnHit!.scenarioNo, '复制场景编号是新 SCN 号').toMatch(/^SCN-\d+$/)
  expect(scnHit!.scenarioNo).not.toBe(scn.scenarioNo)
  expect(scnHit!.steps.length, '场景步骤与源一致').toBe(scn.steps.length)
  assertHealthy(h)
})
test('新建自动化脚本默认带框架正文且归属当前项目', async ({ page }) => {
  const seed = await getSeed()
  const ts = Date.now()
  const name = `E2E默认值脚本${ts}`
  const h = attachHealth(page)
  await page.goto('/automation/web')
  await expect(page.locator('.base-pro-table')).toBeVisible()
  await btn(page, '新建脚本').click()
  const dlg = dialog(page)
  await expect(dlg, '新建脚本弹框应打开').toBeVisible()
  const frameworkSelect = dlg.locator('.el-form-item').filter({ hasText: '框架' }).locator('.el-select').first()
  await expect(frameworkSelect, '框架下拉应渲染').toBeVisible()
  const frameworkText = (await frameworkSelect.innerText()).trim()
  expect(frameworkText.length, '新建应默认选中一个框架').toBeGreaterThan(0)
  const content = await dlg.locator('textarea').last().inputValue()
  expect(content.length, '新建应带起步正文').toBeGreaterThan(0)
  await dlg.getByPlaceholder('例如：登录主流程冒烟').fill(name)
  await btn(dlg, '保存').click()
  await expect(dlg).toBeHidden()
  await expect(rows(page).filter({ hasText: name }), '保存后应出现在当前项目列表').toHaveCount(1)
  const listed = data(await api('GET', `/automation/scripts?projectId=${seed.projectId}&pageSize=100`))
  const hit = (listed.list as { name: string; projectId?: number }[]).find((s) => s.name === name)
  expect(hit, '脚本应归属当前项目，不允许无项目游离').toBeTruthy()
  assertHealthy(h)
})

test('定时调度目标从场景/脚本下拉选择', async ({ page }) => {
  const seed = await getSeed()
  const ts = Date.now()
  const script = data(await api('POST', '/automation/scripts', {
    projectId: seed.projectId, name: `E2E调度目标脚本${ts}`, category: 'web', content: 'print(1)',
  }))
  const h = attachHealth(page)
  await page.goto('/automation/web')
  await expect(page.locator('.base-pro-table')).toBeVisible()
  await btn(page, '定时').click()
  const dlg = dialog(page)
  await dlg.locator('.el-select').first().click()
  await option(page, '脚本').click()
  await dlg.locator('.el-select').nth(1).click()
  const dropdown = page.locator('.el-select-dropdown:visible').last()
  await expect(dropdown).toContainText(script.scriptNo)
  await expect(dropdown, '目标下拉要带名称，不能只给内部 ID').toContainText(`E2E调度目标脚本${ts}`)
  await page.keyboard.press('Escape')
  assertHealthy(h)
})
