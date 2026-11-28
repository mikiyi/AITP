/** AI 质量评测 API（V2.8.0 / 设计 §四，34 条路由）。
 *  与 ci.ts 同口径单独导出，类型就近定义；不污染 index.ts / types.ts。
 */
import { del, get, getBlob, post, put, type BlobResult, type PagePayload } from './request'

export interface EvalRunBrief {
  id: number
  status: string
  total: number
  passed: number
  failed: number
  successRate: number | null
  gateConclusion: string
  finishedAt: string | null
  createdAt: string
}

export interface EvalSuite {
  id: number
  projectId: number
  name: string
  targetType: string
  description: string
  tags: string
  status: string
  visibility: string
  version: number
  caseCount: number
  latestRun?: EvalRunBrief | null
  creator: string
  createdAt: string
  updatedAt: string
}

export interface EvalSuiteDetail extends EvalSuite {
  categories: Array<{ category: string; total: number }>
  recentRuns: EvalRun[]
  trend: Array<{ runId: number; successRate: number | null; gateConclusion: string; createdAt: string }>
}

export interface EvalCase {
  id: number
  projectId: number
  suiteId: number
  caseNo: string
  category: string
  title: string
  severity: string
  expectedType: string
  tags: string
  source: string
  effectiveDate: string
  status: string
  createdAt: string
}

/** 列表带 detail=1 时多出的编辑字段。 */
export interface EvalCaseDetail extends EvalCase {
  expectedText: string
  input: unknown
  assertions: Record<string, unknown>
  rubric: unknown[]
  relevantDocs: unknown[]
  gradedRelevance: Record<string, unknown>
  toolsExpected: Record<string, unknown>
  difficulty: number
  sourceRef: string
  creator: string | null
  updatedAt: string
}

export interface EvalConfig {
  id: number
  projectId: number
  name: string
  profileId: number | null
  provider: string
  model: string
  temperature: number
  topP: number
  maxTokens: number
  seed: number | null
  promptCode: string
  promptVersion: string
  isBaseline: boolean
  note: string
  referenced: boolean
  createdAt: string
  updatedAt: string
}

export interface EvalJudge {
  id: number
  projectId: number
  name: string
  profileId: number | null
  promptCode: string
  strategy: string
  panelSize: number
  temperature: number
  seed: number | null
  judgeConfigs: unknown[]
  agreementRate: number | null
  kappa: number | null
  falsePassRate: number | null
  calibratedAt: string | null
  calibrationSize: number
  status: string
  createdAt: string
}

export interface EvalCalibration {
  id: number
  judge_id: number
  run_id: number
  sample_size: number
  agreement_before: number | null
  agreement_after: number | null
  kappa_after: number | null
  false_pass_rate: number | null
  false_fail_rate: number | null
  position_bias: number | null
  verbosity_bias: number | null
  detail: Record<string, unknown>
  created_at: string
}

export interface EvalCalibrationResult {
  calibrationId: number
  judgeId: number
  runId: number
  sampleSize: number
  agreement: number | null
  kappa: number | null
  falsePassRate: number | null
  falseFailRate: number | null
  matrix: Record<string, number>
  calibrated: boolean
  detail: Record<string, unknown>
}

export interface EvalRun {
  id: number
  projectId: number
  suiteId: number
  suiteVersion: number
  judgeMode: string
  status: string
  total: number
  passed: number
  failed: number
  skipped: number
  errors: number
  judgeInvalid: number
  successRate: number | null
  gateConclusion: string
  caseSetHash: string
  runKey: string
  costMicros: number
  costSource: string
  startedAt: string | null
  finishedAt: string | null
  createdAt: string
}

export interface EvalResultRow {
  id: number
  run_id: number
  case_id: number
  case_no: string
  output_text: string
  tool_trace: string
  retrieved_json: string
  end_state: string
  attempt_count: number
  fallback_used: number
  steps: number
  verdict: string
  judge_valid: number
  score: number | null
  failure_reason: string
  judge_json: string
  human_verdict: string | null
  human_score: number | null
  human_by: string | null
  human_at: string | null
  latency_ms: number
  tokens_in: number
  tokens_out: number
  cost_micros: number
  cost_source: string
  created_at: string
  updated_at: string
  severity?: string
  caseTitle?: string
  category?: string
}

export interface EvalMetricRow {
  id: number
  run_id: number
  metric_key: string
  value: number | null
  ci_lower: number | null
  ci_upper: number | null
  ci_method: string
  unit: string
  numerator: number | null
  denominator: number | null
  sample_size: number
  detail_json?: string
}

export interface EvalRunDetail {
  run: EvalRun
  metrics: Record<string, EvalMetricRow>
  metricOrder: string[]
  counts: Record<string, number>
  results: EvalResultRow[]
}

export interface EvalMetricDiff {
  key: string
  baseline: number | null
  current: number | null
  delta: number | null
  baselineCi: [number | null, number | null]
  currentCi: [number | null, number | null]
  sampleSize: number
}

export interface EvalPairedResult {
  regressedCount: number
  improvedCount: number
  pValue: number
  alpha: number
  significant: boolean
  hardAssertionRegression: boolean
  regressed: unknown[]
  improved: unknown[]
}

export interface EvalCompareResult {
  baselineRunId: number
  currentRunId: number
  alpha: number
  paired: EvalPairedResult
  metrics: EvalMetricDiff[]
  mdeTable: Array<{ nPerGroup: number; mde: number | null }>
}

export interface EvalTrendPoint {
  runId: number
  createdAt: string
  successRate: number | null
  gateConclusion: string
  metrics: Record<string, { value: number | null; lower: number | null; upper: number | null; sampleSize: number } | null>
}

export interface EvalTrendsResult {
  points: EvalTrendPoint[]
  series: Record<string, Array<{ runId: number; createdAt: string; value: number | null; lower: number | null; upper: number | null; sampleSize: number }>>
  mdeTable: Array<{ nPerGroup: number; mde: number | null }>
}

export interface EvalGate {
  id: number
  projectId: number
  suiteId: number
  name: string
  thresholds: Record<string, unknown>
  isHard: boolean
  baselineRunId: number | null
  enabled: boolean
  lastRunId: number | null
  lastConclusion: string
  lastEvaluatedAt: string | null
  createdAt: string
  updatedAt: string
}

export interface EvalGateCheck {
  key: string
  metric: string
  primary: boolean
  value: number | null
  judged?: number
  basis?: string
  limit: number | Record<string, unknown>
  passed: boolean
  reason?: string
}

export interface EvalGateEvaluation {
  conclusion: string
  checks: EvalGateCheck[]
  failures: EvalGateCheck[]
  primaryFailures: EvalGateCheck[]
  sampleSize: number
  minExplore: number
  minPublish: number
  notes: string[]
}

export interface EvalSuiteQuality {
  caseCount: number
  coverage: Array<{ category: string; severity: string; count: number }>
  difficulty: Array<{ level: number; label: string; count: number }>
  redundancy: {
    pairs: number
    duplicates: Array<{ a: string; b: string; similarity: number }>
    ratio: number
  }
  discrimination: {
    casesWithRuns: number
    meanVariance: number | null
    lowDiscrimination: Array<{
      caseId: number
      caseNo: string
      passRate: number
      variance: number
      runs: number
    }>
  }
  annotationConsistency: { kappa: number | null; sampleSize: number; reason?: string }
}

export interface EvalMeta {
  categories: string[]
  severities: Array<{ key: string; weight: number }>
  targetTypes: string[]
  visibilities: string[]
  suiteStatuses: string[]
  caseStatuses: string[]
  runStatuses: string[]
  judgeStrategies: string[]
  gateConclusions: string[]
  failureReasons: string[]
  metricKeys: string[]
  thresholdKeys: string[]
  primaryMetrics: string[]
  exportFormats: string[]
  thresholdDefaults: Record<string, unknown>
}

export interface EvalImportResult {
  dryRun: boolean
  accepted: number
  skipped: number
  created: Array<{ id: number; title: string }>
  items?: Array<{ index: number; title: string; status: string }>
  skippedItems: Array<{ index: number; title: string; reason: string }>
}

/** 回放输出：案例号 / 案例 ID + 模型答案与可选轨迹字段。 */
export interface EvalReplayOutput {
  caseNo?: string
  caseId?: number
  output?: string
  toolTrace?: unknown[]
  retrieved?: unknown[]
  endState?: Record<string, unknown>
  steps?: number
  claims?: unknown[]
  latencyMs?: number
  tokensIn?: number
  tokensOut?: number
  costMicros?: number
  costSource?: string
  attemptCount?: number
  fallbackUsed?: boolean
}

export interface EvalRunCreatePayload {
  projectId?: number | null
  suiteId: number
  configId?: number | null
  judgeId?: number | null
  config: Record<string, unknown>
  judge: Record<string, unknown>
  purpose?: string
  ablationGroup?: string
  triggerType?: string
}

export const evalApi = {
  meta: () => get<EvalMeta>('/evals/meta'),

  suites: (query: Record<string, unknown> = {}) =>
    get<PagePayload<EvalSuite>>('/evals/suites', query as never),
  suite: (id: number) => get<EvalSuiteDetail>(`/evals/suites/${id}`),
  createSuite: (body: Record<string, unknown>) => post<{ id: number }>('/evals/suites', body),
  updateSuite: (id: number, body: Record<string, unknown>) =>
    put<EvalSuiteDetail>(`/evals/suites/${id}`, body),
  removeSuite: (id: number) => del<{ id: number; deleted: boolean }>(`/evals/suites/${id}`),
  duplicateSuite: (id: number, body: Record<string, unknown> = {}) =>
    post<{ id: number; name: string; version: number; caseCount: number }>(
      `/evals/suites/${id}/duplicate`,
      body,
    ),
  quality: (id: number) => get<EvalSuiteQuality>(`/evals/suites/${id}/quality`),

  cases: (suiteId: number, query: Record<string, unknown> = {}) =>
    get<PagePayload<EvalCaseDetail>>(`/evals/suites/${suiteId}/cases`, { ...query, detail: 1 } as never),
  createCase: (suiteId: number, body: Record<string, unknown>) =>
    post<{ id: number }>(`/evals/suites/${suiteId}/cases`, body),
  updateCase: (id: number, body: Record<string, unknown>) =>
    put<{ id: number; caseNo: string; updated: boolean }>(`/evals/cases/${id}`, body),
  removeCase: (id: number) =>
    del<{ id: number; deleted: boolean; caseCount: number }>(`/evals/cases/${id}`),
  importCases: (body: Record<string, unknown>) =>
    post<EvalImportResult>('/evals/cases/import', body),
  caseFromBadcase: (body: Record<string, unknown>) =>
    post<{ id: number }>('/evals/cases/from-badcase', body),

  configs: (query: Record<string, unknown> = {}) =>
    get<PagePayload<EvalConfig>>('/evals/configs', query as never),
  createConfig: (body: Record<string, unknown>) => post<{ id: number }>('/evals/configs', body),
  updateConfig: (id: number, body: Record<string, unknown>) =>
    put<{ id: number; updated: boolean }>(`/evals/configs/${id}`, body),

  judges: (query: Record<string, unknown> = {}) =>
    get<PagePayload<EvalJudge>>('/evals/judges', query as never),
  calibrate: (id: number, body: Record<string, unknown>) =>
    post<EvalCalibrationResult>(`/evals/judges/${id}/calibrate`, body),
  calibrations: (id: number) =>
    get<{ list: EvalCalibration[] }>(`/evals/judges/${id}/calibrations`),

  runs: (query: Record<string, unknown> = {}) =>
    get<PagePayload<EvalRun>>('/evals/runs', query as never),
  createRun: (body: EvalRunCreatePayload) =>
    post<{ runId: number; reused: boolean; runKey: string }>('/evals/runs', body),
  run: (id: number) => get<EvalRunDetail>(`/evals/runs/${id}`),
  resumeRun: (id: number, outputs?: EvalReplayOutput[]) =>
    post<{ run: EvalRun; metrics: Record<string, EvalMetricRow>; counts: Record<string, number> }>(
      `/evals/runs/${id}/resume`,
      outputs ? { outputs } : {},
    ),
  cancelRun: (id: number) =>
    post<{ id: number; status: string; idempotent: boolean }>(`/evals/runs/${id}/cancel`),
  results: (id: number, query: Record<string, unknown> = {}) =>
    get<{ list: EvalResultRow[]; total: number; counts: Record<string, number> }>(
      `/evals/runs/${id}/results`,
      query as never,
    ),
  humanScores: (
    id: number,
    items: Array<{ resultId?: number; caseId?: number; human: string; score?: number }>,
    humanBy?: string,
  ) =>
    post<{ updated: number; metrics: Record<string, EvalMetricRow> }>(
      `/evals/runs/${id}/human-scores`,
      { items, humanBy },
    ),
  exportRun: (id: number, format: 'md' | 'xlsx'): Promise<BlobResult> =>
    getBlob(`/evals/runs/${id}/export`, { format }),

  compare: (baselineRunId: number, currentRunId: number, alpha = 0.05) =>
    get<EvalCompareResult>('/evals/compare', { baselineRunId, currentRunId, alpha }),
  trends: (suiteId: number, keys?: string[], limit = 20) =>
    get<EvalTrendsResult>('/evals/metrics/trends', {
      suiteId,
      keys: keys?.join(',') ?? undefined,
      limit,
    }),

  gates: (query: Record<string, unknown> = {}) =>
    get<{ list: EvalGate[] }>('/evals/gates', query as never),
  createGate: (body: Record<string, unknown>) => post<{ id: number }>('/evals/gates', body),
  updateGate: (id: number, body: Record<string, unknown>) =>
    put<EvalGate>('/evals/gates', { id, ...body }),
  evaluateGate: (id: number, runId?: number) =>
    post<EvalGateEvaluation>(`/evals/gates/${id}/evaluate`, runId ? { runId } : {}),
}