/** 评测工作区字典：指标 / 分类 / 结论 / 失败原因的中文口径。
 *  与后端 `GET /api/v1/evals/meta` 及 `eval/gate.py` 保持一致，页面不再各写一份。
 */

export interface EvalMetricDefinition {
  key: string
  label: string
  unit: 'ratio' | 'ms' | 'micros' | 'step'
  direction: 'min' | 'max' | 'info'
  primary: boolean
  description: string
}

/** 18 项指标，顺序固定（设计 §五）。primary=预注册主指标，只有主指标能把门禁打成 fail。 */
export const EVAL_METRICS: EvalMetricDefinition[] = [
  { key: 'success_rate', label: '任务成功率', unit: 'ratio', direction: 'min', primary: true, description: 'passed / valid' },
  { key: 'error_rate', label: '执行失败率', unit: 'ratio', direction: 'max', primary: false, description: 'errors / total，区分模型不行与环境不行' },
  { key: 'judge_invalid_rate', label: '判定失效率', unit: 'ratio', direction: 'max', primary: false, description: 'judge_invalid / total，>5% 时 judge 指标不可用' },
  { key: 'hallucination_rate', label: '幻觉率', unit: 'ratio', direction: 'max', primary: true, description: '按用例计，三类信号取并集' },
  { key: 'severity_weighted_error', label: '风险加权错误率', unit: 'ratio', direction: 'max', primary: true, description: 'S0/S1/S2 权重 50/10/1' },
  { key: 'instruction_follow_rate', label: '指令遵循率', unit: 'ratio', direction: 'min', primary: false, description: 'Rubric 指令维度' },
  { key: 'tool_call_f1', label: '工具调用 F1', unit: 'ratio', direction: 'min', primary: false, description: '2PR/(P+R)' },
  { key: 'tool_arg_accuracy', label: '工具参数正确率', unit: 'ratio', direction: 'min', primary: false, description: '参数全对调用 / 实际调用' },
  { key: 'steps_avg', label: '平均步数', unit: 'step', direction: 'info', primary: false, description: 'Σ steps / valid' },
  { key: 'redundant_step_ratio', label: '冗余步数比', unit: 'ratio', direction: 'max', primary: false, description: '仅对有 expected_min 的用例' },
  { key: 'rag_recall@5', label: '召回@5', unit: 'ratio', direction: 'min', primary: false, description: 'macro 平均 hit/relevant' },
  { key: 'rag_ndcg@5', label: 'nDCG@5', unit: 'ratio', direction: 'min', primary: false, description: '排序质量，graded relevance 0–3' },
  { key: 'faithfulness', label: '忠实度', unit: 'ratio', direction: 'min', primary: false, description: '被检索片段支撑的断言占比' },
  { key: 'refusal_accuracy', label: '拒答正确率', unit: 'ratio', direction: 'min', primary: true, description: '该拒且拒 + 该答且答 / 相关样本' },
  { key: 'judge_agreement', label: '人机一致率', unit: 'ratio', direction: 'min', primary: false, description: '同判条数 / 有标注条数，κ 见明细' },
  { key: 'false_pass_rate', label: '判定放行错误率', unit: 'ratio', direction: 'max', primary: true, description: '人评 fail 却被 judge 放行' },
  { key: 'latency_p95', label: 'P95 时延', unit: 'ms', direction: 'max', primary: false, description: 'percentile(latency_ms, 95)' },
  { key: 'cost_per_1k', label: '每千次成本', unit: 'micros', direction: 'max', primary: false, description: '仅 provider 账单口径可进门禁' },
]

export const EVAL_METRIC_LABELS: Record<string, string> = Object.fromEntries(
  EVAL_METRICS.map((item) => [item.key, item.label]),
)

/** 指标 key → 单位。渲染数值必须先按单位选格式化器：只有 ratio 才能乘 100 加 %。 */
export const EVAL_METRIC_UNITS: Record<string, EvalMetricDefinition['unit']> = Object.fromEntries(
  EVAL_METRICS.map((item) => [item.key, item.unit]),
)

export interface EvalGateRuleDefinition {
  thresholdKey: string
  metricKey: string
  label: string
  direction: 'min' | 'max'
  defaultLimit: number
  primary: boolean
  defaultEnabled: boolean
  kappaMin?: number
  requireProviderCost?: boolean
}

/** 门禁可配置规则：默认开启的 6 项与后端 DEFAULT_THRESHOLDS 完全一致。 */
export const EVAL_GATE_RULES: EvalGateRuleDefinition[] = [
  { thresholdKey: 'successRate', metricKey: 'success_rate', label: '任务成功率', direction: 'min', defaultLimit: 0.85, primary: true, defaultEnabled: true },
  { thresholdKey: 'errorRate', metricKey: 'error_rate', label: '执行失败率', direction: 'max', defaultLimit: 0.05, primary: false, defaultEnabled: true },
  { thresholdKey: 'judgeInvalidRate', metricKey: 'judge_invalid_rate', label: '判定失效率', direction: 'max', defaultLimit: 0.05, primary: false, defaultEnabled: true },
  { thresholdKey: 'hallucinationRate', metricKey: 'hallucination_rate', label: '幻觉率', direction: 'max', defaultLimit: 0.05, primary: true, defaultEnabled: true },
  { thresholdKey: 'severityWeightedError', metricKey: 'severity_weighted_error', label: '风险加权错误率', direction: 'max', defaultLimit: 0.3, primary: true, defaultEnabled: true },
  { thresholdKey: 'latencyP95Ms', metricKey: 'latency_p95', label: 'P95 时延（ms）', direction: 'max', defaultLimit: 3000, primary: false, defaultEnabled: true },
  { thresholdKey: 'falsePassRate', metricKey: 'false_pass_rate', label: '判定放行错误率', direction: 'max', defaultLimit: 0.05, primary: true, defaultEnabled: false },
  { thresholdKey: 'refusalAccuracy', metricKey: 'refusal_accuracy', label: '拒答正确率', direction: 'min', defaultLimit: 0.8, primary: true, defaultEnabled: false },
  { thresholdKey: 'judgeAgreement', metricKey: 'judge_agreement', label: '人机一致率（κ≥0.6）', direction: 'min', defaultLimit: 0.8, primary: false, defaultEnabled: false, kappaMin: 0.6 },
  { thresholdKey: 'toolCallF1', metricKey: 'tool_call_f1', label: '工具调用 F1', direction: 'min', defaultLimit: 0.8, primary: false, defaultEnabled: false },
  { thresholdKey: 'instructionFollowRate', metricKey: 'instruction_follow_rate', label: '指令遵循率', direction: 'min', defaultLimit: 0.8, primary: false, defaultEnabled: false },
  { thresholdKey: 'ragRecallAtK', metricKey: 'rag_recall@5', label: '召回@5', direction: 'min', defaultLimit: 0.8, primary: false, defaultEnabled: false },
  { thresholdKey: 'ragNdcgAtK', metricKey: 'rag_ndcg@5', label: 'nDCG@5', direction: 'min', defaultLimit: 0.7, primary: false, defaultEnabled: false },
  { thresholdKey: 'faithfulness', metricKey: 'faithfulness', label: '忠实度', direction: 'min', defaultLimit: 0.8, primary: false, defaultEnabled: false },
  { thresholdKey: 'costPer1kMicros', metricKey: 'cost_per_1k', label: '每千次成本上限（micros）', direction: 'max', defaultLimit: 50000000, primary: false, defaultEnabled: false, requireProviderCost: true },
]

export const EVAL_CATEGORY_LABELS: Record<string, string> = {
  golden: '金题',
  bad: '坏例',
  adversarial: '对抗',
  regression: '回归',
}

export const EVAL_SEVERITY_LABELS: Record<string, string> = {
  S0: 'S0 致命（×50）',
  S1: 'S1 严重（×10）',
  S2: 'S2 一般（×1）',
}

export const EVAL_TARGET_TYPE_LABELS: Record<string, string> = {
  llm: '大模型',
  agent: 'Agent',
  rag: 'RAG',
  prompt: '提示词',
}

export const EVAL_VISIBILITY_LABELS: Record<string, string> = {
  private: '私有',
  holdout: '留出（禁止导出）',
  public: '公开',
}

export const EVAL_SUITE_STATUS_LABELS: Record<string, string> = {
  active: '启用',
  archived: '已归档',
}

export const EVAL_CASE_STATUS_LABELS: Record<string, string> = {
  active: '启用',
  archived: '已归档',
}

export const EVAL_RUN_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  finished: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export const EVAL_VERDICT_LABELS: Record<string, string> = {
  pass: '通过',
  fail: '未通过',
  skip: '跳过',
  error: '执行错误',
  invalid: '判定失效',
}

export const EVAL_CONCLUSION_LABELS: Record<string, string> = {
  pass: '通过',
  warn: '观察',
  insufficient: '证据不足',
  fail: '未通过',
}

export const EVAL_JUDGE_STRATEGY_LABELS: Record<string, string> = {
  single: '单评',
  double: '双评',
  panel: '多评仲裁',
}

export const EVAL_DIFFICULTY_LABELS: Record<number, string> = {
  1: '易',
  2: '中',
  3: '难',
}

/** 失败原因枚举（设计 §6.2 + 生命周期 infra.*）。 */
export const EVAL_FAILURE_REASON_LABELS: Record<string, string> = {
  'assert.contain_missing': '缺少必需内容',
  'assert.forbidden_hit': '命中禁止内容',
  'assert.regex_mismatch': '正则不匹配',
  'assert.too_long': '超出长度上限',
  'assert.schema_invalid': 'JSON 结构不符合',
  'assert.end_state_mismatch': '端态断言不符',
  'assert.hallucination': '无来源支撑（幻觉）',
  'rubric.must_pass': 'Rubric 必过项未过',
  'rubric.below_threshold': 'Rubric 总分低于阈值',
  'judge.arbitration': '双评分歧，仲裁判 fail',
  'judge.unparseable': 'judge 输出不可解析',
  'tool.missing_required': '缺少必需工具调用',
  'tool.forbidden_called': '调用了禁止工具',
  'tool.arg_mismatch': '工具参数不符',
  'trace.too_many_steps': '轨迹步数超限',
  'rag.no_recall': '检索未召回相关片段',
  'refusal.should_refuse': '该拒答却作答',
  'refusal.should_answer': '该作答却拒答',
  'infra.timeout': '执行超时',
  'infra.model_unavailable': '模型不可用',
  'infra.stale_run': '心跳超时被回收',
  'no_output': '未提供模型输出',
}

export const EVAL_EXPORT_FORMATS = [
  { value: 'md', label: 'Markdown（md）' },
  { value: 'xlsx', label: 'Excel（xlsx）' },
] as const

export function evalFailureText(reason: string): string {
  if (!reason) {
    return '-'
  }
  const head = reason.split(':')[0]
  return EVAL_FAILURE_REASON_LABELS[head] || reason
}