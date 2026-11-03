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

    <el-table
      v-loading="loading"
      :data="rows"
      :row-key="rowKey"
      border
      stripe
      style="width: 100%"
      :empty-text="loading ? '加载中…' : '暂无数据'"
      @row-click="onRowClick"
    >
      <el-table-column
        v-for="column in columns"
        :key="column.prop"
        :prop="column.prop"
        :label="column.label"
        :width="column.width"
        :min-width="column.minWidth"
        :align="column.align || 'left'"
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
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import type { PagePayload } from '@/api/request'

export interface ColumnDef<T = Record<string, any>> {
  prop: string
  label: string
  width?: number
  minWidth?: number
  align?: 'left' | 'center' | 'right'
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
}

const props = withDefaults(
  defineProps<{
    columns: ColumnDef[]
    fetcher: (query: Record<string, unknown>) => Promise<PagePayload<T>>
    searchFields?: SearchField[]
    rowKey?: string
    pageSize?: number
    autoLoad?: boolean
    extraQuery?: Record<string, unknown>
  }>(),
  {
    searchFields: () => [],
    rowKey: 'id',
    pageSize: 20,
    autoLoad: true,
    extraQuery: () => ({}),
  },
)

const emit = defineEmits<{
  (event: 'row-click', row: T): void
  (event: 'loaded', payload: PagePayload<T>): void
}>()

const rows = ref<T[]>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = ref(props.pageSize)
const loading = ref(false)
const filters = reactive<Record<string, string | number | undefined>>({})

function format(column: ColumnDef, row: T): string {
  if (column.formatter) {
    return column.formatter(row)
  }
  const value = row[column.prop]
  return value === null || value === undefined || value === '' ? '-' : String(value)
}

function onRowClick(row: T): void {
  emit('row-click', row)
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

onMounted(() => {
  if (props.autoLoad) {
    void load()
  }
})

defineExpose({ reload, load })
</script>

<style scoped>
.base-pro-table__footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
