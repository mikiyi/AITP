<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">会话记录</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="MagicStick" @click="router.push('/lab')">回到实验室</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetcher"
        :search-fields="searchFields"
        sync-query
        @row-click="onRowClick"
      >
        <template #module="{ row }">
          <el-tag size="small" effect="plain">{{ capabilityText(row.module) }}</el-tag>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="onRowClick(row)">查看</el-button>
        </template>
      </BaseProTable>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { MagicStick } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import { labApi } from '@/api'
import type { AiSession } from '@/api/types'
import { useLabStore } from '@/stores/lab'

const router = useRouter()
const tableRef = ref<TableExpose | null>(null)
const labStore = useLabStore()

/** 不是单能力、不在能力目录里的会话类型。 */
const MODULE_LABELS: Record<string, string> = {
  lab: '实验室',
  composite: '多入口顺序执行',
}

/** 能力编码 → 中文名称；列表展示中文，筛选仍按编码提交。 */
const capabilityLabels = computed(() => {
  const map = new Map<string, string>(Object.entries(MODULE_LABELS))
  for (const item of labStore.allCapabilities) {
    if (!map.has(item.code)) {
      map.set(item.code, item.label)
    }
  }
  return map
})

function capabilityText(code: string | null | undefined): string {
  if (!code) {
    return '-'
  }
  return capabilityLabels.value.get(code) ?? code
}

const moduleOptions = computed(() => {
  const options = labStore.allCapabilities.map((item) => ({ label: item.label, value: item.code }))
  for (const [code, label] of Object.entries(MODULE_LABELS)) {
    if (!options.some((item) => item.value === code)) {
      options.push({ label, value: code })
    }
  }
  return options
})

const columns: ColumnDef[] = [
  { prop: 'title', label: '会话', minWidth: 240 },
  { prop: 'module', label: '能力', width: 160, slot: 'module' },
  { prop: 'model', label: '模型', width: 160, formatter: (row) => row.model || '-' },
  { prop: 'createdAt', label: '创建时间', width: 180 },
  { prop: 'updatedAt', label: '更新时间', width: 180 },
  { prop: 'id', label: '操作', width: 90, slot: 'actions' },
]

const searchFields = computed<SearchField[]>(() => [
  {
    prop: 'module',
    label: '能力',
    type: 'select',
    options: moduleOptions.value,
  },
])

function fetcher(query: Record<string, unknown>) {
  return labApi.sessions(query)
}

function onRowClick(row: AiSession): void {
  void router.push(`/lab/sessions/${row.id}`)
}

onMounted(() => {
  void labStore.loadAllCapabilities()
})
</script>
