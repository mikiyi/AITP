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
        @row-click="onRowClick"
      >
        <template #module="{ row }">
          <el-tag size="small" effect="plain">{{ row.module || '-' }}</el-tag>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="onRowClick(row)">查看</el-button>
        </template>
      </BaseProTable>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { MagicStick } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import { labApi } from '@/api'
import type { AiSession } from '@/api/types'

const router = useRouter()
const tableRef = ref<TableExpose | null>(null)

const columns: ColumnDef[] = [
  { prop: 'title', label: '会话', minWidth: 240 },
  { prop: 'module', label: '能力', width: 160, slot: 'module' },
  { prop: 'model', label: '模型', width: 160 },
  { prop: 'createdAt', label: '创建时间', width: 180 },
  { prop: 'updatedAt', label: '更新时间', width: 180 },
  { prop: 'id', label: '操作', width: 90, slot: 'actions' },
]

const searchFields: SearchField[] = [{ prop: 'module', label: '能力编号（如 bug_analysis）' }]

function fetcher(query: Record<string, unknown>) {
  return labApi.sessions(query)
}

function onRowClick(row: AiSession): void {
  void router.push(`/lab/sessions/${row.id}`)
}
</script>
