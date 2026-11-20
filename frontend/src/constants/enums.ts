/** 前端枚举常量：与后端 `common/enums.py` 及 `GET /meta/enums` 对齐。

这些值后端有权威源（下拉校验、状态机都按它走），前端各页直接从这里 import，
不要在页面里再写死一份 `['P0','P1','P2']`——那是字典漂移的根源
（回归页就曾因此漏了 P3，和用例页对不上）。
若后端枚举变了，同步改这里一份即可。
 */

/** 用例 / 回归条目优先级，与后端 CASE_PRIORITY 一致 */
export const CASE_PRIORITIES = ['P0', 'P1', 'P2', 'P3'] as const
export type CasePriority = (typeof CASE_PRIORITIES)[number]

/** 优先级下拉选项（label=value），给 el-select / 筛选直接用 */
export const CASE_PRIORITY_OPTIONS = CASE_PRIORITIES.map((p) => ({ label: p, value: p }))

/** 缺陷严重级别，与后端 BUG_SEVERITY 一致 */
export const BUG_SEVERITIES = ['P0', 'P1', 'P2', 'P3'] as const
export type BugSeverity = (typeof BUG_SEVERITIES)[number]
export const BUG_SEVERITY_OPTIONS = BUG_SEVERITIES.map((s) => ({ label: s, value: s }))
