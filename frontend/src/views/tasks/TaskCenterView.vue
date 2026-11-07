<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">任务队列</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-switch v-model="autoRefresh" active-text="自动刷新" />
      </div>
    </div>

    <div class="metric-grid">
      <MetricCard label="排队中" :value="stats.queued ?? 0" />
      <MetricCard label="执行中" :value="stats.running ?? 0" tone="warning" />
      <MetricCard label="成功" :value="stats.success ?? 0" tone="success" />
      <MetricCard label="失败" :value="stats.failed ?? 0" tone="danger" />
    </div>

    <div class="panel panel-pad">
      <div class="toolbar">
        <el-input
          v-model="filters.keyword"
          placeholder="搜索任务名称 / 错误"
          clearable
          style="width: 220px"
          @input="scheduleQuery"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="filters.kind" placeholder="类型" clearable style="width: 170px" @change="reload">
          <el-option v-for="item in kinds" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button :icon="RefreshLeft" @click="resetFilters">重置</el-button>
      </div>

      <el-table v-loading="loading" :data="tasks" border stripe>
        <el-table-column label="任务" min-width="220" show-overflow-tooltip>
          <template #default="scope">
            <div>{{ scope.row.title || '-' }}</div>
            <div class="task-sub">#{{ scope.row.id }} · {{ scope.row.kindLabel }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="112">
          <template #default="scope">
            <el-tag size="small" :type="statusType(scope.row.status)" effect="plain">
              {{ scope.row.statusLabel }}
            </el-tag>
            <div v-if="scope.row.cancelRequested && scope.row.status === 'running'" class="task-sub">
              取消中…
            </div>
          </template>
        </el-table-column>
        <el-table-column label="尝试" width="72" align="center">
          <template #default="scope">{{ scope.row.attempts }}/{{ scope.row.maxAttempts }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="152">
          <template #default="scope">{{ dateTimeText(scope.row.createdAt) }}</template>
        </el-table-column>
        <el-table-column label="结束时间" width="152">
          <template #default="scope">{{ dateTimeText(scope.row.finishedAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click="openDetail(scope.row)">详情</el-button>
            <el-button
              v-if="scope.row.status === 'queued' || scope.row.status === 'running'"
              link
              type="danger"
              :disabled="scope.row.cancelRequested"
              @click="cancelTask(scope.row)"
            >
              取消
            </el-button>
            <el-button
              v-if="scope.row.status === 'failed' || scope.row.status === 'cancelled'"
              link
              type="success"
              @click="retryTask(scope.row)"
            >
              重试
            </el-button>
            <el-button
              v-if="isFinished(scope.row)"
              link
              type="danger"
              @click="removeTask(scope.row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">暂无后台任务。自动化脚本选择「后台执行」后会出现在这里。</span>
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @current-change="load"
          @size-change="reload"
        />
      </div>
    </div>

    <el-dialog v-model="detailVisible" title="任务详情" width="760px" top="6vh">
      <template v-if="current">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="任务">#{{ current.id }} {{ current.title }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ current.kindLabel }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ current.statusLabel }}</el-descriptions-item>
          <el-descriptions-item label="尝试次数">{{ current.attempts }}/{{ current.maxAttempts }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ dateTimeText(current.createdAt) }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ dateTimeText(current.finishedAt) }}</el-descriptions-item>
        </el-descriptions>
        <div v-if="current.errorMessage" class="detail-block">
          <h4>失败原因</h4>
          <pre class="detail-pre error">{{ current.errorMessage }}</pre>
        </div>
        <div class="detail-block">
          <h4>输入</h4>
          <pre class="detail-pre">{{ pretty(current.payload) }}</pre>
        </div>
        <div v-if="Object.keys(current.result ?? {}).length" class="detail-block">
          <h4>结果</h4>
          <pre class="detail-pre">{{ pretty(current.result) }}</pre>
        </div>
      </template>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, RefreshLeft } from '@element-plus/icons-vue'
import MetricCard from '@/components/MetricCard.vue'
import { useAutoQuery } from '@/composables/useAutoQuery'
import { taskApi } from '@/api'
import type { QueueTask } from '@/api/types'
import { dateTimeText } from '@/utils/format'

const tasks = ref<QueueTask[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const stats = ref<Record<string, number>>({})
const kinds = ref<{ value: string; label: string }[]>([])
const autoRefresh = ref(true)
const filters = reactive({ keyword: '', status: '', kind: '' })

const statusOptions = [
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '执行中' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]

const detailVisible = ref(false)
const current = ref<QueueTask | null>(null)

const hasActive = computed(() =>
  tasks.value.some((item) => item.status === 'queued' || item.status === 'running'),
)

function statusType(value: string): 'success' | 'warning' | 'info' | 'danger' {
  if (value === 'success') return 'success'
  if (value === 'running') return 'warning'
  if (value === 'failed') return 'danger'
  return 'info'
}

function isFinished(row: QueueTask): boolean {
  return ['success', 'failed', 'cancelled'].includes(row.status)
}

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value)
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await taskApi.list({
      keyword: filters.keyword,
      status: filters.status,
      kind: filters.kind,
      pageNum: page.value,
      pageSize: pageSize.value,
    })
    tasks.value = result.list ?? []
    total.value = result.total ?? 0
    stats.value = result.stats ?? {}
    kinds.value = result.kinds ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载任务失败')
  } finally {
    loading.value = false
  }
}

function reload(): void {
  page.value = 1
  void load()
}

const scheduleQuery = useAutoQuery(reload)

function resetFilters(): void {
  filters.keyword = ''
  filters.status = ''
  filters.kind = ''
  reload()
}

function openDetail(row: QueueTask): void {
  current.value = row
  detailVisible.value = true
}

async function cancelTask(row: QueueTask): Promise<void> {
  try {
    await ElMessageBox.confirm(
      row.status === 'running'
        ? '运行中的任务会在下一个检查点停止，确定取消？'
        : '确定取消这条排队任务？',
      '取消任务',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await taskApi.cancel(row.id)
    ElMessage.success('已提交取消')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '取消失败')
  }
}

async function retryTask(row: QueueTask): Promise<void> {
  try {
    await taskApi.retry(row.id)
    ElMessage.success('已重新入队')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '重试失败')
  }
}

async function removeTask(row: QueueTask): Promise<void> {
  try {
    await ElMessageBox.confirm('删除后任务记录不可恢复，确定删除？', '删除任务', { type: 'warning' })
  } catch {
    return
  }
  try {
    await taskApi.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

let timer: ReturnType<typeof setInterval> | null = null

function startTimer(): void {
  stopTimer()
  timer = setInterval(() => {
    if (autoRefresh.value && hasActive.value) {
      void load()
    }
  }, 3000)
}

function stopTimer(): void {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

onMounted(async () => {
  await load()
  startTimer()
})

onActivated(() => {
  void load()
  startTimer()
})

onDeactivated(stopTimer)
onUnmounted(stopTimer)
</script>

<style scoped>
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.task-sub {
  margin-top: 2px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.detail-block {
  margin-top: 14px;
}

.detail-block h4 {
  margin: 0 0 6px;
  font-size: 13px;
}

.detail-pre {
  margin: 0;
  padding: 10px 12px;
  max-height: 260px;
  overflow: auto;
  border-radius: 6px;
  background: var(--el-fill-color-light);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.detail-pre.error {
  color: var(--el-color-danger);
}
</style>
