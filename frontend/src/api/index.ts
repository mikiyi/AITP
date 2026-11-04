import { del, get, post, put, request, type PagePayload } from './request'
import type {
  AdoptCaseItem,
  AiModelInfo,
  AiProfile,
  AiRecord,
  AiRecordPage,
  AiSession,
  AiStatus,
  AiTask,
  AuditLog,
  BackupListResult,
  BatchDeleteResult,
  BatchReviewResult,
  Bug,
  BugLog,
  CapabilitySpec,
  CaseTreeNode,
  DbBackup,
  DocumentAttachment,
  EntityRelation,
  KnowledgeArticle,
  KnowledgeDraft,
  KnowledgeMigrateResult,
  KnowledgeRef,
  KnowledgeReindexResult,
  KnowledgeStorage,
  KnowledgeVersion,
  ImageAttachment,
  ModuleCoverage,
  ModuleNode,
  ModuleOption,
  PipelineResult,
  PreflightResult,
  Project,
  PromptTemplate,
  Regression,
  RegressionItem,
  Report,
  ReportItem,
  ReportStats,
  Requirement,
  RestoreResult,
  RouteResponse,
  SearchResult,
  SelfCheckResult,
  SystemConfigItem,
  TestCase,
  Todo,
  TodoPage,
  TodoStats,
  ValidationCase,
  ValidationResult,
  WorkbenchEntry,
  WorkbenchOverview,
  XmindParseResult,
} from './types'

/** 拼接下载类接口的查询串，跳过空值。 */
function exportUrl(path: string, query: Record<string, unknown>): string {
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.append(key, String(value))
    }
  })
  const qs = params.toString()
  return qs ? `/api/v1/${path}?${qs}` : `/api/v1/${path}`
}

/** 工作台概览。 */
export const workbenchApi = {
  overview: (projectId?: number) =>
    get<WorkbenchOverview>('/workbench/overview', { projectId }),
  coverage: (projectId: number) => get('/workbench/coverage', { projectId }),
  entries: (query: Record<string, unknown> = {}) =>
    get<{ list: WorkbenchEntry[]; total: number }>('/workbench/entries', query as never),
  entry: (id: number) => get<WorkbenchEntry>(`/workbench/entries/${id}`),
  createEntry: (body: Record<string, unknown>) =>
    post<WorkbenchEntry>('/workbench/entries', body),
  updateEntry: (id: number, body: Record<string, unknown>) =>
    put<WorkbenchEntry>(`/workbench/entries/${id}`, body),
  publishEntry: (id: number) => post<WorkbenchEntry>(`/workbench/entries/${id}/publish`),
  removeEntry: (id: number) => del(`/workbench/entries/${id}`),
  runEntry: (id: number, body: Record<string, unknown>) =>
    post<PipelineResult & { entryId: number; entryVersion: number }>(
      `/workbench/entries/${id}/run`,
      body,
    ),
}

/** 案例验证沙箱。 */
export const validationApi = {
  cases: (query: Record<string, unknown> = {}) =>
    get<{ list: ValidationCase[]; total: number }>('/validation/cases', query as never),
  case: (id: number) => get<ValidationCase>(`/validation/cases/${id}`),
  createCase: (body: Record<string, unknown>) => post<ValidationCase>('/validation/cases', body),
  updateCase: (id: number, body: Record<string, unknown>) =>
    put<ValidationCase>(`/validation/cases/${id}`, body),
  removeCase: (id: number) => del(`/validation/cases/${id}`),
  runCase: (id: number, body: Record<string, unknown> = {}) =>
    post<ValidationResult>(`/validation/cases/${id}/run`, body),
  runAll: (body: Record<string, unknown> = {}) =>
    post<{
      total: number
      passed: number
      failed: number
      passRate: number
      results: Array<{ id: number; name: string; passed: boolean; errors: string[] }>
    }>('/validation/run-all', body),
}

/** 回归清单。 */
export const regressionApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<Regression>>('/regressions', query as never),
  get: (id: number) => get<Regression>(`/regressions/${id}`),
  create: (body: Record<string, unknown>) => post<Regression>('/regressions', body),
  update: (id: number, body: Record<string, unknown>) =>
    put<Regression>(`/regressions/${id}`, body),
  remove: (id: number) => del(`/regressions/${id}`),
  generate: (id: number, body: Record<string, unknown> = {}) =>
    post<{ id: number; created: number; items: RegressionItem[]; output: string }>(
      `/regressions/${id}/generate`,
      body,
    ),
  items: (id: number) => get<{ list: RegressionItem[] }>(`/regressions/${id}/items`),
  addItem: (id: number, body: Record<string, unknown>) =>
    post<RegressionItem>(`/regressions/${id}/items`, body),
  updateItem: (id: number, itemId: number, body: Record<string, unknown>) =>
    put<RegressionItem>(`/regressions/${id}/items/${itemId}`, body),
  removeItem: (id: number, itemId: number) => del(`/regressions/${id}/items/${itemId}`),
  execute: (id: number, results: Array<{ itemId: number; result: string; remark?: string }>) =>
    post<{ id: number; status: string; updated: number[]; skipped: number[] }>(
      `/regressions/${id}/execute`,
      { results },
    ),
}

/** 任务清单。 */
export const todoApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<TodoPage>('/todos', query as never),
  get: (id: number) => get<Todo>(`/todos/${id}`),
  create: (body: Record<string, unknown>) => post<Todo>('/todos', body),
  update: (id: number, body: Record<string, unknown>) => put<Todo>(`/todos/${id}`, body),
  toggle: (id: number) => post<Todo>(`/todos/${id}/toggle`),
  remove: (id: number) => del<{ id: number; deleted: boolean; removed: number[] }>(`/todos/${id}`),
  move: (id: number, direction: 'up' | 'down') =>
    post<{ moved: boolean; reason?: string }>(`/todos/${id}/move`, { direction }),
  batchUpdate: (ids: number[], body: Record<string, unknown>) =>
    post<{ updated: number[]; count: number; skipped: number[] }>('/todos/batch-update', {
      ids,
      ...body,
    }),
  batchDelete: (ids: number[]) =>
    post<{ deleted: number[]; count: number; skipped: number[] }>('/todos/batch-delete', { ids }),
  reorder: (ids: number[]) => post<{ ordered: number[]; count: number }>('/todos/reorder', { ids }),
  stats: (query: Record<string, unknown> = {}) => get<TodoStats>('/todos/stats', query as never),
  categories: (query: Record<string, unknown> = {}) =>
    get<{ list: TodoStats['categories'] }>('/todos/categories', query as never),
}

/** 通用实体关联。 */
export const relationApi = {  types: () =>
    get<{ entityTypes: string[]; relationTypes: string[] }>('/relations/types'),
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<EntityRelation>>('/relations', query as never),
  of: (entityType: string, entityId: number) =>
    get<{
      list: Array<EntityRelation & { direction: string; peerType: string; peerId: number }>
    }>(`/relations/of/${entityType}/${entityId}`),
  create: (body: Record<string, unknown>) => post<EntityRelation>('/relations', body),
  remove: (id: number) => del(`/relations/${id}`),
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
  batchRemove: (ids: number[]) => post<BatchDeleteResult>('/requirements/batch-delete', { ids }),
  exportUrl: (query: Record<string, unknown>) => exportUrl('requirements/export', query),
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
  batchRemove: (ids: number[]) => post<BatchDeleteResult>('/test-cases/batch-delete', { ids }),
  execute: (id: number, body: Record<string, unknown>) => post(`/test-cases/${id}/execute`, body),
  coverage: (projectId: number) => get('/test-cases/coverage', { projectId }),
  /** 用例树：功能点树上挂各自的用例。 */
  tree: (projectId: number) =>
    get<{ list: CaseTreeNode[]; orphans: TestCase[]; total: number }>('/test-cases/tree', {
      projectId,
    }),
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
  exportUrl: (query: Record<string, unknown>) => exportUrl('test-cases/export', query),
}

/** 缺陷。 */
export const bugApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<Bug>>('/bugs', query as never),
  get: (id: number) => get<Bug>(`/bugs/${id}`),
  create: (body: Record<string, unknown>) => post<Bug>('/bugs', body),
  update: (id: number, body: Record<string, unknown>) => put<Bug>(`/bugs/${id}`, body),
  remove: (id: number) => del(`/bugs/${id}`),
  batchRemove: (ids: number[]) => post<BatchDeleteResult>('/bugs/batch-delete', { ids }),
  transition: (id: number, body: Record<string, unknown>) => post(`/bugs/${id}/transition`, body),
  logs: (id: number) => get<BugLog[]>(`/bugs/${id}/logs`),
  linkCases: (id: number, caseIds: number[]) => post(`/bugs/${id}/cases`, { caseIds }),
  unlinkCases: (id: number, caseIds: number[]) => del(`/bugs/${id}/cases`, { caseIds }),
  stats: () => get('/bugs/stats'),
  exportUrl: (query: Record<string, unknown>) => exportUrl('bugs/export', query),
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
    post<BatchReviewResult>('/knowledge/drafts/batch-review', { draftIds, action, comment }),
  listArticles: (query: Record<string, unknown> = {}) =>
    get<PagePayload<KnowledgeArticle>>('/knowledge/articles', query as never),
  exportArticlesUrl: (query: Record<string, unknown>) => exportUrl('knowledge/articles/export', query),
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
  prompts: () => get<PromptTemplate[]>('/lab/prompts'),
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
  /** 执行前预检：缺项、完整度、主/辅助任务与执行顺序。 */
  preflight: (body: {
    text?: string
    capabilities?: string[]
    variables?: Record<string, unknown>
    moduleId?: number | null
    projectId?: number | null
    topK?: number
  }) => post<PreflightResult>('/lab/preflight', body),
  /** 多入口顺序执行：父任务 + 子任务 + 步骤轨迹。 */
  compositeRun: (body: {
    steps: Array<{
      capability: string
      name?: string
      moduleId?: number | null
      variables: Record<string, unknown>
    }>
    title?: string
    sessionId?: number | null
    profileId?: number | null
    moduleId?: number | null
    continueOnError?: boolean
  }) => post<PipelineResult>('/lab/composite/run', body),
  prompt: (code: string) => get<PromptTemplate>(`/lab/prompts/${encodeURIComponent(code)}`),
  /** 导入文档并抽取文本：Word / Excel / Markdown / PDF / HTML / JSON / TXT。 */
  uploadDocument: (form: FormData) =>
    request<DocumentAttachment>('/lab/attachments/document', { method: 'POST', body: form }),
  /** 上传图片：先去水印再落库。 */
  uploadImage: (form: FormData) =>
    request<ImageAttachment>('/lab/attachments/image', { method: 'POST', body: form }),
  /** 粘贴图片（data URL）：同样先走去水印。 */
  uploadImageDataUrl: (dataUrl: string) =>
    post<ImageAttachment>('/lab/attachments/image', { dataUrl }),
  /** 取图片的 data URL，供多模态模型读取。 */
  attachmentPayload: (id: string) =>
    get<{ attachmentId: string; mediaType: string; dataUrl: string }>(
      `/lab/attachments/${id}/payload`,
    ),
  /** 删除附件。 */
  deleteAttachment: (id: string) => del(`/lab/attachments/${id}`),
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

/** AI 执行记录：证据、缺项、风险点与人工复核结论。 */
export const recordApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<AiRecordPage>('/ai-records', query as never),
  get: (id: number) => get<AiRecord>(`/ai-records/${id}`),
  review: (id: number, status: 'approved' | 'rejected', note = '') =>
    post<{ id: number; reviewStatus: string; reviewNote: string; reviewedAt: string }>(
      `/ai-records/${id}/review`,
      { status, note },
    ),
}

/** 全局搜索：一次检索需求、用例、缺陷、知识。 */
export const searchApi = {
  query: (params: { q: string; types?: string; projectId?: number; limit?: number }) =>
    get<SearchResult>('/search', params as never),
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
  backups: () => get<BackupListResult>('/settings/backups'),
  createBackup: () => post<DbBackup>('/settings/backups'),
  restoreBackup: (name: string, confirm: string) =>
    post<RestoreResult>(`/settings/backups/${encodeURIComponent(name)}/restore`, { confirm }),
  deleteBackup: (name: string) => del(`/settings/backups/${encodeURIComponent(name)}`),
  backupDownloadUrl: (name: string) => `/api/v1/settings/backups/${encodeURIComponent(name)}/download`,
  enums: () => get('/meta/enums'),
  capabilities: () => get('/meta/capabilities'),
}
