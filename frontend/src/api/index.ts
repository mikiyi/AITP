import {
  del,
  get,
  getBlob,
  post,
  postBlob,
  postLong,
  put,
  request,
  LONG_TIMEOUT_MS,
  type BlobResult,
  type PagePayload,
} from './request'
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
  AutomationCategoryInfo,
  AutomationInterpreters,
  AutomationRuntime,
  AutomationRun,
  AutomationRunPage,
  AutomationScript,
  AutomationScriptPage,
  AutoBackupForm,
  AutoBackupPlan,
  BackupListResult,
  BatchDeleteResult,
  BatchReviewResult,
  Bug,
  BugLog,
  CapabilitySpec,
  CaseTreeNode,
  CiSettings,
  DbBackup,
  DocTemplate,
  DocTemplatePage,
  DocTemplateUpload,
  DocumentAttachment,
  HomeCardItem,
  KnowledgeAnswer,
  AssistantEgress,
  AssistantPreview,
  KnowledgeArticle,
  KnowledgeCategory,
  KnowledgeDraft,
  KnowledgeMigrateResult,
  KnowledgeRef,
  KnowledgeReindexResult,
  KnowledgeStorage,
  KnowledgeVersion,
  ImageAttachment,
  MaskInfo,
  ModuleCoverage,
  ModuleNode,
  ModuleOption,
  NotificationChannel,
  NotificationChannelList,
  NotificationLogPage,
  PipelineResult,
  PreflightResult,
  Project,
  ProjectVersion,
  ProjectVersionListResult,
  PromptTemplate,
  QueueTask,
  QueueTaskPage,
  Regression,
  RegressionItem,
  ReleaseGate,
  ReleaseGateResult,
  Report,
  ReportItem,
  ReportStats,
  ReportTrend,
  Requirement,
  SqlAnalysis,
  RestoreResult,
  RouteResponse,
  SearchResult,
  SelfCheckResult,
  SystemConfigItem,
  TestEnv,
  TestEnvListResult,
  TestEnvProbe,
  TestCase,
  InterfaceBatchResult,
  InterfaceExecution,
  TestDataBatch,
  TestDataBatchPage,
  TestDataCatalog,
  TestPlan,
  TestRun,
  TestRunCase,
  Todo,
  TodoPage,
  TodoStats,
  TraceabilityResult,
  ValidationCase,
  ValidationResult,
  VersionCompare,
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

/** 同步执行接口/自动化脚本时，按脚本自身超时再留 1 分钟余量。 */
function runTimeout(body: Record<string, unknown>): number {
  const seconds = Number(body.timeoutSeconds)
  const own = Number.isFinite(seconds) && seconds > 0 ? seconds : 300
  return Math.max(LONG_TIMEOUT_MS, own * 1000 + 60_000)
}

/** 工作台概览。 */
export const workbenchApi = {
  /** 首页快捷入口点击上报，用于按使用频率排序。 */
  reportShortcut: (key: string) => post('/workbench/quick-entry/usage', { key }),
  overview: (projectId?: number, versionId?: number) =>
    get<WorkbenchOverview>('/workbench/overview', { projectId, versionId }),
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
    postLong<{ id: number; created: number; items: RegressionItem[]; output: string }>(
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

/** 测试计划：定义长期测试范围，可据此发起轮次。 */
export const testPlanApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<TestPlan>>('/test-plans', query as never),
  get: (id: number) => get<TestPlan>(`/test-plans/${id}`),
  create: (body: Record<string, unknown>) => post<TestPlan>('/test-plans', body),
  update: (id: number, body: Record<string, unknown>) => put<TestPlan>(`/test-plans/${id}`, body),
  remove: (id: number) => del(`/test-plans/${id}`),
  createRun: (id: number, body: Record<string, unknown>) =>
    post<TestRun>(`/test-plans/${id}/runs`, body),
}

/** 测试轮次：一次具体执行，含用例快照与逐条结果。 */
export const testRunApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<TestRun>>('/test-runs', query as never),
  get: (id: number) => get<TestRun>(`/test-runs/${id}`),
  create: (body: Record<string, unknown>) => post<TestRun>('/test-runs', body),
  update: (id: number, body: Record<string, unknown>) => put<TestRun>(`/test-runs/${id}`, body),
  remove: (id: number) => del(`/test-runs/${id}`),
  addCases: (id: number, body: Record<string, unknown>) =>
    post<{ added: number[]; count: number; skipped: number[] }>(`/test-runs/${id}/cases`, body),
  removeCase: (id: number, caseId: number) => del(`/test-runs/${id}/cases/${caseId}`),
  recordCase: (id: number, caseId: number, body: Record<string, unknown>) =>
    put<{ runCase: TestRunCase; run: TestRun }>(`/test-runs/${id}/cases/${caseId}`, body),
  batchRecord: (id: number, entries: unknown[], executedBy = '') =>
    post<{ updated: number[]; skipped: number[]; run: TestRun }>(`/test-runs/${id}/cases/batch`, {
      entries,
      executedBy,
    }),
  createBug: (id: number, caseId: number, body: Record<string, unknown> = {}) =>
    post<{ created: boolean; bugId: number; bugNo: string; runCaseId: number }>(
      `/test-runs/${id}/cases/${caseId}/bug`,
      body,
    ),
  runInterfaceCase: (id: number, caseId: number, body: Record<string, unknown> = {}) =>
    postLong<InterfaceExecution>(
      `/test-runs/${id}/cases/${caseId}/interface-run`,
      body,
      runTimeout(body),
    ),
  runInterfaceBatch: (id: number, body: Record<string, unknown> = {}) =>
    postLong<InterfaceBatchResult | { queued: boolean; task: QueueTask; runId: number; envId: number }>(
      `/test-runs/${id}/interface-run`,
      body,
      runTimeout(body),
    ),
  complete: (id: number, remark = '') => post<TestRun>(`/test-runs/${id}/complete`, { remark }),
  automationRuns: (id: number) =>
    get<{ list: AutomationRun[] }>(`/test-runs/${id}/automation-runs`),
  runAutomation: (id: number, body: Record<string, unknown>) =>
    postLong<
      | { total: number; runs: AutomationRun[]; runId: number }
      | { queued: boolean; tasks: QueueTask[]; runId: number }
    >(`/test-runs/${id}/automation-run`, body, runTimeout(body)),
  exportUrl: (id: number, query: Record<string, unknown>) =>
    exportUrl(`test-runs/${id}/export`, query),
}

/** 需求追溯矩阵。 */
export const traceabilityApi = {
  get: (query: Record<string, unknown>) =>
    get<TraceabilityResult>('/traceability', query as never),
  exportUrl: (query: Record<string, unknown>) => exportUrl('traceability/export', query),
}

/** 发布准入 / 质量门禁。 */
export const releaseGateApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<ReleaseGate>>('/release-gates', query as never),
  get: (id: number) => get<ReleaseGate>(`/release-gates/${id}`),
  preview: (body: Record<string, unknown>) =>
    post<ReleaseGateResult>('/release-gates/preview', body),
  create: (body: Record<string, unknown>) => post<ReleaseGate>('/release-gates', body),
  remove: (id: number) => del(`/release-gates/${id}`),
}

/** 任务清单。 */export const todoApi = {
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

/** 项目版本：一个项目下可建多套版本，各测试资产通过 versionId 关联。 */
export const projectVersionApi = {
  list: (projectId: number) =>
    get<ProjectVersionListResult>('/project-versions', { projectId } as never),
  get: (id: number) => get<ProjectVersion>(`/project-versions/${id}`),
  create: (body: Partial<ProjectVersion>) => post<ProjectVersion>('/project-versions', body),
  update: (id: number, body: Partial<ProjectVersion>) =>
    put<ProjectVersion>(`/project-versions/${id}`, body),
  remove: (id: number) => del(`/project-versions/${id}`),
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
  runInterface: (id: number, body: Record<string, unknown> = {}) =>
    postLong<InterfaceExecution>(`/test-cases/${id}/interface-run`, body, runTimeout(body)),
  coverage: (projectId: number) => get('/test-cases/coverage', { projectId }),
  /** 功能点用例树：树上每个功能点挂自己的用例，未挂功能点的用例单独返回，供用例列表左侧筛选面板。 */
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
    versionId?: number | null
    cases: AdoptCaseItem[]
  }) =>
    post<{ created: Array<{ id: number; caseNo: string }>; count: number }>(
      '/test-cases/adopt',
      body,
    ),
  importExcel: (form: FormData) =>
    request<{ created: number; skipped: number; renumbered: number; total: number }>('/test-cases/import', {
      method: 'POST',
      body: form,
      timeoutMs: LONG_TIMEOUT_MS,
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
  /** 知识库问答助手：kb 纯本地检索 / llm 纯大模型 / hybrid 检索 + 大模型补强。 */
  ask: (body: { question: string; mode: 'kb' | 'llm' | 'hybrid' }) =>
    postLong<KnowledgeAnswer>('/knowledge/ask', body),
  /** 把模型回答连同溯源信息加入待审池；同一问同答同时间只入池一次。 */
  addAssistantAnswer: (body: Record<string, unknown>) =>
    post<KnowledgeDraft>('/knowledge/assistant/add', body),
  /** 问答链路实际外发评估：本地首选 + 云端兜底也算可能外发。 */
  assistantEgress: (mode: 'llm' | 'hybrid') =>
    get<AssistantEgress>('/knowledge/assistant/egress', { mode }),
  /** 外发前的站内检查：问答助手来源正式条目的 .md 全文与落盘路径。 */
  assistantPreview: () => get<AssistantPreview>('/knowledge/assistant/preview'),
  /** 发起问答助手来源条目的自动整理，只产出待审版本。 */
  organizeArticle: (id: number, body: Record<string, unknown> = {}) =>
    postLong(`/knowledge/articles/${id}/organize`, body),
  organizeStatus: (id: number) =>
    get<{
      articleId: number
      organizeStatus: '' | 'pending' | 'running' | 'done'
      organizeAt: string | null
      task: { status: string; statusLabel: string; errorMessage: string } | null
    }>(`/knowledge/articles/${id}/organize`),
  /** 分类清单：内置中文分类 + 自定义分类。 */
  categories: () => get<{ list: KnowledgeCategory[] }>('/knowledge/categories'),
  createCategory: (name: string) => post<KnowledgeCategory>('/knowledge/categories', { name }),
  removeCategory: (id: number) => del(`/knowledge/categories/${id}`),
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
    postLong<KnowledgeMigrateResult>('/knowledge/storage/migrate', body),
  reindex: () => postLong<KnowledgeReindexResult>('/knowledge/reindex'),
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
  trends: (query: { projectId: number; granularity?: 'day' | 'week'; periods?: number }) =>
    get<ReportTrend>('/reports/trends', query as never),
  versionCompare: (query: {
    projectId: number
    baseVersionId: number
    targetVersionId: number
  }) => get<VersionCompare>('/reports/version-compare', query as never),
  insertTrend: (id: number, body: { granularity: 'day' | 'week'; periods?: number }) =>
    post<{ reportId: number; items: ReportItem[] }>(`/reports/${id}/trend-item`, body),
  generate: (body: Record<string, unknown>) => post<Report>('/reports/generate', body),
  adopt: (body: Record<string, unknown>) => post<Report>('/reports/adopt', body),
  exportUrl: (id: number, format: 'md' | 'html' | 'pdf' | 'print') =>
    `/api/v1/reports/${id}/export?format=${format}`,
  /** PDF 走 fetch 而非直接打开：只有这样才能读到中文字体降级的响应头。 */
  exportBlob: (id: number, format: 'md' | 'html' | 'pdf' | 'print') =>
    getBlob(`/reports/${id}/export`, { format }) as Promise<BlobResult>,
}

/** AI 实验室。 */
export const labApi = {
  capabilities: () => get<CapabilitySpec[]>('/lab/capabilities'),
  mainCapabilities: () => get<CapabilitySpec[]>('/lab/main-capabilities'),
  prompts: () => get<PromptTemplate[]>('/lab/prompts'),
  run: (capability: string, body: Record<string, unknown>) =>
    postLong(`/lab/${capability}/run`, body),
  /** 把产出物一键添加到需求 / 缺陷 / 回归清单。 */
  adoptOutput: (body: {
    capability: string
    output: string
    target: 'requirement' | 'bug' | 'regression' | 'sql'
    projectId: number
    title?: string
    moduleId?: number
    versionId?: number
  }) => post<{ target: string; id: number; title: string }>('/lab/output/adopt', body),
  /** 把能力产出导出成 Excel（用例类能力）；失败时抛业务错误。 */
  exportOutput: (body: {
    capability: string
    output: string
    templateId?: number
    format?: 'xlsx' | 'docx'
  }) => postBlob('/lab/output/export', body) as Promise<BlobResult>,
  /** 把测试点大纲导出成 .xmind 文件；前端整理好 {title, children[]} 树回传。 */
  exportXmind: (body: { title: string; nodes: Array<Record<string, unknown>> }) =>
    postBlob('/lab/output/export-xmind', body) as Promise<BlobResult>,
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
  }) => postLong<PipelineResult>('/lab/composite/run', body),
  /** 停止本机正在跑的顺序执行；当前步骤结束后生效。 */
  compositeCancel: (body: { sessionId?: number | null } = {}) =>
    post<{ cancelled: number }>('/lab/composite/cancel', body),
  prompt: (code: string) => get<PromptTemplate>(`/lab/prompts/${encodeURIComponent(code)}`),
  /** 保存自定义正文：存进库并覆盖磁盘默认模板。 */
  savePrompt: (code: string, text: string) =>
    put<PromptTemplate>(`/lab/prompts/${encodeURIComponent(code)}`, { body: text }),
  /** 一键重置：删掉自定义正文，回到 backend/prompts 的默认模板。 */
  resetPrompt: (code: string) => del<PromptTemplate>(`/lab/prompts/${encodeURIComponent(code)}`),
  /** 导入文档并抽取文本：Word / Excel / Markdown / PDF / HTML / JSON / TXT。 */
  uploadDocument: (form: FormData) =>
    request<DocumentAttachment>('/lab/attachments/document', {
      method: 'POST',
      body: form,
      timeoutMs: LONG_TIMEOUT_MS,
    }),
  /** 上传图片：先去水印再落库。 */
  uploadImage: (form: FormData) =>
    request<ImageAttachment>('/lab/attachments/image', {
      method: 'POST',
      body: form,
      timeoutMs: LONG_TIMEOUT_MS,
    }),
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

/** AI 工具：提示词优化、外发前的站内脱敏检查。 */
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
      mask: MaskInfo
    }>('/ai/prompts/optimize', { capability, rawText }),
  /** 外发前的站内检查：识别文本里的敏感信息，只回类型与数量。 */
  maskPreview: (text: string) => post<MaskInfo>('/ai/mask/preview', { text }),
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

/** 自动化脚本资产：web / app / api / performance 四类共用一套接口。 */
export const automationApi = {
  /** 四类目录的说明与统计。 */
  categories: () => get<AutomationCategoryInfo[]>('/automation/categories'),
  /** 运行时体检：解释器与 JMeter 是否可用。 */
  runtime: () => get<AutomationRuntime>('/automation/runtime'),
  /** 本机解释器体检：每个 Python 装了哪些框架依赖。 */
  interpreters: (category?: string) =>
    get<AutomationInterpreters>('/automation/interpreters', category ? { category } : {}),
  /** 切换执行用的 Python 解释器。 */
  selectInterpreter: (path: string) =>
    post<AutomationRuntime>('/automation/interpreters/select', { path }),
  /** 批量投递执行。 */
  batchRun: (scriptIds: number[], body: Record<string, unknown> = {}) =>
    post<{ queued: boolean; tasks: QueueTask[]; total: number }>('/automation/scripts/batch-run', {
      scriptIds,
      ...body,
    }),
  /** 脚本列表。 */
  list: (query: Record<string, unknown> = {}) =>
    get<AutomationScriptPage>('/automation/scripts', query as never),
  /** 脚本详情（含最近 20 次执行历史）。 */
  detail: (id: number) => get<AutomationScript>(`/automation/scripts/${id}`),
  create: (body: Record<string, unknown>) => post<AutomationScript>('/automation/scripts', body),
  update: (id: number, body: Record<string, unknown>) =>
    put<AutomationScript>(`/automation/scripts/${id}`, body),
  remove: (id: number) => del(`/automation/scripts/${id}`),
  /** 执行脚本；失败也返回记录（status=failed）。 */
  run: (id: number, body: Record<string, unknown> = {}) =>
    postLong<AutomationRun>(`/automation/scripts/${id}/run`, body, runTimeout(body)),
  /** 后台执行：入队后立即返回任务。 */
  runAsync: (id: number, body: Record<string, unknown> = {}) =>
    post<{ queued: boolean; task: QueueTask }>(`/automation/scripts/${id}/run`, {
      ...body,
      async: true,
    }),
  /** 把 AI 实验室的脚本产出一键存成资产。 */
  createFromOutput: (body: Record<string, unknown>) =>
    post<AutomationScript>('/automation/scripts/from-output', body),
  /** 执行历史。 */
  runs: (query: Record<string, unknown> = {}) =>
    get<AutomationRunPage>('/automation/runs', query as never),
  runDetail: (id: number) => get<AutomationRun>(`/automation/runs/${id}`),
}

/** SQL 分析资产：AI 产出的 SQL 分析结论统一归档在这里。 */
export const sqlAnalysisApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<PagePayload<SqlAnalysis>>('/sql-analyses', query as never),
  get: (id: number) => get<SqlAnalysis>(`/sql-analyses/${id}`),
  create: (body: Record<string, unknown>) => post<SqlAnalysis>('/sql-analyses', body),
  update: (id: number, body: Record<string, unknown>) =>
    put<SqlAnalysis>(`/sql-analyses/${id}`, body),
  remove: (id: number) => del(`/sql-analyses/${id}`),
}

/** 测试数据生成：文件分支全程本地，不经过大模型。 */
export const testDataApi = {
  catalog: () => get<TestDataCatalog>('/test-data/formats'),
  generate: (body: Record<string, unknown>) => post<TestDataBatch>('/test-data/files', body),
  batches: (query: Record<string, unknown> = {}) =>
    get<TestDataBatchPage>('/test-data/batches', query as never),
  batch: (id: number) => get<TestDataBatch>(`/test-data/batches/${id}`),
  removeBatch: (id: number) => del(`/test-data/batches/${id}`),
  fileUrl: (id: number) => `/api/v1/test-data/files/${id}/download`,
  archiveUrl: (id: number) => `/api/v1/test-data/batches/${id}/archive`,
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
    postLong<SelfCheckResult>('/settings/ai/selfcheck', body),
  models: (body: Record<string, unknown>) =>
    postLong<AiModelInfo[]>('/settings/ai/models', body),
  config: () => get<SystemConfigItem[]>('/settings/config'),
  updateConfig: (items: Array<{ key: string; value: string; valueType?: string }>) =>
    put('/settings/config', { items }),
  /** 首页卡片布局：固定区 + 可定制区，含每张卡的显隐与顺序；default 取服务端默认排布。 */
  homeCards: (params: { default?: boolean } = {}) =>
    get<{ list: HomeCardItem[] }>('/settings/home-cards', params.default ? { default: 1 } : {}),
  saveHomeCards: (cards: Array<{ key: string; visible: boolean }>) =>
    put<{ list: HomeCardItem[] }>('/settings/home-cards', { cards }),
  auditLogs: (query: Record<string, unknown> = {}) =>
    get<PagePayload<AuditLog>>('/settings/audit-logs', query as never),
  auditResources: (resourceType: string, resourceId: number) =>
    get('/settings/audit-logs/resources', { resourceType, resourceId }),
  backups: () => get<BackupListResult>('/settings/backups'),
  /** 保存自动备份计划：开关、频率、时间、星期、保留份数与磁盘保护。 */
  saveAutoBackup: (body: AutoBackupForm) =>
    put<AutoBackupPlan>('/settings/backups/auto', body),
  /** 立即按自动备份策略执行一次（走任务队列，结果在任务中心可查）。 */
  runAutoBackupNow: () => post<QueueTask>('/settings/backups/auto/run', {}),
  createBackup: () => postLong<DbBackup>('/settings/backups'),
  /** 整包备份：库快照之外，附件、脚本、生成物、密钥、知识库正文一起打包。 */
  createBundleBackup: () => postLong<DbBackup>('/settings/backups/bundle'),
  /** 上传外部备份文件，校验通过后进入备份列表，可再选择恢复。 */
  uploadBackup: (form: FormData) =>
    request<DbBackup>('/settings/backups/upload', {
      method: 'POST',
      body: form,
      timeoutMs: LONG_TIMEOUT_MS,
    }),
  restoreBackup: (name: string, confirm: string, confirmFiles?: string) =>
    postLong<RestoreResult>(`/settings/backups/${encodeURIComponent(name)}/restore`, {
      confirm,
      confirmFiles,
    }),
  deleteBackup: (name: string) => del(`/settings/backups/${encodeURIComponent(name)}`),
  backupDownloadUrl: (name: string) => `/api/v1/settings/backups/${encodeURIComponent(name)}/download`,
  /** CI 触发令牌状态；生成时响应里带一次明文 token。 */
  ci: () => get<CiSettings>('/settings/ci'),
  issueCiToken: (body: Record<string, unknown>) => post<CiSettings>('/settings/ci/token', body),
  revokeCiToken: () => del<{ revoked: number; enabled: boolean }>('/settings/ci/token'),
  notificationChannels: () => get<NotificationChannelList>('/settings/notifications'),
  createNotification: (body: Record<string, unknown>) =>
    post<NotificationChannel>('/settings/notifications', body),
  updateNotification: (id: number, body: Record<string, unknown>) =>
    put<NotificationChannel>(`/settings/notifications/${id}`, body),
  removeNotification: (id: number) => del(`/settings/notifications/${id}`),
  testNotification: (id: number) =>
    post<{ success: boolean; httpStatus: number; message: string }>(
      `/settings/notifications/${id}/test`,
    ),
  notificationLogs: (query: Record<string, unknown> = {}) =>
    get<NotificationLogPage>('/settings/notification-logs', query as never),
  enums: () => get('/meta/enums'),
  capabilities: () => get('/meta/capabilities'),
}

/** 导航未读角标：AI 产出一键添加到资产后累加，进入对应页面后清零。 */
export const badgeApi = {
  list: () => get<{ items: Record<string, number> }>('/badges'),
  clear: (section: string) =>
    post<{ section: string; cleared: number }>(`/badges/${section}/clear`),
  increase: (section: string, count = 1) =>
    post(`/badges/${section}/increase`, { count }),
}

/** 测试环境配置：多套命名环境，供首页环境健康监测使用。口令只回掩码，绝不进大模型。 */
export const testEnvApi = {
  list: () => get<TestEnvListResult>('/test-envs'),
  get: (id: number) => get<TestEnv>(`/test-envs/${id}`),
  create: (body: Record<string, unknown>) => post<TestEnv>('/test-envs', body),
  update: (id: number, body: Record<string, unknown>) => put<TestEnv>(`/test-envs/${id}`, body),
  remove: (id: number) => del<{ id: number; deleted: boolean }>(`/test-envs/${id}`),
  setPassword: (id: number, password: string) =>
    post<{ id: number; passwordMasked: string; hasPassword: boolean }>(
      `/test-envs/${id}/password`,
      { password },
    ),
  /** 对已保存环境做健康探测；quiet 用于首页轮询，不写操作日志。 */
  probe: (id: number, options?: { quiet?: boolean }) =>
    post<TestEnvProbe>(`/test-envs/${id}/probe`, options ?? {}),
  /** 对未保存的草稿配置试连。 */
  probeDraft: (body: Record<string, unknown>) => post<TestEnvProbe>('/test-envs/probe-draft', body),
}

/** 后台任务队列：排队、执行、失败原因、取消与重试。 */
export const taskApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<QueueTaskPage>('/tasks', query as never),
  get: (id: number) => get<QueueTask>(`/tasks/${id}`),
  cancel: (id: number) => post<QueueTask>(`/tasks/${id}/cancel`),
  retry: (id: number) => post<QueueTask>(`/tasks/${id}/retry`),
  remove: (id: number) => del<{ id: number; deleted: boolean }>(`/tasks/${id}`),
}

/** 测试文档模板：按类别（测试用例 / 测试报告 / 测试点脑图）维护产出物模板。 */
export const templateApi = {
  list: (query: Record<string, unknown> = {}) =>
    get<DocTemplatePage>('/doc-templates', query as never),
  /** 能力页下拉：只返回同类模板。 */
  options: (kind: string) => get<{ list: DocTemplate[] }>('/doc-templates/options', { kind }),
  get: (id: number) => get<DocTemplate>(`/doc-templates/${id}`),
  create: (body: Record<string, unknown>) => post<DocTemplate>('/doc-templates', body),
  update: (id: number, body: Record<string, unknown>) =>
    put<DocTemplate>(`/doc-templates/${id}`, body),
  remove: (id: number) => del(`/doc-templates/${id}`),
  /** 上传模板文件：解析正文、列名与字体字号，同时把原文件留一份在本机。 */
  upload: (form: FormData) =>
    request<DocTemplateUpload>('/doc-templates/upload', { method: 'POST', body: form }),
  /** 清理取消保存后遗留在本机的上传原件（后端跳过仍被模板引用的文件）。 */
  cleanupUploads: (storedNames: string[]) =>
    post<{ removed: string[] }>('/doc-templates/uploads/cleanup', { storedNames }),
  /** 模板原文件下载直链：配合 window.open 用，Cookie 鉴权。 */
  fileDownloadUrl: (id: number) => `/api/v1/doc-templates/${id}/file`,
}
