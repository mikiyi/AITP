<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">测试计划与轮次</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openPlanDialog()">新建计划</el-button>
      </div>
    </div>

    <el-tabs v-model="tab" class="exec-tabs">
      <el-tab-pane label="测试计划" name="plans">
        <div class="panel panel-pad">
          <div class="toolbar">
            <el-input
              v-model="planFilters.keyword"
              placeholder="搜索计划名称"
              clearable
              style="width: 220px"
              @input="schedulePlans"
              @keyup.enter="reloadPlans"
              @clear="reloadPlans"
            />
            <el-select v-model="planFilters.status" placeholder="状态" clearable style="width: 140px" @change="reloadPlans">
              <el-option label="启用" value="active" />
              <el-option label="已归档" value="archived" />
            </el-select>
            <el-button :icon="RefreshLeft" @click="resetPlanFilters">重置</el-button>
          </div>

          <el-table v-loading="planLoading" :data="plans" border stripe>
            <el-table-column label="计划名称" min-width="200" show-overflow-tooltip>
              <template #default="scope">
                <div>{{ scope.row.name }}</div>
                <div class="run-sub">
                  负责人：{{ scope.row.owner || '-' }}{{ scope.row.defaultEnvId ? ` · 环境：${envNameOf(scope.row.defaultEnvId)}` : '' }}{{ scope.row.description ? ` · ${scope.row.description}` : '' }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="版本" width="90">
              <template #default="scope">
                <span class="muted">{{ scope.row.versionCode || scope.row.versionName || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="轮次" width="64" align="center">
              <template #default="scope">{{ scope.row.runCount ?? 0 }}</template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="scope">
                <el-tag size="small" :type="scope.row.status === 'active' ? 'success' : 'info'" effect="plain">
                  {{ scope.row.status === 'active' ? '启用' : '已归档' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="更新时间" width="150">
              <template #default="scope">{{ dateTimeText(scope.row.updatedAt) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="200" fixed="right">
              <template #default="scope">
                <el-button link type="primary" :icon="VideoPlay" @click="openLaunch(scope.row)">发起轮次</el-button>
                <el-button link @click="openPlanDialog(scope.row)">编辑</el-button>
                <el-button link type="danger" @click="removePlan(scope.row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <span class="muted">还没有测试计划，先建一个再来发起轮次</span>
            </template>
          </el-table>

          <div class="pager">
            <el-pagination
              v-model:current-page="planPage"
              v-model:page-size="planPageSize"
              :total="planTotal"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              @current-change="loadPlans"
              @size-change="reloadPlans"
            />
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="测试轮次" name="runs">
        <div class="panel panel-pad">
          <div class="toolbar">
            <el-input
              v-model="runFilters.keyword"
              placeholder="搜索轮次名称"
              clearable
              style="width: 220px"
              @input="scheduleRuns"
              @keyup.enter="reloadRuns"
              @clear="reloadRuns"
            />
            <el-select v-model="runFilters.status" placeholder="状态" clearable style="width: 140px" @change="reloadRuns">
              <el-option label="执行中" value="running" />
              <el-option label="已完成" value="completed" />
              <el-option label="已中止" value="aborted" />
            </el-select>
            <el-button :icon="RefreshLeft" @click="resetRunFilters">重置</el-button>
          </div>

          <el-table v-loading="runLoading" :data="runs" border stripe>
            <el-table-column label="轮次" min-width="200" show-overflow-tooltip>
              <template #default="scope">
                <div>{{ scope.row.name }}</div>
                <div class="run-sub">
                  计划：{{ scope.row.planName || scope.row.planId || '-' }} · 环境：{{ scope.row.envName || scope.row.env || '-' }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="版本" width="90">
              <template #default="scope">{{ scope.row.versionCode || '-' }}</template>
            </el-table-column>
            <el-table-column label="结果" width="168" align="center">
              <template #header>
                <span class="run-result-head">通过/失败/阻塞/未执行</span>
              </template>
              <template #default="scope">
                <span class="run-result">
                  <span class="ok">{{ scope.row.passed }}</span>
                  <span class="muted">/</span>
                  <span class="bad">{{ scope.row.failed }}</span>
                  <span class="muted">/</span>
                  <span class="warn">{{ scope.row.blocked }}</span>
                  <span class="muted">/</span>
                  <span class="muted">{{ scope.row.notExecuted }}</span>
                </span>
              </template>
            </el-table-column>
            <el-table-column label="通过率" width="80" align="center">
              <template #default="scope">{{ percent(scope.row) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="scope">
                <el-tag size="small" :type="runStatusType(scope.row.status)" effect="plain">
                  {{ runStatusText(scope.row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="scope">
                <el-button link type="primary" @click="openRun(scope.row)">进入执行</el-button>
                <el-button link type="danger" @click="removeRun(scope.row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <span class="muted">还没有测试轮次，可以从计划发起，或在用例列表选中后创建</span>
            </template>
          </el-table>

          <div class="pager">
            <el-pagination
              v-model:current-page="runPage"
              v-model:page-size="runPageSize"
              :total="runTotal"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              @current-change="loadRuns"
              @size-change="reloadRuns"
            />
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="planDialog" :title="planForm.id ? '编辑测试计划' : '新建测试计划'" width="600px">
      <el-form label-width="90px">
        <el-form-item label="项目" required>
          <el-select
            v-model="planForm.projectId"
            placeholder="选择项目"
            style="width: 100%"
            @change="onFormProjectChange"
          >
            <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="计划名称" required>
          <el-input v-model="planForm.name" placeholder="例如：支付链路 2.3 回归计划" />
        </el-form-item>
        <el-form-item label="版本">
          <el-select v-model="planForm.versionId" placeholder="不指定版本" clearable style="width: 100%">
            <el-option
              v-for="item in formVersions"
              :key="item.id"
              :label="`${item.versionCode} ${item.name || ''}`.trim()"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="负责人">
          <el-input v-model="planForm.owner" placeholder="本地用户" />
        </el-form-item>
        <el-form-item label="默认环境">
          <el-select v-model="planForm.defaultEnvId" placeholder="不指定环境" clearable style="width: 100%">
            <el-option
              v-for="item in envs"
              :key="item.id"
              :label="`${item.name}${item.kindLabel ? `（${item.kindLabel}）` : ''}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="planForm.description" type="textarea" :rows="3" placeholder="本次测试的范围与目标" />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="planForm.status">
            <el-radio value="active">启用</el-radio>
            <el-radio value="archived">归档</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="planDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingPlan" @click="savePlan">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="launchDialog" title="发起测试轮次" width="900px">
      <el-form label-width="90px">
        <el-form-item label="轮次名称" required>
          <el-input v-model="launchForm.name" placeholder="例如：登录回归第 1 轮" />
        </el-form-item>
        <el-form-item label="环境">
          <el-select v-model="launchForm.envId" placeholder="不指定环境" clearable style="width: 100%">
            <el-option
              v-for="item in envs"
              :key="item.id"
              :label="`${item.name}${item.kindLabel ? `（${item.kindLabel}）` : ''}`"
              :value="item.id"
            />
          </el-select>
          <div v-if="launchForm.envId && envKindOf(launchForm.envId) === 'prod'" class="prod-hint">
            这是生产环境，发起轮次前会再次确认。
          </div>
        </el-form-item>
      </el-form>

      <div class="toolbar launch-filter">
        <el-select v-model="launchFilter.requirementId" placeholder="需求" clearable filterable style="width: 220px">
          <el-option
            v-for="item in requirements"
            :key="item.id"
            :label="`${item.reqNo || ''} ${item.title}`.trim()"
            :value="item.id"
          />
        </el-select>
        <el-select v-model="launchFilter.caseType" placeholder="用例类型" clearable style="width: 150px">
          <el-option v-for="item in caseTypes" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="launchFilter.priority" placeholder="优先级" clearable style="width: 120px">
          <el-option v-for="item in priorities" :key="item" :label="item" :value="item" />
        </el-select>
        <el-button type="primary" :icon="Search" :loading="caseLoading" @click="loadCandidates">筛选用例</el-button>
        <div class="spacer" />
        <span class="muted">已选 {{ selectedCaseIds.length }} 条</span>
      </div>

      <el-table
        v-loading="caseLoading"
        :data="candidates"
        border
        size="small"
        max-height="340"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column prop="caseNo" label="编号" width="100" />
        <el-table-column prop="title" label="用例标题" min-width="240" show-overflow-tooltip />
        <el-table-column prop="priority" label="优先级" width="80" align="center" />
        <el-table-column prop="caseType" label="类型" width="100">
          <template #default="scope">{{ caseTypeText(scope.row.caseType) }}</template>
        </el-table-column>
        <el-table-column label="当前状态" width="100">
          <template #default="scope">{{ caseStatusText(scope.row.status) }}</template>
        </el-table-column>
      </el-table>

      <template #footer>
        <el-button @click="launchDialog = false">取消</el-button>
        <el-button type="primary" :loading="launching" @click="launchRun">发起轮次</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onActivated, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, RefreshLeft, Search, VideoPlay } from '@element-plus/icons-vue'
import { caseApi, projectVersionApi, requirementApi, testEnvApi, testPlanApi, testRunApi } from '@/api'
import { useAutoQuery } from '@/composables/useAutoQuery'
import type { ProjectVersion, Requirement, TestCase, TestEnv, TestPlan, TestRun } from '@/api/types'
import { useAppStore } from '@/stores/app'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { dateTimeText } from '@/utils/format'

const appStore = useAppStore()
const router = useRouter()

const tab = ref<'plans' | 'runs'>('plans')
const versions = ref<ProjectVersion[]>([])
const formVersions = ref<ProjectVersion[]>([])
const requirements = ref<Requirement[]>([])
const envs = ref<TestEnv[]>([])

const plans = ref<TestPlan[]>([])
const planTotal = ref(0)
const planPage = ref(1)
const planPageSize = ref(20)
const planLoading = ref(false)
const planDialog = ref(false)
const savingPlan = ref(false)
const planFilters = reactive({ keyword: '', status: '' })

const runs = ref<TestRun[]>([])
const runTotal = ref(0)
const runPage = ref(1)
const runPageSize = ref(20)
const runLoading = ref(false)
const runFilters = reactive({ keyword: '', status: '' })

const launchDialog = ref(false)
const launching = ref(false)
const caseLoading = ref(false)
const candidates = ref<TestCase[]>([])
const selectedCaseIds = ref<number[]>([])
const launchPlan = ref<TestPlan | null>(null)
const launchForm = reactive({ name: '', envId: null as number | null })
const launchFilter = reactive<{ requirementId: number | null; caseType: string; priority: string }>({
  requirementId: null,
  caseType: '',
  priority: '',
})

const planForm = reactive<{
  id: number | null
  projectId: number | null
  versionId: number | null
  defaultEnvId: number | null
  name: string
  owner: string
  description: string
  status: string
}>({
  id: null,
  projectId: null,
  versionId: null,
  defaultEnvId: null,
  name: '',
  owner: '',
  description: '',
  status: 'active',
})

const caseTypes = [
  { value: 'functional', label: '功能' },
  { value: 'interface', label: '接口' },
  { value: 'boundary', label: '边界' },
  { value: 'security', label: '安全' },
  { value: 'ui', label: '界面' },
  { value: 'exception', label: '异常' },
]
const priorities = ['P0', 'P1', 'P2', 'P3']

function caseTypeText(value: string): string {
  return caseTypes.find((item) => item.value === value)?.label ?? value
}

function caseStatusText(value: string): string {
  return { not_executed: '未执行', pass: '通过', fail: '失败', blocked: '阻塞' }[value] ?? value
}

function runStatusText(value: string): string {
  return { pending: '待开始', running: '执行中', completed: '已完成', aborted: '已中止' }[value] ?? value
}

function runStatusType(value: string): 'success' | 'warning' | 'info' | 'danger' {
  if (value === 'completed') return 'success'
  if (value === 'running') return 'warning'
  if (value === 'aborted') return 'danger'
  return 'info'
}

function percent(row: TestRun): string {
  const executed = row.total - row.notExecuted
  if (!executed) return '-'
  return `${Math.round((row.passed / executed) * 100)}%`
}

function envNameOf(envId: number | null): string {
  if (!envId) return ''
  return envs.value.find((item) => item.id === envId)?.name ?? ''
}

function envKindOf(envId: number | null): string {
  if (!envId) return ''
  return envs.value.find((item) => item.id === envId)?.kind ?? ''
}

async function loadFormVersions(projectId: number | null): Promise<void> {
  if (!projectId) {
    formVersions.value = []
    return
  }
  if (projectId === appStore.currentProjectId) {
    formVersions.value = versions.value
    return
  }
  try {
    const result = await projectVersionApi.list(projectId)
    formVersions.value = result.list ?? []
  } catch {
    formVersions.value = []
  }
}

async function loadPlans(): Promise<void> {
  if (!appStore.currentProjectId) return
  planLoading.value = true
  try {
    const page = await testPlanApi.list({
      projectId: appStore.currentProjectId,
      keyword: planFilters.keyword,
      status: planFilters.status,
      pageNum: planPage.value,
      pageSize: planPageSize.value,
    })
    plans.value = page.list ?? []
    planTotal.value = page.total ?? 0
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载测试计划失败')
  } finally {
    planLoading.value = false
  }
}

async function loadRuns(): Promise<void> {
  if (!appStore.currentProjectId) return
  runLoading.value = true
  try {
    const page = await testRunApi.list({
      projectId: appStore.currentProjectId,
      keyword: runFilters.keyword,
      status: runFilters.status,
      pageNum: runPage.value,
      pageSize: runPageSize.value,
    })
    runs.value = page.list ?? []
    runTotal.value = page.total ?? 0
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载测试轮次失败')
  } finally {
    runLoading.value = false
  }
}

async function loadOptions(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) {
    versions.value = []
    requirements.value = []
    return
  }
  try {
    const [versionResult, requirementPage, envResult] = await Promise.all([
      projectVersionApi.list(projectId),
      requirementApi.list({ projectId, pageNum: 1, pageSize: 200 }),
      testEnvApi.list(),
    ])
    versions.value = versionResult.list ?? []
    requirements.value = requirementPage.list ?? []
    envs.value = envResult.list ?? []
  } catch {
    versions.value = []
    requirements.value = []
  }
}

async function loadAll(): Promise<void> {
  await Promise.all([loadOptions(), loadPlans(), loadRuns()])
}

function reloadPlans(): void {
  planPage.value = 1
  void loadPlans()
}

function reloadRuns(): void {
  runPage.value = 1
  void loadRuns()
}

const schedulePlans = useAutoQuery(reloadPlans)
const scheduleRuns = useAutoQuery(reloadRuns)

function resetPlanFilters(): void {
  planFilters.keyword = ''
  planFilters.status = ''
  reloadPlans()
}

function resetRunFilters(): void {
  runFilters.keyword = ''
  runFilters.status = ''
  reloadRuns()
}

function openPlanDialog(row?: TestPlan): void {
  planForm.id = row?.id ?? null
  planForm.projectId = row?.projectId ?? appStore.currentProjectId
  planForm.versionId = row?.versionId ?? null
  planForm.defaultEnvId = row?.defaultEnvId ?? null
  planForm.name = row?.name ?? ''
  planForm.owner = row?.owner ?? ''
  planForm.description = row?.description ?? ''
  planForm.status = row?.status ?? 'active'
  planDialog.value = true
  void loadFormVersions(planForm.projectId)
}

function onFormProjectChange(value: number | null): void {
  planForm.versionId = null
  void loadFormVersions(value)
}

async function savePlan(): Promise<void> {
  if (!planForm.projectId) {
    ElMessage.warning('请选择项目')
    return
  }
  if (!planForm.name.trim()) {
    ElMessage.warning('计划名称不能为空')
    return
  }
  savingPlan.value = true
  try {
    const payload = {
      projectId: planForm.projectId,
      versionId: planForm.versionId,
      defaultEnvId: planForm.defaultEnvId,
      name: planForm.name,
      owner: planForm.owner,
      description: planForm.description,
      status: planForm.status,
    }
    if (planForm.id) {
      await testPlanApi.update(planForm.id, payload)
    } else {
      await testPlanApi.create(payload)
    }
    ElMessage.success('已保存')
    planDialog.value = false
    await loadPlans()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    savingPlan.value = false
  }
}

async function removePlan(row: TestPlan): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除测试计划「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await testPlanApi.remove(row.id)
    ElMessage.success('已删除')
    await loadPlans()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

async function openLaunch(row: TestPlan): Promise<void> {
  launchPlan.value = row
  launchForm.name = `${row.name} · ${new Date().toLocaleString()}`
  launchForm.envId = row.defaultEnvId ?? null
  launchFilter.requirementId = null
  launchFilter.caseType = ''
  launchFilter.priority = ''
  selectedCaseIds.value = []
  candidates.value = []
  launchDialog.value = true
  await loadCandidates()
}

async function loadCandidates(): Promise<void> {
  if (!launchPlan.value) return
  caseLoading.value = true
  try {
    const page = await caseApi.list({
      projectId: launchPlan.value.projectId,
      requirementId: launchFilter.requirementId ?? undefined,
      caseType: launchFilter.caseType || undefined,
      priority: launchFilter.priority || undefined,
      pageNum: 1,
      pageSize: 200,
    })
    candidates.value = page.list ?? []
    if (!candidates.value.length) {
      ElMessage.info('当前筛选条件下没有用例')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载候选用例失败')
  } finally {
    caseLoading.value = false
  }
}

function onSelectionChange(rows: TestCase[]): void {
  selectedCaseIds.value = rows.map((row) => row.id)
}

async function launchRun(): Promise<void> {
  if (!launchPlan.value) return
  if (!launchForm.name.trim()) {
    ElMessage.warning('轮次名称不能为空')
    return
  }
  if (!selectedCaseIds.value.length) {
    ElMessage.warning('请先勾选要执行的用例')
    return
  }
  if (launchForm.envId && envKindOf(launchForm.envId) === 'prod') {
    try {
      await ElMessageBox.confirm(
        `即将在「${envNameOf(launchForm.envId)}」生产环境发起轮次，确认继续？`,
        '生产环境确认',
        { type: 'warning', confirmButtonText: '确认发起', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  launching.value = true
  try {
    const run = await testPlanApi.createRun(launchPlan.value.id, {
      name: launchForm.name,
      envId: launchForm.envId,
      caseIds: selectedCaseIds.value,
    })
    ElMessage.success(`已发起轮次，共 ${run.total} 条用例`)
    launchDialog.value = false
    await loadAll()
    await openRun(run)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '发起轮次失败')
  } finally {
    launching.value = false
  }
}

function openRun(row: TestRun): void {
  void router.push({ name: 'test-run-detail', params: { id: row.id } })
}

async function removeRun(row: TestRun): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除轮次「${row.name}」？快照与结果会一并下线。`, '删除确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await testRunApi.remove(row.id)
    ElMessage.success('已删除')
    await loadRuns()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

watch(
  () => appStore.currentProjectId,
  () => {
    planPage.value = 1
    runPage.value = 1
    void loadAll()
  },
)

let activated = false

onMounted(async () => {
  await appStore.loadProjects()
  await loadAll()
})

onActivated(() => {
  // 首次挂载已由 onMounted 加载，之后从轮次详情返回时刷新统计
  if (!activated) {
    activated = true
    return
  }
  void loadAll()
})
</script>

<style scoped>
.exec-tabs {
  margin-top: 4px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.run-result {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
}

.run-sub {
  margin-top: 2px;
  font-size: 12px;
  color: var(--aitp-text-weak);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-result-head {
  font-size: 11px;
  white-space: nowrap;
}

.run-result .ok {
  color: var(--el-color-success);
}

.run-result .bad {
  color: var(--el-color-danger);
}

.run-result .warn {
  color: var(--el-color-warning);
}

.launch-filter {
  margin-bottom: 10px;
}

.prod-hint {
  margin-top: 8px;
  padding: 8px 11px 8px 12px;
  border: 1px solid color-mix(in srgb, var(--aitp-note-error) 16%, transparent);
  border-left: 3px solid var(--aitp-note-error);
  border-radius: 10px;
  background: color-mix(in srgb, var(--aitp-note-error) 5%, var(--aitp-panel));
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--aitp-text);
}
</style>
