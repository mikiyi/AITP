<template>
  <div class="page home">
    <div class="page-header">
      <ProjectSwitcher />
      <el-button :icon="Grid" style="margin: 0 0 0 auto" @click="router.push('/settings/home-cards')">
        卡片定制
      </el-button>
      <el-button :icon="Refresh" :loading="loading" @click="reload">
        刷新
      </el-button>
    </div>

    <el-alert
      v-if="errorMessage"
      class="home__alert"
      type="error"
      show-icon
      :closable="false"
      :title="errorMessage"
      description="请确认 AITP 后端已启动（默认 http://127.0.0.1:8760），然后点击「刷新」重试。"
    />

    <div class="panel panel-pad home__section">
      <div class="home__section-title">知识库问答助手</div>
      <KnowledgeAssistant compact />
    </div>

    <!-- 数据概览：资产卡 + 指标卡合成一个大边框，固定 6 列两行 -->
    <div class="panel panel-pad home__section">
      <div class="home__section-title">
        数据概览
        <el-select
          v-model="overviewVersionId"
          placeholder="全部版本"
          clearable
          size="small"
          style="margin-left: 10px; width: 180px"
          @change="reload"
        >
          <el-option v-for="item in overviewVersionOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </div>
      <div class="home__stats">
        <MetricCard
          v-if="assets.project !== undefined"
          label="项目"
          :value="assets.project"
          icon="Folder"
          tone="info"
        />
        <MetricCard
          label="需求"
          :value="assets.requirement"
          icon="Document"
          tone="info"
        />
        <MetricCard
          label="测试用例"
          :value="assets.case"
          icon="Tickets"
          tone="primary"
        />
        <MetricCard
          label="缺陷"
          :value="assets.bug"
          icon="Warning"
          tone="danger"
          :hint="`未关闭 ${metrics.bugOpen ?? 0}`"
        />
        <MetricCard
          label="知识资产"
          :value="assets.knowledge"
          icon="Notebook"
          tone="success"
          :hint="`待审核 ${pendingKnowledge}`"
        />
        <MetricCard
          label="测试报告"
          :value="assets.report"
          icon="DataAnalysis"
          tone="primary"
        />
        <MetricCard
          label="用例执行通过率"
          :value="(metrics.casePassRate ?? 0) * 100"
          :precision="2"
          suffix="%"
          icon="TrendCharts"
          tone="success"
        />
        <MetricCard
          label="用例总数"
          :value="metrics.caseTotal ?? 0"
          icon="Tickets"
          tone="primary"
        />
        <MetricCard
          label="未关闭缺陷"
          :value="metrics.bugOpen ?? 0"
          icon="CircleClose"
          tone="danger"
        />
        <MetricCard
          label="AI 任务成功率"
          :value="(metrics.aiTaskSuccessRate ?? 0) * 100"
          :precision="2"
          suffix="%"
          icon="MagicStick"
          tone="primary"
          :hint="`任务总数 ${metrics.aiTaskTotal ?? 0}`"
        />
        <MetricCard
          label="模块节点"
          :value="metrics.moduleTotal ?? 0"
          icon="Share"
          tone="info"
        />
        <MetricCard
          label="待审核产出"
          :value="pendingKnowledge"
          icon="Clock"
          tone="warning"
          hint="审核通过后才计入知识资产"
        />
      </div>
    </div>

    <!-- 快捷入口 -->
    <div class="panel panel-pad home__section">
      <div class="home__section-title">
        功能快捷入口
        <span class="home__section-hint">只展示常用的 10 个入口，不足时按系统原顺序补位</span>
      </div>
      <QuickEntryGrid :entries="shortcuts" />
    </div>

    <!-- 可定制卡片区：显示与顺序在「设置 → 首页卡片定制」里调整 -->
    <div class="home__cards">
      <div
        v-show="cardVisible('recentTasks')"
        class="panel panel-pad home__section home__card"
        :style="cardStyle('recentTasks')"
      >
        <div class="home__section-title">
          最近任务
          <span class="home__section-hint">最近 5 条 AI 任务</span>
        </div>
        <el-table :data="recentTasks" size="small" empty-text="暂无 AI 任务">
          <el-table-column prop="capability" label="能力" min-width="300" class-name="home__task-cap-col">
            <template #default="scope">
              <span class="home__task-cap">{{ scope.row.sessionTitle || scope.row.capability || scope.row.taskType || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="100">
            <template #default="scope">
              <el-tag :type="statusTag(scope.row.status)" size="small" effect="plain">
                {{ scope.row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="180">
            <template #default="{ row }">{{ dateTimeText(row.createdAt) }}</template>
          </el-table-column>
        </el-table>
      </div>

      <div
        v-show="cardVisible('caseStatus')"
        class="panel panel-pad home__section home__card home__chart-panel"
        :style="cardStyle('caseStatus')"
      >
        <div class="home__section-title">用例执行状态</div>
        <div ref="caseChartEl" class="home__chart" />
      </div>

      <div
        v-show="cardVisible('bugStatus')"
        class="panel panel-pad home__section home__card home__chart-panel"
        :style="cardStyle('bugStatus')"
      >
        <div class="home__section-title">缺陷状态分布</div>
        <div ref="bugChartEl" class="home__chart" />
      </div>

      <div
        v-show="cardVisible('projectProgress')"
        class="panel panel-pad home__section home__card"
        :style="cardStyle('projectProgress')"
      >
        <div class="home__section-title">
          项目进度
          <span class="home__section-hint">跟随当前项目与版本筛选</span>
        </div>
        <div class="home__progress">
          <div v-for="bar in progressBars" :key="bar.label" class="home__progress-item">
            <div class="home__progress-head">
              <span>{{ bar.label }}</span>
              <strong>{{ bar.text }}</strong>
            </div>
            <el-progress :percentage="bar.percent" :stroke-width="8" :show-text="false" />
          </div>
        </div>
      </div>

      <div
        v-show="cardVisible('moduleTree')"
        class="panel panel-pad home__section home__card"
        :style="cardStyle('moduleTree')"
      >
        <div class="home__section-title">
          模块结构
          <span class="home__section-hint">点击节点直达模块管理</span>
        </div>
        <ModuleTree :nodes="moduleTree" :loading="moduleLoading" @select="onModuleSelect" />
      </div>

      <div
        v-show="cardVisible('aiUsage')"
        class="panel panel-pad home__section home__card home__chart-panel"
        :style="cardStyle('aiUsage')"
      >
        <div class="home__section-title">
          AI 能力使用
          <span class="home__section-hint">任务量 Top 8，全项目统计</span>
        </div>
        <div ref="aiUsageChartEl" class="home__chart home__chart--short" />
      </div>

      <div
        v-show="cardVisible('bugSeverity')"
        class="panel panel-pad home__section home__card home__chart-panel"
        :style="cardStyle('bugSeverity')"
      >
        <div class="home__section-title">
          缺陷严重级别
          <span class="home__section-hint">P0 最严重</span>
        </div>
        <div ref="bugSeverityChartEl" class="home__chart home__chart--short" />
      </div>

      <div
        v-show="cardVisible('recentReports')"
        class="panel panel-pad home__section home__card"
        :style="cardStyle('recentReports')"
      >
        <div class="home__section-title">
          最近测试报告
          <span class="home__section-hint">最新 5 份</span>
        </div>
        <el-table :data="recentReports" size="small" empty-text="还没有测试报告">
          <el-table-column label="报告" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ row.title || row.reportNo || '-' }}</template>
          </el-table-column>
          <el-table-column label="类型" width="90">
            <template #default="{ row }">{{ reportTypeText(row.reportType) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="reportStatusTag(row.status)">
                {{ reportStatusText(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="150">
            <template #default="{ row }">{{ dateTimeText(row.createdAt) }}</template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 测试环境健康监测：消费测试环境配置，本机探测 + CPU/内存/磁盘动态曲线 -->
      <div
        v-show="cardVisible('envHealth')"
        class="panel panel-pad home__section home__card"
        :style="cardStyle('envHealth')"
      >
        <div class="home__section-title">
          测试环境健康监测
          <span class="home__section-hint">服务运行期间每 15 秒采样，离开页面也继续记录；曲线只存本机内存</span>
        </div>
        <div class="home__env-toolbar">
          <el-switch v-model="envAutoRefresh" size="small" inline-prompt active-text="自动刷新" inactive-text="自动刷新" />
          <el-button
            v-if="envCards.length"
            size="small"
            type="primary"
            plain
            :loading="envProbingAll"
            @click="probeAll()"
          >
            全部探测
          </el-button>
          <el-button size="small" plain @click="goToEnvSettings">
            环境配置
          </el-button>
        </div>

        <div v-if="envCards.length" v-loading="envLoading" class="home__env-list">
          <div v-for="card in envCards" :key="card.id" class="home__env-card">
            <div class="home__env-card-head">
              <div class="home__env-name">
                {{ card.name }}
                <el-tag v-if="card.isDefault" size="small" type="success" effect="plain">默认</el-tag>
                <el-tag size="small" type="info" effect="plain">{{ card.kindLabel || '测试' }}</el-tag>
              </div>
              <div class="home__env-card-meta">
                <el-tag
                  v-if="card.overall"
                  :type="card.overall === 'healthy' ? 'success' : 'danger'"
                  size="small"
                  effect="dark"
                >
                  {{ card.overall === 'healthy' ? '健康' : '异常' }}
                </el-tag>
                <el-tag v-else size="small" type="info" effect="plain">待探测</el-tag>
                <span v-if="card.probedAt" class="muted home__env-time">{{ card.probedAt }}</span>
                <el-button link type="primary" size="small" :loading="card.probing" @click="probeById(card.id)">
                  立即探测
                </el-button>
              </div>
            </div>

            <div class="home__env-servers">
              <div v-for="server in card.servers" :key="server.side" class="home__env-server">
                <div class="home__env-server-head">
                  <span class="home__env-server-name">{{ server.title }}</span>
                  <el-tag
                    v-if="server.ok !== null"
                    :type="server.ok ? 'success' : 'danger'"
                    size="small"
                    effect="plain"
                  >
                    {{ server.ok ? '可达' : '不可达' }}
                  </el-tag>
                  <span v-if="server.latencyMs !== null" class="home__env-latency">{{ server.latencyMs }}ms</span>
                </div>
                <div class="home__env-target">{{ server.target }}</div>

                <div v-if="server.metrics" class="home__metrics">
                  <div class="home__metric">
                    <div class="home__metric-head">
                      <span class="home__metric-name">CPU</span>
                      <span class="home__metric-value">
                        <strong>{{ percentLabel(server.metrics.cpuPercent) }}</strong>
                      </span>
                    </div>
                    <MetricSparkline
                      :points="server.cpuPoints"
                      :timestamps="server.cpuTimes"
                      :window-ms="METRICS_WINDOW_MS"
                      :height="60"
                      color="var(--el-color-primary)"
                    />
                  </div>
                  <div class="home__metric">
                    <div class="home__metric-head">
                      <span class="home__metric-name">内存</span>
                      <span class="home__metric-value">
                        <strong>{{ percentLabel(server.metrics.memory.percent) }}</strong>
                        <span v-if="server.memText" class="home__metric-sub">{{ server.memText }}</span>
                      </span>
                    </div>
                    <MetricSparkline
                      :points="server.memPoints"
                      :timestamps="server.memTimes"
                      :window-ms="METRICS_WINDOW_MS"
                      :height="60"
                      color="var(--el-color-warning)"
                    />
                  </div>
                  <div v-for="disk in server.disks" :key="disk.name" class="home__metric">
                    <div class="home__metric-head">
                      <span class="home__metric-name">磁盘 {{ disk.name }}</span>
                      <span class="home__metric-value">
                        <strong>{{ percentLabel(disk.percent) }}</strong>
                        <span v-if="disk.text" class="home__metric-sub">{{ disk.text }}</span>
                      </span>
                    </div>
                    <MetricSparkline
                      :points="disk.points"
                      :timestamps="disk.times"
                      :window-ms="METRICS_WINDOW_MS"
                      :height="60"
                      color="var(--el-color-success)"
                    />
                  </div>
                  <div class="home__metrics-axis">
                    <span>60 分钟前</span>
                    <span class="home__metrics-window">近 1 小时</span>
                    <span>现在</span>
                  </div>
                </div>
                <div v-else class="home__metrics-empty">
                  {{ server.metricsMessage }}
                  <el-button link type="primary" size="small" @click="goToEnvSettings">配置监控地址</el-button>
                </div>
              </div>
            </div>
          </div>
        </div>
        <el-empty
          v-else
          description="还没有配置测试环境"
          :image-size="80"
        >
          <el-button type="primary" size="small" @click="goToEnvSettings">去配置测试环境</el-button>
        </el-empty>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Grid, Refresh } from '@element-plus/icons-vue'
import type { EChartsOption } from 'echarts'
import { dateTimeText } from '@/utils/format'
import MetricCard from '@/components/MetricCard.vue'
import MetricSparkline from '@/components/MetricSparkline.vue'
import ModuleTree from '@/components/ModuleTree.vue'
import QuickEntryGrid from '@/components/QuickEntryGrid.vue'
import KnowledgeAssistant from '@/components/KnowledgeAssistant.vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { moduleApi, projectVersionApi, settingsApi, testEnvApi, workbenchApi } from '@/api'
import type {
  EnvHealthHistoryResult,
  EnvHealthSide,
  EnvMetricSeries,
  HomeCardItem,
  ModuleNode,
  QuickEntry,
  ServerMetrics,
  TestEnv,
  TestEnvProbe,
  WorkbenchOverview,
} from '@/api/types'
import { useEchart } from '@/composables/useEchart'
import { useChartTheme } from '@/composables/useChartTheme'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const appStore = useAppStore()
const { panel, text, weak, primary, border } = useChartTheme()

const loading = ref(false)
const errorMessage = ref('')
const overview = ref<WorkbenchOverview | null>(null)
const moduleTree = ref<ModuleNode[]>([])
const moduleLoading = ref(false)

const caseChartEl = ref<HTMLElement | null>(null)
const bugChartEl = ref<HTMLElement | null>(null)
const aiUsageChartEl = ref<HTMLElement | null>(null)
const bugSeverityChartEl = ref<HTMLElement | null>(null)

const metrics = computed(() => overview.value?.metrics ?? ({} as WorkbenchOverview['metrics']))
const assets = computed(() => overview.value?.assets ?? ({} as WorkbenchOverview['assets']))
const shortcuts = computed<QuickEntry[]>(() => overview.value?.shortcuts ?? [])
const recentTasks = computed(() => overview.value?.recentTasks ?? [])
const pendingKnowledge = computed(() => overview.value?.pendingKnowledge ?? 0)

// ---- 首页卡片定制：显示与顺序都来自「设置 → 首页卡片定制」----
/** 兜底排布：配置接口没回来时首页仍按默认布局渲染，不会出现空白页 */
const FALLBACK_CARDS: HomeCardItem[] = [
  { key: 'recentTasks', label: '最近任务', description: '', width: 'full', locked: false, visible: true, order: 0 },
  { key: 'caseStatus', label: '用例执行状态', description: '', width: 'half', locked: false, visible: true, order: 1 },
  { key: 'bugStatus', label: '缺陷状态分布', description: '', width: 'half', locked: false, visible: true, order: 2 },
  { key: 'projectProgress', label: '项目进度', description: '', width: 'half', locked: false, visible: true, order: 3 },
  { key: 'moduleTree', label: '模块结构', description: '', width: 'half', locked: false, visible: true, order: 4 },
  { key: 'aiUsage', label: 'AI 能力使用', description: '', width: 'half', locked: false, visible: false, order: 5 },
  { key: 'bugSeverity', label: '缺陷严重级别', description: '', width: 'half', locked: false, visible: false, order: 6 },
  { key: 'recentReports', label: '最近测试报告', description: '', width: 'half', locked: false, visible: false, order: 7 },
  { key: 'envHealth', label: '测试环境健康监测', description: '', width: 'full', locked: false, visible: true, order: 8 },
]

const cardLayout = ref<HomeCardItem[]>(FALLBACK_CARDS)

function cardOf(key: string): HomeCardItem | undefined {
  return cardLayout.value.find((item) => item.key === key)
}

function cardVisible(key: string): boolean {
  return cardOf(key)?.visible ?? false
}

function cardStyle(key: string): Record<string, string> {
  const card = cardOf(key)
  const style: Record<string, string> = { order: String(card?.order ?? 99) }
  if (card?.width === 'full') style.flex = '1 1 100%'
  return style
}

async function loadCardLayout(): Promise<void> {
  try {
    const result = await settingsApi.homeCards()
    cardLayout.value = result.list.filter((item) => !item.locked)
  } catch {
    cardLayout.value = FALLBACK_CARDS
  }
}

/** 隐藏的卡片宽高为 0，重新显示后要补一次 resize，否则图表空白 */
watch(
  () => cardLayout.value.map((item) => `${item.key}:${item.visible}`).join('|'),
  async () => {
    await nextTick()
    charts.forEach((chart) => chart.resize())
  },
)

// ---- 项目进度 ----
interface ProgressBar {
  label: string
  percent: number
  text: string
}

function ratioBar(label: string, done: number, total: number): ProgressBar {
  const percent = total > 0 ? Math.round((done / total) * 10000) / 100 : 0
  return { label, percent, text: total > 0 ? `${percent.toFixed(2)}% · ${done}/${total}` : '暂无数据' }
}

const progressBars = computed<ProgressBar[]>(() => {
  const caseStatus = overview.value?.caseStatus ?? {}
  const bugStatus = overview.value?.bugStatus ?? {}
  const requirementStatus = overview.value?.requirementStatus ?? {}
  const caseTotal = metrics.value.caseTotal ?? 0
  const bugTotal = metrics.value.bugTotal ?? 0
  const requirementTotal = metrics.value.requirementTotal ?? 0
  const executed = caseTotal - (caseStatus.not_executed ?? 0)
  const confirmed = (requirementStatus.approved ?? 0) + (requirementStatus.implemented ?? 0)
  const bugClosed = (bugStatus.closed ?? 0) + (bugStatus.rejected ?? 0)
  return [
    ratioBar('需求确认', confirmed, requirementTotal),
    ratioBar('用例执行', executed, caseTotal),
    ratioBar('用例通过', caseStatus.pass ?? 0, caseTotal),
    ratioBar('缺陷关闭', bugClosed, bugTotal),
  ]
})

const recentReports = computed(() => overview.value?.recentReports ?? [])

const REPORT_TYPE_LABEL: Record<string, string> = {
  summary: '汇总报告',
  iteration: '迭代报告',
  regression: '回归报告',
  acceptance: '验收报告',
}
const REPORT_STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  archived: '已归档',
}

function reportTypeText(value: string): string {
  return REPORT_TYPE_LABEL[value] ?? value
}

function reportStatusText(value: string): string {
  return REPORT_STATUS_LABEL[value] ?? value
}

function reportStatusTag(value: string): 'success' | 'warning' | 'info' {
  if (value === 'published') {
    return 'success'
  }
  if (value === 'archived') {
    return 'info'
  }
  return 'warning'
}

// ---- 数据概览按版本筛选 ----
const overviewVersionId = ref<number | null>(null)
const overviewVersionOptions = ref<Array<{ label: string; value: number }>>([])

async function loadOverviewVersions(): Promise<void> {
  const pid = appStore.currentProjectId
  if (!pid) {
    overviewVersionOptions.value = []
    overviewVersionId.value = null
    return
  }
  try {
    const result = await projectVersionApi.list(pid)
    overviewVersionOptions.value = result.list.map((v) => ({
      label: v.versionCode,
      value: v.id as number,
    }))
  } catch {
    overviewVersionOptions.value = []
  }
}

watch(() => appStore.currentProjectId, () => {
  overviewVersionId.value = null
  void loadOverviewVersions()
  void reload()
})

// ---- 测试环境健康监测 ----
type EnvSide = 'app' | 'db'
const envSides: EnvSide[] = ['app', 'db']

interface EnvProbeRow {
  env: TestEnv
  probe: TestEnvProbe | null
  probing: boolean
  probedAt: string
}

interface EnvServerView {
  side: EnvSide
  title: string
  target: string
  ok: boolean | null
  latencyMs: number | null
  metrics: ServerMetrics | null
  metricsMessage: string
  cpuPoints: number[]
  cpuTimes: number[]
  memPoints: number[]
  memTimes: number[]
  memText: string
  disks: Array<{ name: string; percent: number | null; text: string; points: number[]; times: number[] }>
}

interface EnvCardView {
  id: number
  name: string
  isDefault: boolean
  kindLabel: string
  overall: 'healthy' | 'unhealthy' | null
  probedAt: string
  probing: boolean
  servers: EnvServerView[]
}

const envRows = ref<EnvProbeRow[]>([])
const envLoading = ref(false)
const envProbingAll = ref(false)
const envAutoRefresh = ref(true)
// 曲线历史只留在页面内存里，最多保留最近 1 小时的采样
const metricHistory = reactive<Record<string, MetricSample[]>>({})

interface MetricSample {
  value: number
  ts: number
}

const ENV_REFRESH_MS = 15000
const METRICS_WINDOW_MS = 60 * 60 * 1000
const HISTORY_LIMIT = 260
let envTimer: number | null = null
let autoProbing = false
let activatedBefore = false

function percentLabel(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)}%`
}

function bytesText(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return '—'
  }
  const gb = value / 1024 ** 3
  return gb >= 1024 ? `${(gb / 1024).toFixed(1)} TB` : `${gb.toFixed(1)} GB`
}

function usageText(used: number | null | undefined, total: number | null | undefined): string {
  if (used === null || used === undefined || total === null || total === undefined) {
    return ''
  }
  return `${bytesText(used)} / ${bytesText(total)}`
}

function serverTarget(env: TestEnv, side: EnvSide): string {
  if (side === 'app') {
    return env.app.baseUrl || '未配置应用地址'
  }
  if (env.db.type === 'sqlite') {
    return env.db.host || '本地文件库'
  }
  return env.db.host ? `${env.db.host}:${env.db.port ?? '默认端口'}` : '未配置数据库主机'
}

function pushPoint(key: string, value: number | null | undefined, ts: number): void {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return
  }
  const list = metricHistory[key] ?? (metricHistory[key] = [])
  list.push({ value, ts })
  const cutoff = Date.now() - METRICS_WINDOW_MS
  let drop = 0
  while (drop < list.length && list[drop].ts < cutoff) {
    drop += 1
  }
  if (drop > 0) {
    list.splice(0, drop)
  }
  if (list.length > HISTORY_LIMIT) {
    list.splice(0, list.length - HISTORY_LIMIT)
  }
}

function recordMetrics(envId: number, side: EnvSide, metrics: ServerMetrics | undefined): void {
  if (!metrics?.available) {
    return
  }
  const parsed = metrics.sampledAt ? Date.parse(metrics.sampledAt) : Number.NaN
  const ts = Number.isFinite(parsed) ? parsed : Date.now()
  pushPoint(`${envId}:${side}:cpu`, metrics.cpuPercent, ts)
  pushPoint(`${envId}:${side}:mem`, metrics.memory.percent, ts)
  for (const disk of metrics.disks) {
    pushPoint(`${envId}:${side}:disk:${disk.name}`, disk.percent, ts)
  }
}

function historyOf(envId: number, side: EnvSide, metric: string): MetricSample[] {
  return metricHistory[`${envId}:${side}:${metric}`] ?? []
}

function toSamples(series: EnvMetricSeries): MetricSample[] {
  return series.ts.map((ts, index) => ({ ts, value: series.values[index] }))
}

/** 用服务端历史覆盖本地曲线，进页面就能直接画出来。 */
function applySideHistory(envId: number, side: EnvSide, data: EnvHealthSide): void {
  const prefix = `${envId}:${side}:`
  for (const key of Object.keys(metricHistory)) {
    if (key.startsWith(prefix)) {
      delete metricHistory[key]
    }
  }
  metricHistory[`${prefix}cpu`] = toSamples(data.cpu)
  metricHistory[`${prefix}mem`] = toSamples(data.memory)
  for (const [name, series] of Object.entries(data.disks)) {
    metricHistory[`${prefix}disk:${name}`] = toSamples(series)
  }
}

/** 应用服务端历史；返回是否已有最新探测状态。 */
function applyEnvHistory(payload: EnvHealthHistoryResult): boolean {
  const byId = new Map(payload.list.map((item) => [item.envId, item]))
  let hasData = false
  for (const row of envRows.value) {
    const item = byId.get(row.env.id)
    if (!item) {
      continue
    }
    if (item.probe) {
      row.probe = item.probe
      row.probedAt = item.probedAt
        ? new Date(item.probedAt).toLocaleTimeString('zh-CN', { hour12: false })
        : ''
      hasData = true
    }
    applySideHistory(row.env.id, 'app', item.app)
    applySideHistory(row.env.id, 'db', item.db)
  }
  return hasData
}

/** 读服务端持续采样结果；服务刚启动还没有点位时返回 false，由调用方兜底探测一次。 */
async function loadEnvHistory(): Promise<boolean> {
  try {
    return applyEnvHistory(await testEnvApi.healthHistory())
  } catch {
    return false
  }
}

const envCards = computed<EnvCardView[]>(() =>
  envRows.value.map((row) => ({
    id: row.env.id,
    name: row.env.name,
    isDefault: row.env.isDefault,
    kindLabel: row.env.kindLabel,
    overall: row.probe?.overall ?? null,
    probedAt: row.probedAt,
    probing: row.probing,
    servers: envSides.map((side) => {
      const item = row.probe?.[side]
      const raw = item?.metrics
      const metrics = raw?.available ? raw : null
      const cpuSamples = historyOf(row.env.id, side, 'cpu')
      const memSamples = historyOf(row.env.id, side, 'mem')
      return {
        side,
        title: side === 'app' ? '应用服务器' : '数据库服务器',
        target: serverTarget(row.env, side),
        ok: item?.ok ?? null,
        latencyMs: item?.latencyMs ?? null,
        metrics,
        metricsMessage: raw?.message || (item ? '未接入资源指标' : '待探测'),
        cpuPoints: cpuSamples.map((sample) => sample.value),
        cpuTimes: cpuSamples.map((sample) => sample.ts),
        memPoints: memSamples.map((sample) => sample.value),
        memTimes: memSamples.map((sample) => sample.ts),
        memText: usageText(metrics?.memory.usedBytes, metrics?.memory.totalBytes),
        disks: (metrics?.disks ?? []).map((disk) => {
          const samples = historyOf(row.env.id, side, `disk:${disk.name}`)
          return {
            name: disk.name,
            percent: disk.percent,
            text: usageText(disk.usedBytes, disk.totalBytes),
            points: samples.map((sample) => sample.value),
            times: samples.map((sample) => sample.ts),
          }
        }),
      }
    }),
  })),
)

/** 拉环境清单；有多套时全部展示，每套可单独探测。 */
async function loadEnvs(): Promise<void> {
  envLoading.value = true
  try {
    const result = await testEnvApi.list()
    envRows.value = result.list.map((env) => ({
      env,
      probe: null,
      probing: false,
      probedAt: '',
    }))
  } catch {
    envRows.value = []
  } finally {
    envLoading.value = false
  }
}

/** 单套探测；quiet 用于自动刷新，失败不弹提示、不写操作日志。 */
async function probeEnv(row: EnvProbeRow, quiet = false): Promise<void> {
  row.probing = true
  try {
    const result = await testEnvApi.probe(row.env.id, quiet ? { quiet: true } : undefined)
    row.probe = result
    row.probedAt = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    recordMetrics(row.env.id, 'app', result.app.metrics)
    recordMetrics(row.env.id, 'db', result.db.metrics)
  } catch {
    row.probe = null
    if (!quiet) {
      ElMessage.error(`「${row.env.name}」探测失败`)
    }
  } finally {
    row.probing = false
  }
}

/** 全部探测：并发跑，等所有结果回来；自动刷新时避免上一轮未完成又发起。 */
async function probeAll(quiet = false): Promise<void> {
  if (!envRows.value.length) {
    return
  }
  if (quiet) {
    if (autoProbing) {
      return
    }
    autoProbing = true
  } else {
    envProbingAll.value = true
  }
  try {
    await Promise.all(envRows.value.map((row) => probeEnv(row, quiet)))
  } finally {
    if (quiet) {
      autoProbing = false
    } else {
      envProbingAll.value = false
    }
  }
}

/** 轮询服务端采样结果；服务刚启动还没有点位时手动补探一轮。 */
async function refreshEnvHealth(): Promise<void> {
  if (!envRows.value.length) {
    return
  }
  const hasData = await loadEnvHistory()
  if (!hasData) {
    void probeAll(true)
  }
}

function probeById(id: number): void {
  const row = envRows.value.find((item) => item.env.id === id)
  if (row) {
    void probeEnv(row)
  }
}

function startEnvTimer(): void {
  if (envTimer !== null) {
    return
  }
  envTimer = window.setInterval(() => {
    if (envAutoRefresh.value) {
      void refreshEnvHealth()
    }
  }, ENV_REFRESH_MS)
}

function stopEnvTimer(): void {
  if (envTimer !== null) {
    window.clearInterval(envTimer)
    envTimer = null
  }
}

function goToEnvSettings(): void {
  void router.push('/settings/environments')
}

// 饼图状态 key → 中文标签（与用例/缺陷列表页的状态文案保持一致）
const CASE_STATUS_LABELS: Record<string, string> = {
  not_executed: '未执行',
  pass: '通过',
  fail: '失败',
  blocked: '阻塞',
}
const BUG_STATUS_LABELS: Record<string, string> = {
  open: '新建',
  assigned: '已指派',
  fixed: '已修复',
  retest: '待验证',
  reopened: '重新打开',
  closed: '已关闭',
  rejected: '已拒绝',
}

const caseOption = computed<EChartsOption>(() => {
  const source = overview.value?.caseStatus ?? {}
  const data = Object.entries(source).map(([name, value]) => ({
    name: CASE_STATUS_LABELS[name] ?? name,
    value,
  }))
  return {
    textStyle: { color: text.value },
    tooltip: {
      trigger: 'item',
      // 饼图默认只显示整数百分比，这里统一成两位小数
      formatter: (params: unknown) => {
        const item = params as { name?: string; value?: unknown; percent?: number }
        return `${item.name ?? ''}: ${item.value ?? ''}（${Number(item.percent ?? 0).toFixed(2)}%）`
      },
    },
    legend: { bottom: 0, type: 'scroll', textStyle: { color: weak.value } },
    series: [
      {
        name: '用例状态',
        type: 'pie',
        radius: ['45%', '68%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 4, borderColor: panel.value, borderWidth: 2 },
        label: { formatter: '{b}: {c}', color: text.value },
        data,
      },
    ],
  }
})

const bugOption = computed<EChartsOption>(() => {
  const source = overview.value?.bugStatus ?? {}
  const data = Object.entries(source).map(([name, value]) => ({
    name: BUG_STATUS_LABELS[name] ?? name,
    value,
  }))
  return {
    textStyle: { color: text.value },
    tooltip: {
      trigger: 'item',
      // 饼图默认只显示整数百分比，这里统一成两位小数
      formatter: (params: unknown) => {
        const item = params as { name?: string; value?: unknown; percent?: number }
        return `${item.name ?? ''}: ${item.value ?? ''}（${Number(item.percent ?? 0).toFixed(2)}%）`
      },
    },
    legend: { bottom: 0, type: 'scroll', textStyle: { color: weak.value } },
    series: [
      {
        name: '缺陷状态',
        type: 'pie',
        radius: ['45%', '68%'],
        center: ['50%', '45%'],
        itemStyle: { borderRadius: 4, borderColor: panel.value, borderWidth: 2 },
        label: { formatter: '{b}: {c}', color: text.value },
        data,
      },
    ],
  }
})

/** 能力使用量：横向条形，取数量降序后反转，让最大值落在顶部 */
const aiUsageOption = computed<EChartsOption>(() => {
  const rows = (overview.value?.aiUsage ?? []).slice().reverse()
  return {
    textStyle: { color: text.value },
    grid: { left: 8, right: 30, top: 10, bottom: 10, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: weak.value },
      splitLine: { lineStyle: { color: border.value } },
    },
    yAxis: {
      type: 'category',
      data: rows.map((item) => item.label || item.capability),
      axisLabel: { color: text.value },
      axisLine: { lineStyle: { color: border.value } },
    },
    series: [
      {
        name: '任务数',
        type: 'bar',
        barMaxWidth: 14,
        itemStyle: { color: primary.value, borderRadius: 4 },
        label: { show: true, position: 'right', color: weak.value },
        data: rows.map((item) => item.count),
      },
    ],
  }
})

const BUG_SEVERITY_LABEL: Record<string, string> = {
  P0: 'P0 致命',
  P1: 'P1 严重',
  P2: 'P2 一般',
  P3: 'P3 轻微',
}

/** 严重级别：保留 0 值的级别，柱形高度对比比饼图更直观 */
const bugSeverityOption = computed<EChartsOption>(() => {
  const source = overview.value?.bugSeverity ?? {}
  const keys = Object.keys(source)
  return {
    textStyle: { color: text.value },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: {
      type: 'category',
      data: keys.map((key) => BUG_SEVERITY_LABEL[key] ?? key),
      axisLabel: { color: text.value },
      axisLine: { lineStyle: { color: border.value } },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: weak.value },
      splitLine: { lineStyle: { color: border.value } },
    },
    series: [
      {
        name: '缺陷数',
        type: 'bar',
        barMaxWidth: 34,
        itemStyle: { color: primary.value, borderRadius: 4 },
        label: { show: true, position: 'top', color: weak.value },
        data: keys.map((key) => source[key]),
      },
    ],
  }
})

const charts = [
  useEchart(caseChartEl, caseOption),
  useEchart(bugChartEl, bugOption),
  useEchart(aiUsageChartEl, aiUsageOption),
  useEchart(bugSeverityChartEl, bugSeverityOption),
]

function statusTag(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'success') {
    return 'success'
  }
  if (status === 'failed') {
    return 'danger'
  }
  if (status === 'cancelled') {
    return 'info'
  }
  return 'warning'
}

async function reload(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    overview.value = await workbenchApi.overview(
      appStore.currentProjectId ?? undefined,
      overviewVersionId.value ?? undefined,
    )
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
  await loadModuleTree()
}

async function loadModuleTree(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) {
    moduleTree.value = []
    return
  }
  moduleLoading.value = true
  try {
    const payload = await moduleApi.tree(projectId)
    moduleTree.value = payload.tree
  } catch {
    moduleTree.value = []
  } finally {
    moduleLoading.value = false
  }
}

function onModuleSelect(node: ModuleNode): void {
  void router.push({ path: '/settings/modules', query: { moduleId: String(node.id) } })
}

onMounted(async () => {
  void loadCardLayout()
  void loadOverviewVersions()
  void reload()
  // 服务端已在持续采样，进首页直接读历史；服务刚启动没有点位时才补探一轮
  await loadEnvs()
  if (envRows.value.length) {
    await refreshEnvHealth()
  }
})

// keep-alive 返回首页：刷新环境清单与历史、恢复轮询；离开时停掉计时器
onActivated(() => {
  void loadCardLayout()
  startEnvTimer()
  if (activatedBefore) {
    void (async () => {
      await loadEnvs()
      await refreshEnvHealth()
    })()
  }
  activatedBefore = true
})

onDeactivated(stopEnvTimer)
onBeforeUnmount(stopEnvTimer)
</script>

<style scoped>
.home__alert {
  margin-bottom: 12px;
}

.home__section-hint {
  margin-left: 10px;
  font-size: 12px;
  font-weight: 400;
  color: var(--aitp-text-weak);
}

/* 测试环境健康监测：按环境分卡，应用/数据库两列资源曲线 */
.home__env-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.home__env-list {
  display: grid;
  gap: 12px;
}

.home__env-card {
  border: 1px solid var(--aitp-border);
  border-radius: 10px;
  padding: 12px 14px;
  background: var(--aitp-subtle);
}

.home__env-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.home__env-card-meta {
  display: flex;
  align-items: center;
  gap: 10px;
}

.home__env-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.home__env-time {
  font-size: 12px;
}

.home__env-servers {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 12px;
}

.home__env-server {
  min-width: 0;
  border: 1px solid var(--aitp-border);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--aitp-panel);
}

.home__env-server-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}

.home__env-server-name {
  font-size: 13px;
  font-weight: 600;
}

.home__env-latency {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.home__env-target {
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--aitp-text-weak);
  word-break: break-all;
}

.home__metrics {
  display: grid;
  gap: 10px;
}

.home__metric {
  min-width: 0;
}

.home__metric-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 2px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.home__metric-name {
  flex: none;
}

.home__metric-value {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.home__metric-head strong {
  font-size: 13px;
  color: var(--aitp-text);
  font-variant-numeric: tabular-nums;
}

.home__metric-sub {
  font-size: 11px;
  color: var(--aitp-text-weak);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.home__metrics-axis {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  /* 右侧留出曲线的百分比刻度栏，让「现在」对齐绘图区右边缘 */
  padding-right: 36px;
  margin-top: -2px;
  font-size: 11px;
  color: var(--aitp-text-weak);
}

.home__metrics-window {
  font-variant-numeric: tabular-nums;
}

.home__metrics-empty {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

/* 最近任务「能力」列：拿满剩余宽度，长会话标题单行省略 */
:deep(.home__task-cap-col .cell) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home__stats {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
}

/* 面板里再套一层卡片边框会显得重，内层只留描边 */
.home__stats :deep(.metric-card) {
  padding: 12px 14px;
  box-shadow: none;
  background: var(--aitp-subtle);
}

.home__section {
  margin-bottom: 14px;
}

.home__section-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
}

/* 可定制卡片区：flex + order，隐藏项不占位，宽卡独占一行，半宽卡两两并排 */
.home__cards {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: stretch;
}

.home__cards .home__section {
  margin-bottom: 0;
}

.home__card {
  flex: 1 1 calc(50% - 7px);
  min-width: 300px;
}

.home__chart-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.home__chart {
  flex: 1;
  min-height: 320px;
  width: 100%;
}

/* 新增卡片只占半屏，260 足够看清对比，不再撑到 320 */
.home__chart--short {
  min-height: 260px;
}

/* 项目进度：四条进度条上下排布，标签与数值同行 */
.home__progress {
  display: grid;
  gap: 14px;
}

.home__progress-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.home__progress-head strong {
  font-size: 13px;
  color: var(--aitp-text);
}
</style>
