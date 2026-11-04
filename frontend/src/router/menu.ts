import type { Component } from 'vue'
import {
  ChatDotRound,
  CircleCheck,
  Connection,
  DataAnalysis,
  Document,
  Files,
  Finished,
  Folder,
  Grid,
  HomeFilled,
  List,
  MagicStick,
  Notebook,
  Odometer,
  Operation,
  Reading,
  Refresh,
  Setting,
  Share,
  Sort,
  Tickets,
  Warning,
} from '@element-plus/icons-vue'

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
 * 图标约定：优先选「看一眼就知道是干什么」的图形——
 * 仪表盘=总览、箭头排序=顺序执行、对话框=会话、工单=记录、
 * 文件夹=项目、文档=需求、对勾=用例、分叉=用例树、警告三角=缺陷、
 * 书本=知识库、循环箭头=回归、图表=报告、九宫格=编排入口、
 * 对勾圆=验证沙箱、连接=实体关联。
 */
export const MENU_NODES: MenuNode[] = [
  { type: 'item', path: '/', title: '首页', icon: HomeFilled },
  { type: 'item', path: '/todos', title: '任务清单', icon: List },
  {
    type: 'group',
    key: 'lab',
    title: 'AI 实验室',
    icon: MagicStick,
    children: [
      { path: '/lab', title: '能力总览', icon: Odometer },
      { path: '/lab/pipeline', title: '顺序执行', icon: Sort },
      { path: '/lab/prompts', title: '提示词库', icon: Notebook },
      { path: '/lab/sessions', title: '会话记录', icon: ChatDotRound },
      { path: '/lab/records', title: '执行记录', icon: Tickets },
    ],
  },
  {
    type: 'group',
    key: 'asset',
    title: '测试资产',
    icon: Files,
    children: [
      { path: '/projects', title: '项目', icon: Folder },
      { path: '/requirements', title: '需求', icon: Document },
      { path: '/test-cases', title: '测试用例', icon: Finished },
      { path: '/test-cases/tree', title: '用例树', icon: Share },
      { path: '/bugs', title: '缺陷', icon: Warning },
      { path: '/knowledge', title: '知识库', icon: Reading },
      { path: '/regressions', title: '回归清单', icon: Refresh },
      { path: '/reports', title: '测试报告', icon: DataAnalysis },
    ],
  },
  {
    type: 'group',
    key: 'flow',
    title: '编排与验证',
    icon: Operation,
    children: [
      { path: '/workbench/entries', title: '编排入口', icon: Grid },
      { path: '/workbench/validation', title: '验证沙箱', icon: CircleCheck },
      { path: '/workbench/relations', title: '实体关联', icon: Connection },
    ],
  },
  { type: 'item', path: '/settings', title: '设置', icon: Setting },
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
}

/**
 * 把当前路由映射到导航项：先精确匹配，匹配不到就取最长前缀
 * （详情页如 /test-cases/42 归到 /test-cases）。
 */
export function matchMenu(path: string): MenuMatch {
  const hit = MENU_PATHS.find((item) => item === path)
    ?? MENU_PATHS.find((item) => path.startsWith(`${item}/`))
  if (!hit) {
    return { path: '', group: '' }
  }
  const group = MENU_NODES.find(
    (node): node is MenuGroupNode => node.type === 'group' && node.children.some((child) => child.path === hit),
  )
  return { path: hit, group: group ? group.title : '' }
}

/** 分组是否包含当前选中的入口，用于给分组标题做同样的高亮。 */
export function isGroupActive(group: MenuGroupNode, activePath: string): boolean {
  return group.children.some((child) => child.path === activePath)
}
