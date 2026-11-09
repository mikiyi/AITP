import { reactive, ref } from 'vue'
import type { PagePayload } from '@/api/request'
import type { TestCase } from '@/api/types'

/**
 * 树节点的用例续取（GAP-25 前端 · 树节点超限续取）。
 *
 * 用例树接口对单次回传的用例条数有限量（实测约 200 条 / 次），节点的 `caseCount`
 * 才是全量口径。这里按 `pageNum` / `pageSize` 一页一页地把该节点剩下的用例补齐：
 * - 每次只取一页（默认 100 条，等于后端 `MAX_PAGE_SIZE`），不会一次把上万条拉进内存；
 * - 取到的行按节点缓存，重复展开不重发请求；
 * - 追加时按用例 id 去重，翻页与续取都不会把同一条堆两遍进 DOM；
 * - 同一节点连点只认最后一次请求，慢请求不能把旧数据倒灌回来。
 */

/** 续取单页条数：后端 `MAX_PAGE_SIZE=100`，传更大的值只会被夹回来。 */
export const NODE_CASE_PAGE_SIZE = 100

export interface NodeCaseState {
  /** 已经取到的用例，按页顺序追加。 */
  rows: TestCase[]
  /** 该节点的用例总量（`caseCount` 口径），以接口回的 total 为准。 */
  total: number
  /** 已取到第几页；0 表示一页都没取。 */
  pageNum: number
  loading: boolean
  error: string
}

export type NodeCaseFetcher = (pageNum: number) => Promise<PagePayload<TestCase>>

function blankState(): NodeCaseState {
  return { rows: [], total: 0, pageNum: 0, loading: false, error: '' }
}

export function useNodeCases(pageSize = NODE_CASE_PAGE_SIZE) {
  const states = reactive(new Map<string, NodeCaseState>())
  const expanded = ref<Set<string>>(new Set())
  const sequences = new Map<string, number>()

  function stateOf(key: string): NodeCaseState {
    let state = states.get(key)
    if (!state) {
      state = blankState()
      states.set(key, state)
    }
    return state
  }

  function remaining(key: string): number {
    const state = states.get(key)
    if (!state) {
      return 0
    }
    return Math.max(0, state.total - state.rows.length)
  }

  function isExpanded(key: string): boolean {
    return expanded.value.has(key)
  }

  /** 取该节点的下一页并追加；返回这一页的落地结果，供「加载全部」串起来等。 */
  async function fetchPage(key: string, fetcher: NodeCaseFetcher, totalHint: number): Promise<boolean> {
    const state = stateOf(key)
    if (state.loading) {
      return false
    }
    const my = (sequences.get(key) ?? 0) + 1
    sequences.set(key, my)
    const nextPage = state.pageNum + 1
    state.loading = true
    state.error = ''
    const before = state.rows.length
    try {
      const page = await fetcher(nextPage)
      if (sequences.get(key) !== my) {
        return false
      }
      state.total = Number(page.total ?? totalHint ?? 0)
      const known = new Set(state.rows.map((row) => String(row.id)))
      ;(page.list ?? [])
        .filter((row) => !known.has(String(row.id)))
        .forEach((row) => state.rows.push(row))
      state.pageNum = Number(page.pageNum ?? nextPage)
    } catch (err) {
      if (sequences.get(key) === my) {
        state.error = err instanceof Error ? err.message : '用例加载失败'
      }
      return false
    } finally {
      if (sequences.get(key) === my) {
        state.loading = false
      }
    }
    return state.rows.length > before
  }

  /** 展开节点：没取过就取第一页；取过的直接把缓存摊出来。 */
  function expand(key: string, fetcher: NodeCaseFetcher, totalHint: number): Promise<boolean> {
    expanded.value.add(key)
    const state = stateOf(key)
    if (state.pageNum || state.loading) {
      return Promise.resolve(false)
    }
    return fetchPage(key, fetcher, totalHint)
  }

  function collapse(key: string): void {
    expanded.value.delete(key)
  }

  function toggle(key: string, fetcher: NodeCaseFetcher, totalHint: number): void {
    if (isExpanded(key)) {
      collapse(key)
      return
    }
    void expand(key, fetcher, totalHint)
  }

  /** 续取入口：「还有 N 条，点击加载」点一次取一页。 */
  function loadMore(key: string, fetcher: NodeCaseFetcher, totalHint: number): Promise<boolean> {
    const state = stateOf(key)
    if (state.loading) {
      return Promise.resolve(false)
    }
    if (state.total && state.rows.length >= state.total) {
      return Promise.resolve(false)
    }
    return fetchPage(key, fetcher, totalHint)
  }

  /** 换项目 / 换版本：缓存与展开状态全部作废，否则旧项目的用例还挂在同名节点下。 */
  function reset(): void {
    states.clear()
    expanded.value = new Set()
    sequences.clear()
  }

  /** 用例被增删改后让该节点缓存失效（保存、删除、导入后调用）。 */
  function invalidate(key: string): void {
    states.delete(key)
    sequences.delete(key)
  }

  return {
    states,
    expanded,
    stateOf,
    isExpanded,
    remaining,
    pageSize,
    expand,
    collapse,
    toggle,
    loadMore,
    fetchPage,
    reset,
    invalidate,
  }
}
