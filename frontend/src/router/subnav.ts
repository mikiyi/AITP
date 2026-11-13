/** 合并入口的页内子导航：导航只保留一个入口，页内用子页签切换，路由保持不变。 */

/** AI 记录 = 会话记录 + 执行记录 */
export const RECORD_TABS = [
  { path: '/lab/sessions', label: '会话记录' },
  { path: '/lab/records', label: '执行记录' },
]

/** 执行配置 = 环境 + 自动化 + 性能 + 探索 */
export const EXEC_TABS = [
  { path: '/settings/environments', label: '测试环境' },
  { path: '/settings/automation', label: '自动化' },
  { path: '/settings/performance', label: '性能' },
  { path: '/settings/exploratory', label: '探索性' },
]