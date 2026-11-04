<template>
  <div class="report-charts">
    <div class="report-charts__grid">
      <div class="report-charts__cell">
        <div class="report-charts__title">用例执行状态</div>
        <div ref="caseStatusEl" class="report-charts__chart" />
      </div>
      <div class="report-charts__cell">
        <div class="report-charts__title">用例优先级分布</div>
        <div ref="casePriorityEl" class="report-charts__chart" />
      </div>
      <div class="report-charts__cell">
        <div class="report-charts__title">用例类型分布</div>
        <div ref="caseTypeEl" class="report-charts__chart" />
      </div>
      <div class="report-charts__cell">
        <div class="report-charts__title">缺陷状态分布</div>
        <div ref="bugStatusEl" class="report-charts__chart" />
      </div>
      <div class="report-charts__cell">
        <div class="report-charts__title">缺陷严重级别</div>
        <div ref="bugSeverityEl" class="report-charts__chart" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import type { ReportStats } from '@/api/types'
import { useEchart } from '@/composables/useEchart'
import { useChartTheme } from '@/composables/useChartTheme'

const props = defineProps<{ stats: ReportStats | null }>()

const { panel, text, weak, primary, border } = useChartTheme()

const CASE_STATUS_LABEL: Record<string, string> = {
  not_executed: '未执行',
  pass: '通过',
  fail: '失败',
  blocked: '阻塞',
}

const CASE_TYPE_LABEL: Record<string, string> = {
  functional: '功能',
  interface: '接口',
  boundary: '边界',
  security: '安全',
  ui: 'UI',
  exception: '异常',
}

const BUG_STATUS_LABEL: Record<string, string> = {
  open: '待处理',
  assigned: '处理中',
  fixed: '已修复',
  retest: '待复测',
  reopened: '重新打开',
  closed: '已关闭',
  rejected: '已拒绝',
}

const caseStatusEl = ref<HTMLElement | null>(null)
const casePriorityEl = ref<HTMLElement | null>(null)
const caseTypeEl = ref<HTMLElement | null>(null)
const bugStatusEl = ref<HTMLElement | null>(null)
const bugSeverityEl = ref<HTMLElement | null>(null)

function pairs(
  source: Record<string, number> | undefined,
  labels: Record<string, string> = {},
): Array<{ name: string; value: number }> {
  return Object.entries(source ?? {}).map(([key, value]) => ({
    name: labels[key] ?? key,
    value: Number(value ?? 0),
  }))
}

const caseStatusOption = computed<EChartsOption>(() => ({
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
      radius: ['42%', '66%'],
      center: ['50%', '44%'],
      itemStyle: { borderRadius: 4, borderColor: panel.value, borderWidth: 2 },
      label: { formatter: '{b}: {c}', color: text.value },
      data: pairs(props.stats?.caseStatus, CASE_STATUS_LABEL),
    },
  ],
}))

const casePriorityOption = computed<EChartsOption>(() => ({
  textStyle: { color: text.value },
  tooltip: { trigger: 'axis' },
  grid: { left: 40, right: 16, top: 24, bottom: 28 },
  xAxis: {
    type: 'category',
    data: Object.keys(props.stats?.casePriority ?? {}),
    axisLabel: { color: weak.value },
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
      name: '用例数',
      type: 'bar',
      barMaxWidth: 36,
      itemStyle: { color: primary.value, borderRadius: [4, 4, 0, 0] },
      label: { show: true, position: 'top', color: text.value },
      data: Object.values(props.stats?.casePriority ?? {}).map((value) => Number(value ?? 0)),
    },
  ],
}))

const caseTypeOption = computed<EChartsOption>(() => ({
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
      name: '用例类型',
      type: 'pie',
      radius: '62%',
      center: ['50%', '44%'],
      itemStyle: { borderRadius: 4, borderColor: panel.value, borderWidth: 2 },
      label: { formatter: '{b}: {c}', color: text.value },
      data: pairs(props.stats?.caseType, CASE_TYPE_LABEL),
    },
  ],
}))

const bugStatusOption = computed<EChartsOption>(() => {
  const entries = Object.entries(props.stats?.bugStatus ?? {})
  return {
    textStyle: { color: text.value },
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 16, top: 24, bottom: 28 },
    xAxis: {
      type: 'category',
      data: entries.map(([key]) => BUG_STATUS_LABEL[key] ?? key),
      axisLabel: { color: weak.value },
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
        barMaxWidth: 36,
        itemStyle: { color: '#e6a23c', borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top', color: text.value },
        data: entries.map(([, value]) => Number(value ?? 0)),
      },
    ],
  }
})

const bugSeverityOption = computed<EChartsOption>(() => ({
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
      name: '缺陷级别',
      type: 'pie',
      radius: ['42%', '66%'],
      center: ['50%', '44%'],
      itemStyle: { borderRadius: 4, borderColor: panel.value, borderWidth: 2 },
      label: { formatter: '{b}: {c}', color: text.value },
      data: pairs(props.stats?.bugSeverity),
    },
  ],
}))

useEchart(caseStatusEl, caseStatusOption)
useEchart(casePriorityEl, casePriorityOption)
useEchart(caseTypeEl, caseTypeOption)
useEchart(bugStatusEl, bugStatusOption)
useEchart(bugSeverityEl, bugSeverityOption)
</script>

<style scoped>
.report-charts__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}

.report-charts__cell {
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  padding: 10px 12px 12px;
  min-width: 0;
}

.report-charts__title {
  font-size: 13px;
  color: var(--aitp-text-weak);
  margin-bottom: 6px;
}

/* 固定 min-height，容器高度为 0 时 ECharts 会画不出来 */
.report-charts__chart {
  height: 260px;
  min-height: 260px;
  width: 100%;
}
</style>
