/** BFS：每个页面一次访问，确保无白屏/无 JS 异常/无 5xx。汇总通过/失败清单。 */
import { test, expect } from '@playwright/test'
import { attachHealth, getSeed, ELEMENT_CAP } from './helpers'

const STATIC_ROUTES = [
  '/', '/todos', '/tasks', '/lab', '/lab/pipeline', '/lab/prompts', '/workbench/validation',
  '/lab/records', '/lab/sessions', '/projects', '/requirements', '/test-cases', '/regressions',
  '/test-plans', '/traceability', '/bugs', '/sql-analyses', '/knowledge', '/knowledge/assistant',
  '/reports', '/execution-matrices', '/exploratory',
  '/automation/web', '/automation/app', '/automation/api', '/automation/performance', '/help',
  '/settings', '/settings/security', '/settings/ai', '/settings/knowledge', '/settings/modules',
  '/settings/versions', '/settings/templates', '/settings/backup', '/settings/retention',
  '/settings/exploratory', '/settings/packages', '/settings/external-imports',
  '/settings/automation', '/settings/performance', '/settings/environments', '/settings/ci',
  '/settings/assertions', '/settings/home-cards',
]

test('BFS 全页面一次访问无白屏/报错', async ({ page }) => {
  test.setTimeout(180_000)
  const seed = await getSeed()
  const detail = [
    seed.caseId && `/test-cases/${seed.caseId}`,
    seed.bugId && `/bugs/${seed.bugId}`,
    seed.reportId && `/reports/${seed.reportId}`,
    seed.knowledgeId && `/knowledge/${seed.knowledgeId}`,
  ].filter(Boolean) as string[]
  const routes = [...STATIC_ROUTES, ...detail]
  const results: { route: string; ok: boolean; reason: string }[] = []
  let interactions = 0
  for (const route of routes) {
    const h = attachHealth(page)
    let ok = true
    let reason = ''
    try {
      await page.goto(route)
      await page.locator('.page, .page-header, .el-table, main').first().waitFor({ state: 'visible', timeout: 10_000 })
      const text = (await page.locator('body').innerText()).trim()
      if (text.length < 20) { ok = false; reason = '疑似白屏(正文过短)' }
      if (h.page.length) { ok = false; reason = `JS异常: ${h.page[0]}` }
      if (h.http.length) { ok = false; reason = `5xx: ${h.http[0]}` }
    } catch (e) {
      ok = false; reason = String(e).slice(0, 120)
    }
    interactions++ // BFS 每页只算一次访问
    results.push({ route, ok, reason })
  }
  expect(interactions).toBeLessThanOrEqual(routes.length * ELEMENT_CAP)
  const failed = results.filter((r) => !r.ok)
  console.log('\n=== BFS 清单 ===')
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'} ${r.route}${r.reason ? ' | ' + r.reason : ''}`)
  expect(failed, `失败页面: ${failed.map((f) => f.route).join(', ')}`).toEqual([])
})
