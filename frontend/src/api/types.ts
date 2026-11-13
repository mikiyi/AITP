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
  /** 所属项目版本，列表与详情都会带版本号/版本名称。 */
  versionId: Id | null
  versionCode?: string
  versionName?: string
  reqNo: string
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
  /** 所属项目版本。 */
  versionId: Id | null
  versionCode?: string
  versionName?: string
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

/** 覆盖矩阵的用例统计（行内数字含全部下级功能点）。 */
export interface ModuleCoverageStat {
  caseCount: number
  p0Count: number
  p1Count: number
  passCount: number
  failCount: number
  blockedCount: number
  notExecutedCount: number
}

/** 按功能点统计的覆盖矩阵行。 */
export interface ModuleCoverage extends ModuleCoverageStat {
  moduleId: Id
  name: string
  nodeType: ModuleNodeType
  parentId: Id | null
  path: string
}

/** 功能点覆盖矩阵：未挂功能点的用例单独返回，便于和总数对账。 */
export interface ModuleCoverageResult {
  list: ModuleCoverage[]
  unassigned: ModuleCoverageStat
}

/**
 * GAP-29：一条重复判定，与后端 dedup_service.DuplicateHit.as_dict() 一一对应。
 * `index` 对齐的是模型原始产出数组，不能拿来定位采纳行（采纳行会过滤掉没标题的条目）。
 */
export interface LabDedupHit {
  index: number
  title: string
  similarity: number
  matchedRef?: string
  matchedTitle?: string
  reason?: string
  scope?: string
  /** existing=撞库内已有用例，batch=撞本批次内的另一条。 */
  source?: string
  /** 增量模式下是否已被摘走；标记模式恒为 false。 */
  dropped: boolean
  /** 摘走后能否由用户找回（疑似档不可找回，只标注）。 */
  restorable: boolean
}

/** GAP-29：一次生成的去重结论（done 帧与历史记录里的 dedup）。 */
export interface LabDedupConclusion {
  capability: string
  /** 计数单位：条 / 步 等。 */
  unit?: string
  mode: string
  modeLabel?: string
  applicable?: boolean
  compared?: number
  existingTotal?: number
  hitCount?: number
  suspectCount?: number
  droppedCount?: number
  /** 只回传前若干条命中，超出部分看 hitCount。 */
  items?: LabDedupHit[]
  dropped?: Array<Record<string, unknown>>
  notes?: string[]
  /** 去重环节自身出错时给出原因，不影响产出。 */
  error?: string
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
  /** 所属项目版本。 */
  versionId: Id | null
  versionCode?: string
  versionName?: string
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
  /** 由轮次失败用例转缺陷时写入的关联用例。 */
  linkedCaseIds?: Id[]
  linkedCases?: BugLinkedCase[]
  createdAt?: string
  updatedAt?: string
}

/** 缺陷详情里回显的关联用例。 */
export interface BugLinkedCase {
  id: Id
  caseNo: string
  title: string
  status: string
  priority: string
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

/** 问答助手回答里引用的知识条目。 */
export interface AssistantRef {
  marker: string
  articleId: Id
  title: string
  category?: string
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
  sourceTypeLabel: string
  producer: string
  reviewStatus: string
  refType: string
  refId: Id | null
  refLabel: string
  answeredAt: string | null
  answerMode: string
  answerModel: string
  reviewRequired: boolean
  refs: AssistantRef[]
  articleId: Id | null
  /** 探索性产物审核通过后转出的缺陷 id。 */
  bugId?: Id | null
  /** 仅「加入知识库」返回值：同一问同答同时间只入池一次。 */
  duplicated?: boolean
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
  projectId?: Id | null
  /** 全局知识（projectId 为空）的来源项目；仅提升过的条目有值。 */
  sourceProjectId?: Id | null
  sourceProjectName?: string
  /** 只读「复用项目」列：被哪些项目引用（ref_type='project'），列表接口批量附带。 */
  referencedProjects?: Array<{ projectId: number; projectName: string }>
  promotedAt?: string | null
  promotedBy?: string
  isGlobal?: boolean
  /** 当前条目被哪些项目引用（refType='project'）。 */
  projectRefs?: KnowledgeRef[]
  moduleId: Id | null
  sourceType: string
  sourceTypeLabel: string
  answeredAt: string | null
  answerMode: string
  answerModel: string
  reviewRequired: boolean
  refs: AssistantRef[]
  /** 问答助手来源条目的自动整理状态：'' 不适用 / pending 待整理 / running 整理中 / done 已整理完。 */
  organizeStatus: '' | 'pending' | 'running' | 'done'
  organizeTaskId: Id | null
  organizeAt: string | null
  organizeTask?: {
    id: Id
    status: string
    statusLabel: string
    errorMessage: string
  } | null
  reviewStatus: string
  /** 自动整理合并后被并入的条目 id；非空表示本条已归档，仅作溯源。 */
  mergedInto?: Id | null
  mergedIntoTitle?: string
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
  /** 该提案通过后要并入本条目的重复问答 id。 */
  mergeTargets?: Id[]
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

/** 报告片段：来源项目维护一份，引用报告只登记使用关系。 */
export interface ReportFragment {
  id: Id
  projectId: Id
  sourceProjectName: string
  title: string
  section: string
  content: string
  sourceReportId: Id | null
  sourceReportTitle?: string
  sourceNote: string
  isGlobal: boolean
  createdBy: string
  useCount: number
  createdAt?: string
  updatedAt?: string
}

export interface Report {
  id: Id
  projectId: Id
  /** 所属项目版本。 */
  versionId: Id | null
  versionCode?: string
  versionName?: string
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
  /** 报告详情带出的复用片段（只带引用关系，正文来自源片段）。 */
  fragments?: ReportFragment[]
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

/** 指标趋势：按天/周聚合，空档补 0。 */
export interface ReportTrend {
  projectId: Id
  granularity: 'day' | 'week'
  granularityLabel: string
  startDate: string
  endDate: string
  buckets: string[]
  series: Array<{ key: string; label: string; values: number[] }>
  totals: {
    executions: number
    passRate: number
    newBugs: number
    closedBugs: number
    p0p1Open: number
  }
}

/** 版本对比中的单侧指标。 */
export interface VersionCompareSide {
  versionId: Id
  versionCode: string
  versionName: string
  caseTotal: number
  executed: number
  passed: number
  executeRate: number
  passRate: number
  bugTotal: number
  openBugs: number
  p0p1Open: number
  defectDensity: number
  runs: number
  gateStatus: string
  gateStatusLabel: string
  gateConclusion: string
}

export interface VersionCompare {
  projectId: Id
  base: VersionCompareSide
  target: VersionCompareSide
  metrics: Array<{
    key: string
    label: string
    unit: string
    direction: 'higher' | 'lower' | 'neutral'
    higherIsBetter: boolean
    neutral: boolean
    base: number
    target: number
    delta: number
  }>
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

/** 问答助手回答里的知识库引用（[K1] 标记）。 */
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
  requirementStatus?: Record<string, number>
  bugSeverity?: Record<string, number>
  aiUsage?: Array<{ capability: string; label: string; count: number }>
  recentReports?: Array<{
    id: number
    title: string
    reportType: string
    status: string
    createdAt: string
  }>
}

/** 首页卡片定制项：固定区始终显示，可定制区按 order 排序、visible 控制显隐。 */
export interface HomeCardItem {
  key: string
  label: string
  description: string
  width: 'half' | 'full' | string
  locked: boolean
  visible: boolean
  order: number
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
  /** GAP-30：档案列表直接带回单价现值（微元 / 1000 token，0 = 未配价）。 */
  priceInputMicros?: number
  priceOutputMicros?: number
  priceCurrency?: string
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

/** 本机 SQLite 连通性快照，来自后端健康检查。 */
export interface DatabaseStatus {
  ok: boolean
  path: string
  exists: boolean
  sizeBytes: number
  version: number
  journalMode: string
  latencyMs: number
  error: string
}

/** 写锁计数快照：`db/write_guard.write_stats()` 的返回，健康检查直接透传。 */
export interface DbWriteStats {
  commits: number
  waits: number
  slowWaits: number
  busyErrors: number
  busyRetries: number
  timeouts: number
  totalWaitMs: number
  maxWaitMs: number
  registeredDbs?: number
  slowWaitThresholdMs?: number
  busyRetryLimit?: number
}

export interface HealthStatus {
  status: string
  appVersion: string
  aiConfigured: boolean
  serverTime: string
  database: DatabaseStatus
  /** 进程内写锁计数：跨进程看不到别人的排队，只报本进程这一份。 */
  dbWrite: DbWriteStats
  /** 档案数量与实际可用候选数；空 profileId 的虚拟档案不算「用户配了」。 */
  aiProfileCount: number
  aiCandidateCount: number
  /** 知识库待审草稿数。 */
  knowledgePendingDrafts: number
  /** 任务队列积压：排队与执行中。 */
  taskQueue: { queued: number; running: number }
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
  /** 问答助手打开时预选的回答模式：kb / llm / hybrid。 */
  assistantDefaultMode: string
  articleCount: number
  fileCount: number
  exists: boolean
}

/** 重建知识库索引的结果。 */
export interface KnowledgeReindexResult {
  root: string
  mode: string
  scanned: number
  imported: number
  updated: number
  revived: number
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
  /** 定时任务生成的整包（受保留份数约束）。 */
  isAuto?: boolean
  /** db=单库快照，bundle=整包（库 + 附件/脚本/生成物/密钥/知识正文）。 */
  kind?: 'db' | 'bundle'
  files?: number
}

/** 自动备份计划与最近一次执行结果。 */
export interface AutoBackupPlan {
  enabled: boolean
  frequency: 'daily' | 'weekly'
  frequencyLabel: string
  time: string
  weekday: number
  retention: number
  minFreeMb: number
  nextRunAt: string
  lastRunAt: string
  lastStatus: 'running' | 'success' | 'skipped' | 'failed' | ''
  lastStatusLabel: string
  lastMessage: string
  autoCount: number
  autoBytes: number
  /** -1 表示无法读取磁盘剩余空间。 */
  diskFreeBytes: number
}

/** 保存自动备份计划的入参。 */
export interface AutoBackupForm {
  enabled: boolean
  frequency: 'daily' | 'weekly'
  time: string
  weekday: number
  retention: number
  minFreeMb: number
}

/** 备份列表载荷。 */
export interface BackupListResult {
  list: DbBackup[]
  bundles: DbBackup[]
  dir: string
  dbPath: string
  auto: AutoBackupPlan
}

/** 恢复结果：记录用了哪个备份、恢复前自动存的那份安全备份叫什么。 */
export interface RestoreResult {
  restored: string
  safetyBackup: string
  appliedMigrations: number[]
  safetyBundle?: string
  restoredFiles?: Record<string, string | number>
}

/** 测试环境配置：一套命名环境（多台应用服务器 + 数据库服务器），口令只回掩码。 */
export interface TestEnvAppServer {
  baseUrl: string
  healthPath: string
  /** 可选监控地址（node_exporter /metrics 或 JSON 指标），远程服务器才有 CPU/内存/磁盘 */
  metricsUrl?: string
}

/** app 保留首台字段兼容旧调用方；新代码读 servers。 */
export interface TestEnvApp extends TestEnvAppServer {
  servers?: TestEnvAppServer[]
}

export interface TestEnvDb {
  type: string
  host: string
  port: number | null
  name: string
  username: string
  passwordMasked: string
  hasPassword: boolean
  metricsUrl?: string
}

export interface TestEnv {
  id: number
  name: string
  kind: string
  kindLabel: string
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
  dbTypeLabels?: Record<string, string>
  dbDefaultPorts: Record<string, number>
  kinds?: { value: string; label: string }[]
}

/** 服务器资源指标：本机由后端直采，远程读监控地址；无指标时 available=false。 */
export interface ServerMetricsMemory {
  percent: number | null
  usedBytes: number | null
  totalBytes: number | null
}

export interface ServerMetricsDisk {
  name: string
  percent: number | null
  usedBytes: number | null
  totalBytes: number | null
}

export interface ServerMetrics {
  available: boolean
  source: string
  cpuPercent: number | null
  memory: ServerMetricsMemory
  disks: ServerMetricsDisk[]
  message: string
  sampledAt?: string
}

/** 单套环境的健康探测结果；探测纯本机发起，不进大模型。 */
export interface TestEnvProbeItem {
  ok: boolean
  status: string
  httpStatus?: number
  latencyMs: number
  message: string
  metrics?: ServerMetrics
  /** 应用服务器探测时带上该台的地址，便于多台逐条展示 */
  baseUrl?: string
}

export interface TestEnvAppProbe extends TestEnvProbeItem {
  /** 多台应用服务器的逐台结果；app 本身的 ok/message 是汇总 */
  servers?: TestEnvProbeItem[]
}

export interface TestEnvProbe {
  app: TestEnvAppProbe
  db: TestEnvProbeItem
  overall: 'healthy' | 'unhealthy'
}

/** 一条资源曲线：时间戳（毫秒）与对应值。 */
export interface EnvMetricSeries {
  ts: number[]
  values: number[]
}

export interface EnvHealthSide {
  cpu: EnvMetricSeries
  memory: EnvMetricSeries
  disks: Record<string, EnvMetricSeries>
}

/** 服务端持续采样得到的环境健康历史，进页面直接渲染。 */
export interface EnvHealthHistoryItem {
  envId: number
  probedAt: string
  probe: TestEnvProbe | null
  app: EnvHealthSide
  /** 多台应用服务器逐台曲线，顺序与 app.servers 一致 */
  appServers?: EnvHealthSide[]
  db: EnvHealthSide
}

export interface EnvHealthHistoryResult {
  windowMs: number
  intervalMs: number
  serverTime: string
  list: EnvHealthHistoryItem[]
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

/** 外发脱敏命中的一类敏感信息：只给类型与数量，不含原文。 */
export interface MaskTypeCount {
  code: string
  label: string
  count: number
}

/** 一次 AI 调用的外发脱敏结果。 */
export interface MaskInfo {
  /** 云端模型是否开启脱敏（系统配置 ai.mask.enabled）。 */
  enabled: boolean
  /** 本次是否真的替换过内容；本地模型始终为 false。 */
  applied: boolean
  types: MaskTypeCount[]
  count: number
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
  /** 本次调用的外发脱敏结果；没有记录时表示未脱敏。 */
  mask: MaskInfo
  createdAt: string
  updatedAt: string
}

/** 执行记录列表载荷，附带各复核状态的计数。 */
export interface AiRecordPage extends PagePayload<AiRecord> {
  stats: {
    pending: number
    notRequired: number
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
  /** 数据所属项目；知识条目可能为空（全局知识）。 */
  projectId?: number | null
  /** 项目名，用于提示命中来自其他项目并支持点击切换。 */
  projectName?: string
}

/** 全局搜索按实体类型分组的命中列表。 */
export interface SearchGroup {
  type: string
  typeLabel: string
  /** 该类实体的真实命中总数（可能大于 list.length）。 */
  total: number
  list: SearchHit[]
}

/** 全局搜索结果。 */
export interface SearchResult {
  keyword: string
  groups: SearchGroup[]
  /** 实际返回的条目数。 */
  total: number
  /** 四类实体合计的真实命中数。 */
  matched: number
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

/** 编排资产里的一步。 */
export interface EntryWorkflowStep {
  capability: string
  name: string
  moduleId?: number | null
  variables: Record<string, unknown>
}

/** 可复用的编排资产。 */
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

/** 回归清单的执行覆盖口径：分母为 0 时对应比率是 0，不是 null。 */
export interface RegressionCoverage {
  total: number
  executed: number
  executedRate: number
  autoTotal: number
  autoExecuted: number
  autoRate: number
  autoShare: number
  evidenced: number
  evidenceRate: number
}

/** 回归清单。 */
export interface Regression {
  id: Id
  projectId: Id
  title: string
  changeScope: string
  affectedModules: string
  version: string
  versionId?: Id
  versionCode?: string
  versionName?: string
  riskLevel: string
  status: string
  source: string
  content: string
  items?: RegressionItem[]
  stats?: Record<string, number>
  coverage?: RegressionCoverage
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
  /** GAP-33：auto 由服务端真跑回写，manual 由执行人回写结论。 */
  execMode?: string
  caseId?: Id | null
  envId?: Id | null
  /** 真跑产生的证据快照 id，用于下钻。 */
  evidenceId?: Id | null
  /** GAP-35：手工回写的证据摘要，备注不算证据。 */
  logSummary?: string
  requestSummary?: string
  responseSummary?: string
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
  /** 正文是否被自定义过（true 时 body 来自库里，不是磁盘默认模板）。 */
  customized?: boolean
  /** 磁盘默认正文，用于「恢复默认」前后对照。 */
  defaultBody?: string
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
  versionId?: Id | null
  versionCode?: string
  versionName?: string
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
  /** 本周到期（含今天与已逾期，口径与快捷视图一致）。 */
  dueWeek: number
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
  envId: Id | null
  description: string
  fileName: string
  content: string
  source: string
  runMode: AutomationRunMode
  entryCommand: string
  timeoutSeconds: number | null
  /** 结果回写：覆盖用例的圈定方式（按用例编号 / 按标签）。 */
  caseMatchMode?: AutomationMatchMode
  caseMatchValue?: string
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
  envId?: Id | null
  taskId?: Id | null
  /** 执行时挂载的测试轮次。 */
  testRunId?: Id | null
  /** 回写结论：parsed / partial / fallback / skipped / failed。 */
  writebackStatus?: string
  writebackLabel?: string
  writebackSummary?: string
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
  /** 只有执行详情接口会带用例级回写明细。 */
  caseResults?: AutomationCaseResult[]
  /** 只有执行详情接口会带本次执行目录里的产物（截图、日志等）。 */
  artifacts?: AutomationArtifact[]
}

/** 自动化执行产物：脚本写入 AITP_RUN_DIR 的文件。 */
export interface AutomationArtifact {
  path: string
  name: string
  size: number
  modifiedAt: string
}

/** 自动化执行结果按用例回写的明细。 */
export interface AutomationCaseResult {
  id: Id
  caseId: Id
  caseNo: string
  testName: string
  result: 'passed' | 'failed' | 'blocked' | 'skipped'
  message: string
  /** 映射来源：case_no / script。 */
  mappedBy: string
  runCaseId: Id | null
  testRunId: Id | null
}

export type AutomationMatchMode = 'case_no' | 'tag'

/** 四类目录的说明与统计。 */
export interface AutomationCategoryInfo {
  category: AutomationCategory
  label: string
  scriptCount: number
  runStats: { total: number; success: number; failed: number }
  runner: 'python' | 'jmeter'
}

/** 自动化框架及其在本机的就绪情况，权威清单在后端 automation_frameworks.py。 */
export interface AutomationFrameworkRuntime {
  code: string
  label: string
  targets: string[]
  platforms: string[]
  platformLabels: string[]
  package: string
  packageVersion: string
  packageInstalled: boolean
  command: string
  commandPath: string
  commandAvailable: boolean
  ready: boolean
  setupHint: string
  /** 本次体检使用的 Python 解释器，以及按它给出的安装命令。 */
  interpreter?: string
  installCommand?: string
  /** 装包之后还要执行的准备命令（下载浏览器内核、启动 Appium 等）。 */
  setupCommands?: string[]
  /** 未就绪时缺的具体依赖，供前端逐条展示。 */
  missing?: string[]
  /** Playwright 浏览器内核是否已下载。 */
  browserAvailable?: boolean
  /** Appium Server 地址与连通性。 */
  serverUrl?: string
  serverAvailable?: boolean
}

/** 一个本机 Python 解释器及其框架依赖体检结果。 */
export interface AutomationInterpreterCandidate {
  path: string
  source: string
  version: string
  isCurrent: boolean
  packages: Record<string, string>
  installedCount: number
  totalPackages: number
}

/** 解释器体检清单。 */
export interface AutomationInterpreters {
  current: string
  candidates: AutomationInterpreterCandidate[]
}

/** App 自动化已连接的移动设备：Android 走 adb，鸿蒙走 hdc。 */
export interface AutomationDevice {
  serial: string
  state: string
}

/** 运行时体检结果：解释器、JMeter 与各自动化框架是否可用。 */
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
  /** Appium 服务地址与连通性，执行 App 脚本前必须先起来。 */
  appiumServerUrl?: string
  appiumServerAvailable?: boolean
  appiumServerReady?: boolean
  adbCommand?: string
  adbAvailable?: boolean
  androidDevices?: AutomationDevice[]
  hdcCommand?: string
  hdcAvailable?: boolean
  hosDevices?: AutomationDevice[]
  /** Playwright 浏览器内核是否已下载。 */
  playwrightBrowsersAvailable?: boolean
  platform: string
  frameworks?: AutomationFrameworkRuntime[]
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
  /** 原文件在本机的落盘名；空串说明这条模板没上传过文件，不可下载。 */
  storedName: string
  isDefault: boolean
  createdAt: string
  updatedAt: string
}

/** 上传模板文件的解析结果。 */
export interface DocTemplateUpload {
  fileName: string
  fileType: string
  fileSize: number
  /** 原文件已留存在本机的落盘名，保存模板时要一起回传。 */
  storedName: string
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
  /** 所属项目版本。 */
  versionId: Id | null
  versionCode?: string
  versionName?: string
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
  refs: AssistantRef[]
  model: string
  latencyMs: number
  usage: Record<string, unknown>
  egress: string
  egressWarning: string
  reviewRequired: boolean
  /** 纯知识库回答不调模型，没有脱敏信息。 */
  mask?: MaskInfo
}

/** 问答链路的实际外发评估：任一候选会出本机即为可能外发。 */
export interface AssistantEgress {
  externalPossible: boolean
  egress: string
  candidates: Array<{ provider: string; model: string; isLocal: boolean; egress: string }>
}

/** 外发前站内检查用的一条问答助手正式条目。 */
export interface AssistantPreviewFile {
  articleId: Id
  title: string
  relativePath: string
  absolutePath: string
  exists: boolean
  fromFile: boolean
  truncated: boolean
  content: string
}

export interface AssistantPreview {
  fileMode: string
  storagePath: string
  articleCount: number
  files: AssistantPreviewFile[]
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

/** 测试数据脱敏规则（GAP-39）：字段级，按列名配置。 */
export interface TestDataMaskRule {
  type: 'mask' | 'hash' | 'random' | 'enum' | 'redact'
  keepPrefix?: number
  keepSuffix?: number
  char?: string
  length?: number
  salt?: string
  charset?: 'digits' | 'alnum'
  prefix?: string
  values?: string[]
  replacement?: string
}

/** 可复用的脱敏方案；projectId 为空表示全局方案。 */
export interface TestDataMaskScheme {
  id: Id
  projectId: Id | null
  name: string
  description: string
  rules: Record<string, TestDataMaskRule>
  version: number
  isDefault: boolean
  createdAt?: string
  updatedAt?: string
}

export interface TestDataSampleDetection {
  types: string[]
  hits: number
}

/** 样本池里的一个样本：库里只有列结构与脱敏副本，没有原始样本。 */
export interface TestDataSample {
  id: Id
  projectId: Id
  name: string
  sourceFormat: string
  originalFilename: string
  rowCount: number
  columnCount: number
  columns: string[]
  detection: Record<string, TestDataSampleDetection>
  rules: Record<string, TestDataMaskRule>
  schemeId: Id | null
  schemeVersion: number
  retentionDays: number
  expiresAt: string
  status: string
  createdAt?: string
  updatedAt?: string
  /** 导入时各列实际替换的单元格数 */
  maskedCounts?: Record<string, number>
}

/** 项目级资产包（GAP-41）导出清单与导入结果。 */
export interface ProjectPackageManifest {
  format: string
  version: number
  project: { code: string; name: string; description?: string }
  scope: { history: boolean; evidence: boolean; attachments: boolean }
  counts: Record<string, number>
}

export interface ProjectPackagePreview {
  manifest: ProjectPackageManifest
  counts: Record<string, number>
  conflict: boolean
  existingProject: { id: Id; name: string } | null
}

export interface ProjectPackageImportResult {
  projectId: Id
  code: string
  counts: Record<string, number>
}

/** 外部平台只读导入（GAP-40）。 */
export interface ExternalImportPlatform {
  key: string
  label: string
  kinds: string[]
  kindsLabel: Array<{ key: string; label: string }>
  defaultMapping: Record<string, Record<string, string>>
}

export interface ExternalImportCatalog {
  platforms: ExternalImportPlatform[]
  kinds: ExternalImportPlatform[]
  defaultMapping: Record<string, Record<string, string>>
}

export interface ExternalImportConfig {
  id: Id
  projectId: Id
  platform: string
  baseUrl: string
  username: string
  productId: string
  serverUsername: string
  extra: Record<string, unknown>
  hasPassword: boolean
  passwordMasked: string
  createdAt: string
  updatedAt: string
}

export interface ExternalImportMapping {
  id: Id
  projectId: Id
  platform: string
  name: string
  mapping: Record<string, Record<string, string>>
  isDefault: boolean
  createdAt: string
  updatedAt: string
}

export interface ExternalImportPreview {
  counts: Record<string, number>
  samples: Record<string, string[]>
  errors: string[]
}

export interface ExternalImportResult {
  batchId: Id
  status: string
  fetched: Record<string, number>
  imported: Record<string, number>
  skipped: Record<string, number>
  errors: Record<string, string[]>
}

export interface ExternalImportBatch {
  id: Id
  projectId: Id
  platform: string
  kinds: string
  status: string
  fetched: Record<string, number>
  imported: Record<string, number>
  skipped: Record<string, number>
  errors: Record<string, string[]>
  createdAt: string
}

/** 后台任务队列中的一条任务。 */
export interface QueueTask {
  id: Id
  kind: string
  kindLabel: string
  title: string
  status: string
  statusLabel: string
  priority: number
  attempts: number
  maxAttempts: number
  progress: number
  cancelRequested: boolean
  payload: Record<string, unknown>
  result: Record<string, unknown>
  errorMessage: string
  startedAt: string | null
  finishedAt: string | null
  createdAt: string
  updatedAt: string
}

export interface QueueTaskPage extends PagePayload<QueueTask> {
  stats: Record<string, number>
  kinds: { value: string; label: string }[]
}

/** 测试计划：本次要测什么，不记录结果。 */
export interface TestPlan {
  id: Id
  projectId: Id
  versionId: Id | null
  versionCode?: string
  versionName?: string
  name: string
  description: string
  owner: string
  defaultEnvId: Id | null
  scope: Record<string, unknown>
  status: string
  runCount?: number
  runs?: TestRun[]
  createdAt?: string
  updatedAt?: string
}

/** 轮次内冻结的一条用例快照。 */
export interface TestRunCase {
  id: Id
  runId: Id
  caseId: Id
  seq: number
  caseNo: string
  title: string
  caseType: string
  priority: string
  moduleId: Id | null
  module: string
  requirementId: Id | null
  preconditions: string
  steps: CaseStep[]
  extra?: Record<string, string>
  expectedResult: string
  result: string
  actualResult: string
  remark: string
  bugId: Id | null
  executedBy: string
  executedAt: string | null
}

/** 接口用例执行的一条断言明细。 */
export interface InterfaceAssertion {
  name: string
  passed: boolean
  expected: string
  actual: string
  message: string
}

/** 接口用例一次真实请求的执行结果。 */
export interface InterfaceExecution {
  result: string
  passed: boolean
  statusCode: number | null
  durationMs: number
  message: string
  request: {
    method: string
    url: string
    headers: Record<string, string>
    body: string
    bodyTruncated: boolean
  }
  response: {
    status: number | null
    headers: Record<string, string>
    body: string
    bodyTruncated: boolean
  }
  assertions: InterfaceAssertion[]
  executionId?: number
  runCase?: TestRunCase
  run?: TestRun
  envId?: Id
  envName?: string
}

/** 接口用例批量执行的结果摘要。 */
export interface InterfaceBatchResult {
  runId: Id
  envId: Id
  envName: string
  total: number
  passed: number
  failed: number
  details: Array<{
    runCaseId: Id
    caseId: Id
    caseNo: string
    title: string
    result: string
    statusCode: number | null
    durationMs: number | null
    message: string
  }>
}

/** 测试轮次：一次具体执行。 */
export interface TestRun {
  id: Id
  planId: Id | null
  projectId: Id
  versionId: Id | null
  versionCode?: string
  versionName?: string
  envId: Id | null
  name: string
  planName?: string
  env: string
  envName?: string
  envKind?: string
  owner: string
  status: string
  remark: string
  total: number
  passed: number
  failed: number
  blocked: number
  skipped: number
  notExecuted: number
  startedAt: string | null
  finishedAt: string | null
  /** 轮次类型 smoke/full/special/regression */
  roundType?: string
  goal?: string
  plannedStart?: string
  plannedEnd?: string
  scopePreset?: string
  seq?: number
  stats?: {
    total: number
    counts: Record<string, number>
    byPriority: Record<string, Record<string, number>>
    passRate: number
  }
  cases?: TestRunCase[]
  createdAt?: string
  updatedAt?: string
}

/** 轮次类型 / 范围预设 / 轮次模板元数据（GET /test-runs/round-meta）。 */
export interface RoundTypeMeta {
  value: string
  label: string
  goal: string
  preset: string
}
export interface RoundPresetMeta {
  value: string
  label: string
  desc: string
}
export interface RoundTemplateMeta {
  value: string
  label: string
  rounds: number
}
export interface RoundMeta {
  types: RoundTypeMeta[]
  presets: RoundPresetMeta[]
  templates: RoundTemplateMeta[]
}
/** 范围预设预圈结果（POST /test-plans/{id}/rounds/preview-scope）。 */
export interface RoundScopePreview {
  preset: string
  count: number
  caseIds: number[]
  cases: Array<{ id: number; caseNo: string; title: string; priority: string; caseType: string; status: string }>
}

/** 追溯矩阵的一行：需求维度的覆盖与执行情况。 */
export interface TraceabilityRow {
  requirementId: Id
  reqNo: string
  title: string
  requirementStatus: string
  priority: string
  caseCount: number
  executedCount: number
  passed: number
  failed: number
  blocked: number
  notExecuted: number
  passRate: number
  openBugs: number
  lastExecutedAt: string
  coverageStatus: string
  coverageLabel: string
}

export interface TraceabilityResult {
  projectId: Id
  versionId: Id | null
  runId: Id | null
  summary: {
    requirementTotal: number
    coveredRequirements: number
    uncoveredRequirements: number
    caseCount: number
    executedCount: number
    passed: number
    failed: number
    openBugs: number
    coverageRate: number
    passRate: number
  }
  list: TraceabilityRow[]
}

/** 发布准入的一项检查。 */
export interface ReleaseGateCheck {
  key: string
  label: string
  actual: number
  threshold: number
  passed: boolean
  unit: string
}

export interface ReleaseGateResult {
  status: string
  metrics: Record<string, unknown>
  checks: ReleaseGateCheck[]
  blocking: string[]
}

export interface ReleaseGate {
  id: Id
  projectId: Id
  versionId: Id | null
  versionCode?: string
  versionName?: string
  runId: Id | null
  name: string
  status: string
  thresholds: Record<string, number>
  result: ReleaseGateResult
  conclusion: string
  createdBy: string
  createdAt?: string
  updatedAt?: string
}

/** CI 触发令牌状态；明文 token 只在生成响应里返回一次。 */
export interface CiEventOption {
  value: string
  label: string
}

export interface CiSettings {
  enabled: boolean
  name: string
  tokenPrefix: string
  projectId: Id | null
  lastUsedAt: string | null
  createdAt: string | null
  baseUrl: string
  endpoint: string
  events: CiEventOption[]
  token?: string
}

/** Webhook 通知渠道；projectId 为空表示全局生效。 */
export interface NotificationChannel {
  id: Id
  name: string
  projectId: Id | null
  webhookUrl: string
  events: string[]
  enabled: boolean
  createdAt?: string
  updatedAt?: string
}

export interface NotificationChannelList {
  list: NotificationChannel[]
  eventOptions: CiEventOption[]
}

export interface NotificationLog {
  id: Id
  channelId: Id | null
  channelName: string
  projectId: Id | null
  event: string
  title: string
  status: 'pending' | 'success' | 'failed'
  statusLabel: string
  attempts: number
  httpStatus: number | null
  responseText: string
  errorMessage: string
  taskId: Id | null
  createdAt?: string
  finishedAt: string | null
}

export interface NotificationLogPage extends PagePayload<NotificationLog> {
  /** 发送记录的事件筛选项（含只能手动触发的「测试通知」）。 */
  eventOptions: CiEventOption[]
}

export interface CiRunResult {
  runId: number
  runName: string
  projectId: number
  versionId: number | null
  envId: number | null
  total: number
  status: string
  url: string
  queued: boolean
  tasks: QueueTask[]
  automationQueued: number
  warnings: string[]
}

/**
 * GAP-30：一次调用的成本结论。金额一律「微元」整数，
 * 未配单价时 costMicros 是 null（表示「算不出钱」），不是 0（表示「确实没花钱」）。
 */
export interface AiCostReport {
  priced: boolean
  costMicros: number | null
  costYuan: number | null
  currency: string
  promptTokens: number
  completionTokens: number
  totalTokens: number
  estimated?: boolean
  priceInputMicros?: number
  priceOutputMicros?: number
  priceCurrency?: string
  budget?: AiBudgetStatus
}

/** GAP-30：一段时间的用量汇总。 */
export interface AiCostSpend {
  calls: number
  pricedCalls: number
  unpricedCalls: number
  costMicros: number
  costYuan: number | null
  promptTokens: number
  completionTokens: number
  totalTokens: number
}

/** GAP-30：预算软阈值本身（0 = 不设限）。 */
export interface AiBudgetConfig {
  dailyMicros: number
  dailyYuan: number | null
  monthlyMicros: number
  monthlyYuan: number | null
  warnRatio: number
  callTokens: number
  callTokensCapability: number
  callTokensProject: number
  /** 预算只提示、永不拦截。 */
  hardBlock: boolean
}

/** GAP-30：某个周期「花费 vs 软阈值」的判断结果。 */
export interface AiCostPeriod extends AiCostSpend {
  name: string
  limitMicros: number
  limitYuan: number | null
  ratioPercent: number
  warnRatio: number
  level: string
  levelLabel: string
}

/** GAP-30：预算与用量的当前状态，前端只做提示。 */
export interface AiBudgetStatus {
  currency: string
  budget: AiBudgetConfig
  today: AiCostPeriod
  month: AiCostPeriod
  callTokenLimit: number
  callTokenHint: string
  warnings: string[]
  blocking: boolean
  unpricedCalls: number
}

/** GAP-30：成本视图的分组拆分行（按能力或按项目，两类字段共用一个形状）。 */
export interface AiCostBreakdown {
  capability?: string
  capabilityLabel?: string
  projectId?: number | null
  projectName?: string
  costMicros: number
  costYuan: number | null
  calls: number
  totalTokens: number
}

/** GAP-30：成本视图读侧：总量 + 三个维度拆分 + 预算状态。 */
export interface AiCostSummary {
  day: string
  monthLabel: string
  currency: string
  today: AiCostSpend
  month: AiCostSpend
  scoped: AiCostSpend
  byCapability: AiCostBreakdown[]
  byProject: AiCostBreakdown[]
  byProfile: AiProfilePrice[]
  budget: AiBudgetStatus
  /** 只在请求带 days>0 时返回。 */
  daily?: Array<{ day: string } & AiCostSpend>
  filter?: { projectId: number | null; capability: string }
}

/** GAP-30：一个档案的单价与该档案的累计用量。 */
export interface AiProfilePrice {
  profileId: number
  profileName: string
  provider: string
  model: string
  priced: boolean
  priceInputMicros: number
  priceOutputMicros: number
  priceCurrency: string
  priceUnit: string
  priceInputYuan: number | null
  priceOutputYuan: number | null
  calls: number
  costMicros: number
  costYuan: number | null
}

/** GAP-30：单价表整体口径。 */
export interface AiPriceTable {
  currency: string
  microsPerYuan: number
  priceUnit: string
  profiles: AiProfilePrice[]
}

/** GAP-32：证据快照里的一条断言结果。 */
export interface EvidenceAssertion {
  name?: string
  passed?: boolean
  expected?: string
  actual?: string
  message?: string
  [key: string]: unknown
}

/** GAP-32：证据快照里固化的附件，previewUrl 是当时的可回放地址。 */
export interface EvidenceAttachment {
  attachmentId: number
  fileName: string
  kind: string
  mediaType: string
  suffix: string
  previewUrl: string
  note: string
}

/**
 * GAP-32：一次执行留下的证据快照。
 * 字段与后端 execution_protocol.ExecutionResult.as_dict() 一一对应，那里是唯一出口。
 */
export interface ExecutionEvidence {
  sourceType: string
  sourceTypeLabel: string
  sourceRowId: number | null
  evidenceId: number | null
  projectId: number | null
  caseId: number | null
  runId: number | null
  runCaseId: number | null
  regressionId: number | null
  regressionItemId: number | null
  bugId: number | null
  envId: number | null
  envName: string
  title: string
  result: string
  resultLabel: string
  durationMs: number
  assertions: EvidenceAssertion[]
  assertionSummary: string
  requestSummary: string
  responseSummary: string
  logSummary: string
  attachments: EvidenceAttachment[]
  sourceRef: Record<string, unknown>
  traceId: string
  errorMessage: string
  executedBy: string
  executedAt: string
}

/** GAP-35：报告下钻用的一个失败样本。 */
export interface ReportFailureSample {
  sampleKey: string
  runCaseId: number
  runId: number
  runName: string
  caseId: number | null
  caseNo: string
  title: string
  priority: string
  module: string
  result: string
  resultLabel: string
  executedBy: string
  executedAt: string
  durationMs: number
  bugId: number | null
  bugNo: string
  /** 有没有可下钻的证据快照。 */
  hasEvidence: boolean
  evidenceId: number | null
  sourceType: string
  sourceTypeLabel: string
  sourceRowId: number | null
  evidenceUrl: string
  drillUrl: string
  attachments: EvidenceAttachment[]
  attachmentCount: number
  failurePoints: string[]
  failureSummary: string
  evidenceSummary: string
  failureTotal: number
}

/** GAP-35：报告的失败样本清单；total 是统计范围内的全部，selected 是已勾选的。 */
export interface ReportFailureSamples {
  reportId: number
  scope: string
  total: number
  selected: number[]
  list: ReportFailureSample[]
  evidenceCount: number
  hint: string
  evidenceSection: string
}

/** 全量验证提交后的任务回执；结果要到任务详情里取。 */
export interface ValidationRunTask {
  queued: boolean
  taskId: Id
  task: QueueTask
}

/** 全量验证的汇总口径。 */
export interface ValidationRunSummary {
  total: number
  passed: number
  failed: number
  passRate: number
  results: Array<Record<string, unknown>>
}

/** 示例项目注入结果：各表插了多少行。 */
export interface DemoInjectResult {
  code: string
  counts: Record<string, number>
}

/** 首次引导状态。 */
export interface OnboardingState {
  completed: boolean
  reopenedAt: string
  /** 前端只看这个字段决定要不要弹引导。 */
  showGuide: boolean
}

/** GAP-43 执行矩阵：环境 × 端/浏览器，一次调度逐格真实执行。 */
export interface ExecutionMatrix {
  id: number
  projectId: number
  matrixNo: string
  name: string
  mode: string
  modeLabel: string
  runId: number | null
  runName: string
  envIds: number[]
  scriptIds: number[]
  targetMode: string
  targets: Array<{ target: string; label: string }>
  status: string
  statusLabel: string
  cellTotal: number
  cellDone: number
  summary: Record<string, unknown>
  prodConfirmedBy: string
  prodConfirmedAt: string | null
  errorMessage: string
  createdAt: string
  updatedAt: string
}

/** 矩阵的一格：一条环境 × 端/浏览器组合。 */
export interface MatrixCell {
  id: number
  envId: number | null
  envName: string
  envKind: string
  target: string
  targetLabel: string
  browser: string
  status: string
  statusLabel: string
  total: number
  passed: number
  failed: number
  errorMessage: string
  startedAt: string | null
  finishedAt: string | null
}

export interface MatrixDetail {
  matrix: ExecutionMatrix
  cells: MatrixCell[]
}

/** 一格内一条用例的结论与断言明细。 */
export interface MatrixCellResult {
  result: string
  statusCode: number | null
  durationMs: number | null
  message: string
  assertions: InterfaceAssertion[]
}

/** 同用例跨格对比行：结论矩阵 + 差异归因 + 断言差异。 */
export interface MatrixCompareRow {
  caseId: number | null
  caseNo: string
  title: string
  runCaseId: number | null
  classification: string
  classificationLabel: string
  cells: Record<string, MatrixCellResult>
  assertionDiffs: Array<{
    name: string
    cells: Record<string, { passed: boolean; expected: string; actual: string; message: string }>
  }>
  bugId: number | null
}

export interface MatrixCompare {
  matrix: ExecutionMatrix
  cells: MatrixCell[]
  rows: MatrixCompareRow[]
  stats: {
    commonPass: number
    commonFailure: number
    environmentSpecific: number
    targetSpecific: number
    mixed: number
  }
}

/** 普遍失败一键转缺陷的结果。 */
export interface MatrixBugResult {
  created: Array<{ caseNo: string; title: string; bugId: number; evidenceIds: number[] }>
  skipped: string[]
  total: number
}

/** GAP-44 数据留存：单类型保留天数与到期动作。 */
export interface RetentionItem {
  dataType: string
  dataTypeLabel: string
  days: number
  action: string
  actionLabel: string
  actions: Array<{ value: string; label: string }>
  dueCount?: number
  protected?: Array<{ id: number; title: string; referencedBy: string }>
}

export interface RetentionSettings {
  items: RetentionItem[]
  autoCleanup: boolean
  lastRunAt: string
}

/** 归档包裹：一次清理动作的 zip。 */
export interface ArchiveBundle {
  id: number
  bundleNo: string
  dataType: string
  action: string
  itemCount: number
  periodFrom: string
  periodTo: string
  filePath: string
  fileSize: number
  createdBy: string
  createdAt: string
}

export interface ArchiveItem {
  id: number
  bundleId: number
  dataType: string
  refId: number
  title: string
  happenedAt: string
  summary: string
}

export interface ArchiveBundleDetail {
  bundle: ArchiveBundle
  items: ArchiveItem[]
}

export interface RetentionCleanupResult {
  queued?: boolean
  task?: unknown
  dryRun?: boolean
  totalCleaned?: number
  totalProtected?: number
  types?: Array<{
    dataType: string
    dataTypeLabel: string
    due: number
    archived: number
    cleaned: number
    skipped: number
    action: string
    protected?: Array<{ id: number; title: string; referencedBy: string }>
  }>
}

/** GAP-45 Web 探索性测试配置。 */
export interface ExploratorySettings {
  maxSteps: number
  maxSeconds: number
  perStepTimeout: number
  perStepScreenshots: number
  recordSelectors: boolean
  inputMasking: boolean
  screenshotDir: string
  logSliceDir: string
  logSources: Array<{ name: string; path: string; source: string }>
  logWindowSeconds: number
  mcpCommand: string
  pinnedTools: string[]
}

export interface ExploratoryOption {
  value: string
  label: string
}

export interface ExploratoryMeta {
  drivers: ExploratoryOption[]
  statuses: ExploratoryOption[]
  anomalyTypes: ExploratoryOption[]
  pinnedTools: string[]
  defaults: ExploratorySettings
}

export interface ExplorationPlanStep {
  action: string
  selector?: string
  url?: string
  value?: string
}

export interface ExplorationSession {
  id: Id
  sessionNo: string
  projectId: Id
  name: string
  targetUrl: string
  driver: string
  status: string
  taskId: Id | null
  maxSteps: number
  maxSeconds: number
  stepTotal: number
  stepDone: number
  anomalyCount: number
  draftCount: number
  degraded: boolean
  summary: Record<string, unknown>
  errorMessage: string
  startedAt: string | null
  finishedAt: string | null
  createdBy: string
  createdAt?: string
  updatedAt?: string
}

export interface ExplorationStep {
  id: Id
  sessionId: Id
  stepNo: number
  action: string
  selector: string
  pageUrl: string
  inputMasked: string
  snapshotSummary: string
  observation: string
  isAnomaly: boolean
  anomalyType: string
  detectedAt: string
  lastOkAt: string
  evidenceId: Id | null
  draftId: Id | null
  traceId: string
  durationMs: number
  detail: Record<string, unknown>
  createdAt?: string
}

export interface ExplorationSessionDetail {
  session: ExplorationSession
  steps: ExplorationStep[]
}
