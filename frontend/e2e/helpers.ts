/** E2E 公共工具：隔离后端 API、元素预算（每页≤20）、页面健康断言、种子数据。 */
import type { Page } from '@playwright/test'
import { expect } from '@playwright/test'

export const API = 'http://127.0.0.1:8762/api/v1'
export const ELEMENT_CAP = 20

export async function api(method: string, p: string, body?: unknown): Promise<any> {
  const r = await fetch(`${API}${p}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return r.json()
}
export const data = (b: any) => b?.data

/** 每个页面的交互元素预算，防止死循环；超过 cap 抛错。 */
export function makeBudget(cap = ELEMENT_CAP) {
  const counts = new Map<string, number>()
  return {
    use(pageKey: string) {
      const n = (counts.get(pageKey) ?? 0) + 1
      counts.set(pageKey, n)
      if (n > cap) throw new Error(`页面 ${pageKey} 交互超过 ${cap} 个元素上限`)
    },
  }
}

export interface Health { console: string[]; page: string[]; http: string[] }
export function attachHealth(page: Page): Health {
  const h: Health = { console: [], page: [], http: [] }
  page.on('console', (m) => { if (m.type() === 'error') h.console.push(m.text()) })
  page.on('pageerror', (e) => h.page.push(String(e)))
  page.on('response', (r) => { if (r.status() >= 500) h.http.push(`${r.status()} ${r.url()}`) })
  return h
}
export function assertHealthy(h: Health) {
  expect(h.page, '页面 JS 异常').toEqual([])
  expect(h.http, '5xx 响应').toEqual([])
  // 控制台错误过滤掉资源类噪音（favicon/字体），只留逻辑错误
  const logic = h.console.filter((t) => !/favicon|\.woff|\.ttf|net::|Failed to load resource/i.test(t))
  expect(logic, '控制台逻辑错误').toEqual([])
}

/** 拉取示例项目与各类资产 id，供 BFS/DFS 定位详情页。 */
export async function getSeed() {
  const projects = data(await api('GET', '/projects?pageSize=50'))?.list ?? []
  const demo = projects.find((p: any) => p.code === 'DEMO-EC') ?? projects[0]
  const projectId = demo?.id
  const cases = data(await api('GET', `/test-cases?projectId=${projectId}&pageSize=5`))?.list ?? []
  const bugs = data(await api('GET', `/bugs?projectId=${projectId}&pageSize=5`))?.list ?? []
  const reports = data(await api('GET', `/reports?projectId=${projectId}&pageSize=5`))?.list ?? []
  const knowledge = data(await api('GET', `/knowledge/articles?projectId=${projectId}&pageSize=5`))?.list ?? []
  return {
    projectId,
    caseId: cases[0]?.id,
    bugId: bugs[0]?.id,
    reportId: reports[0]?.id,
    knowledgeId: knowledge[0]?.id,
  }
}

/** 打开页面并等待主体渲染，返回健康收集器。 */
export async function open(page: Page, route: string): Promise<Health> {
  const h = attachHealth(page)
  await page.goto(route)
  await page.locator('.page, .el-table, .page-header').first().waitFor({ state: 'visible' })
  return h
}
