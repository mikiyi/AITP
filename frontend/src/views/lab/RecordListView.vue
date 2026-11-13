<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">AI 执行记录</h2>
      <div class="toolbar" style="margin: 0">
        <el-tag type="warning" effect="plain">待复核 {{ stats.pending }}</el-tag>
        <el-tag type="info" effect="plain">无需复核 {{ stats.notRequired }}</el-tag>
        <el-tag type="success" effect="plain">已采纳 {{ stats.approved }}</el-tag>
        <el-tag type="danger" effect="plain">已驳回 {{ stats.rejected }}</el-tag>
        <el-button :icon="MagicStick" @click="router.push('/lab')">回到实验室</el-button>
      </div>
    </div>

    <RouteSubNav :items="recordTabs" />

    <div class="panel panel-pad">
      <el-alert
        type="info"
        show-icon
        :closable="false"
        title="AI 不代替人拍板：涉及 P0/P1、Prompt 测试或引用无法核实的产出，必须人工复核后才能采纳。"
        style="margin-bottom: 12px"
      />
      <BaseProTable
        :columns="columns"
        :fetcher="fetcher"
        :search-fields="searchFields"
        sync-query
        query-prefix="rec_"
        @row-click="onRowClick"
        @loaded="onLoaded"
      >
        <template #capability="{ row }">
          <el-tag size="small" effect="plain">{{ row.capabilityLabel || row.capability }}</el-tag>
        </template>
        <template #riskLevel="{ row }">
          <el-tag size="small" :type="riskTagType(row.riskLevel)">{{ riskText(row.riskLevel) }}</el-tag>
        </template>
        <template #review="{ row }">
          <el-tag size="small" :type="reviewTagType(row)">{{ reviewText(row) }}</el-tag>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="onRowClick(row)">查看</el-button>
        </template>
      </BaseProTable>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { MagicStick } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField } from '@/components/BaseProTable.vue'
import RouteSubNav from '@/components/RouteSubNav.vue'
import { recordApi } from '@/api'
import type { AiRecord, AiRecordPage } from '@/api/types'
import type { PagePayload } from '@/api/request'
import { useLabStore } from '@/stores/lab'

const router = useRouter()
const labStore = useLabStore()
const recordTabs = [
  { path: '/lab/sessions', label: '会话记录' },
  { path: '/lab/records', label: '执行记录' },
]

const stats = reactive({ pending: 0, notRequired: 0, approved: 0, rejected: 0 })

/** 顺序编排不是能力清单里的条目，但执行记录会显示它，筛选项要能选到。 */
const EXTRA_CAPABILITY_LABELS: Record<string, string> = {
  composite: '多入口顺序执行',
}

const columns: ColumnDef[] = [
  { prop: 'capabilityLabel', label: '能力', width: 150, slot: 'capability' },
  { prop: 'summary', label: '结论摘要', minWidth: 260 },
  { prop: 'riskLevel', label: '风险', width: 100, slot: 'riskLevel' },
  { prop: 'reviewStatus', label: '复核状态', width: 120, slot: 'review' },
  { prop: 'model', label: '模型', width: 150, formatter: (row) => row.model || '-' },
  { prop: 'createdAt', label: '执行时间', width: 180 },
  { prop: 'id', label: '操作', width: 90, slot: 'actions' },
]

const capabilityOptions = computed(() => {
  const options = labStore.allCapabilities.map((item) => ({ label: item.label, value: item.code }))
  for (const [code, label] of Object.entries(EXTRA_CAPABILITY_LABELS)) {
    if (!options.some((item) => item.value === code)) options.push({ label, value: code })
  }
  return options
})

const searchFields = computed<SearchField[]>(() => [
  {
    prop: 'capability',
    label: '能力',
    type: 'select',
    options: capabilityOptions.value,
  },
  {
    prop: 'reviewStatus',
    label: '复核状态',
    type: 'select',
    options: [
      { label: '待复核', value: 'pending' },
      { label: '无需复核', value: 'not_required' },
      { label: '已采纳', value: 'approved' },
      { label: '已驳回', value: 'rejected' },
    ],
  },
  {
    prop: 'riskLevel',
    label: '风险等级',
    type: 'select',
    options: [
      { label: '高风险', value: 'high' },
      { label: '低风险', value: 'low' },
      { label: '无风险', value: 'none' },
    ],
  },
])

function fetcher(query: Record<string, unknown>) {
  return recordApi.list(query)
}

function onLoaded(payload: PagePayload<AiRecord>): void {
  const counts = (payload as AiRecordPage).stats
  stats.pending = counts?.pending ?? 0
  stats.notRequired = counts?.notRequired ?? 0
  stats.approved = counts?.approved ?? 0
  stats.rejected = counts?.rejected ?? 0
}

function riskText(level: string): string {
  if (level === 'high') return '高风险'
  if (level === 'low') return '低风险'
  return '无风险'
}

function riskTagType(level: string): 'danger' | 'warning' | 'info' {
  if (level === 'high') return 'danger'
  if (level === 'low') return 'warning'
  return 'info'
}

function reviewText(row: AiRecord): string {
  if (row.reviewStatus === 'approved') return '已采纳'
  if (row.reviewStatus === 'rejected') return '已驳回'
  return row.reviewRequired ? '待复核' : '无需复核'
}

function reviewTagType(row: AiRecord): 'success' | 'danger' | 'warning' | 'info' {
  if (row.reviewStatus === 'approved') return 'success'
  if (row.reviewStatus === 'rejected') return 'danger'
  return row.reviewRequired ? 'warning' : 'info'
}

function onRowClick(row: AiRecord): void {
  void router.push(`/lab/records/${row.id}`)
}

onMounted(() => {
  void labStore.loadAllCapabilities()
})
</script>
