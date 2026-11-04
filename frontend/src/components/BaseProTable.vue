<template>
  <div class="base-pro-table">
    <div v-if="searchFields.length" class="toolbar">
      <template v-for="field in searchFields" :key="field.prop">
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
          @keyup.enter="reload"
          @clear="reload"
        />
      </template>
      <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
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
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { TableInstance } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
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
}

/** 表格对外暴露的方法，供父组件调用。 */
export interface TableExpose {
  reload: () => void
  load: () => Promise<void>
  clearSelection: () => void
  selectedRows: () => Record<string, any>[]
  filterQuery: () => Record<string, string>
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

async function load(): Promise<void> {
  loading.value = true
  try {
    const payload = await props.fetcher({
      pageNum: pageNum.value,
      pageSize: pageSize.value,
      ...props.extraQuery,
      ...filters,
    })
    rows.value = payload.list ?? []
    total.value = payload.total ?? 0
    emit('loaded', payload)
    syncToQuery()
  } catch (err) {
    rows.value = []
    total.value = 0
    const message = err instanceof Error ? err.message : '加载失败'
    ElMessage.error(message)
  } finally {
    loading.value = false
  }
}

function reload(): void {
  pageNum.value = 1
  void load()
}

function reset(): void {
  Object.keys(filters).forEach((key) => {
    delete filters[key]
  })
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
