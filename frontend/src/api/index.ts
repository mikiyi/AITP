import { del, get, post, put, request, type PagePayload } from './request'
import type {
  AdoptCaseItem,
  AiModelInfo,
  AiProfile,
  AiSession,
  AiStatus,
  AiTask,
  AuditLog,
  Bug,
  BugLog,
  CapabilitySpec,
  KnowledgeArticle,
  KnowledgeDraft,
  KnowledgeMigrateResult,
  KnowledgeRef,
  KnowledgeReindexResult,
  KnowledgeStorage,
  KnowledgeVersion,
  ModuleCoverage,
  ModuleNode,
  ModuleOption,
  Project,
  Report,
  ReportItem,
  ReportStats,
  Requirement,
  RouteResponse,
  SelfCheckResult,
  SystemConfigItem,
  TestCase,
  WorkbenchOverview,
  XmindParseResult,
} from './types'

/** 工作台概览。 */
export const workbenchApi = {
  overview: (projectId?: number) =>
    get<WorkbenchOverview>('/workbench/overview', { projectId }),
  coverage: (projectId: number) => get('/workbench/coverage', { projectId }),
}

/** 项目。 */
export const projectApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<Project>>('/projects', query as never),
  options: () => get<Project[]>('/projects/options'),
  get: (id: number) => get<Project>(`/projects/${id}`),
  create: (body: Partial<Project>) => post<Project>('/projects', body),
  update: (id: number, body: Partial<Project>) => put<Project>(`/projects/${id}`, body),
  remove: (id: number) => del(`/projects/${id}`),
}

/** 需求。 */
export const requirementApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<Requirement>>('/requirements', query as never),
  get: (id: number) => get<Requirement>(`/requirements/${id}`),
  create: (body: Partial<Requirement>) => post<Requirement>('/requirements', body),
  update: (id: number, body: Partial<Requirement>) => put<Requirement>(`/requirements/${id}`, body),
  remove: (id: number) => del(`/requirements/${id}`),
}

/** 四级模块树。 */
export const moduleApi = {
  tree: (projectId: number) =>
    get<{ projectId: number; tree: ModuleNode[] }>('/modules/tree', { projectId }),
  options: (projectId: number, all = false) =>
    get<ModuleOption[]>('/modules/options', { projectId, all: all ? 1 : undefined }),
  get: (id: number) => get<ModuleNode>(`/modules/${id}`),
  create: (body: Record<string, unknown>) => post<ModuleNode>('/modules', body),
  update: (id: number, body: Record<string, unknown>) => put<ModuleNode>(`/modules/${id}`, body),
  move: (id: number, parentId: number | null) => post(`/modules/${id}/move`, { parentId }),
  remove: (id: number) => del(`/modules/${id}`),
  capabilities: (id: number) => get(`/modules/${id}/capabilities`),
  setCapabilities: (id: number, capabilities: unknown[]) =>
    put(`/modules/${id}/capabilities`, { capabilities }),
}

/** 测试用例。 */
export const caseApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<TestCase>>('/test-cases', query as never),
  get: (id: number) => get<TestCase>(`/test-cases/${id}`),
  create: (body: Record<string, unknown>) => post<TestCase>('/test-cases', body),
  update: (id: number, body: Record<string, unknown>) => put<TestCase>(`/test-cases/${id}`, body),
  remove: (id: number) => del(`/test-cases/${id}`),
  execute: (id: number, body: Record<string, unknown>) => post(`/test-cases/${id}/execute`, body),
  coverage: (projectId: number) => get('/test-cases/coverage', { projectId }),
  coverageModules: (projectId: number) =>
    get<{ list: ModuleCoverage[] }>('/test-cases/coverage/modules', { projectId }),
  adopt: (body: {
    projectId: number
    moduleId?: number | null
    requirementId?: number | null
    cases: AdoptCaseItem[]
  }) =>
    post<{ created: Array<{ id: number; caseNo: string }>; count: number }>(
      '/test-cases/adopt',
      body,
    ),
  importExcel: (form: FormData) =>
    request<{ created: number; skipped: number; total: number }>('/test-cases/import', {
      method: 'POST',
      body: form,
    }),
  exportUrl: (query: Record<string, unknown>) => {
    const params = new URLSearchParams()
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params.append(key, String(value))
      }
    })
    return `/api/v1/test-cases/export?${params.toString()}`
  },
}

/** 缺陷。 */
export const bugApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<Bug>>('/bugs', query as never),
  get: (id: number) => get<Bug>(`/bugs/${id}`),
  create: (body: Record<string, unknown>) => post<Bug>('/bugs', body),
  update: (id: number, body: Record<string, unknown>) => put<Bug>(`/bugs/${id}`, body),
  remove: (id: number) => del(`/bugs/${id}`),
  transition: (id: number, body: Record<string, unknown>) => post(`/bugs/${id}/transition`, body),
  logs: (id: number) => get<BugLog[]>(`/bugs/${id}/logs`),
  linkCases: (id: number, caseIds: number[]) => post(`/bugs/${id}/cases`, { caseIds }),
  unlinkCases: (id: number, caseIds: number[]) => del(`/bugs/${id}/cases`, { caseIds }),
  stats: () => get('/bugs/stats'),
}

/** 知识库：待审池 + 正式库。 */
export const knowledgeApi = {
  listDrafts: (query: Record<string, unknown> = {}) =>
    get<PagePayload<KnowledgeDraft>>('/knowledge/drafts', query as never),
  createDraft: (body: Record<string, unknown>) => post<KnowledgeDraft>('/knowledge/drafts', body),
  updateDraft: (id: number, body: Record<string, unknown>) =>
    put<KnowledgeDraft>(`/knowledge/drafts/${id}`, body),
  reviewDraft: (id: number, action: 'approve' | 'reject', comment = '') =>
    post(`/knowledge/drafts/${id}/review`, { action, comment }),
  batchReview: (draftIds: number[], action: 'approve' | 'reject', comment = '') =>
    post('/knowledge/drafts/batch-review', { draftIds, action, comment }),
  listArticles: (query: Record<string, unknown> = {}) =>
    get<PagePayload<KnowledgeArticle>>('/knowledge/articles', query as never),
  getArticle: (id: number) => get<KnowledgeArticle>(`/knowledge/articles/${id}`),
  updateArticle: (id: number, body: Record<string, unknown>) =>
    put<KnowledgeArticle>(`/knowledge/articles/${id}`, body),
  removeArticle: (id: number) => del(`/knowledge/articles/${id}`),
  revokeArticle: (id: number) => post(`/knowledge/articles/${id}/revoke`),
  restoreArticle: (id: number) => post(`/knowledge/articles/${id}/restore`),
  listVersions: (id: number) => get<KnowledgeVersion[]>(`/knowledge/articles/${id}/versions`),
  getVersion: (id: number, version: number) =>
    get<KnowledgeVersion>(`/knowledge/articles/${id}/versions/${version}`),
  diffVersions: (id: number, from: number, to: number) =>
    get(`/knowledge/articles/${id}/diff`, { from, to }),
  reviewVersion: (id: number, version: number, action: 'approve' | 'reject', comment = '') =>
    post(`/knowledge/articles/${id}/versions/${version}/review`, { action, comment }),
  listRefs: (id: number) => get<KnowledgeRef[]>(`/knowledge/articles/${id}/refs`),
  search: (query: Record<string, unknown>) => get('/knowledge/search', query as never),
  tags: () => get<string[]>('/knowledge/tags'),
  storage: () => get<KnowledgeStorage>('/knowledge/storage'),
  updateStorage: (body: Record<string, unknown>) =>
    put<KnowledgeStorage>('/knowledge/storage', body),
  migrateStorage: (body: Record<string, unknown>) =>
    post<KnowledgeMigrateResult>('/knowledge/storage/migrate', body),
  reindex: () => post<KnowledgeReindexResult>('/knowledge/reindex'),
}

/** 测试报告。 */
export const reportApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<Report>>('/reports', query as never),
  get: (id: number) => get<Report>(`/reports/${id}`),
  create: (body: Record<string, unknown>) => post<Report>('/reports', body),
  update: (id: number, body: Record<string, unknown>) => put<Report>(`/reports/${id}`, body),
  remove: (id: number) => del(`/reports/${id}`),
  items: (id: number) => get<ReportItem[]>(`/reports/${id}/items`),
  setItems: (id: number, items: unknown[]) => put(`/reports/${id}/items`, { items }),
  stats: (id: number) => get<ReportStats>(`/reports/${id}/stats`),
  generate: (body: Record<string, unknown>) => post<Report>('/reports/generate', body),
  adopt: (body: Record<string, unknown>) => post<Report>('/reports/adopt', body),
  exportUrl: (id: number, format: 'md' | 'html' | 'pdf' | 'print') =>
    `/api/v1/reports/${id}/export?format=${format}`,
}

/** AI 实验室。 */
export const labApi = {
  capabilities: () => get<CapabilitySpec[]>('/lab/capabilities'),
  mainCapabilities: () => get<CapabilitySpec[]>('/lab/main-capabilities'),
  prompts: () => get('/lab/prompts'),
  run: (capability: string, body: Record<string, unknown>) =>
    post(`/lab/${capability}/run`, body),
  route: (body: { text: string; projectId?: number; topK?: number }) =>
    post<RouteResponse>('/lab/route', body),
  parseXmind: (form: FormData) =>
    request<XmindParseResult>('/lab/xmind/parse', { method: 'POST', body: form }),
  parseOutline: (outlineText: string) =>
    post<XmindParseResult>('/lab/xmind/parse', { outlineText }),
  sessions: (query: Record<string, unknown> = {}) =>
    get<PagePayload<AiSession>>('/lab/sessions', query as never),
  session: (id: number) => get<AiSession>(`/lab/sessions/${id}`),
  tasks: (query: Record<string, unknown> = {}) =>
    get<PagePayload<AiTask>>('/lab/tasks', query as never),
  task: (id: number) => get<AiTask>(`/lab/tasks/${id}`),
  cancelTask: (id: number) => post(`/lab/tasks/${id}/cancel`),
}

/** AI 工具：提示词优化、引用式问答。 */
export const aiApi = {
  optimize: (capability: string, rawText: string) =>
    post<{
      optimized: string
      original: string
      diff: unknown[]
      model: string
      usage: Record<string, unknown>
      failed: boolean
      message: string
    }>('/ai/prompts/optimize', { capability, rawText }),
  ask: (question: string) =>
    post<{
      answer: string
      refs: unknown[]
      model: string
      usage: Record<string, unknown>
      egress: string
      egressWarning: string
      contextFound: boolean
    }>('/ai/ask', { question }),
}

/** 设置：AI 档案、系统配置、枚举。 */
export const settingsApi = {
  profiles: () => get<AiProfile[]>('/settings/ai/profiles'),
  profile: (id: number) => get<AiProfile>(`/settings/ai/profiles/${id}`),
  createProfile: (body: Record<string, unknown>) => post<AiProfile>('/settings/ai/profiles', body),
  updateProfile: (id: number, body: Record<string, unknown>) =>
    put<AiProfile>(`/settings/ai/profiles/${id}`, body),
  removeProfile: (id: number) => del(`/settings/ai/profiles/${id}`),
  setApiKey: (id: number, apiKey: string) =>
    post<{ id: number; apiKeyMasked: string; hasApiKey: boolean }>(
      `/settings/ai/profiles/${id}/api-key`,
      { apiKey },
    ),
  providers: () => get('/settings/ai/providers'),
  status: () => get<AiStatus>('/settings/ai/status'),
  selfCheck: (body: Record<string, unknown>) =>
    post<SelfCheckResult>('/settings/ai/selfcheck', body),
  models: (body: Record<string, unknown>) =>
    post<AiModelInfo[]>('/settings/ai/models', body),
  config: () => get<SystemConfigItem[]>('/settings/config'),
  updateConfig: (items: Array<{ key: string; value: string; valueType?: string }>) =>
    put('/settings/config', { items }),
  auditLogs: (query: Record<string, unknown> = {}) =>
    get<PagePayload<AuditLog>>('/settings/audit-logs', query as never),
  auditResources: (resourceType: string, resourceId: number) =>
    get('/settings/audit-logs/resources', { resourceType, resourceId }),
  enums: () => get('/meta/enums'),
  capabilities: () => get('/meta/capabilities'),
}
