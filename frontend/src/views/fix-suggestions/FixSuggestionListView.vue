<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">修复建议</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <div class="filter-row">
        <el-radio-group v-model="status" size="small" @change="load">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button v-for="item in statuses" :key="item.value" :value="item.value">
            {{ item.label }}（{{ counts[item.value] ?? 0 }}）
          </el-radio-button>
        </el-radio-group>
        <span class="hint">AI 诊断断言失败后给出资产修改建议，采纳前请核对 diff，AI 不会直接改库。</span>
      </div>

      <el-table
        v-loading="loading"
        :data="rows"
        row-key="id"
        class="fix-suggestion-table"
        empty-text="暂无修复建议：到接口用例执行失败后点「生成修复建议」"
        @row-click="openDetail"
      >
        <el-table-column prop="suggestionNo" label="建议编号" width="130" />
        <el-table-column prop="caseNo" label="用例" width="110" />
        <el-table-column label="建议目标" width="140">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.targetLabel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="summary" label="建议摘要" min-width="260" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTag(row.status)" effect="plain">{{ row.statusLabel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="createdAt" label="生成时间" width="180" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openDetail(row)">查看</el-button>
            <el-button
              v-if="row.status === 'pending'"
              link
              type="success"
              @click.stop="adopt(row)"
            >采纳</el-button>
            <el-button
              v-if="row.status === 'pending'"
              link
              type="danger"
              @click.stop="dismiss(row)"
            >忽略</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-drawer v-model="drawerVisible" title="修复建议详情" size="560px">
      <div v-if="current" class="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="建议编号">{{ current.suggestionNo }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ current.statusLabel }}</el-descriptions-item>
          <el-descriptions-item label="目标用例">{{ current.caseNo }}</el-descriptions-item>
          <el-descriptions-item label="建议目标">{{ current.targetLabel }}</el-descriptions-item>
          <el-descriptions-item label="生成模型" :span="2">{{ current.model || '—' }}</el-descriptions-item>
        </el-descriptions>

        <h4 class="section-title">诊断</h4>
        <p class="text-block">{{ current.analysis || '—' }}</p>

        <h4 class="section-title">建议</h4>
        <p class="text-block">{{ current.summary || '—' }}</p>
        <p v-if="current.reason" class="text-block muted">依据：{{ current.reason }}</p>

        <h4 class="section-title">变更预览</h4>
        <pre class="diff-block">{{ current.diffPreview || '（该目标无需字段级 diff）' }}</pre>

        <div v-if="current.status === 'pending'" class="drawer-actions">
          <el-button @click="dismiss(current)">忽略</el-button>
          <el-button type="primary" :loading="acting" @click="adopt(current)">采纳并更新用例</el-button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { fixSuggestionApi } from '@/api'
import type { FixSuggestion } from '@/api/types'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()

const loading = ref(false)
const acting = ref(false)
const rows = ref<FixSuggestion[]>([])
const statuses = ref<Array<{ value: string; label: string }>>([])
const counts = ref<Record<string, number>>({})
const status = ref('')
const drawerVisible = ref(false)
const current = ref<FixSuggestion | null>(null)

function statusTag(value: string): 'success' | 'info' | 'warning' {
  if (value === 'applied') return 'success'
  if (value === 'dismissed') return 'info'
  return 'warning'
}

async function load(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) {
    rows.value = []
    return
  }
  loading.value = true
  try {
    const payload = await fixSuggestionApi.list({
      projectId,
      pageSize: 100,
      ...(status.value ? { status: status.value } : {}),
    })
    rows.value = payload.list
    counts.value = payload.counts
    statuses.value = payload.statuses
  } finally {
    loading.value = false
  }
}

function openDetail(row: FixSuggestion): void {
  current.value = row
  drawerVisible.value = true
}

async function adopt(row: FixSuggestion): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) return
  if (row.targetType === 'bug') {
    ElMessage.warning('该建议判定为接口缺陷，请到缺陷页手工建单')
    return
  }
  try {
    await ElMessageBox.confirm(
      `采纳后将把新值写入用例 ${row.caseNo} 的对应字段，是否继续？`,
      '采纳修复建议',
      { type: 'warning' },
    )
  } catch {
    return
  }
  acting.value = true
  try {
    const result = await fixSuggestionApi.adopt(row.id, projectId)
    ElMessage.success(`已采纳，用例字段「${result.field}」已更新`)
    drawerVisible.value = false
    await load()
  } finally {
    acting.value = false
  }
}

async function dismiss(row: FixSuggestion): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) return
  acting.value = true
  try {
    await fixSuggestionApi.dismiss(row.id, projectId)
    ElMessage.success('已忽略')
    drawerVisible.value = false
    await load()
  } finally {
    acting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.fix-suggestion-table :deep(.el-table__row) {
  cursor: pointer;
}
.detail {
  padding: 0 4px;
}
.section-title {
  margin: 18px 0 8px;
  font-size: 14px;
}
.text-block {
  margin: 0 0 8px;
  line-height: 1.7;
  white-space: pre-wrap;
}
.text-block.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.diff-block {
  margin: 0;
  padding: 10px 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow: auto;
}
.drawer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}
</style>
