/**
 * AI 能力的变量表单定义。
 *
 * 与后端 `aitp/ai/capability_codes.py` 的 variables 一一对应：
 * 变量名必须完全一致，缺一个后端会返回 120102。
 */
export interface CapabilityField {
  name: string
  label: string
  type: 'text' | 'textarea' | 'number'
  placeholder?: string
  rows?: number
  defaultValue?: string
  /** 提示词优化针对的主输入框。 */
  primary?: boolean
}

const CASE_TYPES_HINT = '例如：functional,interface,boundary'

export const CAPABILITY_FORMS: Record<string, CapabilityField[]> = {
  requirement_review: [
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
  knowledge_qa: [
    { name: 'question', label: '问题', type: 'textarea', rows: 6, primary: true, placeholder: '想从知识库里查什么？' },
  ],
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
