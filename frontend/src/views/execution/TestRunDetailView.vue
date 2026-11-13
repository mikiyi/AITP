<template>
  <div class="page">
    <div v-if="run" class="page-header">
      <div class="title-wrap">
        <el-button :icon="ArrowLeft" circle size="small" @click="goBack" />
        <h2 class="page-title">{{ run.name }}</h2>
        <el-tag size="small" :type="runStatusType(run.status)" effect="plain">
          {{ runStatusText(run.status) }}
        </el-tag>
        <el-tag v-if="run.versionCode" size="small" effect="plain">{{ run.versionCode }}</el-tag>
      </div>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button :icon="Download" @click="exportResult">导出结果</el-button>
        <el-button type="primary" plain :icon="Cpu" @click="openAutomation">执行自动化</el-button>
        <el-button type="success" :icon="CircleCheck" :disabled="run.status === 'completed'" @click="complete">
          完成轮次
        </el-button>
      </div>
    </div>

    <div v-if="run" class="panel panel-pad summary">
      <div class="summary-grid">
        <div class="summary-item">
          <span class="summary-label">用例总数</span>
          <strong>{{ run.total }}</strong>
        </div>
        <div class="summary-item ok">
          <span class="summary-label">通过</span>
          <strong>{{ run.passed }}</strong>
        </div>
        <div class="summary-item bad">
          <span class="summary-label">失败</span>
          <strong>{{ run.failed }}</strong>
        </div>
        <div class="summary-item warn">
          <span class="summary-label">阻塞</span>
          <strong>{{ run.blocked }}</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">未执行</span>
          <strong>{{ run.notExecuted }}</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">通过率</span>
          <strong>{{ passRateText }}</strong>
        </div>
      </div>
      <el-progress
        :percentage="executedPercent"
        :stroke-width="8"
        :status="run.failed > 0 ? 'warning' : 'success'"
        :format="() => `已执行 ${run?.total ? run.total - run.notExecuted : 0}/${run?.total ?? 0}`"
      />
      <div class="summary-meta">
        <span>环境：{{ run.envName || run.env || '-' }}<template v-if="run.envKind">（{{ envKindText(run.envKind) }}）</template></span>
        <span>负责人：{{ run.owner || '-' }}</span>
        <span>开始：{{ dateTimeText(run.startedAt) }}</span>
        <span>完成：{{ dateTimeText(run.finishedAt) }}</span>
        <span v-if="run.planName">来自计划：{{ run.planName }}</span>
      </div>
    </div>

    <div v-if="run" class="panel panel-pad">
      <div class="toolbar">
        <el-select v-model="filterResult" placeholder="执行结果" clearable style="width: 140px">
          <el-option v-for="item in resultOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-input
          v-model="keyword"
          placeholder="搜索用例编号 / 标题"
          clearable
          style="width: 220px"
        />
        <el-select v-model="batchResult" placeholder="批量设为" style="width: 130px">
          <el-option v-for="item in resultOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button :disabled="!selectedRows.length || !batchResult" @click="applyBatchResult">
          应用到选中（{{ selectedRows.length }}）
        </el-button>
        <el-button
          class="save-all"
          type="primary"
          :loading="savingAll"
          :disabled="!dirtyIds.length"
          @click="saveAll"
        >
          保存全部结果（{{ dirtyIds.length }}）
        </el-button>
        <el-button
          type="primary"
          plain
          :icon="VideoPlay"
          :disabled="!interfaceCaseCount"
          @click="openInterface()"
        >
          批量执行接口用例（{{ interfaceCaseCount }}）
        </el-button>
      </div>

      <el-table
        ref="tableRef"
        :data="filteredCases"
        border
        stripe
        size="small"
        row-key="id"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="42" reserve-selection />
        <el-table-column prop="seq" label="#" width="48" align="center" />
        <el-table-column label="用例" min-width="220">
          <template #default="scope">
            <div class="case-title">
              <el-button link type="primary" @click="openCase(scope.row)">{{ scope.row.caseNo }}</el-button>
              <span class="case-name">{{ scope.row.title }}</span>
            </div>
            <div class="case-sub">
              <el-tag size="small" effect="plain">{{ scope.row.priority }}</el-tag>
              <span v-if="scope.row.module">{{ scope.row.module }}</span>
              <span v-if="scope.row.caseType === 'interface'" class="iface-hint">
                {{ interfaceHint(scope.row) }}
              </span>
              <span v-if="scope.row.preconditions">前置：{{ scope.row.preconditions }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="预期结果" min-width="160" show-overflow-tooltip>
          <template #default="scope">{{ scope.row.expectedResult || '-' }}</template>
        </el-table-column>
        <el-table-column label="结果" width="118">
          <template #default="scope">
            <el-select
              v-model="scope.row.result"
              size="small"
              :class="resultClass(scope.row.result)"
              @change="onResultChange(scope.row)"
            >
              <el-option v-for="item in resultOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="实际结果" min-width="170">
          <template #default="scope">
            <el-input
              v-model="scope.row.actualResult"
              size="small"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 3 }"
              placeholder="失败时填写实际现象"
              @change="markDirty(scope.row)"
            />
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="120">
          <template #default="scope">
            <el-input
              v-model="scope.row.remark"
              size="small"
              placeholder="备注"
              @change="markDirty(scope.row)"
            />
          </template>
        </el-table-column>
        <el-table-column label="缺陷" width="90">
          <template #default="scope">
            <el-button v-if="scope.row.bugId" link type="danger" @click="openBug(scope.row.bugId)">
              #{{ scope.row.bugId }}
            </el-button>
            <el-button
              v-else
              link
              type="warning"
              :disabled="scope.row.result !== 'failed'"
              @click="createBug(scope.row)"
            >
              提缺陷
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="执行人 / 时间" width="150">
          <template #default="scope">
            <div class="muted">{{ scope.row.executedBy || '-' }}</div>
            <div class="muted">{{ scope.row.executedAt ? dateTimeText(scope.row.executedAt) : '' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="scope">
            <el-button
              v-if="scope.row.caseType === 'interface'"
              link
              type="primary"
              :loading="interfaceRunning && interfaceTarget?.id === scope.row.id"
              @click="openInterface(scope.row)"
            >
              执行接口
            </el-button>
            <el-button link type="danger" @click="removeCase(scope.row)">移除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">轮次里还没有用例</span>
        </template>
      </el-table>
    </div>

    <div v-if="run && automationRuns.length" class="panel panel-pad auto-panel">
      <div class="auto-panel__head">
        <h3>自动化回写</h3>
        <span class="muted">脚本执行完成后按用例自动回写本轮结果</span>
      </div>
      <el-table :data="automationRuns" size="small" border max-height="260">
        <el-table-column label="脚本" min-width="200">
          <template #default="{ row }">
            <div class="auto-script">
              <span>{{ row.scriptName }}</span>
              <span class="muted text-mono">{{ row.scriptNo }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="runNo" label="执行编号" width="120" />
        <el-table-column label="脚本结果" width="100">
          <template #default="{ row }">
            <el-tag :type="autoRunTagType(row.status)" size="small" effect="plain">
              {{ autoRunStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="回写结论" width="130">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.writebackStatus"
              :content="row.writebackSummary || '-'"
              placement="top"
            >
              <el-tag :type="writebackTagType(row.writebackStatus)" size="small" effect="plain">
                {{ row.writebackLabel || row.writebackStatus }}
              </el-tag>
            </el-tooltip>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="writebackSummary" label="回写说明" min-width="240" show-overflow-tooltip />
        <el-table-column label="开始时间" width="170">
          <template #default="{ row }">{{ dateTimeText(row.startedAt) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="interfaceDialogVisible" title="执行接口用例" width="480px">
      <el-form label-width="90px">
        <el-form-item label="执行范围">
          <span v-if="interfaceTarget">{{ interfaceTarget.caseNo }} {{ interfaceTarget.title }}</span>
          <span v-else>全部 {{ interfaceCaseCount }} 条接口用例（投递任务队列逐条执行）</span>
        </el-form-item>
        <el-form-item label="执行环境">
          <el-select v-model="interfaceEnvId" placeholder="请选择测试环境" style="width: 100%">
            <el-option
              v-for="env in envOptions"
              :key="env.id"
              :label="`${env.name}（${env.kindLabel || env.kind}）`"
              :value="env.id"
            />
          </el-select>
          <div v-if="!envOptions.length" class="env-hint">
            还没有测试环境，请先到「设置 → 测试环境」添加并填写应用地址
          </div>
        </el-form-item>
        <el-form-item label="超时（秒）">
          <el-input-number v-model="interfaceTimeout" :min="1" :max="60" />
        </el-form-item>
        <el-alert
          v-if="interfaceEnvKind === 'prod'"
          title="即将向生产环境发起真实请求，请确认接口无副作用。"
          type="warning"
          :closable="false"
          show-icon
        />
      </el-form>
      <template #footer>
        <el-button @click="interfaceDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="interfaceRunning" @click="runInterfaceConfirm">
          开始执行
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="automationDialogVisible" title="执行自动化脚本" width="560px">
      <el-form label-width="96px">
        <el-form-item label="选择脚本" required>
          <el-select
            v-model="automationSelected"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            placeholder="选择要执行的自动化脚本"
            style="width: 100%"
            :loading="automationLoading"
          >
            <el-option
              v-for="item in automationScripts"
              :key="item.id"
              :label="`${item.name}（${item.scriptNo}）`"
              :value="item.id"
            />
          </el-select>
          <div v-if="!automationLoading && !automationScripts.length" class="auto-hint">
            当前项目还没有可执行的自动化脚本，请先到「自动化测试」新建脚本。
          </div>
          <div v-else class="auto-hint">
            脚本需先配置覆盖用例方式与覆盖用例值，执行完成后才会按用例回写本轮结果。
          </div>
        </el-form-item>
        <el-form-item label="超时（秒）">
          <el-input-number v-model="automationTimeout" :min="1" :max="7200" />
        </el-form-item>
        <el-alert
          title="执行会投递到任务队列，可在「任务队列」查看进度；完成后回到本页刷新即可看到回写结果。"
          type="info"
          :closable="false"
          show-icon
        />
      </el-form>
      <template #footer>
        <el-button @click="automationDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="automationRunning"
          :disabled="!automationSelected.length"
          @click="runAutomationConfirm"
        >
          投递执行（{{ automationSelected.length }}）
        </el-button>
      </template>
    </el-dialog>

    <InterfaceResultDialog v-model="interfaceResultVisible" :result="interfaceResult" />
    <ProdConfirmDialog ref="prodDialog" />
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { TableInstance } from 'element-plus'
import { ArrowLeft, CircleCheck, Cpu, Download, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { automationApi, testEnvApi, testRunApi } from '@/api'
import type {
  AutomationRun,
  AutomationScript,
  InterfaceExecution,
  TestEnv,
  TestRun,
  TestRunCase,
} from '@/api/types'
import InterfaceResultDialog from '@/components/InterfaceResultDialog.vue'
import ProdConfirmDialog from '@/components/ProdConfirmDialog.vue'
import { dateTimeText } from '@/utils/format'

const route = useRoute()
const router = useRouter()

const run = ref<TestRun | null>(null)
const rows = ref<TestRunCase[]>([])
const tableRef = ref<TableInstance>()
const loadedRunId = ref<number | null>(null)
const filterResult = ref('')
const keyword = ref('')
const batchResult = ref('')
const selectedRows = ref<TestRunCase[]>([])
const dirtyIds = ref<number[]>([])
const savingAll = ref(false)

const envOptions = ref<TestEnv[]>([])
const interfaceDialogVisible = ref(false)
const interfaceResultVisible = ref(false)
const interfaceRunning = ref(false)
const prodDialog = ref<InstanceType<typeof ProdConfirmDialog> | null>(null)
const interfaceTarget = ref<TestRunCase | null>(null)
const interfaceEnvId = ref<number | null>(null)
const interfaceTimeout = ref(10)
const interfaceResult = ref<InterfaceExecution | null>(null)

const automationRuns = ref<AutomationRun[]>([])
const automationDialogVisible = ref(false)
const automationScripts = ref<AutomationScript[]>([])
const automationSelected = ref<number[]>([])
const automationTimeout = ref(300)
const automationRunning = ref(false)
const automationLoading = ref(false)

const interfaceCaseCount = computed(
  () => rows.value.filter((row) => row.caseType === 'interface').length,
)

const interfaceEnvKind = computed(
  () => envOptions.value.find((env) => env.id === interfaceEnvId.value)?.kind ?? '',
)

const resultOptions = [
  { value: 'not_executed', label: '未执行' },
  { value: 'passed', label: '通过' },
  { value: 'failed', label: '失败' },
  { value: 'blocked', label: '阻塞' },
  { value: 'skipped', label: '跳过' },
]

const envKindLabels: Record<string, string> = {
  dev: '开发',
  test: '测试',
  staging: '预发',
  prod: '生产',
}

function envKindText(value: string): string {
  return envKindLabels[value] ?? value
}

const runId = computed(() => Number(route.params.id))

const filteredCases = computed(() => {
  const needle = keyword.value.trim().toLowerCase()
  return rows.value.filter((row) => {
    if (filterResult.value && row.result !== filterResult.value) return false
    if (!needle) return true
    return (
      String(row.title || '').toLowerCase().includes(needle) ||
      String(row.caseNo || '').toLowerCase().includes(needle)
    )
  })
})

const passRateText = computed(() => {
  if (!run.value) return '-'
  const executed = run.value.total - run.value.notExecuted
  if (!executed) return '-'
  return `${Math.round((run.value.passed / executed) * 100)}%`
})

const executedPercent = computed(() => {
  if (!run.value || !run.value.total) return 0
  return Math.round(((run.value.total - run.value.notExecuted) / run.value.total) * 100)
})

function runStatusText(value: string): string {
  return { pending: '待开始', running: '执行中', completed: '已完成', aborted: '已中止' }[value] ?? value
}

function runStatusType(value: string): 'success' | 'warning' | 'info' | 'danger' {
  if (value === 'completed') return 'success'
  if (value === 'running') return 'warning'
  if (value === 'aborted') return 'danger'
  return 'info'
}

function resultClass(value: string): string {
  if (value === 'passed') return 'result-pass'
  if (value === 'failed') return 'result-fail'
  if (value === 'blocked') return 'result-block'
  return ''
}

async function load(): Promise<void> {
  try {
    const detail = await testRunApi.get(runId.value)
    run.value = detail
    rows.value = (detail.cases ?? []).map((item) => ({ ...item }))
    dirtyIds.value = []
    loadedRunId.value = runId.value
    if (!envOptions.value.length) {
      await loadEnvs()
    }
    await loadAutomationRuns()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载轮次失败')
  }
}

async function loadAutomationRuns(): Promise<void> {
  try {
    const payload = await testRunApi.automationRuns(runId.value)
    automationRuns.value = payload.list ?? []
  } catch {
    automationRuns.value = []
  }
}

function markDirty(row: TestRunCase): void {
  if (!dirtyIds.value.includes(row.id)) {
    dirtyIds.value = [...dirtyIds.value, row.id]
  }
}

function onResultChange(row: TestRunCase): void {
  markDirty(row)
  void saveRow(row)
}

async function saveRow(row: TestRunCase): Promise<void> {
  try {
    const result = await testRunApi.recordCase(runId.value, row.id, {
      result: row.result,
      actualResult: row.actualResult,
      remark: row.remark,
      executedBy: row.executedBy || 'local',
    })
    Object.assign(row, result.runCase)
    run.value = { ...(run.value as TestRun), ...result.run, cases: rows.value }
    dirtyIds.value = dirtyIds.value.filter((id) => id !== row.id)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存结果失败')
  }
}

async function saveAll(): Promise<void> {
  if (!dirtyIds.value.length) return
  savingAll.value = true
  try {
    const entries = rows.value
      .filter((row) => dirtyIds.value.includes(row.id))
      .map((row) => ({
        id: row.id,
        result: row.result,
        actualResult: row.actualResult,
        remark: row.remark,
        executedBy: row.executedBy || 'local',
      }))
    const payload = await testRunApi.batchRecord(runId.value, entries)
    ElMessage.success(`已保存 ${payload.updated.length} 条`)
    await load()
    tableRef.value?.clearSelection()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量保存失败')
  } finally {
    savingAll.value = false
  }
}

function onSelectionChange(selection: TestRunCase[]): void {
  selectedRows.value = selection
}

function interfaceHint(row: TestRunCase): string {
  const extra = row.extra ?? {}
  const method = extra['请求方式'] || extra['请求方法'] || 'GET'
  const url = extra['接口URL'] || '未配置接口URL'
  return `${method} ${url}`
}

async function loadEnvs(): Promise<void> {
  try {
    const result = await testEnvApi.list()
    envOptions.value = result.list
  } catch {
    envOptions.value = []
  }
}

function openInterface(row?: TestRunCase): void {
  interfaceTarget.value = row ?? null
  interfaceEnvId.value = run.value?.envId ?? envOptions.value[0]?.id ?? null
  interfaceTimeout.value = 10
  interfaceDialogVisible.value = true
}

async function runInterfaceConfirm(): Promise<void> {
  if (!interfaceEnvId.value) {
    ElMessage.warning('请先选择执行环境')
    return
  }
  if (interfaceEnvKind.value === 'prod') {
    const ok = (await prodDialog.value?.open(
      '即将向生产环境发起真实接口请求，请确认接口无副作用。',
    )) ?? false
    if (!ok) {
      return
    }
  }
  interfaceRunning.value = true
  try {
    if (interfaceTarget.value) {
      const detail = await testRunApi.runInterfaceCase(runId.value, interfaceTarget.value.id, {
        envId: interfaceEnvId.value,
        timeoutSeconds: interfaceTimeout.value,
      })
      interfaceResult.value = detail
      interfaceResultVisible.value = true
      interfaceDialogVisible.value = false
      await load()
    } else {
      const payload = await testRunApi.runInterfaceBatch(runId.value, {
        envId: interfaceEnvId.value,
        timeoutSeconds: interfaceTimeout.value,
      })
      if ('total' in payload) {
        ElMessage.success(`批量执行完成：通过 ${payload.passed} 条，失败 ${payload.failed} 条`)
        await load()
      } else {
        ElMessage.success(`已投递任务队列（任务 #${payload.task.id}），完成后刷新即可看到结果`)
      }
      interfaceDialogVisible.value = false
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '接口执行失败')
  } finally {
    interfaceRunning.value = false
  }
}

async function openAutomation(): Promise<void> {
  automationSelected.value = []
  automationTimeout.value = 300
  automationLoading.value = true
  automationDialogVisible.value = true
  try {
    const payload = await automationApi.list({
      projectId: run.value?.projectId,
      status: 'ready',
      pageSize: 200,
    })
    automationScripts.value = payload.list ?? []
  } catch (err) {
    automationScripts.value = []
    ElMessage.error(err instanceof Error ? err.message : '加载自动化脚本失败')
  } finally {
    automationLoading.value = false
  }
}

async function runAutomationConfirm(): Promise<void> {
  if (!automationSelected.value.length) {
    return
  }
  automationRunning.value = true
  try {
    const payload = await testRunApi.runAutomation(runId.value, {
      scriptIds: automationSelected.value,
      timeoutSeconds: automationTimeout.value,
    })
    if ('queued' in payload) {
      ElMessage.success(`已投递 ${automationSelected.value.length} 个执行任务，完成后刷新查看回写`)
    } else {
      ElMessage.success(`执行完成，共 ${payload.total} 个脚本，结果已回写`)
    }
    automationDialogVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '执行自动化失败')
  } finally {
    automationRunning.value = false
  }
}

function autoRunStatusText(status: string): string {
  return (
    { running: '执行中', success: '成功', failed: '失败', timeout: '超时', error: '启动失败' }[status] ??
    status
  )
}

function autoRunTagType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'success') return 'success'
  if (status === 'failed' || status === 'error') return 'danger'
  if (status === 'timeout') return 'warning'
  return 'info'
}

function writebackTagType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'parsed') return 'success'
  if (status === 'partial') return 'warning'
  if (status === 'fallback' || status === 'failed') return 'danger'
  return 'info'
}

async function applyBatchResult(): Promise<void> {
  if (!batchResult.value || !selectedRows.value.length) return
  const entries = selectedRows.value.map((row) => ({
    id: row.id,
    result: batchResult.value,
    actualResult: row.actualResult,
    remark: row.remark,
    executedBy: row.executedBy || 'local',
  }))
  try {
    const payload = await testRunApi.batchRecord(runId.value, entries)
    ElMessage.success(`已批量回写 ${payload.updated.length} 条`)
    batchResult.value = ''
    await load()
    tableRef.value?.clearSelection()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量回写失败')
  }
}

async function createBug(row: TestRunCase): Promise<void> {
  try {
    const payload = await testRunApi.createBug(runId.value, row.id)
    ElMessage.success(payload.created ? `已创建缺陷 ${payload.bugNo}` : `已关联缺陷 ${payload.bugNo}`)
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建缺陷失败')
  }
}

async function removeCase(row: TestRunCase): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定从本轮移除用例「${row.title}」？`, '移除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await testRunApi.removeCase(runId.value, row.id)
    ElMessage.success('已移除')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '移除失败')
  }
}

async function complete(): Promise<void> {
  try {
    await ElMessageBox.confirm('完成后本轮会记录完成时间并锁定状态，之后仍可修订个别结果。', '完成轮次', {
      type: 'info',
    })
  } catch {
    return
  }
  try {
    await testRunApi.complete(runId.value)
    ElMessage.success('轮次已完成')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '完成失败')
  }
}

function exportResult(): void {
  // 带上 projectId：后端据此校验轮次归属，避免拿到 id 就能导出别的项目的结果
  window.open(
    testRunApi.exportUrl(runId.value, { format: 'csv', projectId: run.value?.projectId }),
    '_blank',
  )
}

function openCase(row: TestRunCase): void {
  void router.push({ name: 'test-case-detail', params: { id: row.caseId } })
}

function openBug(bugId: number): void {
  void router.push({ name: 'bug-detail', params: { id: bugId } })
}

function goBack(): void {
  void router.push({ name: 'test-plans' })
}

// keep-alive 下路由参数会随其他页面变化，只有仍停留在轮次页时才重新加载
watch(runId, () => {
  if (route.name === 'test-run-detail') {
    void load()
  }
})

onMounted(load)

onActivated(() => {
  if (route.name === 'test-run-detail' && loadedRunId.value !== runId.value) {
    void load()
  }
})
</script>

<style scoped>
.title-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.summary {
  margin-bottom: 12px;
}

.save-all {
  margin-left: auto;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
}

.summary-item strong {
  font-size: 20px;
}

.summary-item.ok strong {
  color: var(--el-color-success);
}

.summary-item.bad strong {
  color: var(--el-color-danger);
}

.summary-item.warn strong {
  color: var(--el-color-warning);
}

.summary-label {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.summary-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 10px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.case-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.case-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.case-sub {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 3px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.iface-hint {
  font-family: monospace;
}

.env-hint {
  margin-top: 8px;
  padding: 8px 11px 8px 12px;
  border: 1px solid color-mix(in srgb, var(--aitp-note-warning) 16%, transparent);
  border-left: 3px solid var(--aitp-note-warning);
  border-radius: 10px;
  background: color-mix(in srgb, var(--aitp-note-warning) 5%, var(--aitp-panel));
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--aitp-text);
}

.auto-panel {
  margin-top: 12px;
}

.auto-panel__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.auto-panel__head h3 {
  margin: 0;
  font-size: 15px;
}

.auto-script {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.auto-hint {
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}

.result-pass :deep(.el-select__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-success) inset;
}

.result-fail :deep(.el-select__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}

.result-block :deep(.el-select__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-warning) inset;
}
</style>
