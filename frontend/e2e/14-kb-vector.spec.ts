/**
 * 四期专项 B：知识库向量检索（hybrid = 关键词 + 向量 RRF 融合）。
 * 走隔离后端 8762 + 本地 mock 嵌入（18791 的 /api/embeddings，按概念回固定方向），
 * 不联网、不烧云端额度。
 */
import { expect, test } from '@playwright/test'
import { api, assertHealthy, data, getSeed, open } from './helpers'

const MOCK_EMBED_BASE = 'http://127.0.0.1:18791'

async function configureEmbedding(enabled: boolean, baseUrl = MOCK_EMBED_BASE): Promise<void> {
  const result = await api('PUT', '/settings/config', {
    items: [
      { key: 'ai.embedding.protocol', value: 'ollama_native', valueType: 'string' },
      { key: 'ai.embedding.baseUrl', value: baseUrl, valueType: 'string' },
      { key: 'ai.embedding.model', value: 'mock-embedding', valueType: 'string' },
      { key: 'ai.embedding.enabled', value: enabled ? '1' : '0', valueType: 'boolean' },
    ],
  })
  expect(result.code, `写入嵌入配置失败：${result.message}`).toBe(200000)
}

async function firstCategory(): Promise<string> {
  const payload = data(await api('GET', '/knowledge/categories')) as any
  const list = payload?.list ?? []
  expect(list.length, '至少应有一个内置知识分类').toBeGreaterThan(0)
  return String(list[0].name)
}

async function publishArticle(projectId: number, title: string, content: string): Promise<number> {
  const category = await firstCategory()
  const created = await api('POST', '/knowledge/drafts', {
    projectId,
    title,
    content,
    summary: 'E2E 向量检索条目',
    category,
    tags: '幂等',
    sourceType: 'manual',
  })
  expect(created.code, `创建知识草稿失败：${created.message}`).toBe(200000)
  const draft = data(created) as any
  expect(draft?.id, '创建知识草稿失败').toBeGreaterThan(0)
  const reviewed = data(await api('POST', `/knowledge/drafts/${draft.id}/review`, {
    action: 'approve',
    reviewer: 'e2e',
    comment: 'E2E 审核通过',
  }))
  expect(reviewed?.articleId, '审核通过后应生成正式条目').toBeGreaterThan(0)
  return Number(reviewed.articleId)
}

test('配置嵌入模型 → 重建索引 → 语义问句召回并带引用', async () => {
  test.setTimeout(120_000) // 重建索引按库内全部文档分块嵌入，慢机型 60s 不够
  const seed = await getSeed()
  await configureEmbedding(true)
  const articleId = await publishArticle(
    seed.projectId,
    `E2E 支付回调幂等 ${Date.now()}`,
    '# 幂等键\n支付回调重复通知必须用幂等键去重，避免重复入账。',
  )

  const rebuilt = data(await api('POST', '/knowledge/embedding/rebuild'))
  expect(rebuilt.embedding.usable, '本地嵌入应可用').toBe(true)
  expect(rebuilt.chunkCount, '重建后应有向量分块').toBeGreaterThan(0)
  expect(rebuilt.embedded, '应至少新增一块向量').toBeGreaterThan(0)

  // 语义问句：与条目正文没有共同关键词，靠向量方向命中
  const answer = data(await api('POST', '/knowledge/ask', {
    question: '重复扣款怎么防',
    mode: 'kb',
  }))
  expect(answer.refs.length, '应召回至少一条知识引用').toBeGreaterThan(0)
  expect(answer.refs.some((ref: any) => Number(ref.articleId) === articleId),
    '引用应包含刚发布的条目').toBe(true)
  expect(answer.answer).toContain('摘自知识库')

  const direct = data(await api('GET', `/knowledge/search?keyword=重复扣款&pageSize=10`))
  expect(JSON.stringify(direct), '知识检索接口应能命中该条目').toContain(String(articleId))
})

test('无嵌入模型时降级：检索与问答照常，不报错', async () => {
  const seed = await getSeed()
  await configureEmbedding(false, '')
  const status = data(await api('GET', '/knowledge/embedding/status'))
  expect(status.embedding.usable, '未配置时嵌入不可用').toBe(false)
  expect(status.embedding.reason).toContain('关键词检索照常')

  const rebuilt = data(await api('POST', '/knowledge/embedding/rebuild'))
  expect(rebuilt.embedding.usable).toBe(false)

  const title = `E2E 降级条目 ${Date.now()}`
  await publishArticle(seed.projectId, title, '# 结论\n验证码登录失败先看短信通道。')
  const literal = data(await api('GET', `/knowledge/search?keyword=${encodeURIComponent(title)}&pageSize=10`))
  expect(JSON.stringify(literal), '关键词检索照常工作').toContain(title)
})

test('AI 设置页展示嵌入模型面板与状态', async ({ page }) => {
  const health = await open(page, '/settings/ai')
  const panel = page.locator('.panel', { hasText: '嵌入模型（语义检索）' })
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('不配置时自动回落关键词检索')
  await expect(panel.getByRole('button', { name: '重建索引' })).toBeVisible()
  await expect(panel.locator('.embedding-status')).toContainText('已索引')
  assertHealthy(health)
})
