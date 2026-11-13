<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">探索会话 {{ detail?.session?.sessionNo }}</h2>
      <el-button :icon="Back" @click="router.push('/exploratory')">返回列表</el-button>
    </div>

    <div v-if="detail" class="panel panel-pad">
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="名称">{{ detail.session.name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="目标地址" :span="2">
          <el-link :href="detail.session.targetUrl" target="_blank" type="primary">
            {{ detail.session.targetUrl }}
          </el-link>
        </el-descriptions-item>
        <el-descriptions-item label="驱动">
          {{ driverLabel(detail.session.driver) }}
          <el-tag v-if="detail.session.degraded" size="small" type="warning" effect="plain">已降级</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag size="small" :type="statusTagType(detail.session.status)" effect="plain">
            {{ statusLabel(detail.session.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="步数进度">
          {{ detail.session.stepDone }} / {{ detail.session.stepTotal }}
        </el-descriptions-item>
        <el-descriptions-item label="异常数">
          <el-tag v-if="detail.session.anomalyCount" size="small" type="danger" effect="plain">
            {{ detail.session.anomalyCount }}
          </el-tag>
          <span v-else>0</span>
        </el-descriptions-item>
        <el-descriptions-item label="产物数">{{ detail.session.draftCount }}</el-descriptions-item>
        <el-descriptions-item label="目标时长">{{ detail.session.maxSeconds }}s</el-descriptions-item>
        <el-descriptions-item label="开始 / 结束">
          {{ dateTimeText(detail.session.startedAt) || '-' }} ~ {{ dateTimeText(detail.session.finishedAt) || '-' }}
        </el-descriptions-item>
      </el-descriptions>

      <el-alert v-if="detail.session.errorMessage" type="error" :closable="false" show-icon
                :title="detail.session.errorMessage" style="margin-top: 12px" />
      <el-alert v-if="summaryNotes.length" type="warning" :closable="false" show-icon
                :title="summaryNotes.join('；')" style="margin-top: 12px" />

      <div class="toolbar" style="margin-top: 16px">
        <h3 style="margin: 0">探索步骤</h3>
        <el-button size="small" :loading="polling" @click="load">刷新</el-button>
        <el-button v-if="detail.session.taskId" size="small" @click="openTask">
          任务 #{{ detail.session.taskId }}
        </el-button>
      </div>

      <el-table :data="detail.steps" border stripe size="small" style="margin-top: 8px">
        <el-table-column label="步" width="60">
          <template #default="scope">{{ scope.row.stepNo }}</template>
        </el-table-column>
        <el-table-column prop="action" label="动作" width="100" />
        <el-table-column prop="selector" label="选择器" min-width="140" show-overflow-tooltip>
          <template #default="scope">
            <span v-if="scope.row.selector">{{ scope.row.selector }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="inputMasked" label="输入" min-width="120" show-overflow-tooltip>
          <template #default="scope">
            <el-tag v-if="scope.row.inputMasked === '[输入已脱敏]'" size="small" type="success" effect="plain">
              输入已脱敏
            </el-tag>
            <span v-else-if="scope.row.inputMasked">{{ scope.row.inputMasked }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="observation" label="观测" min-width="200" show-overflow-tooltip />
        <el-table-column label="异常" width="110">
          <template #default="scope">
            <el-tag v-if="scope.row.isAnomaly" size="small" type="danger" effect="plain">
              {{ anomalyLabel(scope.row.anomalyType) }}
            </el-tag>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="traceId" width="130" show-overflow-tooltip>
          <template #default="scope">
            <span v-if="scope.row.traceId" class="mono">{{ scope.row.traceId }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="证据 / 产物" width="150" fixed="right">
          <template #default="scope">
            <el-button v-if="scope.row.evidenceId" link type="primary"
                       @click="showEvidence(scope.row)">查看证据</el-button>
            <el-tag v-if="scope.row.draftId" size="small" type="success" effect="plain">已入待审池</el-tag>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">尚未执行：回列表点「运行」开始探索</span>
        </template>
      </el-table>
    </div>

    <el-drawer v-model="evidenceVisible" title="异常证据包" size="52%">
      <div v-if="evidence">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="来源">{{ evidence.sourceTypeLabel }}</el-descriptions-item>
          <el-descriptions-item label="结论">
            <el-tag size="small" type="danger" effect="plain">{{ evidence.resultLabel || evidence.result }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="traceId" :span="2">
            <span class="mono">{{ evidence.traceId || '-' }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <h4 style="margin-top: 16px">异常截图（{{ evidence.attachments?.length || 0 }} 张）</h4>
        <div v-if="evidence.attachments?.length" class="shot-list">
          <div v-for="(att, index) in evidence.attachments" :key="index" class="shot-item">
            <el-tag size="small" effect="plain">{{ att.fileName || `截图 ${index + 1}` }}</el-tag>
          </div>
        </div>

        <h4 style="margin-top: 16px">网络快照</h4>
        <pre class="evidence-block">{{ evidence.requestSummary || '（无网络请求）' }}</pre>

        <h4 style="margin-top: 16px">控制台</h4>
        <pre class="evidence-block">{{ evidence.responseSummary || '（无控制台输出）' }}</pre>

        <h4 style="margin-top: 16px">双锚点日志切片</h4>
        <pre class="evidence-block">{{ evidence.logSummary || '（无日志切片）' }}</pre>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back } from '@element-plus/icons-vue'
import { exploratoryApi } from '@/api'
import { dateTimeText } from '@/utils/format'
import type { ExplorationSessionDetail, ExploratoryOption, ExecutionEvidence } from '@/api/types'

const router = useRouter()
const detail = ref<ExplorationSessionDetail | null>(null)
const meta = ref<{ drivers: ExploratoryOption[]; statuses: ExploratoryOption[]; anomalyTypes: ExploratoryOption[] } | null>(null)
const polling = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

const evidenceVisible = ref(false)
const evidence = ref<ExecutionEvidence | null>(null)

const summaryNotes = computed<string[]>(() => {
  const summary = detail.value?.session?.summary
  const notes = summary?.notes
  return Array.isArray(notes) ? notes.map(String) : []
})

function driverLabel(driver: string): string {
  return meta.value?.drivers.find((item) => item.value === driver)?.label || driver
}
function statusLabel(status: string): string {
  return meta.value?.statuses.find((item) => item.value === status)?.label || status
}
function anomalyLabel(code: string): string {
  return meta.value?.anomalyTypes.find((item) => item.value === code)?.label || code
}
function statusTagType(status: string): 'info' | 'warning' | 'success' | 'danger' | 'primary' {
  if (status === 'done') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running' || status === 'queued') return 'warning'
  if (status === 'cancelled') return 'info'
  return 'primary'
}

async function load(): Promise<void> {
  const id = Number(router.currentRoute.value.params.id)
  if (!id) return
  try {
    detail.value = await exploratoryApi.session(id)
    schedulePoll()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载探索会话失败')
  }
}

function schedulePoll(): void {
  const status = detail.value?.session?.status
  if (status === 'queued' || status === 'running') {
    polling.value = true
    timer = setTimeout(() => void load(), 2000)
  } else {
    polling.value = false
  }
}

async function loadMeta(): Promise<void> {
  try {
    meta.value = await exploratoryApi.meta()
  } catch { /* 枚举失败不阻塞 */ }
}

async function showEvidence(row: { id: number }): Promise<void> {
  try {
    evidence.value = await exploratoryApi.stepEvidence(row.id as number)
    evidenceVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载证据失败')
  }
}

function openTask(): void {
  const taskId = detail.value?.session?.taskId
  if (taskId) router.push(`/tasks`)
}

onMounted(() => {
  void loadMeta()
  void load()
})
onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})
</script>

<style scoped>
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.mono {
  font-family: ui-monospace, monospace;
  font-size: 12px;
}
.shot-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.evidence-block {
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--el-fill-color-light);
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 12px;
  max-height: 220px;
  overflow: auto;
}
</style>