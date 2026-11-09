import { computed, ref } from 'vue'
import { NODE_CASE_PAGE_SIZE, useNodeCases } from './useNodeCases'
import type { PagePayload } from '@/api/request'
import type { TestCase } from '@/api/types'

/**
 * 功能点 / 模块树的「摊平 + 用例续取」（GAP-25 前端 · 树节点超限续取）。
 *
 * 树接口单次回传的用例有条数上限（实测约 200），节点上的 `count` 才是全量口径。
 * 这里把「节点 + 展开出来的用例 + 续取入口」摊平成一份行数组，交给 VirtualScroller
 * 只渲染视口内的行：节点再多、续取再多，都只是数组变长，DOM 行数恒定在几十行。
 */

/** 摊平后的树节点：层级、显示文案、用例总量与取数用的筛选串。 */
export interface DrillNode {
  key: string
  /** 层级，0 是顶级；用来算缩进。 */
  depth: number
  label: string
  /** 悬浮提示，一般是全路径。 */
  title: string
  /** 参与关键字过滤的文本。 */
  path: string
  /** 该节点的用例总量（接口 `caseCount` 口径）。 */
  count: number
  /** 有用例才给展开箭头。 */
  expandable: boolean
  /** 传给列表接口的 moduleId：子树 id 列表 / 'none' / 空串（全部）。 */
  query: string
  /** 原节点对象随行带回，外层渲染自己的字段（类型标签、引用计数等）靠它。 */
  source: unknown
}

export interface DrillRow {
  kind: 'node' | 'case' | 'more' | 'error'
  key: string
  /** 所属节点键：续取与批量加载都按它回查。 */
  nodeKey: string
  /** 缩进层级。 */
  indent: number
  label: string
  title: string
  count: number
  expanded: boolean
  expandable: boolean
  active: boolean
  caseItem: TestCase | null
  /** 节点行的原节点对象；用例 / 续取行是它所属节点的。 */
  source: unknown
  remaining: number
  loaded: number
  loading: boolean
  message: string
}

export interface TreeDrilldownOptions {
  /** 已按前序摊平的节点数组（depth 表示层级）。 */
  nodes: () => DrillNode[]
  /** 取某个节点的第 pageNum 页用例。 */
  fetch: (node: DrillNode, pageNum: number) => Promise<PagePayload<TestCase>>
  /** 当前筛选命中的节点，用来做高亮。 */
  isActive?: (node: DrillNode) => boolean
}

/** 「加载全部」的单次上限：每批仍是一页 100 条，只是连着取，防止异常数据把循环卡死。 */
const LOAD_ALL_MAX_PAGES = 300

export function useTreeCaseDrilldown(options: TreeDrilldownOptions) {
  const nodeCases = useNodeCases(NODE_CASE_PAGE_SIZE)
  const keyword = ref('')
  const bulkKeys = ref<Set<string>>(new Set())

  function nodeOf(key: string): DrillNode | undefined {
    return options.nodes().find((item) => item.key === key)
  }

  function fetcherOf(node: DrillNode): (pageNum: number) => Promise<PagePayload<TestCase>> {
    return (pageNum: number) => options.fetch(node, pageNum)
  }

  /**
   * 关键字过滤：命中的节点保留，同时把它的祖先留下。
   * el-tree 的 filter-node-method 就是这个语义，换成摊平行后得自己补祖先，
   * 否则命中项会孤零零地没了上下文。
   */
  const visibleNodes = computed<DrillNode[]>(() => {
    const list = options.nodes()
    const text = keyword.value.trim().toLowerCase()
    if (!text) {
      return list
    }
    const keep = new Set<number>()
    const ancestors: number[] = []
    list.forEach((node, index) => {
      while (ancestors.length && list[ancestors[ancestors.length - 1]].depth >= node.depth) {
        ancestors.pop()
      }
      if (`${node.label} ${node.path}`.toLowerCase().includes(text)) {
        keep.add(index)
        ancestors.forEach((ancestor) => keep.add(ancestor))
      }
      ancestors.push(index)
    })
    return list.filter((_, index) => keep.has(index))
  })

  function controlRow(
    kind: 'more' | 'error',
    node: DrillNode,
    loaded: number,
    message: string,
    loading: boolean,
  ): DrillRow {
    return {
      kind,
      key: `${kind}-${node.key}`,
      nodeKey: node.key,
      indent: node.depth + 1,
      label: '',
      title: message,
      count: 0,
      expanded: false,
      expandable: false,
      active: false,
      caseItem: null,
      source: node,
      remaining: Math.max(0, node.count - loaded),
      loaded,
      loading,
      message,
    }
  }

  const rows = computed<DrillRow[]>(() => {
    const out: DrillRow[] = []
    const filtering = keyword.value.trim() !== ''
    visibleNodes.value.forEach((node) => {
      const state = nodeCases.states.get(node.key)
      const opened = nodeCases.isExpanded(node.key) && !filtering
      const loaded = state?.rows.length ?? 0
      out.push({
        kind: 'node',
        key: `node-${node.key}`,
        nodeKey: node.key,
        indent: node.depth,
        label: node.label,
        title: node.title,
        count: node.count,
        expanded: nodeCases.isExpanded(node.key),
        expandable: node.expandable,
        active: options.isActive?.(node) ?? false,
        caseItem: null,
        source: node,
        remaining: Math.max(0, node.count - loaded),
        loaded,
        loading: state?.loading ?? false,
        message: '',
      })
      // 关键字过滤时只看节点：命中项不该被淹在万级用例里
      if (!opened || !state) {
        return
      }
      state.rows.forEach((item) => {
        out.push({
          kind: 'case',
          key: `case-${node.key}-${item.id}`,
          nodeKey: node.key,
          indent: node.depth + 1,
          label: item.title,
          title: `${item.caseNo} ${item.title}`,
          count: 0,
          expanded: false,
          expandable: false,
          active: false,
          caseItem: item,
          source: node,
          remaining: 0,
          loaded: 0,
          loading: false,
          message: '',
        })
      })
      const remaining = Math.max(0, node.count - state.rows.length)
      if (state.error) {
        out.push(controlRow('error', node, state.rows.length, state.error, state.loading))
      } else if (remaining > 0) {
        out.push(controlRow('more', node, state.rows.length, '', state.loading))
      }
    })
    return out
  })

  function toggle(nodeKey: string): void {
    const node = nodeOf(nodeKey)
    if (!node || !node.expandable) {
      return
    }
    nodeCases.toggle(nodeKey, fetcherOf(node), node.count)
  }

  function loadMore(nodeKey: string): void {
    const node = nodeOf(nodeKey)
    if (!node) {
      return
    }
    void nodeCases.loadMore(nodeKey, fetcherOf(node), node.count)
  }

  /**
   * 「加载全部」：一页 100 条连着取，直到该节点取完。
   * 请求始终走后端分页口径，取回的行只是进数组，渲染仍由窗口化控制在视口内。
   */
  async function loadAll(nodeKey: string): Promise<void> {
    const node = nodeOf(nodeKey)
    if (!node || bulkKeys.value.has(nodeKey)) {
      return
    }
    bulkKeys.value.add(nodeKey)
    try {
      for (let batch = 0; batch < LOAD_ALL_MAX_PAGES; batch += 1) {
        // 中途被收起就停手，别再往看不见的节点里灌数据
        if (!nodeCases.isExpanded(nodeKey)) {
          break
        }
        const state = nodeCases.states.get(nodeKey)
        if (state?.error) {
          break
        }
        if (state && state.total > 0 && state.rows.length >= state.total) {
          break
        }
        const appended = state
          ? await nodeCases.loadMore(nodeKey, fetcherOf(node), node.count)
          : await nodeCases.expand(nodeKey, fetcherOf(node), node.count)
        if (!appended) {
          break
        }
      }
    } finally {
      bulkKeys.value.delete(nodeKey)
    }
  }

  function isBulk(nodeKey: string): boolean {
    return bulkKeys.value.has(nodeKey)
  }

  /** 换项目 / 换筛选：缓存、展开与批量状态全部作废。 */
  function reset(): void {
    nodeCases.reset()
    bulkKeys.value = new Set()
  }

  /** 用例增删改后让某个节点的缓存失效；展开着就重取第一页。 */
  function invalidate(nodeKey: string): void {
    const node = nodeOf(nodeKey)
    nodeCases.invalidate(nodeKey)
    if (node && nodeCases.isExpanded(nodeKey)) {
      void nodeCases.fetchPage(nodeKey, fetcherOf(node), node.count)
    }
  }

  return {
    rows,
    keyword,
    visibleNodes,
    toggle,
    loadMore,
    loadAll,
    isBulk,
    reset,
    invalidate,
    states: nodeCases.states,
  }
}
