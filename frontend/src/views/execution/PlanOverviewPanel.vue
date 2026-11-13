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
        {{ view === 'tree' ? '版本 → 计划 → 轮次，点行展开详情' : view === 'gantt' ? '按版本分组各自时间轴 · 刻度自适应长跨度/跨年份 · 点箭头收起/展开' : '选一个轮次，拖拽用例卡片更新结果' }}
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

    <!-- 甘特图：按版本分组（各自时间轴）或全局轴；刻度自适应长跨度与跨年份 -->
    <div v-else-if="view === 'gantt'" v-loading="loading" class="overview-gantt">
      <div class="gantt-controls">
        <el-radio-group v-model="ganttMode" size="small">
          <el-radio-button value="version">按版本</el-radio-button>
          <el-radio-button value="global">全局轴</el-radio-button>
        </el-radio-group>
        <el-select
          v-model="ganttVersionFilter"
          multiple
          clearable
          collapse-tags
          collapse-tags-tooltip
          placeholder="全部版本"
          class="gantt-version-select"
        >
          <el-option v-for="opt in versionOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>
        <span class="muted gantt-controls__label">刻度</span>
        <el-select v-model="ganttScale" size="small" class="gantt-scale-select">
          <el-option label="自动" value="auto" />
          <el-option label="日" value="day" />
          <el-option label="周" value="week" />
          <el-option label="月" value="month" />
          <el-option label="季" value="quarter" />
          <el-option label="年" value="year" />
        </el-select>
        <el-button size="small" :disabled="!ganttSections.length" @click="toggleAllVersions">
          {{ allVersionsCollapsed ? '全部展开' : '全部收起' }}
        </el-button>
      </div>
      <el-empty v-if="!ganttSections.length" description="还没有带时间的轮次" :image-size="70" />
      <div v-for="sec in ganttSections" :key="sec.key" class="gantt-section">
        <div class="gantt-section__head" @click="toggleVersion(sec.key)">
          <el-icon class="ov-caret" :class="{ 'is-open': !collapsedVersions.has(sec.key) }"><ArrowRight /></el-icon>
          <el-tag size="small" effect="dark" type="primary">{{ sec.tag }}</el-tag>
          <span class="gantt-section__name">{{ sec.name }}</span>
          <span class="muted gantt-section__meta">{{ sec.meta }}</span>
        </div>
        <template v-if="!collapsedVersions.has(sec.key)">
          <div class="gantt-axis">
            <span
              v-for="tick in sec.ticks"
              :key="`${tick.ms}-${tick.label}`"
              class="gantt-tick"
              :class="{ ...tickClass(tick.pct), 'gantt-tick--year': tick.year }"
              :style="{ left: `${tick.pct}%` }"
            >{{ tick.label }}</span>
          </div>
          <div v-for="row in sec.rows" :key="row.id" class="gantt-group">
            <div class="gantt-info">
              <button
                type="button"
                class="gantt-toggle"
                :title="ganttHidden.has(row.id) ? '显示时间甘特' : '隐藏时间甘特'"
                :aria-label="ganttHidden.has(row.id) ? '显示时间甘特' : '隐藏时间甘特'"
                @click="toggleGantt(row.id)"
              >
                <el-icon>
                  <ArrowRight v-if="ganttHidden.has(row.id)" />
                  <ArrowDown v-else />
                </el-icon>
              </button>
              <span class="gantt-info__name" :title="row.name" @click="openRun(row.run)">{{ row.name }}</span>
              <el-tag v-if="row.run.roundType" size="small" effect="plain" type="info">{{ roundLabel(row.run.roundType) }}</el-tag>
              <el-tag size="small" :type="gateType(row.run)" effect="plain">{{ gateLabel(row.run) }}</el-tag>
              <span class="muted gantt-info__meta">
                {{ row.range }} · 执行人 {{ row.run.owner || '-' }} · 通过 {{ row.run.passed }}/{{ row.run.total }}
              </span>
              <span class="gantt-info__rate" :style="{ color: rateColor(row.passRate) }">
                {{ row.passRate == null ? '-' : `${row.passRate}%` }}
              </span>
            </div>
            <div v-show="!ganttHidden.has(row.id)" class="gantt-track">
              <i
                v-for="ym in sec.yearMarks"
                :key="ym.ms"
                class="gantt-year"
                :style="{ left: `${ym.pct}%` }"
                :title="`${ym.label} 年`"
              />
              <div
                class="gantt-bar"
                :style="{ left: `${row.left}%`, width: `max(${row.width}%, 6px)`, background: rateColor(row.passRate) }"
                :title="`${row.name} · ${row.range} · 通过率 ${row.passRate ?? '-'}%`"
                @click="openRun(row.run)"
              />
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- 状态看板 -->
    <div v-else v-loading="loading" class="overview-kanban">
      <div class="kanban-picker">
        <span class="muted">轮次：</span>
        <el-select v-model="kanbanRunId" filterable placeholder="选择轮次" class="kanban-select" @change="loadKanbanCases">
          <el-option v-for="run in runs" :key="run.id" :label="runLabel(run)" :value="run.id" />
        </el-select>
      </div>
      <p v-if="kanbanRun" class="muted kanban-current">
        {{ kanbanRun.name }}<template v-if="kanbanRun.roundType"> · {{ roundLabel(kanbanRun.roundType) }}</template>
        · {{ kanbanRun.plannedStart ? `${kanbanRun.plannedStart} ~ ${kanbanRun.plannedEnd}` : '时间未定' }}
        · 执行人 {{ kanbanRun.owner || '-' }} · 通过 {{ kanbanRun.passed }}/{{ kanbanRun.total }}
      </p>
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
import { ArrowDown, ArrowRight, Refresh } from '@element-plus/icons-vue'
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

const kanbanRun = computed(() => runs.value.find((r) => r.id === kanbanRunId.value) ?? null)

/** 甘特时间行隐藏集合；默认空 = 全部显示 */
const ganttHidden = ref<Set<number>>(new Set())

function toggleGantt(id: number): void {
  const next = new Set(ganttHidden.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  ganttHidden.value = next
}

function runLabel(run: TestRun): string {
  const parts = [run.name]
  if (run.roundType) parts.push(roundLabel(run.roundType))
  if (run.plannedStart) parts.push(`${run.plannedStart} ~ ${run.plannedEnd || run.plannedStart}`)
  return parts.join(' · ')
}

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

type GanttScale = 'auto' | 'day' | 'week' | 'month' | 'quarter' | 'year'
type GanttUnit = Exclude<GanttScale, 'auto'>
interface GanttTick {
  pct: number
  label: string
  ms: number
  year?: boolean
}
interface GanttRow {
  id: number
  name: string
  run: TestRun
  range: string
  passRate: number | null
  left: number
  width: number
}
interface GanttSection {
  key: string
  tag: string
  name: string
  meta: string
  start: number
  rows: GanttRow[]
  ticks: GanttTick[]
  yearMarks: GanttTick[]
}

const DAY_MS = 86400000
const NONE_VERSION = -1
const ganttMode = ref<'version' | 'global'>('version')
const ganttScale = ref<GanttScale>('auto')
const ganttVersionFilter = ref<number[]>([])
const collapsedVersions = ref<Set<string>>(new Set())

const versionOptions = computed(() => {
  const used = new Set(runs.value.map((r) => Number(r.versionId ?? NONE_VERSION)))
  const opts = versions.value
    .filter((v) => used.has(Number(v.id)))
    .map((v) => ({ value: Number(v.id), label: `${v.versionCode} ${v.name || ''}`.trim() }))
  if (used.has(NONE_VERSION)) opts.push({ value: NONE_VERSION, label: '未指定版本' })
  return opts
})

function localDate(ms: number): string {
  const d = new Date(ms)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 跨度决定粒度：短看日、中看周/月、长看季/年；手动刻度优先 */
function unitFor(span: number): GanttUnit {
  if (ganttScale.value !== 'auto') return ganttScale.value
  const days = span / DAY_MS
  if (days <= 16) return 'day'
  if (days <= 100) return 'week'
  if (days <= 400) return 'month'
  if (days <= 1200) return 'quarter'
  return 'year'
}

function floorUnit(ms: number, unit: GanttUnit): number {
  const d = new Date(ms)
  if (unit === 'day') return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  if (unit === 'week') return new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7)).getTime()
  if (unit === 'month') return new Date(d.getFullYear(), d.getMonth(), 1).getTime()
  if (unit === 'quarter') return new Date(d.getFullYear(), Math.floor(d.getMonth() / 3) * 3, 1).getTime()
  return new Date(d.getFullYear(), 0, 1).getTime()
}

function addUnit(ms: number, unit: GanttUnit, n: number): number {
  const d = new Date(ms)
  if (unit === 'day') return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n).getTime()
  if (unit === 'week') return new Date(d.getFullYear(), d.getMonth(), d.getDate() + 7 * n).getTime()
  if (unit === 'month') return new Date(d.getFullYear(), d.getMonth() + n, 1).getTime()
  if (unit === 'quarter') return new Date(d.getFullYear(), d.getMonth() + 3 * n, 1).getTime()
  return new Date(d.getFullYear() + n, 0, 1).getTime()
}

function fmtTick(ms: number, unit: GanttUnit, crossYear: boolean): string {
  const d = new Date(ms)
  const y = d.getFullYear()
  const m = d.getMonth() + 1
  if (unit === 'year') return `${y}年`
  if (unit === 'quarter') return `${y} Q${Math.floor(d.getMonth() / 3) + 1}`
  if (unit === 'month') return crossYear ? `${y}-${String(m).padStart(2, '0')}` : `${m}月`
  return crossYear ? `${y}/${m}/${d.getDate()}` : `${m}/${d.getDate()}`
}

function fmtBound(ms: number, crossYear: boolean): string {
  const d = new Date(ms)
  return crossYear ? `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}` : `${d.getMonth() + 1}/${d.getDate()}`
}

/** 自然边界刻度 + 首尾端点；跨年份补年界标记；密度上限防标签重叠 */
function buildTicks(min: number, max: number): { ticks: GanttTick[]; yearMarks: GanttTick[] } {
  const span = Math.max(max - min, DAY_MS)
  const crossYear = new Date(min).getFullYear() !== new Date(max).getFullYear()
  const unit = unitFor(span)
  const yearMarks: GanttTick[] = []
  if (crossYear) {
    for (let y = new Date(min).getFullYear() + 1; y <= new Date(max).getFullYear(); y++) {
      const t = new Date(y, 0, 1).getTime()
      if (t > min && t < max) yearMarks.push({ pct: ((t - min) / span) * 100, label: `${y}`, ms: t, year: true })
    }
  }
  const gen = (step: number): number[] => {
    const out: number[] = []
    let cur = floorUnit(min, unit)
    if (cur < min) cur = addUnit(cur, unit, 1)
    while (cur <= max && out.length < 200) {
      out.push(cur)
      cur = addUnit(cur, unit, step)
    }
    return out
  }
  let step = 1
  let marks = gen(step)
  while (marks.length > 9 && step < 128) {
    step *= 2
    marks = gen(step)
  }
  const yearMs = new Set(yearMarks.map((m) => m.ms))
  const minLabel = fmtBound(min, crossYear)
  const maxLabel = fmtBound(max, crossYear)
  const ticks: GanttTick[] = [{ pct: 0, label: minLabel, ms: min }]
  for (const t of marks) {
    const pct = ((t - min) / span) * 100
    if (pct < 3 || pct > 97 || yearMs.has(t)) continue
    const label = fmtTick(t, unit, crossYear)
    // 与首尾端点同日的自然刻度不再重复贴标签
    if ((label === minLabel && pct < 25) || (label === maxLabel && pct > 75)) continue
    const d = new Date(t)
    ticks.push({
      pct,
      label,
      ms: t,
      year: unit === 'year' || (d.getMonth() === 0 && unit !== 'day' && unit !== 'week'),
    })
  }
  for (const ym of yearMarks) ticks.push({ ...ym, label: `${ym.label}年` })
  ticks.push({ pct: 100, label: maxLabel, ms: max })
  ticks.sort((a, b) => a.pct - b.pct)
  return { ticks, yearMarks }
}

function runStart(r: TestRun): number {
  return Date.parse(r.plannedStart || r.startedAt || r.createdAt || '')
}

function runEnd(r: TestRun, start: number): number {
  const e = Date.parse(r.plannedEnd || r.finishedAt || r.updatedAt || '')
  return Number.isFinite(e) && e > start ? e : start + DAY_MS
}

/** 按版本分组时每组用自己的时间窗算轴，远距版本互不压缩；全局轴看整体节奏 */
const ganttSections = computed<GanttSection[]>(() => {
  const timed = runs.value
    .filter((r) => !ganttVersionFilter.value.length
      || ganttVersionFilter.value.includes(Number(r.versionId ?? NONE_VERSION)))
    .map((r) => ({ r, start: runStart(r) }))
    .filter((x) => Number.isFinite(x.start))
  if (!timed.length) return []
  const groups: Array<{ key: string; tag: string; name: string; items: typeof timed }> = []
  if (ganttMode.value === 'global') {
    groups.push({ key: 'all', tag: '全局', name: '全部轮次 · 全局时间轴', items: timed })
  } else {
    const byVer = new Map<number, typeof timed>()
    for (const x of timed) {
      const k = Number(x.r.versionId ?? NONE_VERSION)
      byVer.set(k, [...(byVer.get(k) ?? []), x])
    }
    for (const [k, items] of byVer) {
      const v = versions.value.find((item) => Number(item.id) === k)
      groups.push({
        key: `v-${k}`,
        tag: v?.versionCode || '未指定版本',
        name: v?.name || (k === NONE_VERSION ? '未指定版本的轮次' : `版本 ${k}`),
        items,
      })
    }
  }
  const sections = groups.map((g) => {
    const min = Math.min(...g.items.map((x) => x.start))
    const max = Math.max(...g.items.map((x) => runEnd(x.r, x.start)))
    const { ticks, yearMarks } = buildTicks(min, max)
    const span = Math.max(max - min, DAY_MS)
    const rs = g.items.map((x) => x.r)
    const rate = aggregate(rs)
    const rows: GanttRow[] = g.items.map((x) => {
      const end = runEnd(x.r, x.start)
      return {
        id: x.r.id,
        name: x.r.name,
        run: x.r,
        range: `${localDate(x.start)} ~ ${localDate(end)}`,
        passRate: runPassRate(x.r),
        left: ((x.start - min) / span) * 100,
        width: Math.max(((end - x.start) / span) * 100, 0.5),
      }
    })
    return {
      key: g.key,
      tag: g.tag,
      name: g.name,
      meta: `${localDate(min)} ~ ${localDate(max)} · 轮次 ${rs.length} · 通过率 ${rate == null ? '-' : `${rate}%`}`,
      start: min,
      rows,
      ticks,
      yearMarks,
    }
  })
  sections.sort((a, b) => a.start - b.start)
  return sections
})

const allVersionsCollapsed = computed(() =>
  ganttSections.value.length > 0 && ganttSections.value.every((s) => collapsedVersions.value.has(s.key)))

function toggleVersion(key: string): void {
  const next = new Set(collapsedVersions.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsedVersions.value = next
}

function toggleAllVersions(): void {
  collapsedVersions.value = allVersionsCollapsed.value
    ? new Set()
    : new Set(ganttSections.value.map((s) => s.key))
}

function tickClass(pct: number): Record<string, boolean> {
  return { 'gantt-tick--first': pct < 3, 'gantt-tick--last': pct > 97 }
}

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

.gantt-axis { position: relative; height: 22px; margin: 0 11px 10px; border-bottom: 1px solid var(--aitp-border); }
.gantt-tick { position: absolute; transform: translateX(-50%); font-size: 11px; color: var(--aitp-text-weak); white-space: nowrap; }
.gantt-tick--first { transform: none; }
.gantt-tick--last { transform: translateX(-100%); }
.gantt-group {
  display: flex; flex-direction: column; gap: 8px;
  border: 1px solid var(--aitp-border); border-radius: 10px;
  background: var(--aitp-panel); padding: 8px 10px; margin-bottom: 10px;
}
.gantt-group:hover { border-color: var(--el-color-primary-light-5); }
.gantt-info { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.gantt-toggle {
  display: inline-flex; align-items: center; justify-content: center;
  width: 20px; height: 20px; flex: none; padding: 0;
  border: none; border-radius: 4px; background: none;
  color: var(--aitp-text-weak); cursor: pointer;
}
.gantt-toggle:hover { background: var(--aitp-hover); color: var(--el-color-primary); }
.gantt-info__name { font-size: 13px; font-weight: 600; cursor: pointer; word-break: break-all; }
.gantt-info__name:hover { color: var(--el-color-primary); }
.gantt-info__meta { font-size: 12px; }
.gantt-info__rate { margin-left: auto; font-size: 13px; font-weight: 600; }
.gantt-track {
  position: relative; height: 18px; border-radius: 4px;
  background-color: var(--aitp-subtle);
  background-image: linear-gradient(to right, var(--aitp-border) 1px, transparent 1px);
  background-size: 25% 100%;
}
.gantt-bar { position: absolute; top: 2px; bottom: 2px; border-radius: 4px; cursor: pointer; min-width: 6px; }
.gantt-bar:hover { filter: brightness(1.12); }
.gantt-controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.gantt-controls__label { font-size: 12px; }
.gantt-version-select { width: 240px; max-width: 100%; }
.gantt-scale-select { width: 92px; }
.gantt-section { margin-bottom: 14px; }
.gantt-section__head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 4px 2px 8px; cursor: pointer; }
.gantt-section__name { font-size: 13px; font-weight: 600; }
.gantt-section__head:hover .gantt-section__name { color: var(--el-color-primary); }
.gantt-section__meta { font-size: 12px; }
.gantt-tick--year { color: var(--el-color-primary); font-weight: 700; }
.gantt-year { position: absolute; top: 0; bottom: 0; width: 0; border-left: 1px dashed var(--el-color-primary-light-5); pointer-events: none; }

.kanban-picker { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.kanban-select { width: 480px; max-width: 100%; }
.kanban-current { font-size: 12px; line-height: 1.6; margin: -4px 0 12px; }
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