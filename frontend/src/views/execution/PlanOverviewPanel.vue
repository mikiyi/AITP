<template>
  <div class="overview">
    <!-- 顶部指标卡：先看宏观再下钻 -->
    <div class="overview-metrics">
      <div class="ov-metric">
        <span class="ov-metric__label">总用例数</span>
        <span class="ov-metric__value">{{ metrics.totalCases }}</span>
      </div>
      <div class="ov-metric">
        <span class="ov-metric__label">通过率</span>
        <span class="ov-metric__value" :style="{ color: rateColor(metrics.passRate) }">
          {{ metrics.passRate == null ? '-' : `${metrics.passRate}%` }}
        </span>
      </div>
      <div class="ov-metric">
        <span class="ov-metric__label">未闭环缺陷</span>
        <span class="ov-metric__value ov-metric__value--danger">{{ metrics.openBugs }}</span>
      </div>
      <div class="ov-metric">
        <span class="ov-metric__label">执行耗时</span>
        <span class="ov-metric__value">{{ durationText(metrics.totalDurationMs) }}</span>
      </div>
    </div>

    <div class="overview-toolbar">
      <el-radio-group v-model="view" size="small">
        <el-radio-button value="tree">层级列表</el-radio-button>
        <el-radio-button value="gantt">甘特图</el-radio-button>
        <el-radio-button value="kanban">状态看板</el-radio-button>
      </el-radio-group>
      <span class="muted overview-hint">
        {{ view === 'tree' ? '版本 → 计划 → 轮次，点行展开详情' : view === 'gantt' ? '横轴时间 · 纵轴轮次，看节奏与重叠' : '选一个轮次，拖拽用例卡片更新结果' }}
      </span>
      <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <!-- 层级树形列表 -->
    <div v-if="view === 'tree'" v-loading="loading" class="overview-tree">
      <el-empty v-if="!tree.length" description="当前项目还没有版本 / 计划 / 轮次" :image-size="70" />
      <div v-for="ver in tree" :key="ver.key" class="ov-node ov-node--version">
        <div class="ov-row" @click="toggle(ver.key)">
          <el-icon class="ov-caret" :class="{ 'is-open': opened.has(ver.key) }"><ArrowRight /></el-icon>
          <el-tag size="small" effect="dark" type="primary">{{ ver.versionCode || '未指定版本' }}</el-tag>
          <span class="ov-name">{{ ver.name }}</span>
          <span class="muted ov-meta">需求 {{ ver.reqCount }} · 计划 {{ ver.plans.length }} · 轮次 {{ ver.runCount }}</span>
          <span class="ov-rate" :style="{ color: rateColor(ver.passRate) }">{{ ver.passRate == null ? '-' : `${ver.passRate}%` }}</span>
          <span class="ov-progress"><i :style="{ width: `${ver.passRate ?? 0}%`, background: rateColor(ver.passRate) }" /></span>
        </div>
        <div v-show="opened.has(ver.key)" class="ov-children">
          <div v-for="plan in ver.plans" :key="plan.key" class="ov-node ov-node--plan">
            <div class="ov-row" @click="toggle(plan.key)">
              <el-icon class="ov-caret" :class="{ 'is-open': opened.has(plan.key) }"><ArrowRight /></el-icon>
              <el-tag size="small" effect="plain">{{ plan.name }}</el-tag>
              <span class="muted ov-meta">{{ plan.range }} · 用例 {{ plan.totalCases }} · 失败 {{ plan.failed }}</span>
              <span class="ov-rate" :style="{ color: rateColor(plan.passRate) }">{{ plan.passRate == null ? '-' : `${plan.passRate}%` }}</span>
              <span class="ov-progress"><i :style="{ width: `${plan.passRate ?? 0}%`, background: rateColor(plan.passRate) }" /></span>
            </div>
            <div v-show="opened.has(plan.key)" class="ov-children">
              <div v-for="run in plan.runs" :key="run.id" class="ov-row ov-row--run" @click="openRun(run)">
                <span class="ov-run-name">{{ run.name }}</span>
                <el-tag v-if="run.roundType" size="small" effect="plain" type="info">{{ roundLabel(run.roundType) }}</el-tag>
                <el-tag size="small" :type="gateType(run)" effect="plain">{{ gateLabel(run) }}</el-tag>
                <span class="muted ov-meta">
                  {{ run.plannedStart ? `${run.plannedStart}~${run.plannedEnd} · ` : '' }}执行人 {{ run.owner || '-' }} · 通过 {{ run.passed }}/{{ run.total }}
                </span>
                <span class="ov-rate" :style="{ color: rateColor(runPassRate(run)) }">{{ runPassRate(run) == null ? '-' : `${runPassRate(run)}%` }}</span>
                <span class="ov-progress"><i :style="{ width: `${runPassRate(run) ?? 0}%`, background: rateColor(runPassRate(run)) }" /></span>
              </div>
              <div v-for="run in ver.orphanRuns" :key="`o-${run.id}`" class="ov-row ov-row--run" @click="openRun(run)">
                <span class="ov-run-name">{{ run.name }}</span>
                <el-tag size="small" :type="gateType(run)" effect="plain">{{ gateLabel(run) }}</el-tag>
                <span class="muted ov-meta">执行人 {{ run.owner || '-' }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 甘特图 -->
    <div v-else-if="view === 'gantt'" v-loading="loading" class="overview-gantt">
      <el-empty v-if="!gantt.rows.length" description="还没有带时间的轮次" :image-size="70" />
      <template v-else>
        <div class="gantt-axis">
          <span v-for="tick in gantt.ticks" :key="tick.label" class="gantt-tick" :style="{ left: `${tick.pct}%` }">{{ tick.label }}</span>
        </div>
        <div v-for="row in gantt.rows" :key="row.id" class="gantt-row">
          <span class="gantt-label" :title="row.name">{{ row.name }}</span>
          <div class="gantt-track">
            <div
              class="gantt-bar"
              :style="{ left: `${row.left}%`, width: `${row.width}%`, background: rateColor(row.passRate) }"
              :title="`${row.name} · ${row.range} · 通过率 ${row.passRate ?? '-'}%`"
              @click="openRun(row.run)"
            />
          </div>
        </div>
      </template>
    </div>

    <!-- 状态看板 -->
    <div v-else v-loading="loading" class="overview-kanban">
      <div class="kanban-picker">
        <span class="muted">轮次：</span>
        <el-select v-model="kanbanRunId" filterable placeholder="选择轮次" style="width: 260px" @change="loadKanbanCases">
          <el-option v-for="run in runs" :key="run.id" :label="run.name" :value="run.id" />
        </el-select>
      </div>
      <el-empty v-if="!kanbanRunId" description="先选择一个轮次，再拖拽用例卡片更新结果" :image-size="70" />
      <div v-else class="kanban-board">
        <div
          v-for="col in kanbanColumns"
          :key="col.value"
          class="kanban-col"
          @dragover.prevent
          @drop="onDrop(col.value)"
        >
          <div class="kanban-col__head" :style="{ borderColor: col.color }">
            <span>{{ col.label }}</span>
            <el-tag size="small" effect="plain">{{ kanbanCases.filter((c) => c.result === col.value).length }}</el-tag>
          </div>
          <div
            v-for="caseItem in kanbanCases.filter((c) => c.result === col.value)"
            :key="caseItem.id"
            class="kanban-card"
            draggable="true"
            @dragstart="dragCase = caseItem"
            @dragend="dragCase = null"
          >
            <span class="kanban-card__no">{{ caseItem.caseNo }}</span>
            <span class="kanban-card__title">{{ caseItem.title }}</span>
            <el-tag size="small" :type="priorityType(caseItem.priority)" effect="plain">{{ caseItem.priority }}</el-tag>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Refresh } from '@element-plus/icons-vue'
import { bugApi, projectVersionApi, requirementApi, testPlanApi, testRunApi } from '@/api'
import type { ProjectVersion, Requirement, TestPlan, TestRun, TestRunCase } from '@/api/types'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
const router = useRouter()

const view = ref<'tree' | 'gantt' | 'kanban'>('tree')
const loading = ref(false)
const versions = ref<ProjectVersion[]>([])
const plans = ref<TestPlan[]>([])
const runs = ref<TestRun[]>([])
const requirements = ref<Requirement[]>([])
const openBugs = ref(0)
const opened = ref<Set<string>>(new Set())

const kanbanRunId = ref<number | null>(null)
const kanbanCases = ref<TestRunCase[]>([])
const dragCase = ref<TestRunCase | null>(null)

const kanbanColumns = [
  { value: 'not_executed', label: '未执行', color: 'var(--el-color-info)' },
  { value: 'passed', label: '通过', color: 'var(--el-color-success)' },
  { value: 'failed', label: '未通过', color: 'var(--el-color-danger)' },
  { value: 'blocked', label: '阻塞', color: 'var(--el-color-warning)' },
  { value: 'skipped', label: '跳过', color: 'var(--aitp-text-weak)' },
]

function runPassRate(run: TestRun): number | null {
  const executed = run.total - run.notExecuted
  if (!executed) return null
  return Math.round((run.passed / executed) * 100)
}

function rateColor(rate: number | null): string {
  if (rate == null) return 'var(--aitp-text-weak)'
  if (rate >= 90) return 'var(--el-color-success)'
  if (rate >= 60) return 'var(--el-color-warning)'
  return 'var(--el-color-danger)'
}

function priorityType(p: string): 'danger' | 'warning' | 'info' | 'success' {
  if (p === 'P0') return 'danger'
  if (p === 'P1') return 'warning'
  if (p === 'P2') return 'info'
  return 'success'
}

const ROUND_LABELS: Record<string, string> = {
  smoke: '冒烟',
  full: '全覆盖',
  special: '专项',
  regression: '回归',
}

function roundLabel(value: string): string {
  return ROUND_LABELS[value] ?? value
}

function gateLabel(run: TestRun): string {
  if (run.status === 'pending') return '待准入'
  if (run.status === 'running') return '执行中'
  if (run.status === 'aborted') return '已中止'
  return run.failed > 0 ? '准出未过' : '准出通过'
}

function gateType(run: TestRun): 'success' | 'warning' | 'info' | 'danger' {
  if (run.status === 'pending') return 'info'
  if (run.status === 'running') return 'warning'
  if (run.status === 'aborted') return 'danger'
  return run.failed > 0 ? 'danger' : 'success'
}

function durationText(ms: number): string {
  if (!ms) return '-'
  const minutes = Math.round(ms / 60000)
  if (minutes < 60) return `${minutes} 分钟`
  return `${(minutes / 60).toFixed(1)} 小时`
}

function runDurationMs(run: TestRun): number {
  if (!run.startedAt || !run.finishedAt) return 0
  const a = Date.parse(run.startedAt)
  const b = Date.parse(run.finishedAt)
  return Number.isFinite(a) && Number.isFinite(b) && b > a ? b - a : 0
}

const metrics = computed(() => {
  const totalCases = runs.value.reduce((sum, r) => sum + r.total, 0)
  const passed = runs.value.reduce((sum, r) => sum + r.passed, 0)
  const executed = runs.value.reduce((sum, r) => sum + (r.total - r.notExecuted), 0)
  const totalDurationMs = runs.value.reduce((sum, r) => sum + runDurationMs(r), 0)
  return {
    totalCases,
    passRate: executed ? Math.round((passed / executed) * 100) : null,
    openBugs: openBugs.value,
    totalDurationMs,
  }
})

interface PlanNode {
  key: string
  name: string
  range: string
  totalCases: number
  failed: number
  passRate: number | null
  runs: TestRun[]
}
interface VersionNode {
  key: string
  versionCode: string
  name: string
  reqCount: number
  runCount: number
  passRate: number | null
  plans: PlanNode[]
  orphanRuns: TestRun[]
}

function aggregate(rs: TestRun[]): number | null {
  const executed = rs.reduce((s, r) => s + (r.total - r.notExecuted), 0)
  const passed = rs.reduce((s, r) => s + r.passed, 0)
  return executed ? Math.round((passed / executed) * 100) : null
}

function rangeOf(rs: TestRun[]): string {
  const starts = rs.map((r) => r.startedAt || r.createdAt).filter(Boolean) as string[]
  const ends = rs.map((r) => r.finishedAt || r.updatedAt).filter(Boolean) as string[]
  if (!starts.length) return '时间未定'
  const fmt = (s: string) => s.slice(0, 10)
  return `${fmt(starts.sort()[0])} ~ ${fmt((ends.sort().reverse()[0]) || starts.sort()[0])}`
}

const tree = computed<VersionNode[]>(() => {
  const nodes: VersionNode[] = versions.value.map((v) => ({
    key: `v-${v.id}`,
    versionCode: v.versionCode,
    name: v.name || v.versionCode,
    reqCount: requirements.value.filter((r) => Number(r.versionId) === Number(v.id)).length,
    runCount: 0,
    passRate: null,
    plans: [],
    orphanRuns: [],
  }))
  const byId = new Map(nodes.map((n) => [n.key, n]))
  const unassigned: VersionNode = {
    key: 'v-none', versionCode: '', name: '未指定版本', reqCount: requirements.value.filter((r) => r.versionId == null).length,
    runCount: 0, passRate: null, plans: [], orphanRuns: [],
  }
  const planNodes: PlanNode[] = plans.value.map((p) => {
    const rs = runs.value.filter((r) => Number(r.planId) === Number(p.id))
    return {
      key: `p-${p.id}`,
      name: p.name,
      range: rangeOf(rs),
      totalCases: rs.reduce((s, r) => s + r.total, 0),
      failed: rs.reduce((s, r) => s + r.failed, 0),
      passRate: aggregate(rs),
      runs: rs,
    }
  })
  for (const pn of planNodes) {
    const plan = plans.value.find((p) => `p-${p.id}` === pn.key)
    const owner = plan?.versionId != null ? byId.get(`v-${plan.versionId}`) : unassigned
    ;(owner ?? unassigned).plans.push(pn)
  }
  // 没挂计划的轮次归到对应版本的 orphanRuns
  for (const r of runs.value) {
    if (r.planId != null && plans.value.some((p) => Number(p.id) === Number(r.planId))) continue
    const owner = r.versionId != null ? byId.get(`v-${r.versionId}`) : unassigned
    ;(owner ?? unassigned).orphanRuns.push(r)
  }
  const all = [...nodes, unassigned]
  for (const n of all) {
    const rs = [...n.plans.flatMap((p) => p.runs), ...n.orphanRuns]
    n.runCount = rs.length
    n.passRate = aggregate(rs)
  }
  return all.filter((n) => n.plans.length || n.orphanRuns.length || n.reqCount)
})

const gantt = computed(() => {
  const rowsRaw = runs.value
    .map((r) => ({
      run: r,
      start: Date.parse(r.plannedStart || r.startedAt || r.createdAt || ''),
      end: Date.parse(r.plannedEnd || r.finishedAt || r.updatedAt || ''),
    }))
    .filter((x) => Number.isFinite(x.start))
    .map((x) => ({ ...x, end: Number.isFinite(x.end) && x.end > x.start ? x.end : x.start + 86400000 }))
  if (!rowsRaw.length) return { rows: [], ticks: [] }
  const min = Math.min(...rowsRaw.map((x) => x.start))
  const max = Math.max(...rowsRaw.map((x) => x.end))
  const span = Math.max(max - min, 86400000)
  const rows = rowsRaw.map((x) => ({
    id: x.run.id,
    name: x.run.name,
    run: x.run,
    range: `${new Date(x.start).toLocaleDateString()} ~ ${new Date(x.end).toLocaleDateString()}`,
    passRate: runPassRate(x.run),
    left: ((x.start - min) / span) * 100,
    width: Math.max(((x.end - x.start) / span) * 100, 2),
  }))
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    pct: f * 100,
    label: new Date(min + span * f).toLocaleDateString(),
  }))
  return { rows, ticks }
})

function toggle(key: string): void {
  const next = new Set(opened.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  opened.value = next
}

function openRun(run: TestRun): void {
  void router.push({ name: 'test-run-detail', params: { id: run.id } })
}

async function loadKanbanCases(): Promise<void> {
  if (!kanbanRunId.value) {
    kanbanCases.value = []
    return
  }
  try {
    const run = await testRunApi.get(kanbanRunId.value)
    kanbanCases.value = run.cases ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载轮次用例失败')
  }
}

async function onDrop(target: string): Promise<void> {
  const item = dragCase.value
  dragCase.value = null
  if (!item || !kanbanRunId.value || item.result === target) return
  try {
    await testRunApi.recordCase(kanbanRunId.value, item.caseId, { result: target })
    ElMessage.success(`已将 ${item.caseNo} 标记为${kanbanColumns.find((c) => c.value === target)?.label}`)
    await loadKanbanCases()
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '更新用例结果失败')
  }
}

async function load(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) return
  loading.value = true
  try {
    const [v, p, r, req, bugs] = await Promise.all([
      projectVersionApi.list(projectId),
      testPlanApi.list({ projectId, pageNum: 1, pageSize: 200 }),
      testRunApi.list({ projectId, pageNum: 1, pageSize: 200 }),
      requirementApi.list({ projectId, pageNum: 1, pageSize: 500 }),
      bugApi.list({ projectId, pageNum: 1, pageSize: 500 }),
    ])
    versions.value = v.list ?? []
    plans.value = p.list ?? []
    runs.value = r.list ?? []
    requirements.value = req.list ?? []
    openBugs.value = (bugs.list ?? []).filter((b) => !['closed', 'rejected'].includes(b.status)).length
    if (!kanbanRunId.value && runs.value.length) {
      kanbanRunId.value = runs.value[0].id
      await loadKanbanCases()
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载总览失败')
  } finally {
    loading.value = false
  }
}

watch(() => appStore.currentProjectId, () => {
  kanbanRunId.value = null
  kanbanCases.value = []
  void load()
})

onMounted(load)

defineExpose({ reload: load })
</script>

<style scoped>
.overview-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.ov-metric {
  border: 1px solid var(--aitp-border);
  border-radius: 10px;
  padding: 12px 16px;
  background: var(--aitp-panel);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ov-metric__label { font-size: 12px; color: var(--aitp-text-weak); }
.ov-metric__value { font-size: 24px; font-weight: 700; }
.ov-metric__value--danger { color: var(--el-color-danger); }

.overview-toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.overview-hint { font-size: 12px; }

.ov-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px; border-radius: 8px; cursor: pointer;
}
.ov-row:hover { background: var(--aitp-hover); }
.ov-caret { transition: transform .15s; color: var(--aitp-text-weak); }
.ov-caret.is-open { transform: rotate(90deg); }
.ov-name { font-weight: 600; }
.ov-meta { font-size: 12px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ov-rate { font-size: 13px; font-weight: 600; width: 48px; text-align: right; }
.ov-progress { width: 90px; height: 6px; border-radius: 3px; background: var(--aitp-subtle); overflow: hidden; }
.ov-progress i { display: block; height: 100%; border-radius: 3px; }
.ov-children { margin-left: 22px; border-left: 1px solid var(--aitp-border); padding-left: 8px; }
.ov-row--run { cursor: pointer; }
.ov-run-name { font-size: 13px; min-width: 140px; }

.gantt-axis { position: relative; height: 22px; margin-left: 160px; border-bottom: 1px solid var(--aitp-border); }
.gantt-tick { position: absolute; transform: translateX(-50%); font-size: 11px; color: var(--aitp-text-weak); }
.gantt-row { display: flex; align-items: center; gap: 8px; padding: 5px 0; }
.gantt-label { width: 152px; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gantt-track { position: relative; flex: 1; height: 18px; background: var(--aitp-subtle); border-radius: 4px; }
.gantt-bar { position: absolute; top: 2px; bottom: 2px; border-radius: 4px; cursor: pointer; min-width: 6px; }

.kanban-picker { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.kanban-board { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
.kanban-col { border: 1px solid var(--aitp-border); border-radius: 10px; padding: 8px; min-height: 220px; background: var(--aitp-panel); }
.kanban-col__head { display: flex; justify-content: space-between; align-items: center; font-weight: 600; font-size: 13px; border-bottom: 2px solid; padding-bottom: 6px; margin-bottom: 8px; }
.kanban-card { border: 1px solid var(--aitp-border); border-radius: 8px; padding: 8px; margin-bottom: 6px; cursor: grab; background: var(--aitp-bg); display: flex; flex-direction: column; gap: 4px; }
.kanban-card:active { cursor: grabbing; }
.kanban-card__no { font-size: 11px; color: var(--aitp-text-weak); }
.kanban-card__title { font-size: 12px; }

@media (max-width: 900px) {
  .kanban-board { grid-template-columns: repeat(2, 1fr); }
}
</style>