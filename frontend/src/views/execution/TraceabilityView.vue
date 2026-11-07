<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">追溯与发布准入</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="需求追溯矩阵" name="matrix">
        <div class="panel panel-pad">
          <div class="toolbar">
            <el-select v-model="versionId" placeholder="全部版本" clearable style="width: 160px" @change="loadMatrix">
              <el-option
                v-for="item in versions"
                :key="item.id"
                :label="`${item.versionCode} ${item.name || ''}`.trim()"
                :value="item.id"
              />
            </el-select>
            <el-select v-model="matrixRunId" placeholder="按当前用例状态统计" clearable style="width: 260px" @change="loadMatrix">
              <el-option v-for="item in runs" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
            <div class="spacer" />
            <el-button :icon="Download" :disabled="!rows.length" @click="exportMatrix">导出矩阵</el-button>
          </div>

          <div class="metric-grid">
            <div class="metric-cell">
              <span>需求总数</span>
              <strong>{{ summary.requirementTotal }}</strong>
            </div>
            <div class="metric-cell">
              <span>需求覆盖率</span>
              <strong>{{ summary.coverageRate }}%</strong>
              <small>{{ summary.coveredRequirements }} 条已覆盖 / {{ summary.uncoveredRequirements }} 条未覆盖</small>
            </div>
            <div class="metric-cell">
              <span>用例执行率</span>
              <strong>{{ executionRate }}%</strong>
              <small>已执行 {{ summary.executedCount }} / {{ summary.caseCount }}</small>
            </div>
            <div class="metric-cell">
              <span>执行通过率</span>
              <strong>{{ summary.passRate }}%</strong>
              <small>通过 {{ summary.passed }} / 失败 {{ summary.failed }}</small>
            </div>
            <div class="metric-cell">
              <span>未闭环缺陷</span>
              <strong :class="{ danger: summary.openBugs > 0 }">{{ summary.openBugs }}</strong>
            </div>
          </div>

          <el-table v-loading="loading" :data="rows" border stripe>
            <el-table-column prop="reqNo" label="需求编号" width="100" />
            <el-table-column prop="title" label="需求标题" min-width="180" show-overflow-tooltip />
            <el-table-column label="覆盖状态" width="96">
              <template #default="scope">
                <el-tag size="small" :type="coverageType(scope.row.coverageStatus)" effect="plain">
                  {{ scope.row.coverageLabel }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="priority" label="优先级" width="72" align="center" />
            <el-table-column label="用例" width="62" align="center">
              <template #default="scope">{{ scope.row.caseCount }}</template>
            </el-table-column>
            <el-table-column label="已执行" width="72" align="center">
              <template #default="scope">{{ scope.row.executedCount }}</template>
            </el-table-column>
            <el-table-column label="通过 / 失败" width="104" align="center">
              <template #default="scope">
                <span class="ok">{{ scope.row.passed }}</span>
                <span class="muted"> / </span>
                <span class="bad">{{ scope.row.failed }}</span>
              </template>
            </el-table-column>
            <el-table-column label="通过率" width="82" align="center">
              <template #default="scope">{{ rowRate(scope.row) }}</template>
            </el-table-column>
            <el-table-column label="未闭环缺陷" width="100" align="center">
              <template #default="scope">
                <el-button v-if="scope.row.openBugs" link type="danger" @click="goBugs">
                  {{ scope.row.openBugs }}
                </el-button>
                <span v-else class="muted">0</span>
              </template>
            </el-table-column>
            <el-table-column label="最近执行" width="170">
              <template #default="scope">{{ dateTimeText(scope.row.lastExecutedAt) }}</template>
            </el-table-column>
            <template #empty>
              <span class="muted">没有需求，先去需求页建一条再回来查看覆盖情况</span>
            </template>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="发布准入" name="gate">
        <div class="panel panel-pad gate-panel">
          <div class="toolbar">
            <span class="gate-label">选择已完成轮次</span>
            <el-select v-model="gateRunId" placeholder="选择轮次" style="width: 280px" @change="previewGate">
              <el-option
                v-for="item in completedRuns"
                :key="item.id"
                :label="`${item.name}（通过 ${item.passed}/${item.total}）`"
                :value="item.id"
              />
            </el-select>
            <el-button :icon="Search" :disabled="!gateRunId" :loading="previewing" @click="previewGate">
              预览结论
            </el-button>
            <el-button type="primary" :icon="CircleCheck" :disabled="!gateResult" :loading="savingGate" @click="saveGate">
              留存准入记录
            </el-button>
          </div>

          <div class="threshold-grid">
            <div class="threshold-item">
              <span>整体通过率 ≥</span>
              <el-input-number v-model="thresholds.overallPassRate" :min="0" :max="100" :step="5" size="small" controls-position="right" />
            </div>
            <div class="threshold-item">
              <span>P0 通过率 ≥</span>
              <el-input-number v-model="thresholds.p0PassRate" :min="0" :max="100" :step="5" size="small" controls-position="right" />
            </div>
            <div class="threshold-item">
              <span>P1 通过率 ≥</span>
              <el-input-number v-model="thresholds.p1PassRate" :min="0" :max="100" :step="5" size="small" controls-position="right" />
            </div>
            <div class="threshold-item">
              <span>未闭环 P0 缺陷 ≤</span>
              <el-input-number v-model="thresholds.maxOpenP0" :min="0" :max="99" size="small" controls-position="right" />
            </div>
            <div class="threshold-item">
              <span>未闭环 P1 缺陷 ≤</span>
              <el-input-number v-model="thresholds.maxOpenP1" :min="0" :max="99" size="small" controls-position="right" />
            </div>
            <div class="threshold-item">
              <span>需求覆盖率 ≥</span>
              <el-input-number v-model="thresholds.minCoverageRate" :min="0" :max="100" :step="5" size="small" controls-position="right" />
            </div>
          </div>

          <el-alert
            v-if="gateResult"
            :title="gateResult.status === 'passed' ? '满足发布准入' : '不满足发布准入'"
            :type="gateResult.status === 'passed' ? 'success' : 'error'"
            :closable="false"
            show-icon
            class="gate-alert"
          >
            <template #default>
              {{ gateConclusion }}
            </template>
          </el-alert>

          <el-table v-if="gateResult" :data="gateResult.checks" border size="small" class="gate-table">
            <el-table-column prop="label" label="检查项" min-width="160" />
            <el-table-column label="实际值" width="120">
              <template #default="scope">{{ scope.row.actual }}{{ scope.row.unit }}</template>
            </el-table-column>
            <el-table-column label="阈值" width="120">
              <template #default="scope">{{ scope.row.threshold }}{{ scope.row.unit }}</template>
            </el-table-column>
            <el-table-column label="结论" width="100">
              <template #default="scope">
                <el-tag size="small" :type="scope.row.passed ? 'success' : 'danger'" effect="plain">
                  {{ scope.row.passed ? '通过' : '阻断' }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>

          <el-divider content-position="left">历史准入记录</el-divider>
          <el-table :data="gates" border size="small">
            <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
            <el-table-column label="版本" width="96">
              <template #default="scope">{{ scope.row.versionCode || '-' }}</template>
            </el-table-column>
            <el-table-column label="结论" width="84">
              <template #default="scope">
                <el-tag size="small" :type="scope.row.status === 'passed' ? 'success' : 'danger'" effect="plain">
                  {{ scope.row.status === 'passed' ? '通过' : '阻断' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="conclusion" label="说明" min-width="200" show-overflow-tooltip />
            <el-table-column label="时间" width="152">
              <template #default="scope">{{ dateTimeText(scope.row.createdAt) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="70">
              <template #default="scope">
                <el-button link type="danger" @click="removeGate(scope.row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <span class="muted">还没有留存过发布准入记录</span>
            </template>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, Download, Refresh, Search } from '@element-plus/icons-vue'
import { projectVersionApi, releaseGateApi, testRunApi, traceabilityApi } from '@/api'
import type {
  ProjectVersion,
  ReleaseGate,
  ReleaseGateResult,
  TestRun,
  TraceabilityResult,
  TraceabilityRow,
} from '@/api/types'
import { useAppStore } from '@/stores/app'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { dateTimeText } from '@/utils/format'

const appStore = useAppStore()
const router = useRouter()

const tab = ref<'matrix' | 'gate'>('matrix')
const versions = ref<ProjectVersion[]>([])
const runs = ref<TestRun[]>([])
const versionId = ref<number | null>(null)
const matrixRunId = ref<number | null>(null)
const matrix = ref<TraceabilityResult | null>(null)
const loading = ref(false)

const gateRunId = ref<number | null>(null)
const gateResult = ref<ReleaseGateResult | null>(null)
const previewing = ref(false)
const savingGate = ref(false)
const gates = ref<ReleaseGate[]>([])

const thresholds = reactive({
  overallPassRate: 80,
  p0PassRate: 100,
  p1PassRate: 90,
  maxOpenP0: 0,
  maxOpenP1: 3,
  minCoverageRate: 80,
})

const rows = computed(() => matrix.value?.list ?? [])
const summary = computed(() => matrix.value?.summary ?? {
  requirementTotal: 0,
  coveredRequirements: 0,
  uncoveredRequirements: 0,
  caseCount: 0,
  executedCount: 0,
  passed: 0,
  failed: 0,
  openBugs: 0,
  coverageRate: 0,
  passRate: 0,
})
const executionRate = computed(() => {
  const total = summary.value.caseCount
  if (!total) return 0
  return Math.round((summary.value.executedCount / total) * 100)
})
const completedRuns = computed(() => runs.value.filter((item) => item.status === 'completed'))
const gateConclusion = computed(() => {
  if (!gateResult.value) return ''
  if (!gateResult.value.blocking.length) return '所有检查项均达标，可以进入发布评审。'
  return '以下检查项未达标：' + gateResult.value.blocking
    .map((key) => gateResult.value?.checks.find((item) => item.key === key)?.label)
    .filter(Boolean)
    .join('、')
})

function rowRate(row: TraceabilityRow): string {
  if (!row.executedCount) return '-'
  return `${Math.round(row.passRate * 100)}%`
}

function coverageType(value: string): 'success' | 'danger' | 'warning' | 'info' {
  if (value === 'passed') return 'success'
  if (value === 'failed') return 'danger'
  if (value === 'in_progress' || value === 'not_executed') return 'warning'
  return 'info'
}

function goBugs(): void {
  void router.push({ name: 'bugs' })
}

async function loadOptions(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) {
    versions.value = []
    runs.value = []
    return
  }
  try {
    const [versionResult, runPage] = await Promise.all([
      projectVersionApi.list(projectId),
      testRunApi.list({ projectId, pageNum: 1, pageSize: 100 }),
    ])
    versions.value = versionResult.list ?? []
    runs.value = runPage.list ?? []
  } catch {
    versions.value = []
    runs.value = []
  }
}

async function loadMatrix(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) return
  loading.value = true
  try {
    matrix.value = await traceabilityApi.get({
      projectId,
      versionId: versionId.value ?? undefined,
      runId: matrixRunId.value ?? undefined,
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载追溯矩阵失败')
  } finally {
    loading.value = false
  }
}

async function loadGates(): Promise<void> {
  if (!appStore.currentProjectId) return
  try {
    const page = await releaseGateApi.list({
      projectId: appStore.currentProjectId,
      pageNum: 1,
      pageSize: 50,
    })
    gates.value = page.list ?? []
  } catch {
    gates.value = []
  }
}

async function loadAll(): Promise<void> {
  await loadOptions()
  await Promise.all([loadMatrix(), loadGates()])
}

async function previewGate(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId || !gateRunId.value) {
    ElMessage.warning('请先选择轮次')
    return
  }
  previewing.value = true
  try {
    gateResult.value = await releaseGateApi.preview({
      projectId,
      runId: gateRunId.value,
      versionId: versionId.value ?? undefined,
      thresholds: { ...thresholds },
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '计算准入结论失败')
  } finally {
    previewing.value = false
  }
}

async function saveGate(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId || !gateRunId.value) return
  const run = runs.value.find((item) => item.id === gateRunId.value)
  savingGate.value = true
  try {
    await releaseGateApi.create({
      projectId,
      runId: gateRunId.value,
      versionId: versionId.value ?? undefined,
      name: `发布准入 · ${run?.name ?? ''}`,
      thresholds: { ...thresholds },
      createdBy: 'local',
    })
    ElMessage.success('已留存发布准入记录')
    await loadGates()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    savingGate.value = false
  }
}

async function removeGate(row: ReleaseGate): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除准入记录「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await releaseGateApi.remove(row.id)
    ElMessage.success('已删除')
    await loadGates()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

function exportMatrix(): void {
  const projectId = appStore.currentProjectId
  if (!projectId) return
  window.open(
    traceabilityApi.exportUrl({
      projectId,
      versionId: versionId.value ?? undefined,
      runId: matrixRunId.value ?? undefined,
      format: 'csv',
    }),
    '_blank',
  )
}

watch(
  () => appStore.currentProjectId,
  () => {
    versionId.value = null
    matrixRunId.value = null
    gateRunId.value = null
    gateResult.value = null
    void loadAll()
  },
)

let activated = false

onMounted(async () => {
  await appStore.loadProjects()
  await loadAll()
})

onActivated(() => {
  if (!activated) {
    activated = true
    return
  }
  void loadAll()
})
</script>

<style scoped>
.metric-grid {
  display: grid;
  /* 窄屏自动换行，避免“已覆盖 / 未覆盖”这类说明文字被挤成单字换行 */
  grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.metric-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
}

.metric-cell span {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.metric-cell strong {
  font-size: 20px;
}

.metric-cell strong.danger {
  color: var(--el-color-danger);
}

.metric-cell small {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.ok {
  color: var(--el-color-success);
}

.bad {
  color: var(--el-color-danger);
}

.gate-label {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.threshold-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px 20px;
  margin: 12px 0;
  padding: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
}

.threshold-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
  white-space: nowrap;
}

.threshold-item :deep(.el-input-number) {
  width: 104px;
  flex: none;
}

.gate-alert {
  margin-bottom: 12px;
}

.gate-table {
  margin-bottom: 8px;
}
</style>
