/**
 * BUG-3 生产环境执行守卫（API 级，稳定可自动化）。
 * 矩阵含生产环境时，confirmProd=false 必须被拒绝；=true 才投递。
 * 勾选式确认弹窗（ProdConfirmDialog）的 UI 交互已在有头浏览器人工验证。
 */
import { test, expect } from '@playwright/test'
import { api, data, getSeed } from './helpers'

test('BUG-3 生产环境调度必须显式确认（API 守卫）', async () => {
  const seed = await getSeed()
  const env = data(await api('POST', '/test-envs', { name: 'E2E 生产', kind: 'prod' }))
  const script = data(await api('POST', '/automation/scripts', {
    name: 'E2E 生产脚本', category: 'api', framework: 'pytest',
    content: 'def test_ok():\n    assert True\n', envId: env.id, projectId: seed.projectId,
  }))
  const matrix = data(await api('POST', '/execution-matrices', {
    projectId: seed.projectId, name: 'E2E 生产矩阵', mode: 'automation',
    envIds: [env.id], scriptIds: [script.id],
  }))
  const mid = matrix.matrix.id

  // 未确认 → 拒绝
  const denied = await api('POST', `/execution-matrices/${mid}/run`, { confirmProd: false })
  expect(denied.code).not.toBe(200000)
  expect(String(denied.message)).toContain('生产环境')

  // 显式确认 → 投递任务队列
  const okRun = await api('POST', `/execution-matrices/${mid}/run`, { confirmProd: true })
  expect(okRun.code).toBe(200000)
  expect(okRun.data.task.id).toBeGreaterThan(0)
})
