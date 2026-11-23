/** P1-7 探索异常一键登记缺陷 + P1-8 轮次详情一键生成范围报告。 */
import { expect, test } from '@playwright/test'
import { api, data, getSeed, open, assertHealthy } from './helpers'

test('探索异常一键登记缺陷：按钮变缺陷链接，接口回写 bugId', async ({ page }) => {
  const seed = await getSeed()
  const health = await open(page, '/exploratory')
  await page.locator('.el-table__row', { hasText: '登录页无脚本探索演示' }).waitFor()

  const row = page.locator('.el-table__row', { hasText: '登录页无脚本探索演示' })
  await row.getByRole('button', { name: '明细' }).click()
  await expect(page).toHaveURL(/\/exploratory\/\d+/)

  const anomalyRow = page.locator('.el-table__row', { hasText: '接口 4xx/5xx' })
  await anomalyRow.waitFor()
  await expect(anomalyRow.getByRole('button', { name: '登记缺陷' })).toBeVisible()
  await anomalyRow.getByRole('button', { name: '登记缺陷' }).click()

  await expect(page.locator('.el-message', { hasText: '已登记缺陷 AI_BUG-' })).toBeVisible({ timeout: 10_000 })
  await expect(anomalyRow.locator('button', { hasText: 'AI_BUG-' })).toBeVisible({ timeout: 10_000 })

  const sessions = data(await api('GET', `/exploratory/sessions?projectId=${seed.projectId}&keyword=${encodeURIComponent('登录页无脚本探索演示')}&pageSize=5`))
  const sessionId = sessions.list[0].id
  const detail = data(await api('GET', `/exploratory/sessions/${sessionId}?projectId=${seed.projectId}`))
  const anomaly = detail.steps.find((step: any) => step.isAnomaly)
  expect(anomaly.bugId, '异常步应回写缺陷 id').toBeTruthy()
  expect(String(anomaly.bugNo)).toMatch(/^AI_BUG-/)

  const bugs = data(await api('GET', `/bugs?projectId=${seed.projectId}&keyword=${encodeURIComponent(anomaly.bugNo)}&pageSize=5`))
  expect(bugs.list.some((bug: any) => bug.id === anomaly.bugId), '缺陷应真实落库').toBeTruthy()
  assertHealthy(health)
})

test('轮次详情生成范围报告：只统计本轮用例并跳转报告页', async ({ page }) => {
  const seed = await getSeed()
  const cases = data(await api('GET', `/test-cases?projectId=${seed.projectId}&pageSize=2`)).list
  expect(cases.length).toBeGreaterThanOrEqual(2)

  const name = `E2E 范围轮次 ${Date.now()}`
  const run = data(await api('POST', '/test-runs', {
    projectId: seed.projectId, name, caseIds: cases.slice(0, 2).map((item: any) => item.id),
  }))
  expect(run.id).toBeTruthy()

  const health = await open(page, `/test-runs/${run.id}`)
  await expect(page.getByRole('button', { name: '生成报告' })).toBeVisible()
  await page.getByRole('button', { name: '生成报告' }).click()
  await expect(page).toHaveURL(/\/reports\/\d+/, { timeout: 15_000 })

  const reports = data(await api('GET', `/reports?projectId=${seed.projectId}&keyword=${encodeURIComponent(name)}&pageSize=5`))
  const report = reports.list.find((item: any) => item.sourceType === 'run' && item.sourceId === run.id)
  expect(report, '报告应登记轮次来源').toBeTruthy()
  expect(report.sourceLabel).toContain(name)

  const detail = data(await api('GET', `/reports/${report.id}?projectId=${seed.projectId}`))
  const caseSection = detail.items.find((item: any) => item.section === 'case')
  expect(caseSection.metric.totalCases, '范围报告只统计本轮快照').toBe(2)
  expect(detail.periodStart, '统计区间回落到轮次创建日').toHaveLength(10)
  assertHealthy(health)
})
