import type { Component } from 'vue'
import {
  Aim,
  Bell,
  Box,
  Briefcase,
  Calendar,
  ChatLineRound,
  Checked,
  CircleCheck,
  Collection,
  CollectionTag,
  Connection,
  Cpu,
  DataAnalysis,
  DataLine,
  Document,
  DocumentAdd,
  DocumentChecked,
  DocumentCopy,
  Finished,
  Folder,
  FolderOpened,
  Grid,
  HomeFilled,
  Iphone,
  Link,
  List,
  Lock,
  Memo,
  Menu,
  Monitor,
  Notebook,
  Odometer,
  Postcard,
  Promotion,
  QuestionFilled,
  Reading,
  Refresh,
  SetUp,
  Setting,
  Share,
  Sort,
  Stopwatch,
  Suitcase,
  Switch,
  Tickets,
  Timer,
  TrendCharts,
  Upload,
  VideoPlay,
  Warning,
} from '@element-plus/icons-vue'
import AiLabIcon from '@/components/AiLabIcon.vue'

/** 侧边导航的叶子项，直接对应一个路由。 */
export interface MenuItemNode {
  type: 'item'
  path: string
  title: string
  icon: Component
}

/** 侧边导航的分组项，展开后是一组子入口。 */
export interface MenuGroupNode {
  type: 'group'
  /** 分组标识，同时作为 el-sub-menu 的 index */
  key: string
  title: string
  icon: Component
  children: MenuChild[]
}

/** 分组下的子入口。 */
export interface MenuChild {
  path: string
  title: string
  icon: Component
}

export type MenuNode = MenuItemNode | MenuGroupNode

/**
 * 侧边导航结构。
 * 侧边栏渲染、当前项高亮、面包屑分组都从这里取，避免两处各写一份导致对不上。
 *
 * 分组按测试工作流排：首页（入口）→ AI 实验室（智能辅助）→ 任务中心（待办/后台）
 * → 测试预案（开跑前圈范围）→ 测试资产（输入本体）→ 质量结论（报告/追溯/SQL）
 * → 自动化脚本（按目标管脚本）→ 执行与探索（矩阵/探索/执行配置）→ 知识库（沉淀）
 * → 数据与迁移（资产包/外部导入）→ 设置（仅全局偏好）→ 帮助文档。
 *
 * IA 原则：资产只放"输入本体"，结论独立成「质量结论」；脚本资产与执行编排分两组；
 * 域配置（环境/自动化/性能/探索/知识库/模块树/断言）下沉到业务域，设置只留全局项；
 * 合并入口（AI 记录、执行配置）用页内子页签切换，路由保持不变以兼容旧链接。
 *
 * 图标约定（两条硬规则）：
 * 1) 图标与菜单内容契合——看图标大致能猜到点进去做什么；
 * 2) 全导航图标不重复——每个一级 / 二级入口各用一个独立图形。
 * 例：仪表盘=能力总览、排序=顺序执行、笔记本=提示词库、对勾圆=验证沙箱、
 * 圆对话框=会话记录、线对话框=问答助手、工单=执行记录、清单=效能List、计时=任务队列、
 * 火箭=测试预案、日历=测试计划、循环箭头=回归清单、公文包=测试资产、文件夹=项目、
 * 文档副本=版本、文档=需求、完成勾=用例、警告三角=缺陷、柱图=测试报告、折线=SQL 分析、
 * 分享节点=追溯与准入、装配=自动化、显示器=Web、手机=App、链接=接口、秒表=性能、
 * 网格=执行矩阵、瞄准=探索性测试、书本=知识库、收藏夹=知识库管理、
 * 齿轮=设置、卡片=首页卡片定制、备忘录=操作日志、锁=访问口令、CPU=AI 设置、
 * 收藏标签=知识库设置、菜单树=模块树、文档加=模板、连接=环境、滑杆=自动化设置、
 * 直方图=性能设置、铃铛=CI 通知、文档勾=断言库、开文件夹=备份、箱子=留存归档、
 * 罗盘=探索设置、行李箱=资产包、上传=外部导入、问号=帮助。
 */
export const MENU_NODES: MenuNode[] = [
  { type: 'item', path: '/', title: '首页', icon: HomeFilled },
  {
    type: 'group',
    key: 'lab',
    title: 'AI 实验室',
    icon: AiLabIcon,
    children: [
      { path: '/lab', title: '能力总览', icon: Odometer },
      { path: '/lab/pipeline', title: 'AI 流水线', icon: Sort },
      { path: '/lab/prompts', title: '提示词库', icon: Notebook },
      { path: '/workbench/validation', title: '验证沙箱', icon: CircleCheck },
      { path: '/lab/records', title: 'AI 记录', icon: Tickets },
    ],
  },
  {
    type: 'group',
    key: 'task',
    title: '任务中心',
    icon: Checked,
    children: [
      { path: '/todos', title: '我的待办', icon: List },
      { path: '/tasks', title: '后台任务', icon: Timer },
    ],
  },
  {
    type: 'group',
    key: 'plan',
    title: '测试预案',
    icon: Promotion,
    children: [
      { path: '/test-plans', title: '测试计划', icon: Calendar },
      { path: '/regressions', title: '回归清单', icon: Refresh },
    ],
  },
  {
    type: 'group',
    key: 'asset',
    title: '测试资产',
    icon: Briefcase,
    children: [
      { path: '/projects', title: '项目', icon: Folder },
      { path: '/settings/versions', title: '版本', icon: DocumentCopy },
      { path: '/settings/modules', title: '功能点', icon: Menu },
      { path: '/requirements', title: '需求', icon: Document },
      { path: '/test-cases', title: '测试用例', icon: Finished },
      { path: '/bugs', title: '缺陷', icon: Warning },
      { path: '/settings/assertions', title: '断言库', icon: DocumentChecked },
    ],
  },
  {
    type: 'group',
    key: 'quality',
    title: '质量结论',
    icon: TrendCharts,
    children: [
      { path: '/reports', title: '测试报告', icon: DataAnalysis },
      { path: '/traceability', title: '追溯与准入', icon: Share },
      { path: '/sql-analyses', title: 'SQL 分析', icon: DataLine },
    ],
  },
  {
    type: 'group',
    key: 'autscript',
    title: '自动化脚本',
    icon: SetUp,
    children: [
      { path: '/automation/web', title: 'Web 自动化', icon: Monitor },
      { path: '/automation/app', title: 'App 自动化', icon: Iphone },
      { path: '/automation/api', title: '接口自动化', icon: Link },
      { path: '/automation/performance', title: '性能测试', icon: Stopwatch },
    ],
  },
  {
    type: 'group',
    key: 'exec',
    title: '执行与探索',
    icon: VideoPlay,
    children: [
      { path: '/execution-matrices', title: '执行矩阵', icon: Grid },
      { path: '/exploratory', title: '探索性测试', icon: Aim },
      { path: '/settings/environments', title: '执行配置', icon: Connection },
    ],
  },
  {
    type: 'group',
    key: 'knowledge',
    title: '知识库',
    icon: Reading,
    children: [
      { path: '/knowledge', title: '知识库管理', icon: Collection },
      { path: '/knowledge/assistant', title: '问答助手', icon: ChatLineRound },
      { path: '/settings/knowledge', title: '知识库设置', icon: CollectionTag },
    ],
  },
  {
    type: 'group',
    key: 'data',
    title: '数据与迁移',
    icon: Switch,
    children: [
      { path: '/settings/packages', title: '项目资产包', icon: Suitcase },
      { path: '/settings/external-imports', title: '外部平台导入', icon: Upload },
    ],
  },
  {
    type: 'group',
    key: 'settings',
    title: '设置',
    icon: Setting,
    children: [
      { path: '/settings/home-cards', title: '首页卡片定制', icon: Postcard },
      { path: '/settings', title: '操作日志', icon: Memo },
      { path: '/settings/security', title: '访问口令', icon: Lock },
      { path: '/settings/ai', title: 'AI 设置', icon: Cpu },
      { path: '/settings/templates', title: '测试文档模板', icon: DocumentAdd },
      { path: '/settings/backup', title: '数据备份与还原', icon: FolderOpened },
      { path: '/settings/retention', title: '数据留存与归档', icon: Box },
      { path: '/settings/ci', title: 'CI 与通知', icon: Bell },
    ],
  },
  { type: 'item', path: '/help', title: '帮助文档', icon: QuestionFilled },
]

/** 所有可点击的导航路径（含分组内的子入口），按路径长度倒序便于前缀匹配。 */
const MENU_PATHS: string[] = MENU_NODES.flatMap((node) =>
  node.type === 'item'
    ? [node.path]
    : node.children.map((child) => child.path),
).sort((a, b) => b.length - a.length)

/** 当前路由命中的导航信息。 */
export interface MenuMatch {
  /** 命中的导航路径，用来驱动侧栏高亮；没命中时为空串。 */
  path: string
  /** 所属分组的标题；不在任何分组里时为空串。 */
  group: string
  /** 命中入口的标题；没命中时为空串。 */
  title: string
  /** 是否精确命中该入口；详情页（/test-cases/42）命中的是上级入口，为 false。 */
  exact: boolean
}

function titleOf(path: string): string {
  for (const node of MENU_NODES) {
    if (node.type === 'item') {
      if (node.path === path) {
        return node.title
      }
      continue
    }
    const hit = node.children.find((child) => child.path === path)
    if (hit) {
      return hit.title
    }
  }
  return ''
}

/**
 * 把当前路由映射到导航项：先精确匹配，匹配不到就取最长前缀
 * （详情页如 /test-cases/42 归到 /test-cases）。
 */
export function matchMenu(path: string): MenuMatch {
  const exact = MENU_PATHS.find((item) => item === path)
  const hit = exact ?? MENU_PATHS.find((item) => path.startsWith(`${item}/`))
  if (!hit) {
    return { path: '', group: '', title: '', exact: false }
  }
  const group = MENU_NODES.find(
    (node): node is MenuGroupNode => node.type === 'group' && node.children.some((child) => child.path === hit),
  )
  return { path: hit, group: group ? group.title : '', title: titleOf(hit), exact: Boolean(exact) }
}

/** 分组是否包含当前选中的入口，用于给分组标题做同样的高亮。 */
export function isGroupActive(group: MenuGroupNode, activePath: string): boolean {
  return group.children.some((child) => child.path === activePath)
}
