/**
 * V2.8.0：评测子包（AI 评测 / 评测集 / 用例 / 跑批 / 门禁 / 评测配置）。
 * 隔离后端 8762 + 临时库；执行走回放 outputs，零模型调用，不烧额度。
 */
import { expect, test } from '@playwright/test'
import { api, data, open, assertHealthy } from './helpers'

async function newProject(name: string): Promise<number> {
  const created = data(await api('POST', '/projects', {
    code: `E2E-V28-${Date.now()}-${Math.floor(Math.random() * 1000)}`,
    name,
  }))
  expect(created?.id, '创建隔离项目失败').toBeGreaterThan(0)
  return created.id
}

async function switchProject(page: any, name: string): Promise<void> {
  await page.locator('.project-switcher__select').click()
  await page.locator('.el-select-dropdown__item', { hasText: name }).click()
}

/** el-tabs 的未激活面板常驻 DOM（display:none），断言统一收敛到当前可见面板。 */
function activePane(page: any) {
  return page.locator('.el-tab-pane:visible')
}

test('评测工作台：建集 → 回放跑批 → 门禁证据不足 → 冻结与 holdout 防护', async ({ page }) => {
  const projectName = `E2E 评测 ${Date.now()}`
  const projectId = await newProject(projectName)

  // 1) UI 新建评测集
  const health = await open(page, '/evals')
  await switchProject(page, projectName)
  await expect(page.locator('.page-title')).toHaveText('AI 评测')
  await expect(page.locator('.el-tabs__item')).toHaveCount(5)
  await expect(page.locator('.el-tabs__item', { hasText: '配置' })).toHaveCount(0)
  await expect(page.locator('.eval-context')).toHaveCount(0)
  await page.getByRole('button', { name: '新建评测集' }).click()
  const suiteDialog = page.locator('.el-dialog', { hasText: '新建评测集' })
  await expect(suiteDialog).toBeVisible()
  await suiteDialog.locator('input').first().fill('金融问答金题集')
  await suiteDialog.getByRole('button', { name: '保存' }).click()
  await expect(activePane(page).locator('.el-table')).toContainText('金融问答金题集')
  await expect(activePane(page).locator('.el-table')).toContainText('未跑批')

  const suites = data(await api('GET', `/evals/suites?projectId=${projectId}&pageSize=100`))
  const suite = (suites?.list ?? []).find((item: any) => item.name === '金融问答金题集')
  expect(suite?.id, 'UI 建的评测集未落库').toBeGreaterThan(0)

  // 2) 新增用例 → 发起跑批（queued）→ 回放执行
  const created = data(await api('POST', `/evals/suites/${suite.id}/cases`, {
    title: '退款到账时效',
    category: 'golden',
    severity: 'S1',
    assertions: { must_contain: ['1-3 个工作日'] },
    input_json: { question: '退款多久到账？' },
    expected_type: 'assert',
    expected_text: '1-3 个工作日',
  }))
  expect(created?.id, '新增用例失败').toBeGreaterThan(0)

  const runRaw = data(await api('POST', '/evals/runs', {
    suiteId: suite.id,
    purpose: 'run',
    triggerType: 'manual',
  }))
  expect(runRaw?.runId, '创建跑批失败').toBeGreaterThan(0)
  expect(runRaw.reused).toBe(false)

  const replayRaw = await api('POST', `/evals/runs/${runRaw.runId}/resume`, {
    outputs: [{
      caseNo: 'EC-001',
      output: '退款将在 1-3 个工作日内到账',
      latencyMs: 120,
      tokensIn: 30,
      tokensOut: 12,
    }],
  })
  expect(replayRaw.code, `回放执行失败：${JSON.stringify(replayRaw)}`).toBe(200000)

  const detail = data(await api('GET', `/evals/runs/${runRaw.runId}`))
  expect(detail.run.status).toBe('finished')
  expect(detail.run.passed).toBe(1)
  expect(detail.metricOrder, '指标必须是 18 项固定顺序').toHaveLength(18)
  expect(detail.metricOrder[0]).toBe('success_rate')
  expect(detail.results[0].verdict).toBe('pass')

  // 幂等：同集 + 同配置 + 同种子重复发起复用既有跑批
  const again = data(await api('POST', '/evals/runs', { suiteId: suite.id, purpose: 'run', triggerType: 'manual' }))
  expect(again.runId).toBe(runRaw.runId)
  expect(again.reused).toBe(true)

  // 3) 门禁：有效样本 1 < 探索门槛 30 → insufficient
  const gateCreated = data(await api('POST', '/evals/gates', { suiteId: suite.id, name: '发布门禁' }))
  expect(gateCreated?.id, '创建门禁失败').toBeGreaterThan(0)
  const gateEval = data(await api('POST', `/evals/gates/${gateCreated.id}/evaluate`, { runId: runRaw.runId }))
  expect(gateEval.conclusion, `样本 1 条应为证据不足：${JSON.stringify(gateEval)}`).toBe('insufficient')
  expect(gateEval.sampleSize).toBe(1)
  expect(gateEval.checks.length).toBeGreaterThanOrEqual(6)

  // 列表页健康度：最近跑批 + 门禁结论随列表返回
  const suitesAfterGate = data(await api('GET', `/evals/suites?projectId=${projectId}&pageSize=100`))
  const suiteAfterGate = (suitesAfterGate?.list ?? []).find((item: any) => item.id === suite.id)
  expect(suiteAfterGate?.latestRun?.id, '列表应带最近跑批').toBe(runRaw.runId)
  expect(suiteAfterGate?.latestRun?.gateConclusion, '门禁求值应回写到最近跑批').toBe('insufficient')

  // 4) 冻结与删除保护（非快乐路径）
  const frozenEdit = await api('PUT', `/evals/cases/${created.id}`, { title: '改名试试' })
  expect(frozenEdit.code, '已进入跑批结果的用例必须冻结').toBe(160110)
  const frozenDelete = await api('DELETE', `/evals/cases/${created.id}`)
  expect(frozenDelete.code).toBe(160110)
  const suiteDelete = await api('DELETE', `/evals/suites/${suite.id}`)
  expect(suiteDelete.code, '有跑批的评测集禁止删除').toBe(160101)

  // 5) holdout 防污染：禁止导出（160107）
  const holdout = data(await api('POST', '/evals/suites', {
    projectId,
    name: `holdout 金题集 ${Date.now()}`,
    visibility: 'holdout',
  }))
  expect(holdout?.id).toBeGreaterThan(0)
  const holdoutCase = await api('POST', `/evals/suites/${holdout.id}/cases`, {
    title: '防污染验证',
    category: 'golden',
    severity: 'S0',
    assertions: { must_contain: ['OK'] },
  })
  expect(holdoutCase.code).toBe(200000)
  const holdoutRun = data(await api('POST', '/evals/runs', { suiteId: holdout.id, purpose: 'run' }))
  const holdoutReplay = await api('POST', `/evals/runs/${holdoutRun.runId}/resume`, {
    outputs: [{ caseNo: 'EC-001', output: 'OK' }],
  })
  expect(holdoutReplay.code).toBe(200000)
  const denied = await api('GET', `/evals/runs/${holdoutRun.runId}/export?format=md`)
  expect(denied.code, `holdout 集必须拒绝导出：${JSON.stringify(denied)}`).toBe(160107)

  // 6) 质量面板取服务端口径
  const quality = data(await api('GET', `/evals/suites/${suite.id}/quality`))
  expect(quality.caseCount).toBe(1)
  expect(Array.isArray(quality.coverage)).toBe(true)
  const trends = data(await api('GET', `/evals/metrics/trends?suiteId=${suite.id}&keys=success_rate&limit=5`))
  expect(trends.points.length).toBeGreaterThanOrEqual(1)

  // 7) UI 回看：公共上下文条 + 头部主操作随 tab 切换
  const suiteRow = activePane(page).locator('.el-table__row', { hasText: '金融问答金题集' })
  await suiteRow.getByRole('button', { name: '进入' }).click()
  await expect(activePane(page).locator('.el-table')).toContainText('EC-001')
  const contextBar = page.locator('.eval-context')
  await expect(contextBar).toBeVisible()
  await expect(contextBar.locator('.el-select')).toContainText('金融问答金题集')
  await expect(contextBar).toContainText('1 条')
  await expect(page.getByRole('button', { name: '新增用例', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '新建评测集' })).toHaveCount(0)

  await page.getByRole('tab', { name: '跑批' }).click()
  await expect(page.getByRole('button', { name: '发起跑批', exact: true })).toBeVisible()
  const runRow = activePane(page).locator('.el-table__row', { hasText: `#${runRaw.runId}` })
  await runRow.getByRole('button', { name: '详情' }).click()
  const drawer = page.locator('.el-drawer', { hasText: '跑批详情' })
  await expect(drawer).toContainText('18 项指标')
  await expect(drawer).toContainText('逐条结果')
  await expect(drawer).toContainText('任务成功率')
  await expect(drawer).toContainText('EC-001')
  await drawer.locator('.el-drawer__close-btn').click()

  await page.getByRole('tab', { name: '门禁' }).click()
  await expect(page.getByRole('button', { name: '新建门禁', exact: true })).toBeVisible()
  await expect(activePane(page).locator('.el-table')).toContainText('发布门禁')
  await expect(activePane(page).locator('.el-table')).toContainText('证据不足')

  // 门禁结论弹窗：值必须按指标单位渲染（时延是 ms，不能被当成比率乘 100 加 %）
  await activePane(page)
    .locator('.el-table__row', { hasText: '发布门禁' })
    .getByRole('button', { name: '求值', exact: true })
    .click()
  const evalDialog = page.locator('.el-dialog', { hasText: '门禁求值' })
  await expect(evalDialog).toBeVisible()
  await evalDialog.locator('.el-select').click()
  await page
    .locator('.el-select-dropdown:visible .el-select-dropdown__item', { hasText: `#${runRaw.runId}` })
    .click()
  await evalDialog.getByRole('button', { name: '开始求值' }).click()
  const gateDialog = page.locator('.el-dialog', { hasText: '门禁结论' })
  await expect(gateDialog).toBeVisible()
  const latencyRow = gateDialog.locator('.el-table__row', { hasText: 'P95 时延' })
  await expect(latencyRow).toContainText('120 ms')
  await expect(latencyRow, '时延属于 ms 量纲，不能显示成百分比').not.toContainText('%')
  await expect(
    gateDialog.locator('.el-table__row', { hasText: '任务成功率' }),
    '比率指标仍应显示百分比',
  ).toContainText('%')
  await gateDialog.locator('.el-dialog__headerbtn').click()

  // 返回评测集列表：健康度列展示最近跑批与门禁结论
  await contextBar.getByRole('button', { name: '返回评测集列表' }).click()
  const suiteTable = activePane(page).locator('.el-table')
  await expect(suiteTable.locator('.el-table__row', { hasText: '金融问答金题集' })).toContainText('证据不足')
  await expect(suiteTable).toContainText(`#${runRaw.runId}`)
  await expect(page.getByRole('button', { name: '新建评测集' })).toBeVisible()
  await expect(contextBar).toHaveCount(0)

  // 8) AI 评测配置：按钮跳转到 设置 → AI 设置 的「AI 评测配置」tab，改完点「返回 AI 评测」回到原页面
  await page.getByRole('button', { name: 'AI 评测配置' }).click()
  await expect(page, '按钮应跳到设置页的 AI 评测配置 tab').toHaveURL(/\/settings\/ai\?tab=eval-config/)
  await expect(page.locator('.ai-settings-tabs .el-tabs__item.is-active')).toContainText('AI 评测配置')
  const evalConfigTab = page.locator('.el-tab-pane:visible')
  await evalConfigTab.getByRole('button', { name: '新建配置' }).click()
  const configDialog = page.locator('.el-dialog', { hasText: '新建评测配置' })
  await expect(configDialog).toBeVisible()
  await expect(configDialog).toContainText('评测对象')
  await expect(configDialog).toContainText('采样参数')
  await expect(configDialog).toContainText('追溯与备注（可选）')

  const configName = configDialog.locator('input').first()
  await expect(configName, '默认档案应预填名称').toHaveValue(/本地大模型 \(Ollama\)/)
  await configDialog
    .locator('.el-form-item')
    .filter({ has: page.locator('.el-form-item__label', { hasText: /^服务商$/ }) })
    .locator('.el-select')
    .click()
  await page.locator('.el-select-dropdown:visible .el-select-dropdown__item', { hasText: 'DeepSeek 深度求索' }).click()
  await expect(configName, '换服务商后名称应自动跟随').toHaveValue('DeepSeek 深度求索 / deepseek-chat')
  await expect(
    configDialog
      .locator('.el-form-item')
      .filter({ has: page.locator('.el-form-item__label', { hasText: /^模型$/ }) })
      .locator('.el-select'),
  ).toContainText('deepseek-chat')

  // 非快乐路径：清空名称 → 保存被拦截
  await configName.fill('')
  await configDialog.getByRole('button', { name: '保存' }).click()
  await expect(page.locator('.el-message', { hasText: '配置名称不能为空' })).toBeVisible()
  await configName.fill('E2E 评测配置')
  await configDialog.getByRole('button', { name: '保存' }).click()
  await expect(page.locator('.el-message--success').last()).toContainText('已保存')
  await expect(evalConfigTab.locator('.el-table').first()).toContainText('E2E 评测配置')
  const configs = data(await api('GET', `/evals/configs?projectId=${projectId}&pageSize=100`))
  const savedConfig = (configs?.list ?? []).find((item: any) => item.name === 'E2E 评测配置')
  expect(savedConfig?.provider, '配置应落库').toBe('deepseek')
  expect(savedConfig?.model).toBe('deepseek-chat')

  // 返回按钮：回到点「AI 评测配置」那一刻的 AI 评测页面
  await page.getByRole('button', { name: '返回 AI 评测' }).click()
  await expect(page, '返回按钮应回到 AI 评测页').toHaveURL(/\/evals/)
  await expect(page.locator('.page-title')).toHaveText('AI 评测')
  assertHealthy(health)
})

test('评测工作台：空态与 tab 禁用、同名评测集拒绝', async ({ page }) => {
  const projectName = `E2E 评测空态 ${Date.now()}`
  const projectId = await newProject(projectName)

  const health = await open(page, '/evals')
  await switchProject(page, projectName)
  await expect(page.getByText('还没有评测集')).toBeVisible()
  await expect(page.locator('.page-title')).toHaveText('AI 评测')
  await expect(page.locator('.el-tabs__item')).toHaveCount(5)
  await expect(page.locator('.el-tabs__item', { hasText: '配置' })).toHaveCount(0)
  await expect(page.locator('.eval-context')).toHaveCount(0)

  const caseTab = page.locator('.el-tabs__item', { hasText: '用例' })
  const runTab = page.locator('.el-tabs__item', { hasText: '跑批' })
  const qualityTab = page.locator('.el-tabs__item', { hasText: '质量' })
  const gateTab = page.locator('.el-tabs__item', { hasText: '门禁' })
  await expect(caseTab).toHaveClass(/is-disabled/)
  await expect(runTab).toHaveClass(/is-disabled/)
  await expect(qualityTab).toHaveClass(/is-disabled/)
  await expect(gateTab).toHaveClass(/is-disabled/)

  const dialog = page.locator('.el-dialog', { hasText: '新建评测集' })
  await page.getByRole('button', { name: '新建评测集' }).click()
  await dialog.locator('input').first().fill('重复名集')
  await dialog.getByRole('button', { name: '保存' }).click()
  await expect(activePane(page).locator('.el-table')).toContainText('重复名集')

  await page.getByRole('button', { name: '新建评测集' }).click()
  await dialog.locator('input').first().fill('重复名集')
  await dialog.getByRole('button', { name: '保存' }).click()
  await expect(page.locator('.el-message', { hasText: '同名评测集已存在' })).toBeVisible()
  await dialog.getByRole('button', { name: '取消' }).click()

  // 进入后用例/跑批/门禁解锁，空用例集允许浏览但禁止发起跑批
  await page.locator('.el-table__row', { hasText: '重复名集' }).getByRole('button', { name: '进入' }).click()
  await expect(page.locator('.el-tabs__item', { hasText: '用例' })).not.toHaveClass(/is-disabled/)
  await expect(page.locator('.el-tabs__item', { hasText: '质量' })).not.toHaveClass(/is-disabled/)
  await expect(page.locator('.eval-context')).toContainText('重复名集')
  await expect(page.getByRole('button', { name: '新增用例', exact: true })).toBeVisible()
  await expect(activePane(page).locator('.el-table')).toContainText('还没有用例')
  const emptyRun = await api('POST', '/evals/runs', {
    suiteId: data(await api('GET', `/evals/suites?projectId=${projectId}`))?.list?.[0]?.id,
  })
  expect(emptyRun.code, '空评测集禁止发起跑批').toBe(100100)
  assertHealthy(health)
})