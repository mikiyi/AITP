<template>
  <div class="page home">
    <div class="page-header">
      <el-button :icon="Refresh" :loading="loading" style="margin: 0 0 0 auto" @click="reload">
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

    <div class="home__grid">
      <div class="panel panel-pad home__section">
        <div class="home__section-title">最近任务</div>
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

      <div class="panel panel-pad home__section home__chart-panel">
        <div class="home__section-title">用例执行状态</div>
        <div ref="caseChartEl" class="home__chart" />
      </div>
    </div>

    <div class="home__grid">
      <div class="panel panel-pad home__section home__chart-panel">
        <div class="home__section-title">缺陷状态分布</div>
        <div ref="bugChartEl" class="home__chart" />
      </div>

      <div class="panel panel-pad home__section">
        <div class="home__section-title">模块结构</div>
        <ModuleTree :nodes="moduleTree" :loading="moduleLoading" @select="onModuleSelect" />
      </div>
    </div>

    <!-- 测试环境健康监测：消费测试环境配置，本机探测应用/数据库可达性 -->
    <div class="panel panel-pad home__section">
      <div class="home__section-title">
        测试环境健康监测
        <span class="home__section-hint">按「测试环境配置」里的目标做本机可达性探测，信息不出本机</span>
      </div>
      <div class="home__env-toolbar">
        <el-button
          v-if="envRows.length"
          size="small"
          type="primary"
          plain
          :loading="envProbingAll"
          @click="probeAll"
        >
          全部探测
        </el-button>
        <el-button size="small" plain @click="goToEnvSettings">
          环境配置
        </el-button>
      </div>

      <el-table v-if="envRows.length" :data="envRows" size="small" v-loading="envLoading">
        <el-table-column label="环境" min-width="160">
          <template #default="{ row }">
            <div class="home__env-name">
              {{ row.env.name }}
              <el-tag v-if="row.env.isDefault" size="small" type="success" effect="plain">默认</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="应用服务器" min-width="220">
          <template #default="{ row }">
            <span v-if="row.probe" class="home__env-status">
              <el-tag :type="row.probe.app.ok ? 'success' : 'danger'" size="small" effect="plain">
                {{ row.probe.app.ok ? '可达' : '不可达' }}
              </el-tag>
              <span class="home__env-latency">{{ row.probe.app.latencyMs }}ms</span>
            </span>
            <el-tag v-else size="small" type="info" effect="plain">待探测</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="数据库服务器" min-width="220">
          <template #default="{ row }">
            <span v-if="row.probe" class="home__env-status">
              <el-tag :type="row.probe.db.ok ? 'success' : 'danger'" size="small" effect="plain">
                {{ row.probe.db.ok ? '可达' : '不可达' }}
              </el-tag>
              <span class="home__env-latency">{{ row.probe.db.latencyMs }}ms</span>
            </span>
            <el-tag v-else size="small" type="info" effect="plain">待探测</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="整体" width="110">
          <template #default="{ row }">
            <el-tag
              v-if="row.probe"
              :type="row.probe.overall === 'healthy' ? 'success' : 'danger'"
              size="small"
              effect="plain"
            >
              {{ row.probe.overall === 'healthy' ? '健康' : '异常' }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :loading="row.probing" @click="probeEnv(row)">
              探测
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty
        v-else
        description="还没有配置测试环境"
        :image-size="80"
      >
        <el-button type="primary" size="small" @click="goToEnvSettings">去配置测试环境</el-button>
      </el-empty>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import type { EChartsOption } from 'echarts'
import { dateTimeText } from '@/utils/format'
import MetricCard from '@/components/MetricCard.vue'
import ModuleTree from '@/components/ModuleTree.vue'
import QuickEntryGrid from '@/components/QuickEntryGrid.vue'
import KnowledgeAssistant from '@/components/KnowledgeAssistant.vue'
import { moduleApi, projectVersionApi, testEnvApi, workbenchApi } from '@/api'
import type { ModuleNode, QuickEntry, TestEnv, TestEnvProbe, WorkbenchOverview } from '@/api/types'
import { useEchart } from '@/composables/useEchart'
import { useChartTheme } from '@/composables/useChartTheme'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const appStore = useAppStore()
const { panel, text, weak } = useChartTheme()

const loading = ref(false)
const errorMessage = ref('')
const overview = ref<WorkbenchOverview | null>(null)
const moduleTree = ref<ModuleNode[]>([])
const moduleLoading = ref(false)

const caseChartEl = ref<HTMLElement | null>(null)
const bugChartEl = ref<HTMLElement | null>(null)

const metrics = computed(() => overview.value?.metrics ?? ({} as WorkbenchOverview['metrics']))
const assets = computed(() => overview.value?.assets ?? ({} as WorkbenchOverview['assets']))
const shortcuts = computed<QuickEntry[]>(() => overview.value?.shortcuts ?? [])
const recentTasks = computed(() => overview.value?.recentTasks ?? [])
const pendingKnowledge = computed(() => overview.value?.pendingKnowledge ?? 0)

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
      label: v.versionCode + (v.name ? ` ${v.name}` : ''),
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
interface EnvProbeRow {
  env: TestEnv
  probe: TestEnvProbe | null
  probing: boolean
}
const envRows = ref<EnvProbeRow[]>([])
const envLoading = ref(false)
const envProbingAll = ref(false)

/** 拉环境清单；有多套时全部展示，每套可单独探测。 */
async function loadEnvs(): Promise<void> {
  envLoading.value = true
  try {
    const result = await testEnvApi.list()
    envRows.value = result.list.map((env) => ({ env, probe: null, probing: false }))
  } catch {
    envRows.value = []
  } finally {
    envLoading.value = false
  }
}

/** 单套探测。 */
async function probeEnv(row: EnvProbeRow): Promise<void> {
  row.probing = true
  try {
    row.probe = await testEnvApi.probe(row.env.id)
  } catch {
    row.probe = null
    ElMessage.error(`「${row.env.name}」探测失败`)
  } finally {
    row.probing = false
  }
}

/** 全部探测：并发跑，等所有结果回来。 */
async function probeAll(): Promise<void> {
  envProbingAll.value = true
  try {
    await Promise.all(envRows.value.map((row) => probeEnv(row)))
  } finally {
    envProbingAll.value = false
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

useEchart(caseChartEl, caseOption)
useEchart(bugChartEl, bugOption)

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
  void loadOverviewVersions()
  void reload()
  // 有配置测试环境时自动探测一次，进首页就能看到健康状态
  await loadEnvs()
  if (envRows.value.length) {
    void probeAll()
  }
})
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

/* 测试环境健康监测卡片 */
.home__env-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.home__env-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.home__env-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.home__env-latency {
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

.home__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 14px;
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

@media (max-width: 1100px) {
  .home__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
