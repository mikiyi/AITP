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

    <el-table
      ref="tableRef"
      v-loading="loading"
      :data="rows"
      :row-key="rowKey"
      :max-height="maxHeight"
      border
      stripe
      style="width: 100%"
      :empty-text="loading ? '加载中…' : '暂无数据'"
      @row-click="onRowClick"
      @selection-change="onSelectionChange"
    >
      <el-table-column v-if="selectable" type="selection" width="46" align="center" />
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
          <slot :name="column.slot || column.prop" :row="scope.row" :value="scope.row[column.prop]">
            {{ format(column, scope.row) }}
          </slot>
        </template>
      </el-table-column>
      <template #empty>
        <span class="muted">{{ loading ? '加载中…' : '暂无数据' }}</span>
      </template>
    </el-table>

    <div class="base-pro-table__footer">
      <el-pagination
        v-model:current-page="pageNum"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        background
        @current-change="load"
        @size-change="reload"
      />
    </div>
  </div>
</template>

<script setup lang="ts" generic="T extends Record<string, any>">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { TableInstance } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { useAutoQuery } from '@/composables/useAutoQuery'
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
const pageSize = ref(props.pageSize)
const loading = ref(false)
const filters = reactive<Record<string, string | number | undefined>>({})
const selectedRows = ref<T[]>([])
const tableRef = ref<TableInstance | null>(null)

const PAGE_KEY = 'page'
const SIZE_KEY = 'size'
const PAGE_SIZES = [10, 20, 50, 100]

/** 工具栏只渲染有控件的筛选项；hidden 项交给页面里的外部控件（如功能点面板）驱动。 */
const visibleSearchFields = computed(() => props.searchFields.filter((field) => !field.hidden))

/** el-table 内部对行数据做了 Unwrap，事件参数用宽松类型再接回 T。 */
type RawRow = Record<string, any>

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

function onRowClick(row: RawRow): void {
  emit('row-click', row as T)
}

function onSelectionChange(selection: RawRow[]): void {
  const next = selection as T[]
  selectedRows.value = next
  emit('selection-change', next)
}

function clearSelection(): void {
  selectedRows.value = []
  tableRef.value?.clearSelection()
}

let loadSeq = 0

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
    emit('loaded', payload)
    syncToQuery()
  } catch (err) {
    if (my !== loadSeq) {
      return
    }
    rows.value = []
    total.value = 0
    const message = err instanceof Error ? err.message : '加载失败'
    ElMessage.error(message)
  } finally {
    if (my === loadSeq) {
      loading.value = false
    }
  }
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
</style>
