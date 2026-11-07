<template>
  <div class="trend-panel">
    <div class="trend-panel__head">
      <span class="trend-panel__title">指标趋势</span>
      <div class="trend-panel__controls">
        <el-radio-group v-model="granularity" size="small" @change="changeGranularity">
          <el-radio-button value="day">按天</el-radio-button>
          <el-radio-button value="week">按周</el-radio-button>
        </el-radio-group>
        <el-select v-model="periods" size="small" style="width: 110px" @change="loadTrend">
          <el-option v-for="item in periodOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button
          v-if="reportId"
          size="small"
          type="primary"
          plain
          :loading="inserting"
          :disabled="!trend"
          @click="insertTrend"
        >
          插入趋势章节
        </el-button>
      </div>
    </div>

    <div class="trend-panel__summary">
      <span>执行 {{ trend?.totals.executions ?? 0 }}</span>
      <span>通过率 {{ formatRate(trend?.totals.passRate ?? 0) }}</span>
      <span>新增缺陷 {{ trend?.totals.newBugs ?? 0 }}</span>
      <span>关闭缺陷 {{ trend?.totals.closedBugs ?? 0 }}</span>
      <span>P0/P1 遗留 {{ trend?.totals.p0p1Open ?? 0 }}</span>
      <span v-if="trend" class="trend-panel__range">{{ trend.startDate }} ~ {{ trend.endDate }}</span>
    </div>

    <div v-loading="trendLoading" ref="chartEl" class="trend-panel__chart" />
    <el-empty v-if="!trendLoading && !trend" description="暂无趋势数据" :image-size="60" />

    <el-divider />

    <div class="trend-panel__head">
      <span class="trend-panel__title">版本对比</span>
      <div class="trend-panel__controls">
        <el-select v-model="baseVersionId" size="small" placeholder="基准版本" style="width: 150px">
          <el-option
            v-for="item in versions"
            :key="item.id"
            :label="versionLabel(item)"
            :value="item.id"
          />
        </el-select>
        <span class="trend-panel__arrow">→</span>
        <el-select v-model="targetVersionId" size="small" placeholder="目标版本" style="width: 150px">
          <el-option
            v-for="item in versions"
            :key="item.id"
            :label="versionLabel(item)"
            :value="item.id"
          />
        </el-select>
        <el-button size="small" :disabled="!canCompare" :loading="compareLoading" @click="loadCompare">
          对比
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="versions.length < 2"
      type="info"
      :closable="false"
      show-icon
      :title="versions.length ? '至少需要两个版本才能对比' : '该项目还没有版本'"
    >
      <template #default>
        先到
        <router-link class="trend-panel__link" to="/settings/versions">项目版本管理</router-link>
        创建并维护版本，再回来对比。
      </template>
    </el-alert>
    <template v-else-if="compare">
      <div class="trend-panel__gates">
        <span>
          {{ compare.base.versionCode || compare.base.versionName }}：
          <el-tooltip
            :disabled="!compare.base.gateConclusion"
            :content="compare.base.gateConclusion"
            placement="top"
          >
            <el-tag size="small" :type="gateTag(compare.base.gateStatus)">{{ compare.base.gateStatusLabel }}</el-tag>
          </el-tooltip>
        </span>
        <span>
          {{ compare.target.versionCode || compare.target.versionName }}：
          <el-tooltip
            :disabled="!compare.target.gateConclusion"
            :content="compare.target.gateConclusion"
            placement="top"
          >
            <el-tag size="small" :type="gateTag(compare.target.gateStatus)">{{ compare.target.gateStatusLabel }}</el-tag>
          </el-tooltip>
        </span>
      </div>
      <el-table :data="compare.metrics" size="small" border>
        <el-table-column prop="label" label="指标" min-width="120" />
        <el-table-column label="基准" min-width="110" align="right">
          <template #default="{ row }">{{ formatMetric(row.base, row.unit) }}</template>
        </el-table-column>
        <el-table-column label="目标" min-width="110" align="right">
          <template #default="{ row }">{{ formatMetric(row.target, row.unit) }}</template>
        </el-table-column>
        <el-table-column label="变化" min-width="110" align="right">
          <template #default="{ row }">
            <span :class="deltaClass(row)">{{ formatDelta(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="期望" width="90" align="center">
          <template #default="{ row }">{{ directionLabel(row) }}</template>
        </el-table-column>
      </el-table>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { EChartsOption } from 'echarts'
import { projectVersionApi, reportApi } from '@/api'
import type { Id, ProjectVersion, ReportTrend, VersionCompare } from '@/api/types'
import { useEchart } from '@/composables/useEchart'
import { useChartTheme } from '@/composables/useChartTheme'

const props = defineProps<{ projectId: Id; reportId?: Id }>()
const emit = defineEmits<{ inserted: [items: unknown[]] }>()

const { text, weak, primary, border } = useChartTheme()

const granularity = ref<'day' | 'week'>('day')
const periods = ref(14)
const trend = ref<ReportTrend | null>(null)
const trendLoading = ref(false)
const inserting = ref(false)
const chartEl = ref<HTMLElement | null>(null)

const versions = ref<ProjectVersion[]>([])
const baseVersionId = ref<Id | null>(null)
const targetVersionId = ref<Id | null>(null)
const compare = ref<VersionCompare | null>(null)
const compareLoading = ref(false)

const periodOptions = computed(() =>
  granularity.value === 'day'
    ? [
        { value: 7, label: '最近 7 天' },
        { value: 14, label: '最近 14 天' },
        { value: 30, label: '最近 30 天' },
      ]
    : [
        { value: 4, label: '最近 4 周' },
        { value: 8, label: '最近 8 周' },
        { value: 12, label: '最近 12 周' },
      ],
)

const canCompare = computed(
  () =>
    Boolean(baseVersionId.value && targetVersionId.value) &&
    baseVersionId.value !== targetVersionId.value,
)

function seriesOf(key: string): number[] {
  return trend.value?.series.find((item) => item.key === key)?.values ?? []
}

const chartOption = computed<EChartsOption>(() => ({
  textStyle: { color: text.value },
  tooltip: { trigger: 'axis' },
  legend: { top: 0, textStyle: { color: weak.value } },
  grid: { left: 44, right: 48, top: 36, bottom: 30 },
  xAxis: {
    type: 'category',
    data: trend.value?.buckets ?? [],
    axisLabel: { color: weak.value },
    axisLine: { lineStyle: { color: border.value } },
  },
  yAxis: [
    {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: weak.value },
      splitLine: { lineStyle: { color: border.value } },
    },
    {
      type: 'value',
      min: 0,
      max: 100,
      axisLabel: { color: weak.value, formatter: '{value}%' },
      splitLine: { show: false },
    },
  ],
  series: [
    {
      name: '执行数',
      type: 'bar',
      barMaxWidth: 28,
      itemStyle: { color: primary.value, borderRadius: [4, 4, 0, 0] },
      data: seriesOf('executions'),
    },
    {
      name: '新增缺陷',
      type: 'line',
      smooth: true,
      itemStyle: { color: '#f56c6c' },
      data: seriesOf('newBugs'),
    },
    {
      name: '关闭缺陷',
      type: 'line',
      smooth: true,
      itemStyle: { color: '#67c23a' },
      data: seriesOf('closedBugs'),
    },
    {
      name: 'P0/P1 遗留',
      type: 'line',
      smooth: true,
      itemStyle: { color: '#e6a23c' },
      data: seriesOf('p0p1Open'),
    },
    {
      name: '通过率',
      type: 'line',
      smooth: true,
      yAxisIndex: 1,
      itemStyle: { color: '#909399' },
      data: seriesOf('passRate'),
    },
  ],
}))

useEchart(chartEl, chartOption)

function formatRate(value: number): string {
  return `${Number(value ?? 0).toFixed(2)}%`
}

function formatMetric(value: number, unit: string): string {
  if (unit === '%') {
    return `${Number(value ?? 0).toFixed(2)}%`
  }
  if (unit === '个/用例') {
    return Number(value ?? 0).toFixed(2)
  }
  return String(Math.round(Number(value ?? 0)))
}

function formatDelta(row: {
  delta: number
  unit: string
  higherIsBetter: boolean
}): string {
  const value = Number(row.delta ?? 0)
  const sign = value > 0 ? '+' : ''
  return `${sign}${formatMetric(value, row.unit)}`
}

function directionLabel(row: { higherIsBetter: boolean; neutral: boolean; unit: string }): string {
  if (row.neutral) {
    return '中性'
  }
  return row.higherIsBetter ? '越高越好' : '越低越好'
}

function deltaClass(row: {
  delta: number
  higherIsBetter: boolean
  neutral: boolean
}): string {
  if (row.neutral || !row.delta) {
    return 'trend-panel__delta trend-panel__delta--flat'
  }
  const better = row.higherIsBetter ? row.delta > 0 : row.delta < 0
  return better
    ? 'trend-panel__delta trend-panel__delta--better'
    : 'trend-panel__delta trend-panel__delta--worse'
}

function gateTag(status: string): 'success' | 'danger' | 'info' {
  if (status === 'passed') {
    return 'success'
  }
  return status === 'blocked' ? 'danger' : 'info'
}

function versionLabel(item: ProjectVersion): string {
  return item.versionCode ? `${item.versionCode} ${item.name}`.trim() : item.name
}

function changeGranularity(value: 'day' | 'week'): void {
  periods.value = value === 'day' ? 14 : 8
  void loadTrend()
}

async function loadTrend(): Promise<void> {
  if (!granularity.value) {
    return
  }
  trendLoading.value = true
  try {
    trend.value = await reportApi.trends({
      projectId: Number(props.projectId),
      granularity: granularity.value,
      periods: periods.value,
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载趋势失败')
  } finally {
    trendLoading.value = false
  }
}

async function loadVersions(): Promise<void> {
  try {
    const result = await projectVersionApi.list(Number(props.projectId))
    versions.value = result.list ?? []
    const usable = versions.value.map((item) => item.id)
    baseVersionId.value = usable[0] ?? null
    targetVersionId.value = usable[1] ?? null
  } catch {
    versions.value = []
  }
}

async function loadCompare(): Promise<void> {
  if (!canCompare.value) {
    ElMessage.warning('请选择两个不同的版本')
    return
  }
  compareLoading.value = true
  try {
    compare.value = await reportApi.versionCompare({
      projectId: Number(props.projectId),
      baseVersionId: Number(baseVersionId.value),
      targetVersionId: Number(targetVersionId.value),
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '版本对比失败')
  } finally {
    compareLoading.value = false
  }
}

function refresh(): void {
  void loadVersions()
  void loadTrend()
}

async function insertTrend(): Promise<void> {  if (!props.reportId) {
    return
  }
  inserting.value = true
  try {
    const result = await reportApi.insertTrend(Number(props.reportId), {
      granularity: granularity.value,
      periods: periods.value,
    })
    ElMessage.success('已插入趋势章节')
    emit('inserted', result.items)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '插入失败')
  } finally {
    inserting.value = false
  }
}

watch(
  () => props.projectId,
  () => {
    compare.value = null
    void loadVersions()
    void loadTrend()
  },
)

watch([baseVersionId, targetVersionId], () => {
  compare.value = null
})

defineExpose({ refresh })

onMounted(() => {
  void loadVersions()
  void loadTrend()
})
</script>

<style scoped>
.trend-panel {
  min-width: 0;
}

.trend-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.trend-panel__title {
  font-size: 14px;
  font-weight: 600;
}

.trend-panel__controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.trend-panel__arrow {
  color: var(--aitp-text-weak);
}

.trend-panel__summary {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  color: var(--aitp-text-weak);
  margin-bottom: 8px;
}

.trend-panel__range {
  margin-left: auto;
}

.trend-panel__chart {
  height: 300px;
  min-height: 300px;
  width: 100%;
}

.trend-panel__gates {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  font-size: 13px;
  margin: 6px 0 10px;
}

.trend-panel__delta--better {
  color: #67c23a;
}

.trend-panel__delta--worse {
  color: #f56c6c;
}

.trend-panel__delta--flat {
  color: var(--aitp-text-weak);
}

.trend-panel__link {
  color: var(--el-color-primary);
  text-decoration: none;
}

.trend-panel__link:hover {
  text-decoration: underline;
}
</style>
