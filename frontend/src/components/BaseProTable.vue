<template>
  <div class="base-pro-table">
    <div v-if="visibleSearchFields.length" class="toolbar">
      <template v-for="field in visibleSearchFields" :key="field.prop">
        <el-select
          v-if="field.type === 'select'"
          v-model="filters[field.prop]"
          :placeholder="field.label"
          clearable
          size="default"
          style="width: 160px"
          @change="reload"
        >
          <el-option
            v-for="option in field.options || []"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-input
          v-else
          v-model="filters[field.prop]"
          :placeholder="field.label"
          clearable
          size="default"
          style="width: 180px"
          @input="scheduleReload"
          @keyup.enter="reload"
          @clear="reload"
        />
      </template>
      <el-button :icon="Refresh" @click="reset">重置</el-button>
      <div class="spacer" />
      <slot name="toolbar" />
    </div>

    <div v-if="selectable && selectedRows.length" class="base-pro-table__batch">
      <span class="muted">已选 {{ selectedRows.length }} 项</span>
      <slot name="batch" :rows="selectedRows" />
      <el-button link size="small" @click="clearSelection">取消选择</el-button>
    </div>

    <div ref="bodyRef" class="base-pro-table__body">
      <el-table
        ref="tableRef"
        v-loading="loading"
        element-loading-text="加载中…"
        :data="renderRows"
        :row-key="rowKey"
        :max-height="boundedMaxHeight"
        :row-class-name="rowClassName"
        :row-style="rowStyle"
        border
        stripe
        style="width: 100%"
        :empty-text="loading ? '加载中…' : '暂无数据'"
        @row-click="onRowClick"
        @selection-change="onSelectionChange"
        @scroll="onTableScroll"
      >
        <el-table-column
          v-if="selectable"
          type="selection"
          width="46"
          align="center"
          :selectable="rowSelectable"
        />
        <el-table-column
          v-for="column in columns"
          :key="column.prop"
          :prop="column.prop"
          :label="column.label"
          :width="column.width"
          :min-width="column.minWidth"
          :align="column.align || 'left'"
          :fixed="column.fixed || false"
          show-overflow-tooltip
        >
          <template #default="scope">
            <template v-if="!isSpacerRow(scope.row)">
              <slot :name="column.slot || column.prop" :row="scope.row" :value="scope.row[column.prop]">
                {{ format(column, scope.row) }}
              </slot>
            </template>
          </template>
        </el-table-column>
        <template #empty>
          <div v-if="firstLoading" class="base-pro-table__skeleton">
            <el-skeleton :rows="skeletonRows" animated />
          </div>
          <span v-else-if="loading" class="muted">加载中…</span>
          <!-- 接口失败不能留在「暂无数据」上：给原因 + 重试入口，否则用户只能靠猜 -->
          <div v-else-if="loadError" class="base-pro-table__error">
            <el-icon class="base-pro-table__error-icon" :size="20"><WarningFilled /></el-icon>
            <p class="base-pro-table__error-text">{{ loadError }}</p>
            <el-button size="small" :icon="Refresh" @click="load">重试</el-button>
          </div>
          <slot v-else name="empty">
            <el-empty :image-size="58" :description="hasActiveFilters ? '没有符合当前筛选的记录' : '暂无数据'">
              <el-button v-if="hasActiveFilters" size="small" @click="reset">重置筛选</el-button>
              <el-button v-else size="small" :icon="Refresh" @click="load">刷新</el-button>
            </el-empty>
          </slot>
        </template>
      </el-table>
    </div>

    <div class="base-pro-table__footer">
      <el-pagination
        v-model:current-page="pageNum"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="PAGE_SIZES"
        layout="total, sizes, prev, pager, next, jumper"
        background
        @current-change="load"
        @size-change="reload"
      />
    </div>
  </div>
</template>

<script setup lang="ts" generic="T extends Record<string, any>">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { TableInstance } from 'element-plus'
import type { CSSProperties } from 'vue'
import { Refresh, WarningFilled } from '@element-plus/icons-vue'
import { useAutoQuery } from '@/composables/useAutoQuery'
import { SPACER_FLAG, spacerHeights, useTableRowWindow } from '@/composables/useTableRowWindow'
import type { PagePayload } from '@/api/request'
import { dateTimeText } from '@/utils/format'

export interface ColumnDef<T = Record<string, any>> {
  prop: string
  label: string
  width?: number
  minWidth?: number
  align?: 'left' | 'center' | 'right'
  /** 固定列，通常操作列用 'right'，横向滚动时仍可见 */
  fixed?: boolean | 'left' | 'right'
  slot?: string
  formatter?: (row: T) => string
}

export interface SearchField {
  prop: string
  label: string
  type?: 'text' | 'select'
  options?: Array<{ label: string; value: string | number }>
  /** 不在工具栏渲染控件，但仍参与筛选、地址栏同步与导出；供左侧树这类外部控件驱动。 */
  hidden?: boolean
}

/** 表格对外暴露的方法，供父组件调用。 */
export interface TableExpose {
  reload: () => void
  load: () => Promise<void>
  clearSelection: () => void
  selectedRows: () => Record<string, any>[]
  filterQuery: () => Record<string, string>
  clearFilter: (prop: string | string[]) => void
  setFilter: (prop: string, value: string | number | undefined | null) => void
}

const props = withDefaults(
  defineProps<{
    columns: ColumnDef[]
    fetcher: (query: Record<string, unknown>) => Promise<PagePayload<T>>
    searchFields?: SearchField[]
    rowKey?: string
    pageSize?: number
    /** 表格体最大高度（像素）；超出时表格内部滚动，分页条紧跟在表格下方。 */
    maxHeight?: number
    autoLoad?: boolean
    extraQuery?: Record<string, unknown>
    /** 开启左侧复选框，配合 #batch 插槽做批量操作。 */
    selectable?: boolean
    /** 把筛选与分页写进地址栏，刷新或粘贴链接都能还原当前视图。 */
    syncQuery?: boolean
    /** 同页面多个表格时用前缀区分各自的查询参数。 */
    queryPrefix?: string
    /** 关掉长列表窗口化（个别页面要靠整表 DOM 做打印、全量勾选时用）。 */
    virtual?: boolean
    /** 超过这个行数才开始只渲染视口内的行。 */
    virtualThreshold?: number
    /** 窗口化生效、页面又没显式给高度时，表格体的最大高度。 */
    virtualMaxHeight?: number
  }>(),
  {
    searchFields: () => [],
    rowKey: 'id',
    pageSize: 20,
    autoLoad: true,
    extraQuery: () => ({}),
    selectable: false,
    syncQuery: false,
    queryPrefix: '',
    virtual: true,
    virtualThreshold: 40,
    virtualMaxHeight: 560,
  },
)

const emit = defineEmits<{
  (event: 'row-click', row: T): void
  (event: 'loaded', payload: PagePayload<T>): void
  (event: 'selection-change', rows: T[]): void
  (event: 'filters-reset'): void
}>()

const route = useRoute()
const router = useRouter()

const rows = ref<T[]>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = ref(clampPageSize(props.pageSize))
const loading = ref(false)
/** 取数失败的原因留在组件里，空态才能给出口而不是假报「暂无数据」。 */
const loadError = ref('')
const filters = reactive<Record<string, string | number | undefined>>({})
const hasActiveFilters = computed(() =>
  Object.values(filters).some((value) => value !== '' && value !== undefined && value !== null),
)
const selectedRows = ref<T[]>([])
const tableRef = ref<TableInstance | null>(null)
const bodyRef = ref<HTMLElement | null>(null)

const PAGE_KEY = 'page'
const SIZE_KEY = 'size'
/** 与后端 `common/pagination.MAX_PAGE_SIZE` 对齐：超过 100 的 pageSize 不进请求。 */
const MAX_PAGE_SIZE = 100
const PAGE_SIZES = [10, 20, 50, 100]
/** 表头高度只用于估算可视行数，差一两像素由上下缓冲吸收。 */
const TABLE_HEADER_HEIGHT = 40

/** 工具栏只渲染有控件的筛选项；hidden 项交给页面里的外部控件（如功能点面板）驱动。 */
const visibleSearchFields = computed(() => props.searchFields.filter((field) => !field.hidden))

/** el-table 内部对行数据做了 Unwrap，事件参数用宽松类型再接回 T。 */
type RawRow = Record<string, any>

function clampPageSize(value: number): number {
  const size = Number.isFinite(value) && value > 0 ? Math.floor(value) : 20
  return Math.min(size, MAX_PAGE_SIZE)
}

function format(column: ColumnDef, row: T): string {
  if (column.formatter) {
    return column.formatter(row)
  }
  const value = row[column.prop]
  if (value === null || value === undefined || value === '') {
    return '-'
  }
  // 接口字段约定：以 At 结尾的都是时间戳，一律转成「本地时间 年-月-日 时:分:秒」
  if (column.prop.endsWith('At')) {
    return dateTimeText(value)
  }
  return String(value)
}

/*
 * ---- 长列表窗口化 ----
 *
 * 行数超过阈值时只把视口内的行交给 el-table，窗口前后插占位行垫回高度：
 * 滚动条总长、表头吸顶、斑马纹、多选、行点击、tooltip 都还是 el-table 原生行为，
 * DOM 里的真实行数从「接口给多少渲多少」收敛到「视口能放多少」。
 */
const tableWindow = useTableRowWindow({
  rowCount: computed(() => rows.value.length),
  viewportHeight: computed(() =>
    Math.max(120, (props.maxHeight ?? props.virtualMaxHeight) - TABLE_HEADER_HEIGHT),
  ),
  threshold: props.virtual ? props.virtualThreshold : Number.MAX_SAFE_INTEGER,
})

/** 窗口化是否真的生效（行数够多且调用方没关掉）。 */
const virtualized = computed(() => props.virtual && tableWindow.enabled.value)

/** 窗口化生效时必须给表格有界高度，否则没有内部滚动条、也就拿不到 scrollTop。 */
const boundedMaxHeight = computed(() =>
  virtualized.value ? (props.maxHeight ?? props.virtualMaxHeight) : props.maxHeight,
)

const skeletonRows = computed(() =>
  Math.max(3, Math.min(10, Math.ceil((props.maxHeight ?? 260) / 48))),
)

interface SpacerMeta {
  which: 'top' | 'bottom'
  height: number
  /** 同侧可能有上下两个占位行，用序号区分高度与 row-key。 */
  slot: number
}

const spacerRows = computed<SpacerMeta[]>(() => {
  if (!virtualized.value) {
    return []
  }
  const list: SpacerMeta[] = []
  spacerHeights(tableWindow.padBefore.value, tableWindow.rowHeight.value).forEach((height, slot) => {
    list.push({ which: 'top', height, slot })
  })
  spacerHeights(tableWindow.padAfter.value, tableWindow.rowHeight.value).forEach((height, slot) => {
    list.push({ which: 'bottom', height, slot })
  })
  return list
})

function isSpacerRow(row: unknown): boolean {
  return (
    typeof row === 'object' &&
    row !== null &&
    (row as Record<string, unknown>)[SPACER_FLAG] !== undefined
  )
}

function spacerMeta(row: unknown): SpacerMeta {
  return row as unknown as SpacerMeta
}

/** 交给 el-table 的数据：顶部占位行 + 视口内的真实行 + 底部占位行。 */
const renderRows = computed<T[]>(() => {
  if (!virtualized.value) {
    return rows.value as unknown as T[]
  }
  const head = spacerRows.value
    .filter((item) => item.which === 'top')
    .map((item) => makeSpacer(item))
  const tail = spacerRows.value
    .filter((item) => item.which === 'bottom')
    .map((item) => makeSpacer(item))
  const slice = rows.value.slice(tableWindow.start.value, tableWindow.end.value)
  return [...head, ...(slice as unknown as Record<string, unknown>[]), ...tail] as unknown as T[]
})

function makeSpacer(meta: SpacerMeta): Record<string, unknown> {
  // 占位行不带业务字段，只带标记；row-key 造一个唯一值，免得两个占位行撞 key
  return {
    [SPACER_FLAG]: `${meta.which}-${meta.slot}`,
    [props.rowKey]: `__vt_${meta.which}_${meta.slot}`,
    which: meta.which,
    height: meta.height,
    slot: meta.slot,
  }
}

function rowClassName(data: { row: RawRow; rowIndex: number }): string {
  return isSpacerRow(data.row) ? 'base-pro-table__spacer' : ''
}

function rowStyle(data: { row: RawRow; rowIndex: number }): CSSProperties {
  if (!isSpacerRow(data.row)) {
    return {}
  }
  return { height: `${spacerMeta(data.row).height}px` }
}

/** 占位行不能被勾选，否则批量操作会拿到一条并不存在的记录。 */
function rowSelectable(row: RawRow): boolean {
  return !isSpacerRow(row)
}

function onTableScroll(event: { scrollTop: number; scrollLeft: number }): void {
  if (!virtualized.value) {
    return
  }
  tableWindow.onScroll(event.scrollTop)
}

function onRowClick(row: RawRow): void {
  if (isSpacerRow(row)) {
    return
  }
  emit('row-click', row as T)
}

function onSelectionChange(selection: RawRow[]): void {
  const next = selection.filter((row) => !isSpacerRow(row)) as T[]
  selectedRows.value = next
  emit('selection-change', next)
}

function clearSelection(): void {
  selectedRows.value = []
  tableRef.value?.clearSelection()
}

/** 量一次真实行高：不同 size、不同列内容下 el-table 的行高并不固定。 */
async function measureRowHeight(): Promise<void> {
  await nextTick()
  const root = bodyRef.value
  if (!root) {
    return
  }
  const tr = root.querySelector(
    'tbody tr.el-table__row:not(.base-pro-table__spacer)',
  ) as HTMLElement | null
  const height = tr?.offsetHeight ?? 0
  if (height >= 20 && height <= 200) {
    tableWindow.rowHeight.value = height
  }
}

watch(
  () => rows.value.length,
  () => {
    if (virtualized.value) {
      void measureRowHeight()
    }
  },
)

let loadSeq = 0

/** 首屏骨架：接口回来前先给 loading / 骨架，不留白屏。 */
const firstLoading = ref(props.autoLoad)

async function load(): Promise<void> {
  const my = ++loadSeq
  loading.value = true
  try {
    const payload = await props.fetcher({
      pageNum: pageNum.value,
      pageSize: pageSize.value,
      ...props.extraQuery,
      ...filters,
    })
    // 连续切换筛选/翻页时只认最后一次请求，避免慢请求把新结果覆盖回去
    if (my !== loadSeq) {
      return
    }
    const list = payload.list ?? []
    const totalCount = payload.total ?? 0
    // 删到最后一页时当前页码可能已经越界，自动退回最后一页再拉一次
    if (!list.length && totalCount > 0 && pageNum.value > 1) {
      const lastPage = Math.max(1, Math.ceil(totalCount / pageSize.value))
      if (lastPage < pageNum.value) {
        pageNum.value = lastPage
        void load()
        return
      }
    }
    rows.value = list
    total.value = totalCount
    loadError.value = ''
    resetScrollToTop()
    emit('loaded', payload)
    syncToQuery()
  } catch (err) {
    if (my !== loadSeq) {
      return
    }
    rows.value = []
    total.value = 0
    resetScrollToTop()
    const message = err instanceof Error ? err.message : '加载失败'
    loadError.value = message
    ElMessage.error(message)
  } finally {
    if (my === loadSeq) {
      loading.value = false
      firstLoading.value = false
    }
  }
}

/** 翻页 / 换筛选后回到顶部：窗口化下留着旧滚动位置会停在上一页那一段里。 */
function resetScrollToTop(): void {
  tableWindow.scrollToTop()
  tableRef.value?.setScrollTop(0)
}

function reload(): void {
  pageNum.value = 1
  void load()
}

/*
 * 筛选条件即改即查：下拉/清空立即重查，输入框打打字要防抖，
 * 否则每敲一个字都发一次请求。有输入型筛选项时不再放「查询」按钮，
 * 避免「选了自动查、打了要点按钮」两套口径打架。
 */
const scheduleReload = useAutoQuery(reload)

function reset(): void {
  Object.keys(filters).forEach((key) => {
    delete filters[key]
  })
  emit('filters-reset')
  reload()
}

/** 当前筛选条件（不含分页与前缀），供导出等场景复用。 */
function filterQuery(): Record<string, string> {
  const query: Record<string, string> = {}
  props.searchFields.forEach((field) => {
    const value = filters[field.prop]
    if (value !== undefined && value !== null && String(value) !== '') {
      query[field.prop] = String(value)
    }
  })
  return query
}

/**
 * 外部联动用：清掉筛选项并重新加载。
 * 页面切换项目后，旧项目的版本、功能点等筛选项在新项目里不存在，留着会把列表筛成空。
 */
function clearFilter(prop: string | string[]): void {
  const list = Array.isArray(prop) ? prop : [prop]
  list.forEach((key) => {
    delete filters[key]
  })
  reload()
}

/** 外部联动用：把某个筛选项设成给定值并重查（空值等同清除），配合隐藏筛选项使用。 */
function setFilter(prop: string, value: string | number | undefined | null): void {
  if (value === undefined || value === null || String(value) === '') {
    delete filters[prop]
  } else {
    filters[prop] = value
  }
  reload()
}

// ---- 查询参数与地址栏同步 ----

/** 本表格负责的查询键；只比较这些键，避免同页多表格互相触发重载。 */
function ownKeys(): string[] {
  const keys = props.searchFields.map((field) => `${props.queryPrefix}${field.prop}`)
  keys.push(`${props.queryPrefix}${PAGE_KEY}`, `${props.queryPrefix}${SIZE_KEY}`)
  return keys
}

function stateToQuery(): Record<string, string> {
  const query: Record<string, string> = {}
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== '') {
      query[`${props.queryPrefix}${key}`] = String(value)
    }
  })
  if (pageNum.value !== 1) {
    query[`${props.queryPrefix}${PAGE_KEY}`] = String(pageNum.value)
  }
  if (pageSize.value !== props.pageSize) {
    query[`${props.queryPrefix}${SIZE_KEY}`] = String(pageSize.value)
  }
  return query
}

function firstValue(raw: unknown): string {
  if (Array.isArray(raw)) {
    return String(raw[0] ?? '')
  }
  return raw === undefined || raw === null ? '' : String(raw)
}

function optionValue(prop: string, raw: string): string | number {
  const field = props.searchFields.find((item) => item.prop === prop)
  const hit = field?.options?.find((item) => String(item.value) === raw)
  return hit ? hit.value : raw
}

function isSameAsState(): boolean {
  const next = stateToQuery()
  const current = route.query
  return ownKeys().every((key) => firstValue(current[key]) === (next[key] ?? ''))
}

function syncToQuery(): void {
  if (!props.syncQuery || isSameAsState()) {
    return
  }
  // 只改写自己的键，保留同页其他表格的参数，否则两个表格会互相清掉对方的筛选
  const next: Record<string, string | null | (string | null)[]> = { ...route.query }
  ownKeys().forEach((key) => {
    delete next[key]
  })
  Object.assign(next, stateToQuery())
  void router.replace({ query: next })
}

function hydrateFromQuery(): void {
  const current = route.query
  Object.keys(filters).forEach((key) => {
    delete filters[key]
  })
  props.searchFields.forEach((field) => {
    const raw = firstValue(current[`${props.queryPrefix}${field.prop}`])
    if (raw) {
      filters[field.prop] = field.type === 'select' ? optionValue(field.prop, raw) : raw
    }
  })
  const page = Number(firstValue(current[`${props.queryPrefix}${PAGE_KEY}`]))
  pageNum.value = Number.isInteger(page) && page > 1 ? page : 1
  const size = Number(firstValue(current[`${props.queryPrefix}${SIZE_KEY}`]))
  if (Number.isInteger(size) && PAGE_SIZES.includes(size)) {
    pageSize.value = size
  }
}

watch(
  () => route.query,
  () => {
    if (!props.syncQuery || isSameAsState()) {
      return
    }
    hydrateFromQuery()
    void load()
  },
)

onMounted(() => {
  if (props.syncQuery) {
    hydrateFromQuery()
  }
  if (props.autoLoad) {
    void load()
  }
  void measureRowHeight()
})

defineExpose({
  reload,
  load,
  clearSelection,
  selectedRows: () => selectedRows.value,
  filterQuery,
  clearFilter,
  setFilter,
})
</script>

<style scoped>
.base-pro-table__footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.base-pro-table__batch {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  padding: 8px 12px;
  font-size: 13px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

/* 首屏骨架撑出与表体相当的区块，避免「点了没反应」的空白 */
.base-pro-table__skeleton {
  width: 100%;
  padding: 10px 14px;
  text-align: left;
}

.base-pro-table__error {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  padding: 18px 14px;
}

.base-pro-table__error-icon {
  color: var(--el-color-danger);
}

.base-pro-table__error-text {
  margin: 0;
  max-width: 520px;
  font-size: 13px;
  line-height: 1.6;
  text-align: center;
  color: var(--aitp-text);
  word-break: break-word;
}

/*
 * 占位行只负责把没渲染的行数垫成高度：去掉内边距与边框、不响应 hover 与点击，
 * 这样滚动条总长与真实整表一致，视觉上就是一段表格底色。
 */
.base-pro-table__body :deep(tr.base-pro-table__spacer) {
  pointer-events: none;
}

.base-pro-table__body :deep(tr.base-pro-table__spacer td.el-table__cell) {
  padding: 0;
  border-right-color: transparent;
  border-bottom-color: transparent;
}

.base-pro-table__body :deep(tr.base-pro-table__spacer .cell) {
  min-height: 0;
  line-height: 0;
  white-space: nowrap;
}

.base-pro-table__body :deep(tr.base-pro-table__spacer .el-checkbox) {
  display: none;
}

.base-pro-table__body :deep(.el-table__empty-block) {
  min-height: 120px;
}
</style>
