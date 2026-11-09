import { computed, ref, type ComputedRef, type Ref } from 'vue'

/**
 * el-table 的行窗口化计算（GAP-25 前端 · 长列表虚拟滚动）。
 *
 * el-table 本身没有虚拟滚动，改用它又会把现有列样式 / 多选 / 行点击 / 斑马纹 / 空态全部换掉，
 * 所以这里只做「算出该渲染哪几行」：真实行按窗口切片，窗口前后的行用占位行垫高度，
 * 滚动条总长、表头吸顶、斑马纹都还是 el-table 原生的。
 */

/** DOM 量到真实行高之前的兜底值：element-plus 默认 size 的一行约 40px。 */
export const ESTIMATED_ROW_HEIGHT = 40

/** 占位行的标记字段，模板与事件回调靠它跳过占位行。 */
export const SPACER_FLAG = '__vtSpacer'

export interface RowWindowParams {
  /** 接口回来的总行数（一次多少就多少，不再往上堆）。 */
  rowCount: Ref<number>
  /** 表头之外可见区的高度（像素）。 */
  viewportHeight: Ref<number>
  /** 超过这个行数才开窗口化：20 条这类常规分页不动 DOM 结构。 */
  threshold?: number
  /** 可见区上下各多渲染几行，快速滚动时不露白。 */
  overscan?: number
}

export interface RowWindow {
  /** 当前行高（像素）：首次渲染后用真实 DOM 校准，斑马纹与占位高度都按它算。 */
  rowHeight: Ref<number>
  /** 表格体现在滚到哪（像素）。 */
  scrollTop: Ref<number>
  /** 窗口化是否生效。 */
  enabled: ComputedRef<boolean>
  /** 窗口首行在整表里的下标（含）。 */
  start: ComputedRef<number>
  /** 窗口末行在整表里的下标（不含）。 */
  end: ComputedRef<number>
  /** 窗口前面没渲染的行数——垫这些行的高度。 */
  padBefore: ComputedRef<number>
  /** 窗口后面没渲染的行数。 */
  padAfter: ComputedRef<number>
  /** 滚动回调。 */
  onScroll: (top: number) => void
  /** 回到顶部（翻页、换筛选后用）。 */
  scrollToTop: () => void
}

export function useTableRowWindow(params: RowWindowParams): RowWindow {
  const threshold = params.threshold ?? 40
  const overscan = params.overscan ?? 4
  const rowHeight = ref(ESTIMATED_ROW_HEIGHT)
  const scrollTop = ref(0)

  const enabled = computed(() => params.rowCount.value > threshold)
  // 可见行数取「视口能放的行数 + 上下缓冲」，宁可多渲染几行也不要滚动露白
  const visibleRows = computed(() =>
    Math.max(6, Math.ceil(params.viewportHeight.value / rowHeight.value) + 1),
  )

  const start = computed(() => {
    if (!enabled.value) {
      return 0
    }
    const raw = Math.floor(scrollTop.value / rowHeight.value) - overscan
    return Math.min(Math.max(raw, 0), Math.max(0, params.rowCount.value - 1))
  })

  const end = computed(() => {
    if (!enabled.value) {
      return params.rowCount.value
    }
    return Math.min(params.rowCount.value, start.value + visibleRows.value + overscan * 2)
  })

  const padBefore = computed(() => (enabled.value ? start.value : 0))
  const padAfter = computed(() => (enabled.value ? Math.max(0, params.rowCount.value - end.value) : 0))

  function onScroll(top: number): void {
    scrollTop.value = top > 0 ? top : 0
  }

  function scrollToTop(): void {
    scrollTop.value = 0
  }

  return { rowHeight, scrollTop, enabled, start, end, padBefore, padAfter, onScroll, scrollToTop }
}

/**
 * 把「窗口前有 N 行没渲染」折成 1~2 个占位行的高度（像素）。
 *
 * 拆两行不只是美观：el-table 的斑马纹按「数据下标奇偶」上色，占位行的数量必须与
 * 窗口起始下标同奇偶，滚动时行的深浅才不会整片翻转。
 */
export function spacerHeights(padRows: number, rowHeight: number): number[] {
  if (padRows <= 0) {
    return []
  }
  const count = padRows % 2 === 0 ? 2 : 1
  const first = Math.floor(padRows / count)
  const heights = Array.from({ length: count }, (_, index) =>
    (index === count - 1 ? padRows - first * (count - 1) : first) * rowHeight,
  )
  return heights.filter((height) => height > 0)
}
