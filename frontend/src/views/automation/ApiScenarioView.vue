<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">接口场景</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button type="primary" :icon="Plus" @click="openCreate">新建场景</el-button>
        <el-button :icon="Refresh" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </div>
    <p class="muted page-hint">
      把多个接口按业务流程串联：上一步响应提取变量（JSONPath/正则），下一步用 {{ varSyntax }} 引用；
      复用接口断言与测试数据，支持失败即停/继续与数据驱动。
    </p>

    <div class="panel panel-pad">
      <el-table :data="rows" v-loading="loading" border stripe>
        <el-table-column prop="scenarioNo" label="编号" width="110" />
        <el-table-column prop="name" label="场景名称" min-width="180" show-overflow-tooltip />
        <el-table-column label="步骤数" width="80" align="center">
          <template #default="{ row }">{{ row.steps.length }}</template>
        </el-table-column>
        <el-table-column label="数据驱动" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.dataSource" size="small" type="success" effect="plain">有</el-tag>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="success" :loading="runningId === row.id" @click="run(row)">执行</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 编辑 -->
    <el-dialog v-model="editorVisible" :title="editing ? '编辑场景' : '新建场景'" width="860px" top="4vh" custom-class="scenario-dialog">
      <el-form label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如：登录-下单-查询 串联" />
        </el-form-item>
        <el-form-item label="执行环境">
          <el-select v-model="form.envId" clearable filterable placeholder="选择环境（步骤可用相对路径）" style="width: 100%">
            <el-option v-for="e in envOptions" :key="e.id" :label="e.name" :value="e.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="测试数据">
          <el-input v-model="form.dataSource" type="textarea" :rows="2"
                    placeholder="可选：内联数据、本地 txt/Excel/Json/SQL 路径，或 pool:<数据池名称>；有记录集则逐条跑整个场景" />
        </el-form-item>
        <el-form-item label="步骤">
          <div class="scenario-steps">
            <div v-for="(step, i) in form.steps" :key="i" class="scenario-step">
              <div class="scenario-step__head">
                <span class="scenario-step__no">{{ i + 1 }}</span>
                <el-input v-model="step.name" placeholder="步骤名" style="width: 140px" />
                <el-select v-model="step.type" style="width: 100px">
                  <el-option label="接口" value="interface" />
                  <el-option label="浏览器" value="browser" />
                </el-select>
                <template v-if="step.type !== 'browser'">
                  <el-select v-model="step.extra['请求方式']" style="width: 90px">
                    <el-option v-for="m in ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']" :key="m" :label="m" :value="m" />
                  </el-select>
                  <el-input v-model="step.extra['接口URL']" placeholder="/path 或 {{baseUrl}}/path，可用 {{var}}" style="flex: 1" />
                </template>
                <template v-else>
                  <el-select v-model="step.action" style="width: 100px">
                    <el-option v-for="a in ['navigate', 'click', 'type']" :key="a" :label="a" :value="a" />
                  </el-select>
                  <el-input v-model="step.url" placeholder="URL（navigate）" style="flex: 1" />
                  <el-input v-model="step.selector" placeholder="选择器（click/type）" style="flex: 1" />
                </template>
                <el-button link :disabled="i === 0" @click="move(i, -1)">上移</el-button>
                <el-button link :disabled="i === form.steps.length - 1" @click="move(i, 1)">下移</el-button>
                <el-button link type="danger" @click="form.steps.splice(i, 1)">删除</el-button>
              </div>
              <div class="scenario-step__body">
                <el-input v-model="step.extra['请求体']" type="textarea" :rows="2" placeholder="请求体（JSON，可用 {{var}}）" />
                <el-input v-model="step.extra['断言规则']" type="textarea" :rows="2" placeholder="断言规则，每行一条，如 json:$.code = 0" />
                <div class="scenario-step__extract">
                  <div v-for="(ex, j) in step.extract" :key="j" class="scenario-step__extract-row">
                    <el-input v-model="ex.var" placeholder="变量名" style="width: 120px" />
                    <el-input v-model="ex.path" placeholder="JSONPath 如 $.data.token" style="flex: 1" />
                    <el-button link type="danger" @click="step.extract.splice(j, 1)">移除</el-button>
                  </div>
                  <el-button link type="primary" size="small" @click="step.extract.push({ var: '', path: '' })">+ 提取变量</el-button>
                  <el-checkbox v-model="step.continueOnError" style="margin-left: 12px">失败继续</el-checkbox>
                </div>
              </div>
            </div>
            <el-button :icon="Plus" plain @click="addStep">添加步骤</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 执行回放 -->
    <el-dialog v-model="replayVisible" title="场景执行回放" width="900px" top="4vh" custom-class="scenario-dialog">
      <div v-if="result">
        <div class="scenario-replay__summary">
          <el-tag :type="result.passed ? 'success' : 'danger'" effect="dark">{{ result.passed ? '通过' : '未通过' }}</el-tag>
          <span class="muted">{{ result.runNo }} · {{ result.recordCount }} 条数据 · {{ result.durationMs }} ms</span>
          <span v-if="trend" class="muted">历史通过率 {{ trend.passRate ?? '-' }}%（{{ trend.total }} 次）</span>
          <span class="scenario-replay__trend">
            <i v-for="(p, i) in (trend?.points ?? []).slice(-20)" :key="i" :class="p.passed ? 'ok' : 'bad'" :title="`${p.runNo} ${p.passed ? '通过' : '失败'}`" />
          </span>
          <el-button link type="primary" size="small" @click="openReport">HTML 报告</el-button>
          <el-button link type="primary" size="small" :loading="reporting" @click="generateScopedReport">
            纳入报告
          </el-button>
          <el-tag v-if="result.autoBugId" size="small" type="warning" effect="plain">已自动转缺陷 #{{ result.autoBugId }}</el-tag>
          <el-button v-if="!result.passed" link type="danger" size="small" :loading="bugging" @click="toBug">转缺陷</el-button>
        </div>
        <div v-if="clusters.length" class="scenario-replay__cluster">
          <strong>失败聚类：</strong>
          <el-tag v-for="c in clusters" :key="c.name" size="small" type="danger" effect="plain" style="margin-right: 6px">
            {{ c.name }} ×{{ c.count }}
          </el-tag>
        </div>
        <div v-if="diff.length" class="scenario-replay__diff">
          <strong>与上轮对比：</strong>
          <span v-for="d in diff" :key="d.name" class="muted" style="margin-right: 10px">
            {{ d.name }}：{{ d.prev ? '通过' : '失败' }} → {{ d.now ? '通过' : '失败' }}
          </span>
        </div>
        <div v-for="(run, ri) in result.runs" :key="ri" class="scenario-replay__run">
          <div v-if="result.runs.length > 1" class="scenario-replay__record muted">数据记录 {{ ri + 1 }}</div>
          <div v-for="s in run.steps" :key="s.stepNo" class="scenario-replay__step" :class="{ 'is-fail': !s.passed }">
            <div class="scenario-replay__step-head">
              <el-tag size="small" :type="s.passed ? 'success' : 'danger'" effect="plain">{{ s.passed ? '通过' : '失败' }}</el-tag>
              <strong>{{ s.stepNo }}. {{ s.name }}</strong>
              <span class="muted">HTTP {{ s.statusCode ?? '-' }} · {{ s.durationMs }} ms</span>
              <el-tag v-for="(v, k) in s.extracted" :key="k" size="small" type="info" effect="plain">{{ k }}={{ v }}</el-tag>
            </div>
            <div class="muted text-mono scenario-replay__url">{{ s.request.method }} {{ s.request.url }}</div>
            <div class="scenario-replay__waterfall" :title="`${s.durationMs} ms`">
              <i :style="{ width: waterfallWidth(s) + '%' }" :class="s.passed ? 'ok' : 'bad'" />
              <span class="muted">{{ s.durationMs }} ms</span>
            </div>
            <div v-if="s.assertions?.length" class="scenario-replay__asserts">
              <div v-for="(a, ai) in s.assertions" :key="ai" :class="a.passed ? 'ok' : 'bad'">
                {{ a.passed ? '✓' : '✗' }} {{ a.name }}<span v-if="!a.passed">：{{ a.message }}</span>
              </div>
            </div>
            <pre v-if="s.response?.body" class="scenario-replay__body">{{ s.response.body }}</pre>
          </div>
          <div class="muted scenario-replay__vars">共享变量：{{ JSON.stringify(run.variables) }}</div>
        </div>
      </div>
      <template #footer>
        <el-button @click="replayVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { apiScenarioApi, reportApi, testEnvApi } from '@/api'
import type { ApiScenario, ApiScenarioRunResult, ApiScenarioStep, ApiScenarioStepResult } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { fetchAllPages } from '@/utils/fetchAll'

const appStore = useAppStore()
const router = useRouter()
const varSyntax = '{{var}}'
const loading = ref(false)
const rows = ref<ApiScenario[]>([])
const envOptions = ref<{ id: number; name: string }[]>([])
const editorVisible = ref(false)
const editing = ref<ApiScenario | null>(null)
const saving = ref(false)
const runningId = ref<number | null>(null)
const replayVisible = ref(false)
const result = ref<ApiScenarioRunResult | null>(null)
const runScenarioId = ref<number | null>(null)
const trend = ref<{ points: { runNo: string; passed: boolean; durationMs: number; createdAt: string }[]; total: number; passRate: number | null } | null>(null)
const prevRun = ref<ApiScenarioRunResult | null>(null)
const bugging = ref(false)
const reporting = ref(false)

const clusters = computed(() => {
  const map = new Map<string, number>()
  for (const rec of result.value?.runs ?? []) {
    for (const s of rec.steps) {
      if (s.passed) continue
      for (const a of s.assertions ?? []) {
        if (a.passed) continue
        const key = a.name || s.name
        map.set(key, (map.get(key) ?? 0) + 1)
      }
    }
  }
  return [...map.entries()].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count)
})

const diff = computed(() => {
  const cur = result.value?.runs[0]?.steps ?? []
  const prev = prevRun.value?.runs[0]?.steps ?? []
  if (!prev.length) return []
  return cur.map((s, i) => ({ name: s.name, now: s.passed, prev: prev[i]?.passed ?? false }))
    .filter((d) => d.now !== d.prev)
})

function waterfallWidth(s: ApiScenarioStepResult): number {
  const all = (result.value?.runs ?? []).flatMap((r) => r.steps)
  const max = Math.max(...all.map((x) => x.durationMs), 1)
  return Math.max(2, Math.round((s.durationMs * 100) / max))
}

function openReport(): void {
  if (result.value && runScenarioId.value != null) {
    window.open(apiScenarioApi.reportUrl(runScenarioId.value, result.value.runId), '_blank')
  }
}

async function generateScopedReport(): Promise<void> {
  if (!result.value || runScenarioId.value == null) return
  const projectId = appStore.currentProjectId
  if (!projectId) {
    ElMessage.warning('先选择一个项目')
    return
  }
  reporting.value = true
  try {
    const report = await reportApi.generate({
      projectId,
      reportType: 'iteration',
      sourceType: 'scenario',
      sourceId: runScenarioId.value,
      sourceRunId: result.value.runId,
    })
    ElMessage.success(`已生成场景报告 ${report.reportNo}`)
    void router.push(`/reports/${report.id}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '生成报告失败')
  } finally {
    reporting.value = false
  }
}

async function toBug(): Promise<void> {
  if (!result.value || runScenarioId.value == null) return
  bugging.value = true
  try {
    const r = await apiScenarioApi.toBug(runScenarioId.value, result.value.runId)
    ElMessage.success(`已转缺陷 ${r.bugNo}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '转缺陷失败')
  } finally {
    bugging.value = false
  }
}

const emptyStep = (): ApiScenarioStep => ({
  name: '', type: 'interface', extra: { 请求方式: 'GET', 接口URL: '', 请求体: '', 断言规则: '' },
  extract: [], continueOnError: false, waitMs: 0,
})
const form = reactive({
  name: '', envId: null as number | null, dataSource: '',
  steps: [emptyStep()] as ApiScenarioStep[],
})

async function reload(): Promise<void> {
  if (!appStore.currentProjectId) return
  loading.value = true
  try {
    rows.value = await fetchAllPages((pageNum, pageSize) =>
      apiScenarioApi.list({ projectId: appStore.currentProjectId, pageNum, pageSize }))
    const envs = await testEnvApi.list()
    envOptions.value = (envs.list ?? []).map((e) => ({ id: e.id as number, name: e.name }))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载场景失败')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  Object.assign(form, { name: '', envId: null, dataSource: '', steps: [emptyStep()] })
  editorVisible.value = true
}

async function openEdit(row: ApiScenario): Promise<void> {
  const detail = await apiScenarioApi.get(row.id)
  editing.value = detail
  Object.assign(form, {
    name: detail.name, envId: detail.envId, dataSource: detail.dataSource,
    steps: detail.steps.length ? detail.steps : [emptyStep()],
  })
  editorVisible.value = true
}

function addStep(): void { form.steps.push(emptyStep()) }
function move(i: number, d: number): void {
  const [s] = form.steps.splice(i, 1)
  form.steps.splice(i + d, 0, s)
}

async function save(): Promise<void> {
  if (!form.name.trim()) { ElMessage.warning('名称必填'); return }
  if (!form.steps.some((s) => (s.extra['接口URL'] || '').trim())) { ElMessage.warning('至少一步填写接口URL'); return }
  saving.value = true
  try {
    const payload = {
      projectId: appStore.currentProjectId, name: form.name, envId: form.envId,
      dataSource: form.dataSource, steps: form.steps,
    }
    if (editing.value) await apiScenarioApi.update(editing.value.id, payload)
    else await apiScenarioApi.create(payload)
    ElMessage.success('已保存')
    editorVisible.value = false
    await reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function run(row: ApiScenario): Promise<void> {
  runningId.value = row.id
  runScenarioId.value = row.id
  try {
    result.value = await apiScenarioApi.run(row.id, {})
    replayVisible.value = true
    trend.value = await apiScenarioApi.trend(row.id).catch(() => null)
    const runsPage = await apiScenarioApi.runs(row.id, { pageSize: 2 }).catch(() => null)
    prevRun.value = (runsPage?.list ?? [])[1] ?? null
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '执行失败')
  } finally {
    runningId.value = null
  }
}

async function remove(row: ApiScenario): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除场景「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  await apiScenarioApi.remove(row.id)
  ElMessage.success('已删除')
  await reload()
}

onMounted(reload)
watch(() => appStore.currentProjectId, () => reload())
</script>

<style scoped>
.page-hint { margin: 0 0 12px; font-size: 12px; }
.scenario-steps { width: 100%; display: flex; flex-direction: column; gap: 10px; }
.scenario-step { border: 1px solid var(--aitp-border); border-radius: 8px; padding: 8px; }
.scenario-step__head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.scenario-step__no { width: 22px; height: 22px; border-radius: 50%; background: var(--el-color-primary-light-8); color: var(--el-color-primary); display: inline-flex; align-items: center; justify-content: center; font-size: 12px; flex: none; }
.scenario-step__body { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
.scenario-step__extract { display: flex; flex-direction: column; gap: 4px; }
.scenario-step__extract-row { display: flex; gap: 6px; align-items: center; }
.scenario-replay__summary { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.scenario-replay__run { margin-bottom: 12px; }
.scenario-replay__record { font-size: 12px; margin-bottom: 4px; }
.scenario-replay__step { border: 1px solid var(--aitp-border); border-left: 3px solid var(--el-color-success); border-radius: 6px; padding: 8px; margin-bottom: 8px; }
.scenario-replay__step.is-fail { border-left-color: var(--el-color-danger); }
.scenario-replay__step-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.scenario-replay__url { font-size: 12px; margin: 4px 0; word-break: break-all; }
.scenario-replay__asserts { font-size: 12px; display: flex; flex-direction: column; gap: 2px; }
.scenario-replay__asserts .ok { color: var(--el-color-success); }
.scenario-replay__asserts .bad { color: var(--el-color-danger); }
.scenario-replay__body { margin: 6px 0 0; max-height: 160px; overflow-y: auto; font-size: 12px; background: var(--aitp-subtle); border-radius: 4px; padding: 6px; white-space: pre-wrap; word-break: break-all; }
.scenario-replay__vars { font-size: 12px; word-break: break-all; }
.scenario-replay__trend { display: inline-flex; gap: 2px; align-items: center; }
.scenario-replay__trend i { width: 6px; height: 12px; border-radius: 2px; display: inline-block; }
.scenario-replay__trend i.ok { background: var(--el-color-success); }
.scenario-replay__trend i.bad { background: var(--el-color-danger); }
.scenario-replay__cluster, .scenario-replay__diff { font-size: 12px; margin: 6px 0; }
.scenario-replay__waterfall { display: flex; align-items: center; gap: 6px; margin: 4px 0; }
.scenario-replay__waterfall i { height: 8px; border-radius: 4px; display: inline-block; }
.scenario-replay__waterfall i.ok { background: var(--el-color-success); }
.scenario-replay__waterfall i.bad { background: var(--el-color-danger); }
.scenario-replay__waterfall span { font-size: 11px; }
</style>

<style>
.scenario-dialog .el-dialog__body { max-height: calc(100vh - 220px); overflow-y: auto; }
</style>