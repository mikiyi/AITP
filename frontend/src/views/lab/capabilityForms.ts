/**
 * AI 能力的变量表单定义。
 *
 * 与后端 `aitp/ai/capability_codes.py` 的 variables 一一对应：
 * 变量名必须完全一致，缺一个后端会返回 120102。
 */

/** 下拉/多选候选项。 */
export interface CapabilityOption {
  label: string
  value: string
  /** 提交给模型时用的文本，缺省用 value。 */
  promptText?: string
  /** 仅在 dependsOn 的字段取对应值时出现，例如框架随目标类型联动。 */
  showFor?: Record<string, string[]>
}

export interface CapabilityField {
  name: string
  label: string
  type: 'text' | 'textarea' | 'number' | 'select' | 'multiselect'
  placeholder?: string
  rows?: number
  defaultValue?: string
  /** 提示词优化针对的主输入框。 */
  primary?: boolean
  /** select / multiselect 的候选项。 */
  options?: CapabilityOption[]
  /** 只有当依赖字段取到指定值时才显示该字段。 */
  dependsOn?: { field: string; values: string[] }
}

const CASE_TYPES_HINT = '例如：functional,interface,boundary'

/** 自动化脚本支持的开发语言（当前只开放 Python）。 */
const SCRIPT_LANGUAGES: CapabilityOption[] = [
  { label: 'Python', value: 'python', promptText: 'Python' },
]

/** 自动化脚本的目标类型。 */
const TARGET_TYPES: CapabilityOption[] = [
  { label: 'Web 网页端', value: 'web', promptText: 'web（网页 / Web 端）' },
  { label: '接口', value: 'api', promptText: 'api（接口）' },
  { label: 'App 移动端', value: 'app', promptText: 'app（移动端）' },
]

/**
 * 主流自动化框架，随目标类型与 APP 平台联动。
 * 框架清单与平台的绑定关系以 `backend/aitp/common/automation_frameworks.py` 为准：
 * 鸿蒙（HOS）用 Hypium，iOS / Android 用 Appium。
 */
const AUTOMATION_FRAMEWORKS: CapabilityOption[] = [
  { label: 'Selenium', value: 'selenium', promptText: 'selenium', showFor: { targetType: ['web'] } },
  { label: 'Playwright', value: 'playwright', promptText: 'playwright', showFor: { targetType: ['web'] } },
  { label: 'Requests', value: 'requests', promptText: 'requests（HTTP 接口）', showFor: { targetType: ['api'] } },
  { label: 'Unittest', value: 'unittest', promptText: 'unittest', showFor: { targetType: ['api'] } },
  {
    label: 'Appium（iOS / Android）',
    value: 'appium',
    promptText: 'appium（iOS / Android，需 Appium Server 与 Appium-Python-Client）',
    showFor: { targetType: ['app'], appPlatforms: ['ios', 'android'] },
  },
  {
    label: 'Hypium（鸿蒙 HOS）',
    value: 'hypium',
    promptText: 'hypium（HarmonyOS 鸿蒙，需 hdc 与 hypium 包）',
    showFor: { targetType: ['app'], appPlatforms: ['hos'] },
  },
  {
    label: 'UiAutomator2（Android）',
    value: 'uiautomator2',
    promptText: 'uiautomator2（仅 Android）',
    showFor: { targetType: ['app'], appPlatforms: ['android'] },
  },
  { label: 'Pytest', value: 'pytest', promptText: 'pytest', showFor: { targetType: ['web', 'app', 'api'] } },
]

/** App 目标平台。 */
const APP_PLATFORMS: CapabilityOption[] = [
  { label: 'iOS', value: 'ios', promptText: 'iOS' },
  { label: 'HOS（鸿蒙）', value: 'hos', promptText: 'HOS（HarmonyOS 鸿蒙）' },
  { label: 'Android', value: 'android', promptText: 'Android' },
]

/** 造数 SQL 的目标数据库方言：写错方言产出的语句直接执行不了。 */
const SQL_DIALECTS: CapabilityOption[] = [
  { label: 'MySQL 8', value: 'MySQL 8.0', promptText: 'MySQL 8.0' },
  { label: 'MariaDB', value: 'MariaDB 10.6', promptText: 'MariaDB 10.6' },
  { label: 'PostgreSQL 16', value: 'PostgreSQL 16', promptText: 'PostgreSQL 16' },
  { label: 'SQLite 3', value: 'SQLite 3', promptText: 'SQLite 3' },
  { label: 'Oracle 19c', value: 'Oracle 19c', promptText: 'Oracle 19c' },
  { label: 'SQL Server 2019', value: 'SQL Server 2019', promptText: 'SQL Server 2019' },
  { label: 'ClickHouse', value: 'ClickHouse', promptText: 'ClickHouse' },
]

export const CAPABILITY_FORMS: Record<string, CapabilityField[]> = {
  prompt_optimize: [
    { name: 'rawText', label: '原始提示词', type: 'textarea', rows: 10, primary: true, placeholder: '写下你现在的提示词，AI 会改写成结构化、可直接执行的版本' },
    { name: 'capability', label: '目标能力', type: 'text', placeholder: '例如：用例生成（可选）' },
    { name: 'capabilityGoal', label: '能力目标', type: 'text', placeholder: '例如：生成可执行测试用例（可选）' },
  ],
  requirement_confirm: [
    { name: 'requirement', label: '需求原文', type: 'textarea', rows: 10, primary: true, placeholder: '粘贴需求描述' },
    { name: 'projectName', label: '项目名称', type: 'text' },
  ],
  testpoint_generation: [
    { name: 'requirement', label: '需求原文', type: 'textarea', rows: 10, primary: true, placeholder: '粘贴需求描述' },
    { name: 'projectName', label: '项目名称', type: 'text' },
    { name: 'moduleCount', label: '建议模块数', type: 'number', defaultValue: '5' },
  ],
  case_generation: [
    { name: 'requirement', label: '需求原文', type: 'textarea', rows: 10, primary: true, placeholder: '粘贴需求描述' },
    { name: 'caseTypes', label: '用例类型', type: 'text', defaultValue: 'functional,interface,boundary,exception', placeholder: CASE_TYPES_HINT },
    { name: 'count', label: '生成条数', type: 'number', defaultValue: '10' },
    { name: 'projectName', label: '项目名称', type: 'text' },
  ],
  xmind_to_case: [
    { name: 'xmindOutline', label: 'XMind 大纲', type: 'textarea', rows: 10, primary: true, placeholder: '粘贴大纲文本，或上传 .xmind 文件后自动填充' },
    { name: 'caseTypes', label: '用例类型', type: 'text', defaultValue: 'functional,interface,boundary', placeholder: CASE_TYPES_HINT },
    { name: 'projectName', label: '项目名称', type: 'text' },
  ],
  bug_analysis: [
    { name: 'bugTitle', label: '缺陷标题', type: 'text', primary: true },
    { name: 'bugDescription', label: '缺陷描述', type: 'textarea', rows: 5 },
    { name: 'reproSteps', label: '复现步骤', type: 'textarea', rows: 4 },
    { name: 'expectedResult', label: '预期结果', type: 'textarea', rows: 3 },
    { name: 'actualResult', label: '实际结果', type: 'textarea', rows: 3 },
    { name: 'env', label: '环境', type: 'text', placeholder: '例如：测试环境 Chrome 130 / Windows 11' },
    { name: 'version', label: '版本', type: 'text' },
  ],
  log_analysis: [
    { name: 'logContent', label: '日志内容', type: 'textarea', rows: 12, primary: true, placeholder: '粘贴异常日志或堆栈' },
    { name: 'errorType', label: '异常类型', type: 'text', placeholder: '例如：NullPointerException' },
    { name: 'context', label: '上下文', type: 'textarea', rows: 3 },
  ],
  sql_analysis: [
    { name: 'sqlContent', label: 'SQL 语句', type: 'textarea', rows: 8, primary: true },
    { name: 'schemaDesc', label: '表结构说明', type: 'textarea', rows: 6 },
    { name: 'intent', label: '查询意图', type: 'textarea', rows: 3 },
  ],
  regression_generation: [
    { name: 'changeScope', label: '变更范围', type: 'textarea', rows: 6, primary: true },
    { name: 'affectedModules', label: '受影响模块', type: 'textarea', rows: 4 },
    { name: 'projectName', label: '项目名称', type: 'text' },
  ],
  report_generation: [
    { name: 'projectName', label: '项目名称', type: 'text' },
    { name: 'reportType', label: '报告类型', type: 'text', defaultValue: 'summary' },
    { name: 'periodStart', label: '统计开始', type: 'text', placeholder: 'YYYY-MM-DD' },
    { name: 'periodEnd', label: '统计结束', type: 'text', placeholder: 'YYYY-MM-DD' },
    { name: 'metricsJson', label: '指标数据', type: 'textarea', rows: 6, primary: true, placeholder: 'JSON 文本' },
    { name: 'caseSummary', label: '用例情况', type: 'textarea', rows: 5 },
    { name: 'bugSummary', label: '缺陷情况', type: 'textarea', rows: 5 },
  ],
  project_progress_board: [
    { name: 'projectName', label: '项目名称', type: 'text' },
    { name: 'progressData', label: '进度数据', type: 'textarea', rows: 8, primary: true },
    { name: 'period', label: '统计周期', type: 'text', placeholder: '例如：2026 第 45 周' },
    { name: 'milestones', label: '里程碑', type: 'textarea', rows: 5 },
  ],
  api_test_design: [
    { name: 'projectName', label: '项目名称', type: 'text' },
    { name: 'apiDescription', label: '接口说明', type: 'textarea', rows: 8, primary: true, placeholder: '接口路径、参数、返回结构' },
    { name: 'baseUrl', label: '服务地址', type: 'text', placeholder: '例如：https://api.example.com' },
  ],
  test_plan: [
    { name: 'projectName', label: '项目名称', type: 'text' },
    { name: 'period', label: '统计周期', type: 'text', placeholder: '例如：2026 第 45 周' },
    { name: 'requirementSummary', label: '需求概要', type: 'textarea', rows: 6, primary: true },
    { name: 'modules', label: '模块清单', type: 'textarea', rows: 5, placeholder: '每行一个模块' },
  ],
  case_review: [
    { name: 'cases', label: '待评审用例', type: 'textarea', rows: 10, primary: true, placeholder: '粘贴用例清单' },
    { name: 'requirement', label: '需求原文', type: 'textarea', rows: 8 },
    { name: 'criteria', label: '评审判据', type: 'textarea', rows: 4, placeholder: '本次评审重点关注什么' },
  ],
  bug_classification: [
    { name: 'bugTitle', label: '缺陷标题', type: 'text', primary: true },
    { name: 'bugDescription', label: '缺陷描述', type: 'textarea', rows: 6 },
    { name: 'modulePath', label: '可能归属的模块', type: 'text', placeholder: '选择功能点后自动带入，也可手动填写' },
  ],
  risk_assessment: [
    { name: 'projectName', label: '项目名称', type: 'text' },
    { name: 'changeScope', label: '变更范围', type: 'textarea', rows: 6, primary: true },
    { name: 'metricsJson', label: '质量数据', type: 'textarea', rows: 5, placeholder: '用例通过率、缺陷数等' },
  ],
  automation_script: [
    { name: 'projectName', label: '项目名称', type: 'text' },
    { name: 'targetType', label: '目标类型', type: 'select', options: TARGET_TYPES, defaultValue: 'web' },
    { name: 'framework', label: '自动化框架', type: 'select', options: AUTOMATION_FRAMEWORKS, defaultValue: 'selenium' },
    { name: 'language', label: '开发语言', type: 'select', options: SCRIPT_LANGUAGES, defaultValue: 'python' },
    {
      name: 'appPlatforms',
      label: 'APP 平台',
      type: 'multiselect',
      options: APP_PLATFORMS,
      dependsOn: { field: 'targetType', values: ['app'] },
    },
    { name: 'scenario', label: '测试场景', type: 'textarea', rows: 8, primary: true, placeholder: '要自动化的业务流程与校验点' },
    { name: 'baseUrl', label: '服务地址', type: 'text', placeholder: '例如：https://www.example.com' },
    { name: 'extraRequirements', label: '补充要求', type: 'textarea', rows: 4, placeholder: '例如：失败截图、等待策略、账号信息' },
  ],
  performance_script: [
    { name: 'projectName', label: '项目名称', type: 'text' },
    { name: 'scenario', label: '压测场景', type: 'textarea', rows: 8, primary: true, placeholder: '压测的业务流程与接口' },
    { name: 'baseUrl', label: '服务地址', type: 'text', placeholder: '例如：https://api.example.com' },
    { name: 'threads', label: '并发线程数', type: 'number', defaultValue: '20' },
    { name: 'duration', label: '持续时间（秒）', type: 'number', defaultValue: '60' },
    { name: 'rampUp', label: '爬坡时间（秒）', type: 'number', defaultValue: '10' },
    { name: 'dataFile', label: '数据文件', type: 'text', placeholder: 'CSV 路径，没有可留空' },
    { name: 'extraRequirements', label: '补充要求', type: 'textarea', rows: 4, placeholder: '例如：登录前置、思考时间、断言点' },
  ],
  test_data_generation: [
    { name: 'dialect', label: '目标数据库', type: 'select', options: SQL_DIALECTS, defaultValue: 'MySQL 8.0' },
    {
      name: 'intent',
      label: '数据需求描述',
      type: 'textarea',
      rows: 8,
      primary: true,
      placeholder: '例如：给订单表造 30 条数据，覆盖待支付/已支付/已取消三种状态，其中 2 条金额为 0，1 条下单时间是三年前',
    },
    { name: 'schemaDesc', label: '表结构说明', type: 'textarea', rows: 6, placeholder: '粘贴建表语句或字段清单，包含主键、外键与唯一约束' },
    { name: 'rowCount', label: '期望条数', type: 'number', defaultValue: '20' },
    { name: 'projectName', label: '项目名称', type: 'text' },
  ],
  exploratory_web: [
    { name: 'projectName', label: '项目名称', type: 'text' },
    { name: 'targetUrl', label: '目标地址', type: 'text', placeholder: '例如：http://127.0.0.1:8760/' },
    { name: 'maxSteps', label: '最大步数', type: 'number', defaultValue: '20' },
    { name: 'maxSeconds', label: '最大时长（秒）', type: 'number', defaultValue: '180' },
  ],
}

/**
 * 跨能力通用变量的中文兜底标签：上下文装配等变量不在单个能力的表单里定义，
 * 但会出现在顺序执行的步骤表单中，不能露出英文变量名。
 */
const VARIABLE_LABEL_FALLBACK: Record<string, string> = {
  projectName: '项目名称',
  requirement: '需求原文',
  caseTypes: '用例类型',
  count: '生成条数',
  outputTemplate: '输出模板',
  contextModules: '上下文模块',
  contextCases: '上下文用例',
  contextBugs: '上下文缺陷',
  contextKnowledge: '上下文知识',
  moduleCount: '建议模块数',
  moduleId: '功能点',
  targetUrl: '目标地址',
  maxSteps: '最大步数',
  maxSeconds: '最大时长（秒）',
}

/** 取某个能力下变量对应的中文标签，用于卡片上展示输入项。 */
export function variableLabel(capability: string, variable: string): string {
  const field = (CAPABILITY_FORMS[capability] ?? []).find((item) => item.name === variable)
  if (field) {
    return field.label
  }
  return VARIABLE_LABEL_FALLBACK[variable] ?? variable
}

/** 取能力的主输入变量名；没有标注时退回第一个 textarea。 */
export function primaryFieldOf(capability: string): string {
  const fields = CAPABILITY_FORMS[capability] ?? []
  const marked = fields.find((item) => item.primary)
  if (marked) {
    return marked.name
  }
  const textarea = fields.find((item) => item.type === 'textarea')
  return textarea ? textarea.name : (fields[0]?.name ?? '')
}

/** 当前取值下需要展示的字段：带 dependsOn 的字段按依赖字段的取值决定是否出现。 */
export function visibleFields(
  capability: string,
  values: Record<string, string>,
): CapabilityField[] {
  return (CAPABILITY_FORMS[capability] ?? []).filter((field) => {
    if (!field.dependsOn) {
      return true
    }
    return field.dependsOn.values.includes(values[field.dependsOn.field] ?? '')
  })
}

/**
 * 当前取值下可选的候选项：候选项的 showFor 决定它在哪几种取值下出现。
 * 多选字段传的是数组，数组为空表示还没选，不按平台收窄候选项；
 * 非空时只要命中 allowed 里任一项就保留（例如勾了 iOS 就还看得到 Appium）。
 */
export function visibleOptions(
  field: CapabilityField,
  values: Record<string, string | string[]>,
): CapabilityOption[] {
  const options = field.options ?? []
  return options.filter((option) => {
    if (!option.showFor) {
      return true
    }
    return Object.entries(option.showFor).every(([name, allowed]) => {
      const current = values[name]
      if (Array.isArray(current)) {
        return current.length === 0 || current.some((item) => allowed.includes(item))
      }
      return allowed.includes(String(current ?? ''))
    })
  })
}

/** 候选项提交给模型时用的文本。 */
export function optionPromptText(field: CapabilityField, value: string): string {
  const option = (field.options ?? []).find((item) => item.value === value)
  return option?.promptText ?? value
}
