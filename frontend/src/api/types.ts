import type { PagePayload } from './request'

export type Id = number

export interface Project {
  id: Id
  code: string
  name: string
  description: string
  owner: string
  status: string
  createdAt?: string
  updatedAt?: string
}

/** 项目版本：一个项目下可建多套版本，各测试资产通过 versionId 关联。 */
export interface ProjectVersion {
  id: Id
  projectId: Id
  versionCode: string
  name: string
  description: string
  status: string
  sortOrder: number
  assetCount?: number
  createdAt?: string
  updatedAt?: string
}

export interface ProjectVersionListResult {
  list: ProjectVersion[]
  statusOptions: Array<{ value: string; label: string }>
}

export interface Requirement {
  id: Id
  projectId: Id
  title: string
  content: string
  priority: string
  status: string
  source: string
  createdAt?: string
  updatedAt?: string
}
export type ModuleNodeType = 'module' | 'submodule' | 'feature'

export interface ModuleNode {
  id: Id
  projectId: Id
  parentId: Id | null
  nodeType: ModuleNodeType
  name: string
  code: string
  description: string
  sortOrder: number
  status: string
  depth: number
  path: string
  references: ModuleReferences
  children: ModuleNode[]
  createdAt?: string
  updatedAt?: string
}

export interface ModuleReferences {
  cases?: number
  bugs?: number
  knowledge?: number
  [key: string]: number | undefined
}

export interface ModuleOption {
  id: Id
  name: string
  nodeType: ModuleNodeType
  projectId: Id
  path: string
}

export interface CapabilityBinding {
  capability: string
  label: string
  promptCode: string | null
  enabled: boolean
  config: Record<string, unknown>
}

export interface TestCase {
  id: Id
  projectId: Id
  requirementId: Id | null
  moduleId: Id | null
  caseNo: string
  title: string
  caseType: string
  priority: string
  status: string
  preconditions: string
  steps: CaseStep[]
  expectedResult: string
  actualResult: string
  tags: string
  source: string
  aiTaskId: Id | null
  creator: string
  /** 类型专属附加字段：接口用例的请求方式/断言规则等，导出 xlsx 时按列展开。 */
  extra: Record<string, string>
  createdAt?: string
  updatedAt?: string
}

export interface CaseStep {
  action: string
  expected: string
}

/** 按功能点统计的覆盖矩阵行。 */
export interface ModuleCoverage {
  moduleId: Id
  name: string
  nodeType: ModuleNodeType
  parentId: Id | null
  path: string
  caseCount: number
  p0Count: number
  p1Count: number
  passCount: number
  failCount: number
  blockedCount: number
  notExecutedCount: number
}

/** AI 生成用例的采纳载荷。 */
export interface AdoptCaseItem {
  title: string
  caseType?: string
  priority?: string
  preconditions?: string
  steps?: CaseStep[]
  expectedResult?: string
  tags?: string
  moduleId?: Id | null
  requirementId?: Id | null
}

export interface Bug {
  id: Id
  projectId: Id
  moduleId: Id | null
  bugNo: string
  title: string
  description: string
  severity: string
  priority: string
  status: string
  bugType: string
  steps: string[]
  expectedResult: string
  actualResult: string
  assignee: string
  reporter: string
  version: string
  env: string
  module: string
  aiTaskId: Id | null
  createdAt?: string
  updatedAt?: string
}

export interface BugLog {
  id: Id
  bugId: Id
  fromStatus: string
  toStatus: string
  operator: string
  comment: string
  createdAt?: string
}

export interface KnowledgeDraft {
  id: Id
  title: string
  content: string
  summary: string
  category: string
  tags: string
  moduleId: Id | null
  sourceType: string
  producer: string
  reviewStatus: string
  refType: string
  refId: Id | null
  refLabel: string
  createdAt?: string
  updatedAt?: string
}

/** 知识分类：名称即分类值，内置分类不可删除。 */
export interface KnowledgeCategory {
  id: Id
  name: string
  sortOrder: number
  isSystem: boolean
  createdAt: string
}

export interface KnowledgeArticle {
  id: Id
  title: string
  content: string
  summary: string
  category: string
  tags: string
  moduleId: Id | null
  reviewStatus: string
  currentVersion: number
  draftVersion: number | null
  filePath: string
  fileChanged?: boolean
  createdAt?: string
  updatedAt?: string
}

export interface KnowledgeVersion {
  id: Id
  articleId: Id
  version: number
  title: string
  content: string
  summary: string
  category: string
  tags: string
  reviewStatus: string
  reviewer: string
  reviewComment: string
  changeLog: string
  createdAt?: string
}

export interface KnowledgeRef {
  id: Id
  articleId: Id
  refType: string
  refId: Id | null
  refLabel: string
  label?: string
  createdAt?: string
}

export interface ReportItem {
  id: Id
  reportId: Id
  section: string
  title: string
  content: string
  metric: Record<string, unknown>
  sortOrder: number
}

export interface Report {
  id: Id
  projectId: Id
  reportNo: string
  title: string
  reportType: string
  periodStart: string
  periodEnd: string
  summary: string
  status: string
  content: string
  creator: string
  aiTaskId: Id | null
  createdAt?: string
  updatedAt?: string
}

/** 报告图表数据：口径与工作台实时统计一致。 */
export interface ReportStats {
  reportId: Id
  projectId: Id
  periodStart: string
  periodEnd: string
  caseStatus: Record<string, number>
  casePriority: Record<string, number>
  caseType: Record<string, number>
  bugStatus: Record<string, number>
  bugSeverity: Record<string, number>
  metrics: {
    caseTotal: number
    passed: number
    failed: number
    blocked: number
    notExecuted: number
    passRate: number
    bugTotal: number
    openBugs: number
  }
}

export interface AiTask {
  id: Id
  capability: string
  moduleId: Id | null
  sessionId: Id | null
  status: string
  model: string
  promptCode: string
  latencyMs: number
  usage: Record<string, unknown>
  result: Record<string, unknown>
  errorCode: number | null
  errorMessage: string | null
  createdAt?: string
}

export interface AiSession {
  id: Id
  title: string
  module: string
  model: string
  createdAt?: string
  updatedAt?: string
  messages?: AiMessage[]
}

export interface AiMessage {
  id: Id
  sessionId: Id
  role: string
  content: string
  reasoning: string
  promptCode: string
  model: string
  tokensPrompt: number
  tokensCompletion: number
  createdAt?: string
}

export interface CapabilitySpec {
  code: string
  label: string
  goal: string
  keywords: string[]
  promptCode: string
  variables: string[]
  outputFormat: string
  mindmap: boolean
  adoptable: boolean
  /** core=闭环主能力，extended=提示词库里补充的能力 */
  category: string
  /** 卡片上展示的能力特性：导出 Excel/Word、采纳、入库、模板、历史产出等 */
  features: Array<{ key: string; label: string }>
  /** 该能力被使用的次数，用于能力卡片右上角标注使用频次。 */
  useCount?: number
  /** 提示词库里的标题 */
  promptTitle: string
  /** 提示词库里的说明 */
  promptDescription: string
  /** 提示词模板的必填变量 */
  requiredVariables: string[]
  /** 提示词模板的可选变量 */
  optionalVariables: string[]
}

export interface RouteResult {
  capability: string
  label: string
  score: number
  reason: string
  targetModuleId?: Id
  targetModulePath?: string
}

export interface ModuleRouteResult {
  moduleId: Id
  path: string
  score: number
  reason: string
}

export interface RouteResponse {
  capabilityRoutes: RouteResult[]
  moduleRoutes: ModuleRouteResult[]
  source: 'ai' | 'fallback'
}

/** 脑图节点：与后端 xmind_parser / testpoint-generation 的输出结构一致。 */
export interface MindNode {
  title: string
  children?: MindNode[]
  /** 补充说明，渲染在标题后面。 */
  note?: string
  /** 优先级标签，例如 P0。 */
  priority?: string
}

/** xmind 解析结果。 */
export interface XmindParseResult {
  tree: MindNode[]
  outlineText: string
  source: string
}

/** 文档导入结果：抽取出的纯文本。 */
export interface DocumentAttachment {
  attachmentId: string
  fileName: string
  kind: string
  text: string
  truncated: boolean
  chars: number
  note: string
}

/** 图片处理结果：已去水印。 */
export interface ImageAttachment {
  attachmentId: string
  fileName: string
  mediaType: string
  width: number
  height: number
  size: number
  watermarkApplied: boolean
  regions: Array<Record<string, unknown>>
  note: string
  previewUrl: string
}

/** 知识问答引用（[K1] 标记）。 */
export interface KnowledgeRef {
  marker: string
  articleId: Id
  title: string
  category?: string
}

export interface WorkbenchOverview {
  metrics: {
    caseTotal: number
    casePassRate: number
    bugTotal: number
    bugOpen: number
    requirementTotal: number
    knowledgeTotal: number
    reportTotal: number
    moduleTotal: number
    aiTaskTotal: number
    aiTaskSuccessRate: number
  }
  assets: {
    project: number
    requirement: number
    case: number
    bug: number
    knowledge: number
    report: number
    task: number
  }
  pendingKnowledge: number
  recentTasks: RecentTask[]
  shortcuts: QuickEntry[]
  caseStatus: Record<string, number>
  bugStatus: Record<string, number>
}

export interface RecentTask {
  id: Id
  taskType: string
  capability: string | null
  status: string
  moduleId: Id | null
  createdAt?: string
  finishedAt?: string | null
}

export interface QuickEntry {
  key: string
  label: string
  path: string
  group: string
}

export interface AiProfile {
  id: Id
  name: string
  provider: string
  protocol: string
  baseUrl: string
  model: string
  priority: number
  isDefault: boolean
  isActive: boolean
  isLocal: boolean
  apiKeyMasked: string
  hasApiKey: boolean
  /** 是否从本机环境变量读取 Key；开启后忽略密钥库里的 Key */
  useEnvKey: boolean
  /** 环境变量名，必须与本机变量同名 */
  envKeyName: string
  /** user=用户变量，system=系统变量 */
  envKeyScope: 'user' | 'system'
  /** 当前进程/注册表能否读到该变量 */
  envKeyPresent: boolean
  extra: Record<string, unknown>
  temperature: number
  maxTokens: number
  timeout: number
  sortOrder: number
  createdAt?: string
  updatedAt?: string
}

export interface SystemConfigItem {
  key: string
  value: string
  valueType: string
  scope: string
  description: string
  /** 取值说明：可填哪些值、每个值是什么意思；为空时回退到 description */
  hint: string
  isSystem: boolean
}

export interface AuditLog {
  id: Id
  actor: string
  action: string
  resourceType: string
  resourceId: Id | null
  resourceLabel: string
  detail: Record<string, unknown>
  result: string
  traceId: string
  clientIp: string
  createdAt?: string
}

/** 按优先级排好序的 AI 候选档案。 */
export interface AiCandidate {
  provider: string
  model: string
  profileId: Id | null
  profileName: string | null
  priority: number
  isLocal: boolean
  hasApiKey: boolean
  source: string
  sourceLabel: string
}

/** 当前生效的 AI 配置与各字段来源层。 */
export interface AiStatus {
  configured: boolean
  provider: string
  model: string
  baseUrl: string
  profileId: Id | null
  profileName: string | null
  priority: number
  isLocal: boolean
  locality: string
  egress: string
  hasApiKey: boolean
  sources: Record<string, string>
  sourceLabels: Record<string, string>
  candidates: AiCandidate[]
  error: string
}

/** 自检结果。 */
export interface SelfCheckResult {
  ok: boolean
  reason: string
  message: string
  hint: string
  latencyMs: number
  reply: string
  usage: Record<string, unknown>
  draft: boolean
  provider: string
  model: string
  baseUrl: string
  profileId: Id | null
  profileName: string | null
  isLocal: boolean
  hasApiKey: boolean
}

/** 服务端可用模型。 */
export interface AiModelInfo {
  id: string
  label: string
  size: number
  family: string
  parameterSize: string
  quantization: string
  source: string
}

/** 知识库存放状态。 */
export interface KnowledgeStorage {
  storagePath: string
  fileMode: string
  autoReindex: boolean
  articleCount: number
  fileCount: number
  exists: boolean
}

/** 重建知识库索引的结果。 */
export interface KnowledgeReindexResult {
  root: string
  scanned: number
  imported: number
  updated: number
  skipped: number
}

/** 迁移知识库存放目录的结果。 */
export interface KnowledgeMigrateResult extends KnowledgeReindexResult {
  copied: number
  source: string
  target: string
  removedFromSource: number
}

/** 待审池批量审核结果：failed 里带每条未处理的原因。 */
export interface BatchReviewResult {
  action: 'approve' | 'reject'
  succeeded: number[]
  failed: Array<{ draftId: number, code: number, message: string }>
  successCount: number
  failCount: number
}

/** 批量删除结果：skipped 是已删除或不存在因而没动到的 id。 */
export interface BatchDeleteResult {
  deleted: number[]
  count: number
  skipped: number[]
}

/** 本机数据库备份文件。 */
export interface DbBackup {
  name: string
  sizeBytes: number
  createdAt: string
  isSafety: boolean
}

/** 备份列表载荷。 */
export interface BackupListResult {
  list: DbBackup[]
  dir: string
  dbPath: string
}

/** 恢复结果：记录用了哪个备份、恢复前自动存的那份安全备份叫什么。 */
export interface RestoreResult {
  restored: string
  safetyBackup: string
  appliedMigrations: number[]
}

/** 测试环境配置：一套命名环境（应用服务器 + 数据库服务器），口令只回掩码。 */
export interface TestEnvApp {
  baseUrl: string
  healthPath: string
}

export interface TestEnvDb {
  type: string
  host: string
  port: number | null
  name: string
  username: string
  passwordMasked: string
  hasPassword: boolean
}

export interface TestEnv {
  id: number
  name: string
  description: string
  app: TestEnvApp
  db: TestEnvDb
  isDefault: boolean
  sortOrder: number
  createdAt: string
  updatedAt: string
}

export interface TestEnvListResult {
  list: TestEnv[]
  dbTypes: string[]
  dbDefaultPorts: Record<string, number>
}

/** 单套环境的健康探测结果；探测纯本机发起，不进大模型。 */
export interface TestEnvProbeItem {
  ok: boolean
  status: string
  httpStatus?: number
  latencyMs: number
  message: string
}

export interface TestEnvProbe {
  app: TestEnvProbeItem
  db: TestEnvProbeItem
  overall: 'healthy' | 'unhealthy'
}

/** AI 执行记录里的一条证据：产出物引用到的条目，已回库核对。 */
export interface AiRecordEvidence {
  kind: string
  ref: string
  title: string
  verified: boolean
  detail: string
}

/** 执行记录里的缺项：模型自述信息不足，或本次没填的可选变量。 */
export interface AiRecordMissing {
  kind: string
  label: string
  detail: string
}

/** 执行记录里的风险点：命中行为安全规则的条目。 */
export interface AiRecordRisk {
  rule: string
  level: string
  title: string
  detail: string
}

/** 一次 AI 能力执行的结构化记录。 */
export interface AiRecord {
  id: Id
  taskId: Id | null
  sessionId: Id | null
  capability: string
  capabilityLabel: string
  model: string
  summary: string
  /** 产出原文：仅详情接口返回，列表里为空串。 */
  outputText: string
  evidence: AiRecordEvidence[]
  missing: AiRecordMissing[]
  risks: AiRecordRisk[]
  riskLevel: string
  reviewRequired: boolean
  reviewStatus: string
  reviewNote: string | null
  reviewedAt: string | null
  /** 补四段：原始输入 / 路由命中 / 步骤轨迹 / 模型用量。 */
  input: Record<string, unknown>
  route: RouteResult[]
  steps: PipelineStep[]
  usage: Record<string, unknown>
  promptCode: string
  latencyMs: number | null
  createdAt: string
  updatedAt: string
}

/** 执行记录列表载荷，附带各复核状态的计数。 */
export interface AiRecordPage extends PagePayload<AiRecord> {
  stats: {
    pending: number
    approved: number
    rejected: number
  }
}

/** 全局搜索命中的一条结果。 */
export interface SearchHit {
  id: Id
  type: string
  typeLabel: string
  title: string
  no: string
  snippet: string
  updatedAt: string
  route: string
}

/** 全局搜索按实体类型分组的命中列表。 */
export interface SearchGroup {
  type: string
  typeLabel: string
  list: SearchHit[]
}

/** 全局搜索结果。 */
export interface SearchResult {
  keyword: string
  groups: SearchGroup[]
  total: number
}

/** 执行前预检里单个能力的体检结论。 */
export interface PreflightTask {
  capability: string
  label: string
  role: 'primary' | 'auxiliary'
  order: number
  score: number
  required: string[]
  optional: string[]
  provided: string[]
  missing: string[]
  missingOptional: string[]
  completeness: number
  blockers: string[]
  warnings: string[]
  ready: boolean
}

/** 执行前预检的整体结论。 */
export interface PreflightResult {
  source: string
  routes: RouteResult[]
  tasks: PreflightTask[]
  order: string[]
  primary: string
  auxiliary: string[]
  overallCompleteness: number
  modelReady: boolean
  ready: boolean
  blockers: string[]
  warnings: string[]
}

/** 顺序执行里一步的真实轨迹。 */
export interface PipelineStep {
  order: number
  capability: string
  label: string
  stepName: string
  status: 'success' | 'failed' | 'skipped' | 'pending' | 'running'
  taskId?: number
  recordId?: number
  model?: string
  latencyMs?: number
  summary?: string
  errorMessage?: string
  reviewRequired?: boolean
}

/** 多入口顺序执行的结果。 */
export interface PipelineResult {
  parentTaskId: number
  sessionId: number | null
  status: string
  steps: PipelineStep[]
  records: number[]
  parentRecordId: number
  output: string
  errorMessage: string
}

/** 编排入口里的一步。 */
export interface EntryWorkflowStep {
  capability: string
  name: string
  moduleId?: number | null
  variables: Record<string, unknown>
}

/** 工作台编排入口。 */
export interface WorkbenchEntry {
  id: Id
  code: string
  name: string
  capability: string
  description: string
  triggers: string[]
  required: string[]
  knowledge: string[]
  workflow: EntryWorkflowStep[]
  version: number
  status: string
  createdAt?: string
  updatedAt?: string
  /** 发布历史，仅详情接口返回。 */
  versions?: Array<{ id: Id; version: number; status: string; createdAt?: string }>
}

/** 案例验证沙箱里的一个案例。 */
export interface ValidationCase {
  id: Id
  name: string
  description: string
  inputText: string
  capability: string
  variables: Record<string, unknown>
  expectedRoutes: string[]
  expectedMissing: string[]
  expectedRules: string[]
  status: string
  lastResult: ValidationResult
  version: number
  createdAt?: string
  updatedAt?: string
}

/** 案例跑完之后的逐条比对结果。 */
export interface ValidationResult {
  passed: boolean
  checkedAt: string
  route: {
    source: string
    actual: Array<{ capability: string; score: number; reason: string }>
    expected: string[]
    missed: string[]
  }
  missing: {
    actual: AiRecordMissing[]
    expected: string[]
    missed: string[]
  }
  rules: {
    actual: string[]
    expected: string[]
    missed: string[]
  }
  errors: string[]
  output: string
  model: string
  latencyMs: number
  reviewRequired: boolean
}

/** 回归清单。 */
export interface Regression {
  id: Id
  projectId: Id
  title: string
  changeScope: string
  affectedModules: string
  version: string
  riskLevel: string
  status: string
  source: string
  content: string
  items?: RegressionItem[]
  stats?: Record<string, number>
  createdAt?: string
  updatedAt?: string
}

/** 回归清单里的一条待验证项。 */
export interface RegressionItem {
  id: Id
  regressionId: Id
  seq: number
  title: string
  module: string
  priority: string
  reason: string
  verifyTarget: string
  result: string
  remark: string
  executedBy: string | null
  executedAt: string | null
}

/** 通用实体关联。 */
export interface EntityRelation {
  id: Id
  sourceType: string
  sourceId: Id
  relationType: string
  targetType: string
  targetId: Id
  remark: string
  createdAt?: string
}

/** 提示词模板。 */
export interface PromptTemplate {
  code: string
  title: string
  description: string
  variables: string[]
  optional: string[]
  path: string
  mtime: number
  /** 该提示词被使用的次数，用于提示词库按使用频次排序。 */
  useCount?: number
  body?: string
}

/** 用例树节点：功能点 + 挂在它下面的用例。 */
export interface CaseTreeNode extends ModuleNode {
  cases: TestCase[]
  caseCount: number
  passCount: number
  failCount: number
}

export type { PagePayload }

/** 任务清单里的一条任务；子任务与派生字段只在取详情/列表时出现。 */
export interface Todo {
  id: Id
  todoNo: string
  parentId: number
  projectId: Id
  moduleId: Id
  title: string
  description: string
  status: string
  priority: string
  category: string
  tags: string
  dueDate: string
  progress: number
  isPinned: boolean
  sortOrder: number
  completedAt?: string
  createdAt?: string
  updatedAt?: string
  /** 后端算好的派生标记，前端直接用来上色，不再自己比日期。 */
  isOverdue?: boolean
  isDueToday?: boolean
  childDone?: number
  childTotal?: number
  children?: Todo[]
}

/** 任务清单顶部指标。 */
export interface TodoSummary {
  total: number
  active: number
  open: number
  doing: number
  done: number
  cancelled: number
  dueToday: number
  overdue: number
  pinned: number
  completionRate: number
}

export interface TodoStats {
  summary: TodoSummary
  byStatus: Record<string, number>
  categories: Array<{ category: string; total: number; active: number }>
}

export interface TodoPage extends PagePayload<Todo> {
  view: string
  stats: TodoSummary
}

/** 自动化脚本类别：web / app / api / performance。 */
export type AutomationCategory = 'web' | 'app' | 'api' | 'performance'

/** 脚本执行方式：直接执行 / pytest / JMeter 命令行。 */
export type AutomationRunMode = 'script' | 'pytest' | 'jmeter'

/** 执行状态。 */
export type AutomationRunStatus =
  | 'running'
  | 'success'
  | 'failed'
  | 'timeout'
  | 'error'

/** 一条自动化脚本资产。 */
export interface AutomationScript {
  id: Id
  scriptNo: string
  name: string
  category: AutomationCategory
  categoryLabel: string
  targetType: string
  framework: string
  language: string
  appPlatforms: string
  projectId: Id | null
  moduleId: Id | null
  description: string
  fileName: string
  content: string
  source: string
  runMode: AutomationRunMode
  entryCommand: string
  timeoutSeconds: number | null
  status: string
  lastRunStatus: string
  lastRunAt: string
  remark: string
  createdAt?: string
  updatedAt?: string
  /** 只有详情接口会带执行历史。 */
  runs?: AutomationRun[]
}

/** 一次执行的留痕。 */
export interface AutomationRun {
  id: Id
  runNo: string
  scriptId: Id
  scriptNo: string
  scriptName: string
  category: AutomationCategory
  categoryLabel: string
  status: AutomationRunStatus
  exitCode: number | null
  durationMs: number | null
  commandLine: string
  stdout: string
  stderr: string
  errorMessage: string
  startedAt?: string
  finishedAt?: string
  createdAt?: string
}

/** 四类目录的说明与统计。 */
export interface AutomationCategoryInfo {
  category: AutomationCategory
  label: string
  scriptCount: number
  runStats: { total: number; success: number; failed: number }
  runner: 'python' | 'jmeter'
}

/** 运行时体检结果：解释器与 JMeter 是否可用。 */
export interface AutomationRuntime {
  pythonInterpreter: string
  pythonInterpreterConfigured: boolean
  pythonInterpreterAvailable: boolean
  pythonTimeout: number
  pythonVersion: string
  jmeterHome: string
  jmeterCommand: string
  jmeterArgs: string[]
  jmeterAvailable: boolean
  platform: string
}

/** 脚本列表分页，额外带各分类计数。 */
export interface AutomationScriptPage extends PagePayload<AutomationScript> {
  categoryCounts: Record<string, number>
}

/** 执行历史分页，额外带各分类统计。 */
export interface AutomationRunPage extends PagePayload<AutomationRun> {
  categoryStats: Record<string, { total: number; success: number; failed: number }>
}

/** 测试文档模板类别：测试用例 / 测试报告 / 测试点脑图。 */
export type DocTemplateKind = 'case' | 'report' | 'mindmap'

/** 测试文档模板：约束 AI 产出的结构、风格，并决定 Excel 列顺序。 */
export interface DocTemplate {
  id: Id
  name: string
  kind: DocTemplateKind
  kindLabel: string
  description: string
  /** 导出 Excel 时的列顺序，只有测试用例类模板会用到。 */
  columns: string[]
  /** 模板正文，会渲染进提示词的 outputTemplate 变量。 */
  content: string
  /** 从上传文件解析出的字体、字号等版式说明。 */
  styleHint: string
  fileName: string
  fileType: string
  fileSize: number
  isDefault: boolean
  createdAt: string
  updatedAt: string
}

/** 上传模板文件的解析结果。 */
export interface DocTemplateUpload {
  fileName: string
  fileType: string
  fileSize: number
  content: string
  columns: string[]
  styleHint: string
}

/** 模板列表分页。 */
export type DocTemplatePage = PagePayload<DocTemplate>

/** 一条 SQL 分析资产：被分析的 SQL + 分析结论。 */
export interface SqlAnalysis {
  id: Id
  projectId: Id
  moduleId: Id | null
  sqlNo: string
  title: string
  sqlText: string
  analysis: string
  riskLevel: string
  riskLabel: string
  source: string
  tags: string
  createdAt?: string
  updatedAt?: string
}

/** 知识库问答助手的回答。 */
export interface KnowledgeAnswer {
  mode: 'kb' | 'llm' | 'hybrid'
  answer: string
  refs: Array<{ marker: string; articleId: Id; title: string; category?: string }>
  model: string
  latencyMs: number
  usage: Record<string, unknown>
  egress: string
  egressWarning: string
  reviewRequired: boolean
}

/** 测试数据生成：一种可生成的文件格式。 */
export interface TestDataFormat {
  ext: string
  label: string
  category: string
  mediaType: string
  minBytes: number
  builder: string
  mode: string
  modeLabel: string
  /** 下拉里展示的「.ext｜名称」。 */
  name: string
}

/** 测试数据生成的格式目录，一次性拉全后在前端模糊匹配。 */
export interface TestDataCatalog {
  total: number
  formats: TestDataFormat[]
  categories: Array<{ name: string; count: number }>
  modes: Array<{ mode: string; label: string; count: number }>
}

/** 批次里生成的单个文件。 */
export interface TestDataFile {
  id: Id
  batchId: Id
  fileName: string
  ext: string
  sizeBytes: number
  /** 是否逐字节命中请求大小。 */
  exact: boolean
  note: string
  relPath: string
  createdAt?: string
}

/** 一次生成 = 一个批次：同格式、同命名规则、N 个文件。 */
export interface TestDataBatch {
  id: Id
  projectId: Id
  moduleId: Id | null
  batchNo: string
  ext: string
  formatLabel: string
  buildMode: string
  /** 为空表示按该格式最小规格产出。 */
  sizeBytes: number | null
  fileCount: number
  createdFiles: number
  totalBytes: number
  namePrefix: string
  nameSuffix: string
  tokenMode: string
  tokenLabel: string
  dirName: string
  remark: string
  createdAt?: string
  updatedAt?: string
  files?: TestDataFile[]
}

export type TestDataBatchPage = PagePayload<TestDataBatch>
