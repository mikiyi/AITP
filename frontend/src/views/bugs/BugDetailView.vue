<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">缺陷详情</h2>
      <el-button :icon="Back" @click="router.back()">返回</el-button>
    </div>

    <el-skeleton v-if="loading" :rows="6" animated />

    <div v-else-if="detail" class="bug-detail">
      <div class="panel panel-pad">
        <div class="bug-detail__head">
          <div>
            <div class="muted">{{ detail.bugNo }}</div>
            <h3 class="bug-detail__title">{{ detail.title }}</h3>
          </div>
          <div class="bug-detail__tags">
            <el-tag :type="severityTag(detail.severity)" size="small" effect="plain">{{ detail.severity }}</el-tag>
            <el-tag :type="statusTag(detail.status)" size="small" effect="plain">
              {{ statusLabel(detail.status) }}
            </el-tag>
          </div>
        </div>

        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="类型">{{ detail.bugType ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="指派">{{ detail.assignee || '-' }}</el-descriptions-item>
          <el-descriptions-item label="报告人">{{ detail.reporter || '-' }}</el-descriptions-item>
          <el-descriptions-item label="版本">{{ detail.version || '-' }}</el-descriptions-item>
          <el-descriptions-item label="环境">{{ detail.env || '-' }}</el-descriptions-item>
          <el-descriptions-item label="功能点">{{ detail.moduleId ?? '-' }}</el-descriptions-item>
        </el-descriptions>
      </div>

      <div class="panel panel-pad">
        <div class="section-title">描述</div>
        <div class="pre">{{ detail.description || '无' }}</div>
        <div class="section-title">复现步骤</div>
        <div class="pre">{{ (detail.steps || []).join('\n') || '无' }}</div>
        <div class="section-title">实际结果 / 预期结果</div>
        <div class="pre">{{ detail.actualResult || '无' }}</div>
        <div class="pre">{{ detail.expectedResult || '无' }}</div>
      </div>

      <div class="panel panel-pad">
        <div class="section-title">状态流转</div>
        <div class="toolbar">
          <el-select v-model="nextStatus" placeholder="选择目标状态" style="width: 180px">
            <el-option
              v-for="item in allowedTransitions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <el-input v-model="comment" placeholder="流转说明（选填）" style="flex: 1" />
          <el-button type="primary" :loading="transitioning" @click="doTransition">流转</el-button>
        </div>

        <el-table :data="logs" size="small" border empty-text="暂无流转记录">
          <el-table-column prop="fromStatus" label="原状态" width="110" />
          <el-table-column prop="toStatus" label="新状态" width="110" />
          <el-table-column prop="operator" label="操作人" width="120" />
          <el-table-column prop="comment" label="说明" min-width="200" />
          <el-table-column prop="createdAt" label="时间" width="170" />
        </el-table>
      </div>
    </div>

    <el-empty v-else description="缺陷不存在" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back } from '@element-plus/icons-vue'
import { bugApi } from '@/api'
import type { Bug, BugLog } from '@/api/types'

const route = useRoute()
const router = useRouter()
const detail = ref<Bug | null>(null)
const logs = ref<BugLog[]>([])
const loading = ref(true)
const nextStatus = ref('')
const comment = ref('')
const transitioning = ref(false)

const transitions: Record<string, string[]> = {
  open: ['assigned', 'fixed', 'rejected', 'closed'],
  assigned: ['open', 'fixed', 'rejected', 'closed'],
  fixed: ['retest', 'reopened', 'closed'],
  retest: ['fixed', 'closed', 'reopened'],
  reopened: ['assigned', 'fixed', 'closed'],
  closed: ['reopened'],
  rejected: ['open', 'closed'],
}

const labels: Record<string, string> = {
  open: '新建',
  assigned: '已指派',
  fixed: '已修复',
  retest: '待验证',
  reopened: '重新打开',
  closed: '已关闭',
  rejected: '已拒绝',
}

const allowedTransitions = computed(() =>
  (transitions[detail.value?.status ?? 'open'] ?? []).map((value) => ({
    value,
    label: labels[value] ?? value,
  })),
)

function statusLabel(value: string): string {
  return labels[value] ?? value
}

function severityTag(value: string): 'danger' | 'warning' | 'info' | 'primary' {
  if (value === 'P0') {
    return 'danger'
  }
  if (value === 'P1') {
    return 'warning'
  }
  if (value === 'P2') {
    return 'info'
  }
  return 'primary'
}

function statusTag(value: string): 'success' | 'danger' | 'warning' | 'info' | 'primary' {
  if (value === 'closed') {
    return 'success'
  }
  if (value === 'rejected') {
    return 'info'
  }
  if (value === 'reopened') {
    return 'danger'
  }
  if (value === 'fixed') {
    return 'primary'
  }
  return 'warning'
}

async function loadLogs(): Promise<void> {
  try {
    logs.value = await bugApi.logs(Number(route.params.id))
  } catch {
    logs.value = []
  }
}

async function doTransition(): Promise<void> {
  if (!nextStatus.value) {
    ElMessage.warning('请选择目标状态')
    return
  }
  transitioning.value = true
  try {
    await bugApi.transition(Number(route.params.id), {
      toStatus: nextStatus.value,
      comment: comment.value,
    })
    ElMessage.success('流转成功')
    nextStatus.value = ''
    comment.value = ''
    detail.value = await bugApi.get(Number(route.params.id))
    await loadLogs()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '流转失败')
  } finally {
    transitioning.value = false
  }
}

onMounted(async () => {
  try {
    detail.value = await bugApi.get(Number(route.params.id))
    await loadLogs()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载缺陷失败')
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.bug-detail__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.bug-detail__title {
  margin: 4px 0 0;
  font-size: 17px;
}

.bug-detail__tags {
  display: flex;
  gap: 6px;
}

.section-title {
  font-weight: 600;
  font-size: 13px;
  margin: 14px 0 8px;
}

.pre {
  white-space: pre-wrap;
  line-height: 1.7;
  background: #f7f9fc;
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  padding: 10px 12px;
}
</style>
