import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

/**
 * 28 条业务路由。
 * meta.title 用于面包屑与标签页，meta.icon 对应 Element Plus 图标名。
 */
const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'workbench',
    component: () => import('@/views/workbench/HomeView.vue'),
    meta: { title: '首页', icon: 'HomeFilled' },
  },
  {
    path: '/todos',
    name: 'todos',
    component: () => import('@/views/todos/TodoView.vue'),
    meta: { title: '效能List', icon: 'List' },
  },
  {
    path: '/tasks',
    name: 'tasks',
    component: () => import('@/views/tasks/TaskCenterView.vue'),
    meta: { title: '任务队列', icon: 'Timer' },
  },
  {
    path: '/lab',
    name: 'lab',
    component: () => import('@/views/lab/LabHomeView.vue'),
    meta: { title: 'AI 实验室', icon: 'MagicStick' },
  },
  {
    path: '/lab/pipeline',
    name: 'lab-pipeline',
    component: () => import('@/views/lab/PipelineView.vue'),
    meta: { title: '顺序执行', icon: 'Sort' },
  },
  {
    path: '/lab/prompts',
    name: 'lab-prompts',
    component: () => import('@/views/lab/PromptLibraryView.vue'),
    meta: { title: '提示词库', icon: 'Notebook' },
  },
  {
    path: '/lab/:capability',
    name: 'lab-capability',
    component: () => import('@/views/lab/CapabilityView.vue'),
    meta: { title: 'AI 能力', hidden: true },
  },
  {
    path: '/lab/sessions',
    name: 'lab-sessions',
    component: () => import('@/views/lab/SessionListView.vue'),
    meta: { title: '会话记录', icon: 'ChatDotRound' },
  },
  {
    path: '/lab/sessions/:id',
    name: 'lab-session-detail',
    component: () => import('@/views/lab/SessionDetailView.vue'),
    meta: { title: '会话详情', hidden: true },
  },
  {
    path: '/lab/records',
    name: 'lab-records',
    component: () => import('@/views/lab/RecordListView.vue'),
    meta: { title: '执行记录', icon: 'Tickets' },
  },
  {
    path: '/lab/records/:id',
    name: 'lab-record-detail',
    component: () => import('@/views/lab/RecordDetailView.vue'),
    meta: { title: '执行记录详情', hidden: true },
  },
  {
    path: '/projects',
    name: 'projects',
    component: () => import('@/views/projects/ProjectListView.vue'),
    meta: { title: '项目', icon: 'Folder' },
  },
  {
    path: '/requirements',
    name: 'requirements',
    component: () => import('@/views/requirements/RequirementListView.vue'),
    meta: { title: '需求', icon: 'Document' },
  },
  {
    path: '/test-cases',
    name: 'test-cases',
    component: () => import('@/views/cases/CaseListView.vue'),
    meta: { title: '测试用例', icon: 'Tickets' },
  },
  {
    path: '/test-cases/:id',
    name: 'test-case-detail',
    component: () => import('@/views/cases/CaseDetailView.vue'),
    meta: { title: '用例详情', hidden: true },
  },
  {
    path: '/regressions',
    name: 'regressions',
    component: () => import('@/views/regressions/RegressionListView.vue'),
    meta: { title: '回归清单', icon: 'Refresh' },
  },
  {
    path: '/test-plans',
    name: 'test-plans',
    component: () => import('@/views/execution/TestPlanListView.vue'),
    meta: { title: '测试计划', icon: 'Calendar' },
  },
  {
    path: '/test-runs/:id',
    name: 'test-run-detail',
    component: () => import('@/views/execution/TestRunDetailView.vue'),
    meta: { title: '测试轮次执行', hidden: true },
  },
  {
    path: '/traceability',
    name: 'traceability',
    component: () => import('@/views/execution/TraceabilityView.vue'),
    meta: { title: '追溯与准入', icon: 'TrendCharts' },
  },
  {
    path: '/workbench/validation',
    name: 'workbench-validation',
    component: () => import('@/views/workbench/ValidationView.vue'),
    meta: { title: '验证沙箱', icon: 'CircleCheck' },
  },
  {
    path: '/bugs',
    name: 'bugs',
    component: () => import('@/views/bugs/BugListView.vue'),
    meta: { title: '缺陷', icon: 'Warning' },
  },
  {
    path: '/bugs/:id',
    name: 'bug-detail',
    component: () => import('@/views/bugs/BugDetailView.vue'),
    meta: { title: '缺陷详情', hidden: true },
  },
  {
    path: '/sql-analyses',
    name: 'sql-analyses',
    component: () => import('@/views/sql/SqlAnalysisListView.vue'),
    meta: { title: 'SQL 分析', icon: 'DataLine' },
  },
  {
    path: '/knowledge',
    name: 'knowledge',
    component: () => import('@/views/knowledge/KnowledgeView.vue'),
    meta: { title: '知识库管理', icon: 'Notebook' },
  },
  {
    path: '/knowledge/assistant',
    name: 'knowledge-assistant',
    component: () => import('@/views/knowledge/KnowledgeAssistantView.vue'),
    meta: { title: '知识库问答助手', hidden: true },
  },
  {
    path: '/knowledge/:id',
    name: 'knowledge-detail',
    component: () => import('@/views/knowledge/KnowledgeDetailView.vue'),
    meta: { title: '知识详情', hidden: true },
  },
  {
    path: '/knowledge/:id/versions',
    name: 'knowledge-versions',
    component: () => import('@/views/knowledge/KnowledgeVersionView.vue'),
    meta: { title: '版本记录', hidden: true },
  },
  {
    path: '/reports',
    name: 'reports',
    component: () => import('@/views/reports/ReportListView.vue'),
    meta: { title: '测试报告', icon: 'DataAnalysis' },
  },
  {
    path: '/reports/:id',
    name: 'report-detail',
    component: () => import('@/views/reports/ReportDetailView.vue'),
    meta: { title: '报告详情', hidden: true },
  },
  {
    path: '/automation/:category(web|app|api|performance)',
    name: 'automation-scripts',
    component: () => import('@/views/automation/AutomationScriptView.vue'),
    meta: { title: '自动化脚本', hidden: true },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/settings/SettingsView.vue'),
    meta: { title: '操作日志', icon: 'Setting' },
  },
  {
    path: '/settings/security',
    name: 'settings-security',
    component: () => import('@/views/settings/AccessSecurityView.vue'),
    meta: { title: '访问口令', hidden: true },
  },
  {
    path: '/settings/ai',
    name: 'settings-ai',
    component: () => import('@/views/settings/AiSettingsView.vue'),
    meta: { title: 'AI 设置', hidden: true },
  },
  {
    path: '/settings/knowledge',
    name: 'settings-knowledge',
    component: () => import('@/views/settings/KnowledgeSettingsView.vue'),
    meta: { title: '知识库设置', hidden: true },
  },
  {
    path: '/settings/modules',
    name: 'settings-modules',
    component: () => import('@/views/settings/ModuleManageView.vue'),
    meta: { title: '模块树设置', hidden: true },
  },
  {
    path: '/settings/versions',
    name: 'settings-versions',
    component: () => import('@/views/settings/VersionManageView.vue'),
    meta: { title: '项目版本管理', hidden: true },
  },
  {
    path: '/settings/templates',
    name: 'settings-templates',
    component: () => import('@/views/settings/DocTemplateSettingsView.vue'),
    meta: { title: '测试文档模板设置', hidden: true },
  },
  {
    path: '/settings/backup',
    name: 'settings-backup',
    component: () => import('@/views/settings/BackupSettingsView.vue'),
    meta: { title: '数据备份与还原设置', hidden: true },
  },
  {
    path: '/settings/automation',
    name: 'settings-automation',
    component: () => import('@/views/settings/AutomationSettingsView.vue'),
    meta: { title: '自动化管理设置', hidden: true },
  },
  {
    path: '/settings/performance',
    name: 'settings-performance',
    component: () => import('@/views/settings/PerformanceSettingsView.vue'),
    meta: { title: '性能测试管理设置', hidden: true },
  },
  {
    path: '/settings/environments',
    name: 'settings-environments',
    component: () => import('@/views/settings/EnvironmentSettingsView.vue'),
    meta: { title: '测试环境配置', hidden: true },
  },
  {
    path: '/settings/ci',
    name: 'settings-ci',
    component: () => import('@/views/settings/CiNotificationView.vue'),
    meta: { title: 'CI 与通知', hidden: true },
  },
  {
    path: '/settings/assertions',
    name: 'settings-assertions',
    component: () => import('@/views/settings/AssertionRuleSettingsView.vue'),
    meta: { title: '断言库', hidden: true },
  },
  {
    path: '/settings/home-cards',
    name: 'settings-home-cards',
    component: () => import('@/views/settings/HomeCardSettingsView.vue'),
    meta: { title: '首页卡片定制', hidden: true },
  },
  {
    path: '/help',
    name: 'help',
    component: () => import('@/views/settings/ProductHelpView.vue'),
    meta: { title: '帮助文档' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/error/NotFoundView.vue'),
    meta: { title: '页面不存在', hidden: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = (to.meta.title as string | undefined) ?? ''
  document.title = title ? `${title} · AITestPlatform` : 'AITestPlatform · AI 测试工作台'
})

export default router
