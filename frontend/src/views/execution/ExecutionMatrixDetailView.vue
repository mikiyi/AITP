<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">
        <el-button link :icon="Back" @click="router.push({ name: 'execution-matrices' })" />
        {{ detail?.matrix.name || '执行矩阵' }}
        <el-tag v-if="detail" size="small" effect="plain">{{ detail.matrix.matrixNo }}</el-tag>
      </h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Refresh" @click="refreshAll">刷新</el-button>
        <el-button
          type="primary"
          :disabled="!detail || ['queued', 'running'].includes(detail.matrix.status)"
          @click="dispatch"
        >一次调度</el-button>
        <el-button
          type="danger"
          plain
          :disabled="!compare || compare.stats.commonFailure === 0"
          :loading="creatingBugs"
          @click="createBugs"
        >普遍失败转缺陷（{{ compare?.stats.commonFailure ?? 0 }}）</el-button>
      </div>
    </div>

    <div v-loading="loading" class="panel panel-pad">
      <el-descriptions v-if="detail" :column="4" border size="small">
        <el-descriptions-item label="类型">{{ detail.matrix.modeLabel }}</el-descriptions-item>
        <el-descriptions-item label="执行对象">
          {{ detail.matrix.mode === 'interface' ? detail.matrix.runName : `${detail.matrix.scriptIds.length} 个脚本` }}
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag size="small" :type="statusTagType(detail.matrix.status)" effect="plain">
            {{ detail.matrix.statusLabel }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="格子进度">
          {{ detail.matrix.cellDone }} / {{ detail.matrix.cellTotal }}
          <span v-if="detail.matrix.errorMessage" class="matrix-error">（{{ detail.matrix.errorMessage }}）</span>
        </el-descriptions-item>
        <el-descriptions-item v-if="detail.matrix.prodConfirmedAt" label="生产确认" :span="2">
          {{ detail.matrix.prodConfirmedBy || 'local' }} · {{ dateTimeText(detail.matrix.prodConfirmedAt) }}
        </el-descriptions-item>
        <el-descriptions-item label="更新时间" :span="2">{{ dateTimeText(detail.matrix.updatedAt) }}</el-descriptions-item>
      </el-descriptions>

      <h3 class="section-title">格子执行情况</h3>
      <el-table v-if="detail" :data="detail.cells" border stripe size="small">
        <el-table-column prop="envName" label="环境" min-width="120">
          <template #default="scope">
            {{ scope.row.envName }}
            <el-tag v-if="scope.row.envKind === 'prod'" type="danger" size="small" effect="plain">生产</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="targetLabel" :label="detail.matrix.mode === 'interface' ? '端（应用服务器）' : '浏览器'" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="scope">
            <el-tag size="small" :type="cellTagType(scope.row.status)" effect="plain">{{ scope.row.statusLabel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="结论" width="140">
          <template #default="scope">
            <span v-if="scope.row.status === 'done'">
              共 {{ scope.row.total }} · 过 {{ scope.row.passed }} · 败 {{ scope.row.failed }}
            </span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="errorMessage" label="失败原因" min-width="160" show-overflow-tooltip>
          <template #default="scope"><span class="muted">{{ scope.row.errorMessage || '-' }}</span></template>
        </el-table-column>
      </el-table>
    </div>

    <div v-loading="compareLoading" class="panel panel-pad">
      <h3 class="section-title">跨格对比与差异归因</h3>
      <div v-if="compare" class="compare-stats">
        <el-tag type="success" effect="plain">普遍通过 {{ compare.stats.commonPass }}</el-tag>
        <el-tag type="danger">普遍失败 {{ compare.stats.commonFailure }}</el-tag>
        <el-tag type="warning" effect="plain">环境特有 {{ compare.stats.environmentSpecific }}</el-tag>
        <el-tag type="warning">端特有 {{ compare.stats.targetSpecific }}</el-tag>
        <el-tag type="info" effect="plain">混合差异 {{ compare.stats.mixed }}</el-tag>
      </div>
      <el-table v-if="compare" :data="compare.rows" border stripe>
        <el-table-column type="expand">
          <template #default="scope">
            <div class="drilldown">
              <div v-for="cell in compare.cells" :key="cell.id" class="drill-cell">
                <div class="drill-head">
                  <el-tag size="small" :type="verdictType(scope.row.cells[String(cell.id)]?.result)" effect="plain">
                    {{ cell.envName }} / {{ cell.targetLabel }}
                  </el-tag>
                  <span class="muted">{{ scope.row.cells[String(cell.id)]?.message || '-' }}</span>
                </div>
                <div v-if="scope.row.cells[String(cell.id)]?.assertions?.length" class="assertions">
                  <div v-for="(assertion, index) in scope.row.cells[String(cell.id)].assertions" :key="index"
                       class="assertion" :class="{ failed: !assertion.passed }">
                    <span>{{ assertion.passed ? '✓' : '✗' }} {{ assertion.name }}</span>
                    <span class="muted">期望 {{ assertion.expected || '-' }} / 实际 {{ assertion.actual || '-' }}</span>
                  </div>
                </div>
              </div>
              <div v-if="scope.row.assertionDiffs.length" class="diffs">
                <div class="diff-title">断言差异（各格实际值不一致）</div>
                <div v-for="diff in scope.row.assertionDiffs" :key="diff.name" class="diff-item">
                  <span class="diff-name">{{ diff.name }}</span>
                  <span v-for="cell in compare.cells" :key="cell.id" class="diff-cell">
                    {{ cell.envName }}/{{ cell.targetLabel }}：
                    <template v-if="diff.cells[String(cell.id)]">
                      {{ diff.cells[String(cell.id)].passed ? '通过' : '失败' }}（实际 {{ diff.cells[String(cell.id)].actual || '-' }}）
                    </template>
                    <template v-else>-</template>
                  </span>
                </div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="caseNo" label="用例编号" width="130" />
        <el-table-column prop="title" label="标题" min-width="160" show-overflow-tooltip />
        <el-table-column label="归因" width="110">
          <template #default="scope">
            <el-tag size="small" :type="classTagType(scope.row.classification)">{{ scope.row.classificationLabel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="各格结论" min-width="220">
          <template #default="scope">
            <span v-for="cell in compare.cells" :key="cell.id" class="verdict-chip">
              <el-tag size="small" :type="verdictType(scope.row.cells[String(cell.id)]?.result)" effect="plain">
                {{ cell.envName }}/{{ cell.targetLabel }}
                {{ verdictText(scope.row.cells[String(cell.id)]?.result) }}
              </el-tag>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="缺陷" width="90">
          <template #default="scope">
            <el-link v-if="scope.row.bugId" type="primary" @click="router.push({ name: 'bugs', query: { keyword: scope.row.caseNo } })">
              #{{ scope.row.bugId }}
            </el-link>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">还没有执行结论，先点「一次调度」逐格真实执行</span>
        </template>
      </el-table>
    </div>

    <ProdConfirmDialog ref="prodDialog" />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, Refresh } from '@element-plus/icons-vue'
import { executionMatrixApi } from '@/api'
import ProdConfirmDialog from '@/components/ProdConfirmDialog.vue'
import { dateTimeText } from '@/utils/format'
import type { MatrixCompare, MatrixDetail } from '@/api/types'

const route = useRoute()
const router = useRouter()
const matrixId = computed(() => Number(route.params.id))

const loading = ref(false)
const compareLoading = ref(false)
const detail = ref<MatrixDetail | null>(null)
const compare = ref<MatrixCompare | null>(null)
const creatingBugs = ref(false)
let poller: ReturnType<typeof setInterval> | null = null

function statusTagType(status: string): 'info' | 'warning' | 'success' | 'danger' | 'primary' {
  if (status === 'done') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running' || status === 'queued') return 'warning'
  if (status === 'cancelled') return 'info'
  return 'primary'
}

function cellTagType(status: string): 'info' | 'warning' | 'success' | 'danger' {
  if (status === 'done') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

function classTagType(classification: string): 'success' | 'danger' | 'warning' | 'info' {
  if (classification === 'common_pass') return 'success'
  if (classification === 'common_failure') return 'danger'
  if (classification === 'environment_specific' || classification === 'target_specific') return 'warning'
  return 'info'
}

function verdictType(result: string | undefined): 'success' | 'danger' | 'info' {
  if (result === 'passed') return 'success'
  if (result === 'failed' || result === 'blocked') return 'danger'
  return 'info'
}

function verdictText(result: string | undefined): string {
  if (result === 'passed') return '通过'
  if (result === 'failed') return '失败'
  if (result === 'blocked') return '阻塞'
  if (result === 'skipped') return '跳过'
  return '未执行'
}

async function loadDetail(): Promise<void> {
  loading.value = true
  try {
    detail.value = await executionMatrixApi.get(matrixId.value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载矩阵失败')
  } finally {
    loading.value = false
  }
}

async function loadCompare(): Promise<void> {
  compareLoading.value = true
  try {
    compare.value = await executionMatrixApi.compare(matrixId.value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载对比失败')
  } finally {
    compareLoading.value = false
  }
}

async function refreshAll(): Promise<void> {
  await Promise.all([loadDetail(), loadCompare()])
  const status = detail.value?.matrix.status
  if (status === 'queued' || status === 'running') {
    ensurePoller()
  } else {
    stopPoller()
  }
}

function ensurePoller(): void {
  if (poller) return
  poller = setInterval(() => {
    void loadDetail().then(() => {
      const status = detail.value?.matrix.status
      if (status !== 'queued' && status !== 'running') {
        stopPoller()
        void loadCompare()
      }
    })
  }, 2000)
}

function stopPoller(): void {
  if (poller) {
    clearInterval(poller)
    poller = null
  }
}

const prodDialog = ref<InstanceType<typeof ProdConfirmDialog> | null>(null)

async function dispatch(): Promise<void> {
  try {
    const payload = await executionMatrixApi.run(matrixId.value, { confirmProd: false })
    ElMessage.success(`已投递任务队列（任务 #${payload.task.id}）`)
    ensurePoller()
    void loadDetail()
  } catch (err) {
    const message = err instanceof Error ? err.message : '调度失败'
    if (message.includes('生产环境')) {
      const ok = (await prodDialog.value?.open(message)) ?? false
      if (ok) {
        await dispatchConfirmed()
      }
      return
    }
    ElMessage.error(message)
  }
}

async function dispatchConfirmed(): Promise<void> {
  try {
    const payload = await executionMatrixApi.run(matrixId.value, { confirmProd: true })
    ElMessage.success(`已确认生产环境执行并投递任务队列（任务 #${payload.task.id}）`)
    ensurePoller()
    void loadDetail()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '调度失败')
  }
}

async function createBugs(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '把所有「普遍失败」的用例各转一条缺陷，描述带各格结论与执行证据，是否继续？',
      '转缺陷确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  creatingBugs.value = true
  try {
    const result = await executionMatrixApi.createBugs(matrixId.value)
    if (result.total > 0) {
      ElMessage.success(`已创建 ${result.total} 条缺陷，各格证据已挂到缺陷上`)
    } else {
      ElMessage.info('没有新的普遍失败用例可转（可能已转过）')
    }
    void loadCompare()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '转缺陷失败')
  } finally {
    creatingBugs.value = false
  }
}

onMounted(() => {
  void refreshAll()
})

onBeforeUnmount(stopPoller)
</script>

<style scoped>
.section-title {
  margin: 18px 0 10px;
  font-size: 14px;
}

.matrix-error {
  color: var(--el-color-danger);
  font-size: 12px;
}

.compare-stats {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}

.verdict-chip {
  margin-right: 6px;
  display: inline-block;
  margin-bottom: 4px;
}

.drilldown {
  padding: 8px 12px;
}

.drill-cell {
  margin-bottom: 10px;
}

.drill-head {
  display: flex;
  gap: 8px;
  align-items: center;
}

.assertions {
  margin: 6px 0 0 24px;
  font-size: 12px;
}

.assertion {
  display: flex;
  justify-content: space-between;
  max-width: 720px;
  color: var(--el-color-success);
}

.assertion.failed {
  color: var(--el-color-danger);
}

.diffs {
  margin-top: 8px;
  border-top: 1px dashed var(--el-border-color);
  padding-top: 8px;
}

.diff-title {
  font-weight: 600;
  margin-bottom: 6px;
}

.diff-item {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  margin-bottom: 4px;
}

.diff-name {
  font-weight: 600;
}
</style>
