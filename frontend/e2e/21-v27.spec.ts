/**
 * V2.7.0：App 场景引擎、通知模板、缺陷半自动流转、MCP 服务端、质量洞察。
 * 全部使用隔离后端 8762 + 临时库；App 执行走 backend/scripts/mock_adb.py 本地桩，
 * 不依赖真机与 Android SDK，也不烧模型额度。
 */
import path from 'node:path'
import { expect, test } from '@playwright/test'
import { API, api, data, open, assertHealthy } from './helpers'

const BACKEND_ORIGIN = API.replace(/\/api\/v1$/, '')
/** Playwright 以 frontend 为 cwd 启动，仓库根在上一级。 */
const ROOT = path.resolve(process.cwd(), '..')
const PYTHON = path.join(ROOT, '.venv', 'Scripts', 'python.exe')
/** 后端以仓库根为 cwd 启动，adb 命令直接指向本地桩脚本。 */
const MOCK_ADB = `"${PYTHON}" backend/scripts/mock_adb.py`

async function newProject(name: string): Promise<number> {
  const created = data(await api('POST', '/projects', {
    code: `E2E-V27-${Date.now()}-${Math.floor(Math.random() * 1000)}`,
    name,
  }))
  expect(created?.id, '创建隔离项目失败').toBeGreaterThan(0)
  return created.id
}

async function switchProject(page: any, name: string): Promise<void> {
  await page.locator('.project-switcher__select').click()
  await page.locator('.el-select-dropdown__item', { hasText: name }).click()
}

async function configureAppDriver(): Promise<void> {
  const saved = data(await api('PUT', '/settings/app-driver', {
    adbCommand: MOCK_ADB,
    deviceSerial: 'E2E123',
    commandTimeout: 30,
  }))
  expect(saved?.adbCommand, 'App 执行配置保存失败').toContain('mock_adb.py')
}

async function createAppScenario(
  projectId: number, name: string, steps: Record<string, unknown>[],
): Promise<any> {
  const scenario = data(await api('POST', '/api-scenarios', {
    projectId,
    name,
    scenarioType: 'app',
    status: 'ready',
    steps,
  }))
  expect(scenario?.id, '创建 App 场景失败').toBeGreaterThan(0)
  return scenario
}

test('App 场景引擎：adb 桩驱动成功链路、失败链路与缺陷待审', async ({ page }) => {
  await configureAppDriver()
  const probe = data(await api('POST', '/settings/app-driver/probe', {}))
  expect(probe.ok, `设备探测失败：${JSON.stringify(probe)}`).toBe(true)
  expect(probe.devices.map((item: any) => item.serial)).toContain('E2E123')

  const projectName = `E2E App 场景 ${Date.now()}`
  const projectId = await newProject(projectName)

  const scenario = await createAppScenario(projectId, `App 登录链路 ${Date.now()}`, [
    { name: '启动 App', type: 'app', action: 'launch', package: 'com.example.app' },
    { name: '点登录', type: 'app', action: 'tap', target: '登录' },
    { name: '输入账号', type: 'app', action: 'input', target: '账号', value: 'tester' },
    { name: '等待', type: 'app', action: 'wait', waitMs: 10 },
    { name: '断言登录成功', type: 'app', action: 'assertText', target: '登录成功' },
    { name: '截图', type: 'app', action: 'screenshot' },
  ])

  const runRaw = await api('POST', `/api-scenarios/${scenario.id}/run?projectId=${projectId}`, {})
  const run = data(runRaw)
  expect(run, `App 场景执行失败：${JSON.stringify(runRaw)}`).toBeTruthy()
  expect(run.passed, `成功链路应通过：${JSON.stringify(run)}`).toBe(true)
  expect(run.runs[0].steps).toHaveLength(6)
  const appStep = run.runs[0].steps[0]
  expect(appStep.request.method).toBe('APP')
  expect(appStep.statusCode).toBeNull()
  const shotStep = run.runs[0].steps[5]
  expect(shotStep.response.body).toContain('app-shots')

  const failing = await createAppScenario(projectId, `App 失败断言 ${Date.now()}`, [
    { name: '断言不存在文案', type: 'app', action: 'assertText', target: '不存在的文案' },
  ])
  const failRaw = await api('POST', `/api-scenarios/${failing.id}/run?projectId=${projectId}`, {})
  const failed = data(failRaw)
  expect(failed.passed, `失败链路应未通过：${JSON.stringify(failed)}`).toBe(false)
  expect(failed.autoBugDraftId, '待审模式下失败应生成缺陷草稿').toBeGreaterThan(0)

  const drafts = data(await api('GET', `/bugs/drafts?projectId=${projectId}&status=pending`))
  expect(drafts.list.some((item: any) => item.source === 'scenario')).toBe(true)

  const health = await open(page, '/automation/scenario')
  await switchProject(page, projectName)
  await page.getByRole('tab', { name: 'App 场景' }).click()
  await expect(page.locator('.el-table')).toContainText(`App 登录链路`)
  await expect(page.locator('.page-hint')).toContainText('adb')
  await page.getByRole('button', { name: 'App 执行配置' }).click()
  const driverDialog = page.locator('.el-dialog', { hasText: 'App 执行配置' })
  await expect(driverDialog).toBeVisible()
  await driverDialog.getByRole('button', { name: '测试连接' }).click()
  await expect(driverDialog).toContainText('可用')
  await expect(driverDialog).toContainText('E2E123')
  await driverDialog.getByRole('button', { name: '取消' }).click()
  assertHealthy(health)
})

test('通知模板：四渠道渲染预览、邮箱 SMTP 配置与模板编辑入口', async ({ page }) => {
  const subjects: string[] = []
  for (const channelType of ['webhook', 'dingtalk', 'wecom', 'feishu', 'email']) {
    const preview = data(await api('POST', '/settings/notifications/preview', {
      channelType,
      template: { title: '【AITP】{{eventLabel}}：{{title}}', text: '{{title}}|{{projectName}}' },
    }))
    expect(preview.channelType, `预览渠道类型不符：${JSON.stringify(preview)}`).toBe(channelType)
    expect(preview.subject).toContain('【AITP】')
    subjects.push(preview.subject)
    if (channelType === 'dingtalk') {
      expect(preview.body.msgtype).toBe('markdown')
    } else if (channelType === 'wecom') {
      expect(preview.body.markdown.content).toContain('示例：登录模块回归失败')
    } else if (channelType === 'feishu') {
      expect(preview.body.msg_type).toBe('text')
    } else if (channelType === 'email') {
      expect(preview.body).toBeNull()
      expect(preview.text).toContain('|')
    } else {
      expect(typeof preview.body).toBe('object')
    }
  }
  expect(new Set(subjects).size).toBe(1)

  const projectName = `E2E 通知模板 ${Date.now()}`
  const projectId = await newProject(projectName)
  const channel = data(await api('POST', '/settings/notifications', {
    name: `邮箱通知 ${Date.now()}`,
    projectId,
    channelType: 'email',
    webhookUrl: '',
    events: ['run.completed'],
    enabled: true,
    template: { title: '【AITP】{{eventLabel}}：{{title}}', text: '{{dataSummary}}' },
    config: {
      smtpHost: 'smtp.126.com',
      smtpPort: 465,
      smtpUser: 'yichengrenhg@126.com',
      fromAddr: 'yichengrenhg@126.com',
      toAddrs: 'yichengrenhg@126.com',
      useSsl: true,
      useTls: false,
      password: 'e2e-not-real',
    },
  }))
  expect(channel.channelType).toBe('email')
  expect(channel.config.toAddrs).toBe('yichengrenhg@126.com')
  expect(channel.config.hasPassword).toBe(true)
  expect(channel.config.password, '密码不应回传').toBeUndefined()
  expect(channel.webhookUrl).toBe('')

  const list = data(await api('GET', '/settings/notifications'))
  expect(list.list.some((item: any) => item.id === channel.id && item.channelType === 'email')).toBe(true)
  expect(list.channelTypes.map((item: any) => item.value)).toEqual(
    expect.arrayContaining(['dingtalk', 'wecom', 'feishu', 'email']),
  )
  expect(list.templateVariables).toContain('eventLabel')
  expect(list.emailHint).toContain('yichengrenhg@126.com')

  const health = await open(page, '/settings/ci')
  const row = page.locator('.el-table__row', { hasText: channel.name })
  await expect(row).toContainText('邮箱')
  await expect(row).toContainText('yichengrenhg@126.com')
  await page.getByRole('button', { name: '新建渠道' }).click()
  const dialog = page.locator('.channel-dialog')
  await expect(dialog).toBeVisible()
  await dialog.locator('.el-radio-button', { hasText: '邮箱' }).click()
  await expect(dialog).toContainText('SMTP 服务器')
  await expect(dialog.locator('input[placeholder*="yichengrenhg@126.com"]')).toBeVisible()
  await dialog.getByRole('button', { name: '渲染预览' }).click()
  await expect(dialog.locator('.template-preview__body')).toBeVisible({ timeout: 15_000 })
  await expect(dialog.locator('.template-preview__subject')).toContainText('【AITP】')
  await dialog.getByRole('button', { name: '取消' }).click()
  assertHealthy(health)
})

test('缺陷待审：App 场景失败进入待审区，人工确认后生成正式缺陷', async ({ page }) => {
  await configureAppDriver()
  const projectName = `E2E 待审流转 ${Date.now()}`
  const projectId = await newProject(projectName)
  const scenario = await createAppScenario(projectId, `App 待审失败 ${Date.now()}`, [
    { name: '断言不存在文案', type: 'app', action: 'assertText', target: '绝不会出现的文案' },
  ])
  const run = data(await api('POST', `/api-scenarios/${scenario.id}/run?projectId=${projectId}`, {}))
  expect(run.passed).toBe(false)
  const draftId = Number(run.autoBugDraftId)
  expect(draftId).toBeGreaterThan(0)

  const health = await open(page, '/bugs')
  await switchProject(page, projectName)
  const panel = page.locator('.drafts-panel')
  await expect(panel).toBeVisible({ timeout: 15_000 })
  await expect(panel).toContainText('缺陷待审区')
  await expect(panel).toContainText('场景失败')

  const draftRow = panel.locator('.el-table__row', { hasText: 'App 待审失败' })
  await expect(draftRow).toBeVisible()
  await draftRow.getByRole('button', { name: '确认转缺陷' }).click()
  await expect(panel).toHaveCount(0, { timeout: 15_000 })

  const confirmed = data(await api('GET', `/bugs/drafts?projectId=${projectId}`))
  expect(confirmed.list.some((item: any) => item.id === draftId && item.status === 'confirmed')).toBe(true)
  const bugs = data(await api('GET', `/bugs?projectId=${projectId}&pageSize=50`))
  expect(bugs.list.some((item: any) => item.title.includes('App 待审失败'))).toBe(true)
  assertHealthy(health)
})

test('失败转缺陷三档：设置页切档生效、旧键同步与非法档位拦截', async ({ page }) => {
  await configureAppDriver()
  const projectName = `E2E 三档流转 ${Date.now()}`
  const projectId = await newProject(projectName)
  const scenario = await createAppScenario(projectId, `App 三档失败 ${Date.now()}`, [
    { name: '断言不存在文案', type: 'app', action: 'assertText', target: '绝不会出现的文案' },
  ])

  async function configValue(key: string): Promise<string> {
    const rows = (data(await api('GET', '/settings/config')) ?? []) as any[]
    return String(rows.find((item) => item.key === key)?.value ?? '')
  }

  const health = await open(page, '/settings/performance')
  const block = page.locator('.settings-block', { hasText: 'JMeter 启动配置' })
  const group = page.locator('.el-form-item', { hasText: '失败自动转缺陷' })
  const saveBtn = block.getByRole('button', { name: '保存' })
  await expect(group.locator('.el-radio-button__inner')).toHaveText(['关闭', '待审（默认）', '自动'])
  await expect(group.locator('.el-radio-button.is-active')).toContainText('待审')

  /** 保存后按钮必须立刻回到可点：运行时体检（探本机 JMeter/adb）是后台刷新，不能锁住表单。 */
  async function saveMode(label: string): Promise<void> {
    await group.locator('.el-radio-button', { hasText: label }).click()
    await saveBtn.click()
    await expect(page.locator('.el-message--success').last()).toContainText('已保存', { timeout: 15_000 })
    await expect(saveBtn).toBeEnabled({ timeout: 5_000 })
  }

  // 自动档：两键一起写，失败直接落正式缺陷
  await saveMode('自动')
  await expect(group).toContainText('直接生成 P2 缺陷')
  await expect.poll(() => configValue('automation.auto_bug_mode')).toBe('auto')
  expect(await configValue('automation.auto_bug')).toBe('true')
  const autoRun = data(await api('POST', `/api-scenarios/${scenario.id}/run?projectId=${projectId}`, {}))
  expect(autoRun.passed).toBe(false)
  expect(Number(autoRun.autoBugId)).toBeGreaterThan(0)
  expect(autoRun.autoBugDraftId).toBeNull()

  // 第二次失败执行还要再建一条：去重只按本次执行编号
  const autoRunAgain = data(await api('POST', `/api-scenarios/${scenario.id}/run?projectId=${projectId}`, {}))
  expect(Number(autoRunAgain.autoBugId)).toBeGreaterThan(0)
  expect(autoRunAgain.autoBugId).not.toBe(autoRun.autoBugId)

  // 关闭档：既不建缺陷也不落待审
  await saveMode('关闭')
  await expect(group).toContainText('不自动生成任何缺陷')
  await expect.poll(() => configValue('automation.auto_bug_mode')).toBe('off')
  const offRun = data(await api('POST', `/api-scenarios/${scenario.id}/run?projectId=${projectId}`, {}))
  expect(offRun.passed).toBe(false)
  expect(offRun.autoBugId).toBeNull()
  expect(offRun.autoBugDraftId).toBeNull()

  // 回到默认待审档：只进待审区，旧布尔键同步回 false
  await saveMode('待审')
  await expect.poll(() => configValue('automation.auto_bug_mode')).toBe('review')
  expect(await configValue('automation.auto_bug')).toBe('false')
  const reviewRun = data(await api('POST', `/api-scenarios/${scenario.id}/run?projectId=${projectId}`, {}))
  expect(Number(reviewRun.autoBugDraftId)).toBeGreaterThan(0)
  expect(reviewRun.autoBugId).toBeNull()

  // 非法档位：接口拒绝且不覆盖原值
  const illegal = await api('PUT', '/settings/config', {
    items: [{ key: 'automation.auto_bug_mode', value: 'half' }],
  })
  expect(illegal.code).not.toBe(200000)
  expect(illegal.message).toContain('off / review / auto')
  expect(await configValue('automation.auto_bug_mode')).toBe('review')

  await page.reload()
  await expect(group.locator('.el-radio-button.is-active')).toContainText('待审', { timeout: 15_000 })
  assertHealthy(health)
})

test('MCP 服务端：只读工具、写待审、令牌校验与设置页入口', async ({ page }) => {
  const projectName = `E2E MCP ${Date.now()}`
  const projectId = await newProject(projectName)
  const saved = data(await api('PUT', '/settings/mcp', { enabled: true, allowWrite: false }))
  expect(saved.enabled).toBe(true)
  expect(saved.endpoint).toContain('/api/v1/mcp')

  const endpoint = `${BACKEND_ORIGIN}/api/v1/mcp`
  const init = await (await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize' }),
  })).json()
  expect(init.result.protocolVersion).toBe('2024-11-05')
  expect(init.result.serverInfo.name).toBe('aitp')

  const listRes = await (await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/list' }),
  })).json()
  const toolNames = listRes.result.tools.map((item: any) => item.name)
  expect(toolNames).toEqual(expect.arrayContaining([
    'cases.search', 'runs.recent', 'insights.failure_clusters', 'bugs.draft.create',
  ]))

  const denied = await (await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0', id: 3, method: 'tools/call',
      params: { name: 'bugs.draft.create', arguments: { projectId, title: 'MCP 待审缺陷' } },
    }),
  })).json()
  expect(denied.result.isError, '未开启写权限时写工具应被拒绝').toBe(true)

  await api('PUT', '/settings/mcp', { enabled: true, allowWrite: true })
  const created = await (await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0', id: 4, method: 'tools/call',
      params: {
        name: 'bugs.draft.create',
        arguments: {
          projectId,
          title: 'MCP 提交的候选缺陷',
          description: '来自 E2E MCP 客户端',
          severity: 'P1',
        },
      },
    }),
  })).json()
  expect(created.result.isError).toBe(false)
  const drafts = data(await api('GET', `/bugs/drafts?projectId=${projectId}`))
  expect(drafts.list.some((item: any) => item.source === 'mcp' && item.title.includes('MCP 提交'))).toBe(true)

  const generated = data(await api('POST', '/settings/mcp/token'))
  expect(generated.token.length).toBeGreaterThan(10)
  expect(generated.tokenPreview).toBe(`•••• ${String(generated.token).slice(-4)}`)
  const unauthorized = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 5, method: 'tools/list' }),
  })
  expect(unauthorized.status).toBe(403)
  const authorized = await (await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${generated.token}` },
    body: JSON.stringify({ jsonrpc: '2.0', id: 6, method: 'tools/list' }),
  })).json()
  expect(authorized.result.tools.length).toBeGreaterThan(0)

  const settingsAfterUse = data(await api('GET', '/settings/mcp'))
  expect(JSON.stringify(settingsAfterUse)).not.toContain(generated.token)
  expect(settingsAfterUse.lastUsedAt).toBeTruthy()
  expect(settingsAfterUse.tokens.some((item: any) => item.status === 'active')).toBe(true)

  const rotated = data(await api('POST', '/settings/mcp/token'))
  expect(rotated.token).not.toBe(generated.token)
  const oldToken = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${generated.token}` },
    body: JSON.stringify({ jsonrpc: '2.0', id: 7, method: 'tools/list' }),
  })
  expect(oldToken.status, '轮换后旧令牌应立即失效').toBe(403)
  const newToken = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${rotated.token}` },
    body: JSON.stringify({ jsonrpc: '2.0', id: 8, method: 'tools/list' }),
  })
  expect(newToken.status).toBe(200)

  const logs = data(await api('GET', '/settings/mcp/logs'))
  expect(logs.list.some((item: any) => item.tool === 'bugs.draft.create')).toBe(true)

  const health = await open(page, '/settings/mcp')
  await expect(page.locator('.mcp-endpoint')).toContainText('/api/v1/mcp')
  // 「最近调用」表也会列出 bugs.draft.create，故按「工具清单」区块定位，避免 strict 模式命中两张表
  await expect(
    page.locator('.settings-block', { hasText: '工具清单' })
      .locator('code', { hasText: 'bugs.draft.create' }),
  ).toBeVisible()
  await expect(page.locator('.mcp-token').first()).toContainText('••••')
  await expect(page.locator('.page')).not.toContainText(rotated.token)

  await page.getByRole('button', { name: '轮换令牌' }).click()
  await page.getByRole('button', { name: '继续轮换' }).click()
  const tokenDialog = page.locator('.el-dialog', { hasText: '新的访问令牌' })
  await expect(tokenDialog).toBeVisible({ timeout: 15_000 })
  const revealed = (await tokenDialog.locator('.mcp-token-reveal code').innerText()).trim()
  expect(revealed.length).toBeGreaterThan(10)
  await tokenDialog.getByRole('button', { name: '复制令牌' }).click()
  await tokenDialog.getByRole('button', { name: '我已保存' }).click()
  await expect(tokenDialog).toBeHidden()
  await expect(page.locator('.page')).not.toContainText(revealed)
  await expect(page.getByText('已停用（已轮换）').first()).toBeVisible()
  assertHealthy(health)
})

test('质量洞察：跨轮次失败聚类、依赖防环与变更影响面', async ({ page }) => {
  const projectName = `E2E 质量洞察 ${Date.now()}`
  const projectId = await newProject(projectName)
  const env = data(await api('POST', '/test-envs', {
    name: `V27 洞察环境 ${Date.now()}`,
    kind: 'test',
    app: { baseUrl: BACKEND_ORIGIN },
  }))

  const baseCase = data(await api('POST', '/test-cases', {
    projectId,
    title: '登录-基础校验',
    caseType: 'interface',
    priority: 'P1',
    extra: {
      接口URL: '/api/v1/health', 请求方式: 'GET', 预期状态码: '200',
      断言规则: 'json:$.appVersion = 9.9.9',
    },
  }))
  const dependentCase = data(await api('POST', '/test-cases', {
    projectId,
    title: '下单-依赖登录态',
    caseType: 'interface',
    priority: 'P1',
    extra: {
      接口URL: '/api/v1/health', 请求方式: 'GET', 预期状态码: '200',
      断言规则: 'json:$.appVersion = 9.9.9',
    },
  }))
  const run = data(await api('POST', '/test-runs', {
    projectId,
    name: `V27 洞察轮次 ${Date.now()}`,
    caseIds: [baseCase.id, dependentCase.id],
    envId: env.id,
  }))
  await api('POST', `/test-runs/${run.id}/interface-run?projectId=${projectId}`, {
    envId: env.id, sync: true, workers: 1,
  })

  const clusters = data(await api('GET', `/insights/failure-clusters?projectId=${projectId}&rounds=5`))
  expect(clusters.runCount).toBeGreaterThan(0)
  expect(clusters.totalFailures, '失败轮次应形成聚类样本').toBeGreaterThan(0)
  expect(clusters.clusters.length).toBeGreaterThan(0)
  expect(clusters.clusters[0].label).toBeTruthy()

  const dependency = data(await api('POST', '/insights/dependencies', {
    projectId,
    caseId: dependentCase.id,
    dependsOnCaseId: baseCase.id,
    note: '共用登录态',
  }))
  expect(dependency.id).toBeGreaterThan(0)
  const dependencyList = data(await api('GET', `/insights/dependencies?projectId=${projectId}`))
  expect(dependencyList.total).toBe(1)
  expect(dependencyList.items[0].dependsOnCaseNo).toBe(baseCase.caseNo)

  const cycle = await api('POST', '/insights/dependencies', {
    projectId,
    caseId: baseCase.id,
    dependsOnCaseId: dependentCase.id,
  })
  expect(cycle.code, '反向依赖应被判定成环').not.toBe(200000)

  const impact = data(await api('POST', '/insights/impact', {
    projectId, caseIds: [baseCase.id], requirementIds: [],
  }))
  expect(impact.direct.length).toBe(1)
  expect(impact.affected.some((item: any) => item.caseId === dependentCase.id && item.depth === 1)).toBe(true)
  expect(impact.total).toBe(2)

  const health = await open(page, '/insights')
  await switchProject(page, projectName)
  await expect(page.locator('.page-title')).toContainText('质量洞察')
  await expect(page.locator('.el-table').first()).toContainText('登录-基础校验')
  await page.getByRole('tab', { name: '变更影响面' }).click()
  const dependencyTable = page.locator('.panel', { hasText: '用例依赖' }).locator('.el-table')
  await expect(dependencyTable).toContainText('下单-依赖登录态')
  await expect(dependencyTable).toContainText('共用登录态')
  assertHealthy(health)
})
