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
    meta: { title: '工作台', icon: 'HomeFilled' },
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
    path: '/test-cases/tree',
    name: 'test-case-tree',
    component: () => import('@/views/cases/CaseTreeView.vue'),
    meta: { title: '用例树', icon: 'Share' },
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
    path: '/workbench/entries',
    name: 'workbench-entries',
    component: () => import('@/views/workbench/EntryView.vue'),
    meta: { title: '编排入口', icon: 'Grid' },
  },
  {
    path: '/workbench/validation',
    name: 'workbench-validation',
    component: () => import('@/views/workbench/ValidationView.vue'),
    meta: { title: '验证沙箱', icon: 'CircleCheck' },
  },
  {
    path: '/workbench/relations',
    name: 'workbench-relations',
    component: () => import('@/views/workbench/RelationView.vue'),
    meta: { title: '实体关联', icon: 'Connection' },
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
    path: '/knowledge',
    name: 'knowledge',
    component: () => import('@/views/knowledge/KnowledgeView.vue'),
    meta: { title: '知识库', icon: 'Notebook' },
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
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/settings/SettingsView.vue'),
    meta: { title: '系统设置', icon: 'Setting' },
  },
  {
    path: '/settings/ai',
    name: 'settings-ai',
    component: () => import('@/views/settings/AiSettingsView.vue'),
    meta: { title: 'AI 配置', hidden: true },
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
    meta: { title: '模块树管理', hidden: true },
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
  document.title = title ? `${title} · AITestPilot` : 'AITestPilot · AI 测试工作台'
})

export default router
