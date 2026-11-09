<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">{{ report?.title ?? '报告详情' }}</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Back" @click="router.push('/reports')">返回列表</el-button>
        <el-button :icon="Edit" @click="openEdit">编辑</el-button>
        <el-button
          v-if="report && report.status !== 'published'"
          type="success"
          :icon="Promotion"
          :loading="changingStatus"
          @click="changeStatus('published')"
        >
          发布
        </el-button>
        <el-button
          v-if="report && report.status !== 'archived'"
          :icon="Box"
          :loading="changingStatus"
          @click="changeStatus('archived')"
        >
          归档
        </el-button>
        <el-button
          v-if="report?.status === 'archived'"
          :loading="changingStatus"
          @click="changeStatus('draft')"
        >
          退回草稿
        </el-button>
        <el-button :icon="Refresh" :loading="statsLoading" @click="refreshAll">刷新数据</el-button>
      </div>
    </div>

    <div class="report-metrics">
      <MetricCard
        label="用例总数"
        :value="metrics.caseTotal"
        icon="Document"
        tone="primary"
      />
      <MetricCard
        label="用例通过率"
        :value="metrics.passRate * 100"
        :precision="2"
        suffix="%"
        icon="CircleCheck"
        tone="success"
      />
      <MetricCard
        label="失败用例"
        :value="metrics.failed"
        icon="CircleClose"
        tone="danger"
      />
      <MetricCard
        label="未闭环缺陷"
        :value="metrics.openBugs"
        icon="Warning"
        tone="warning"
        :hint="`缺陷总数 ${metrics.bugTotal}`"
      />
    </div>

    <div class="panel panel-pad">
      <div class="section-title">质量图表</div>
      <ReportCharts :stats="stats" />
    </div>

    <div v-if="report" class="panel panel-pad">
      <ReportTrendPanel
        ref="trendPanel"
        :project-id="report.projectId"
        :report-id="report.id"
        @inserted="onTrendInserted"
      />
    </div>

    <div v-loading="loading" class="report-layout">
      <div class="panel panel-pad report-main">
        <el-descriptions v-if="report" :column="3" border size="small">
          <el-descriptions-item label="编号">{{ report.reportNo ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ reportTypeLabel(report.reportType) }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ statusLabel(report.status) }}</el-descriptions-item>
          <el-descriptions-item label="统计开始">{{ report.periodStart || '-' }}</el-descriptions-item>
          <el-descriptions-item label="统计结束">{{ report.periodEnd || '-' }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ dateTimeText(report.updatedAt) }}</el-descriptions-item>
        </el-descriptions>

        <el-divider />

        <div v-if="items.length" class="report-items">
          <div v-for="item in items" :key="item.id" class="report-item">
            <div class="report-item__head">
              <el-tag size="small" effect="plain">{{ item.section }}</el-tag>
              <span class="report-item__title">{{ item.title }}</span>
            </div>
            <MarkdownView v-if="item.content" :source="item.content" />
          </div>
        </div>

        <MarkdownView v-if="report?.content" :source="report.content" />
        <el-empty v-if="!report?.content && !items.length" description="报告还没有正文" :image-size="80" />
      </div>

      <div class="panel panel-pad report-side">
        <h3>章节</h3>
        <div class="report-sections">
          <el-checkbox
            v-for="section in reportSections"
            :key="section.value"
            v-model="section.enabled"
            :label="section.label"
          />
        </div>
        <el-button type="primary" size="small" :loading="savingItems" @click="saveItems">
          保存章节
        </el-button>

        <el-divider />

        <h3>导出</h3>
        <div class="report-exports">
          <el-button size="small" @click="openExport('md')">Markdown</el-button>
          <el-button size="small" @click="openExport('html')">HTML</el-button>
          <el-button size="small" :loading="exportingPdf" @click="openExport('pdf')">PDF</el-button>
          <el-button size="small" @click="openExport('print')">打印视图</el-button>
        </div>
      </div>
    </div>

    <div v-loading="samplesLoading" class="panel panel-pad">
      <div class="section-title">
        失败样本与执行证据
        <el-tag size="small" effect="plain">范围内失败 {{ sampleTotal }} 条</el-tag>
        <el-tag size="small" effect="plain">有证据 {{ evidenceCount }} 条</el-tag>
        <el-tag v-if="sampleSelected.length" size="small" type="warning" effect="plain">
          已保存所选 {{ sampleSelected.length }} 条
        </el-tag>
      </div>
      <p class="samples-tip">
        这里只列本报告统计范围内真实失败的轮次用例。勾选后保存，报告预览与 Markdown / HTML / PDF
        导出只带这些样本的证据；不保存或清空选择则按全量失败样本导出。
      </p>
      <el-table
        ref="samplesTable"
        :data="samples"
        size="small"
        border
        max-height="420"
        row-key="runCaseId"
        @selection-change="onSampleChange"
      >
        <el-table-column type="selection" width="42" reserve-selection />
        <el-table-column prop="caseNo" label="用例编号" width="110" show-overflow-tooltip />
        <el-table-column prop="title" label="失败用例" min-width="170" show-overflow-tooltip />
        <el-table-column prop="module" label="模块" width="120" show-overflow-tooltip />
        <el-table-column label="结论" width="76">
          <template #default="scope">
            <el-tag :type="scope.row.result === 'failed' ? 'danger' : 'warning'" size="small" effect="plain">
              {{ scope.row.resultLabel || scope.row.result }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="失败要点" min-width="200" show-overflow-tooltip>
          <template #default="scope">{{ scope.row.failurePoints.join('；') || '—' }}</template>
        </el-table-column>
        <el-table-column label="证据" width="96">
          <template #default="scope">
            <span v-if="scope.row.hasEvidence" class="ev-ok">EV-{{ scope.row.evidenceId }}</span>
            <el-tooltip v-else content="这条样本没有执行证据快照，通常是手工判定失败且没有留痕" placement="top">
              <span class="ev-none">无快照</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="关联缺陷" width="110" show-overflow-tooltip>
          <template #default="scope">{{ scope.row.bugNo || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="104" fixed="right">
          <template #default="scope">
            <el-button
              v-if="scope.row.hasEvidence"
              link
              type="primary"
              size="small"
              @click="openEvidence(scope.row)"
            >
              查看证据
            </el-button>
            <span v-else class="ev-none">—</span>
          </template>
        </el-table-column>
        <template #empty>
          <span class="samples-tip">{{ sampleHint }}</span>
        </template>
      </el-table>
      <div class="samples-actions">
        <el-button size="small" :loading="samplesLoading" @click="loadSamples">刷新样本</el-button>
        <el-button size="small" type="primary" :loading="savingSamples" @click="saveSelection">
          保存所选（{{ picked.length }}）
        </el-button>
        <el-button size="small" :loading="savingSamples" @click="clearSelection">回到全量</el-button>
      </div>
    </div>

    <EvidenceDrawer v-model="evidenceVisible" :locator="evidenceLocator" title="失败样本执行证据" />

    <el-dialog v-model="editVisible" title="编辑报告" width="720px">
      <el-form label-width="90px">
        <el-form-item label="标题">
          <el-input v-model="editForm.title" />
        </el-form-item>
        <el-form-item label="统计开始">
          <el-input v-model="editForm.periodStart" placeholder="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="统计结束">
          <el-input v-model="editForm.periodEnd" placeholder="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="正文">
          <el-input v-model="editForm.content" type="textarea" :rows="14" placeholder="支持 Markdown" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, Box, Edit, Promotion, Refresh } from '@element-plus/icons-vue'
import MarkdownView from '@/components/MarkdownView.vue'
import MetricCard from '@/components/MetricCard.vue'
import EvidenceDrawer from '@/components/EvidenceDrawer.vue'
import ReportCharts from '@/components/ReportCharts.vue'
import ReportTrendPanel from '@/components/ReportTrendPanel.vue'
import { reportApi } from '@/api'
import { dateTimeText } from '@/utils/format'
import type { Report, ReportFailureSample, ReportFailureSamples, ReportItem, ReportStats } from '@/api/types'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const savingItems = ref(false)
const statsLoading = ref(false)
const exportingPdf = ref(false)
const report = ref<Report | null>(null)
const trendPanel = ref<InstanceType<typeof ReportTrendPanel> | null>(null)
const items = ref<ReportItem[]>([])
const stats = ref<ReportStats | null>(null)

const metrics = computed(
  () =>
    stats.value?.metrics ?? {
      caseTotal: 0,
      passed: 0,
      failed: 0,
      blocked: 0,
      notExecuted: 0,
      passRate: 0,
      bugTotal: 0,
      openBugs: 0,
    },
)

const samples = ref<ReportFailureSample[]>([])
const sampleTotal = ref(0)
const sampleSelected = ref<number[]>([])
const sampleHint = ref('本次统计范围内没有失败样本，报告不附带执行证据。')
const samplesLoading = ref(false)
const savingSamples = ref(false)
const picked = ref<ReportFailureSample[]>([])
const samplesTable = ref<{ clearSelection: () => void; toggleRowSelection: (row: ReportFailureSample, selected: boolean) => void } | null>(null)
const evidenceVisible = ref(false)
const evidenceLocator = ref<Record<string, string | number>>({})

const evidenceCount = computed(() => samples.value.filter((item) => item.hasEvidence).length)

function applySamples(data: ReportFailureSamples): void {
  samples.value = data.list ?? []
  sampleTotal.value = Number(data.total ?? 0)
  sampleSelected.value = (data.selected ?? []).map((id) => Number(id))
  sampleHint.value = data.hint || '本次统计范围内没有失败样本，报告不附带执行证据。'
  picked.value = []
  // 后端保存过的「所选样本」要能回显：先清空再按 runCaseId 勾回，避免刷新后选择丢失
  samplesTable.value?.clearSelection()
  const wanted = new Set(sampleSelected.value)
  samples.value
    .filter((row) => wanted.has(Number(row.runCaseId)))
    .forEach((row) => samplesTable.value?.toggleRowSelection(row, true))
}

async function loadSamples(): Promise<void> {
  if (!report.value) {
    return
  }
  samplesLoading.value = true
  try {
    applySamples(await reportApi.failureSamples(report.value.id))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败样本失败')
  } finally {
    samplesLoading.value = false
  }
}

function onSampleChange(rows: ReportFailureSample[]): void {
  picked.value = rows
}

async function selectSamples(ids: number[], successText: string): Promise<void> {
  if (!report.value) {
    return
  }
  savingSamples.value = true
  try {
    applySamples(await reportApi.selectSamples(report.value.id, ids))
    ElMessage.success(successText)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存所选样本失败')
  } finally {
    savingSamples.value = false
  }
}

function saveSelection(): void {
  if (!picked.value.length) {
    ElMessage.warning('先勾选要保存的失败样本；恢复全量口径请点「回到全量」')
    return
  }
  void selectSamples(
    picked.value.map((row) => Number(row.runCaseId)),
    `已保存 ${picked.value.length} 条所选样本，预览与导出只带这些证据`,
  )
}

function clearSelection(): void {
  void selectSamples([], '已回到全量失败样本口径')
}

function openEvidence(row: ReportFailureSample): void {
  // 有证据号按证据取，没有就按轮次用例取：查不到时抽屉给中文降级，不弹错
  evidenceLocator.value = row.evidenceId
    ? { evidenceId: Number(row.evidenceId) }
    : { runCaseId: Number(row.runCaseId) }
  evidenceVisible.value = true
}

const editVisible = ref(false)
const editForm = reactive({ title: '', periodStart: '', periodEnd: '', content: '' })
const changingStatus = ref(false)

const TYPE_LABELS: Record<string, string> = {
  summary: '测试总结',
  iteration: '迭代报告',
  regression: '回归报告',
  acceptance: '验收报告',
}

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  archived: '已归档',
}

function reportTypeLabel(value: string | undefined): string {
  return value ? TYPE_LABELS[value] ?? value : '-'
}

function statusLabel(value: string | undefined): string {
  return value ? STATUS_LABELS[value] ?? value : '-'
}

async function changeStatus(next: 'draft' | 'published' | 'archived'): Promise<void> {
  if (!report.value) {
    return
  }
  if (next !== 'draft') {
    const tips: Record<string, string> = {
      published: '发布后代表本轮报告已定稿，是否继续？',
      archived: '归档后报告会从默认视图淡出，可在状态筛选「已归档」中找回，是否继续？',
    }
    try {
      await ElMessageBox.confirm(tips[next], next === 'published' ? '发布确认' : '归档确认', {
        type: 'warning',
      })
    } catch {
      return
    }
  }
  changingStatus.value = true
  try {
    report.value = await reportApi.update(report.value.id, { status: next })
    ElMessage.success(`已${next === 'draft' ? '退回草稿' : next === 'published' ? '发布' : '归档'}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '状态更新失败')
  } finally {
    changingStatus.value = false
  }
}

interface SectionOption {
  value: string
  label: string
  enabled: boolean
}

const sectionOptions: Array<{ value: string; label: string }> = [
  { value: 'overview', label: '总览' },
  { value: 'progress', label: '进展' },
  { value: 'case', label: '用例情况' },
  { value: 'bug', label: '缺陷情况' },
  { value: 'coverage', label: '覆盖情况' },
  { value: 'trend', label: '趋势' },
  { value: 'risk', label: '风险' },
  { value: 'conclusion', label: '结论与建议' },
]

const reportSections = ref<SectionOption[]>(sectionOptions.map((item) => ({ ...item, enabled: false })))

const enabledSections = computed(() => reportSections.value.filter((item) => item.enabled).map((item) => item.value))

async function load(): Promise<void> {
  loading.value = true
  try {
    const detail = (await reportApi.get(Number(route.params.id))) as Report & { items?: ReportItem[] }
    report.value = detail
    items.value = detail.items ?? []
    reportSections.value = sectionOptions.map((item) => ({
      ...item,
      enabled: items.value.some((entry) => entry.section === item.value),
    }))
    void loadSamples()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载报告失败')
  } finally {
    loading.value = false
  }
}

async function loadStats(): Promise<void> {
  statsLoading.value = true
  try {
    stats.value = await reportApi.stats(Number(route.params.id))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载图表数据失败')
  } finally {
    statsLoading.value = false
  }
}

async function refreshAll(): Promise<void> {
  await Promise.all([loadStats(), trendPanel.value?.refresh()])
}

function onTrendInserted(next: ReportItem[] | unknown[]): void {
  items.value = next as ReportItem[]
  reportSections.value = sectionOptions.map((item) => ({
    ...item,
    enabled: items.value.some((entry) => entry.section === item.value),
  }))
}

function openEdit(): void {
  if (!report.value) {
    return
  }
  Object.assign(editForm, {
    title: report.value.title ?? '',
    periodStart: report.value.periodStart ?? '',
    periodEnd: report.value.periodEnd ?? '',
    content: report.value.content ?? '',
  })
  editVisible.value = true
}

async function save(): Promise<void> {
  if (!report.value) {
    return
  }
  saving.value = true
  try {
    await reportApi.update(report.value.id, { ...editForm })
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function saveItems(): Promise<void> {
  if (!report.value) {
    return
  }
  const removedWithContent = items.value.filter(
    (item) => item.content && !enabledSections.value.includes(item.section),
  )
  if (removedWithContent.length) {
    try {
      await ElMessageBox.confirm(
        `取消勾选的 ${removedWithContent.length} 个章节已有正文，保存后会从报告中删除，是否继续？`,
        '删除章节确认',
        { type: 'warning' },
      )
    } catch {
      return
    }
  }
  savingItems.value = true
  try {
    const payload = enabledSections.value.map((section, index) => {
      const existing = items.value.find((item) => item.section === section)
      return {
        section,
        title: existing?.title ?? '',
        content: existing?.content ?? '',
        metric: existing?.metric ?? {},
        sortOrder: index,
      }
    })
    await reportApi.setItems(report.value.id, payload)
    ElMessage.success('章节已保存')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    savingItems.value = false
  }
}

/** 本机没有可用中文字体时的降级说明：Windows/macOS 自带，Linux 需装 fonts-noto-cjk。 */
const PDF_FONT_WARNING =
  '本机没找到可用中文字体，PDF 已按英文字体导出，中文可能显示为方块。' +
  'Windows 与 macOS 一般自带（黑体 / 微软雅黑 / 苹方），Linux 请安装中文字体，' +
  '如 sudo apt install fonts-noto-cjk 或 sudo dnf install google-noto-sans-cjk-fonts，装好无需重启，重新导出即可。'

async function openExport(format: 'md' | 'html' | 'pdf' | 'print'): Promise<void> {
  if (!report.value) {
    return
  }
  if (format !== 'pdf') {
    window.open(reportApi.exportUrl(report.value.id, format), '_blank')
    return
  }
  // PDF 必须走 fetch：window.open 读不到响应头，字体降级就无从提示
  exportingPdf.value = true
  try {
    const result = await reportApi.exportBlob(report.value.id, 'pdf')
    const objectUrl = URL.createObjectURL(result.blob)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = result.filename || `${report.value.title || '测试报告'}.pdf`
    link.click()
    URL.revokeObjectURL(objectUrl)
    if (result.fontWarning) {
      ElMessage.warning(PDF_FONT_WARNING)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : 'PDF 导出失败')
  } finally {
    exportingPdf.value = false
  }
}

onMounted(() => {
  void load()
  void loadStats()
})
</script>

<style scoped>
.report-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.report-layout {
  display: grid;
  grid-template-columns: 1fr 280px;
  gap: 12px;
  align-items: start;
}

.report-main {
  min-height: 420px;
}

.report-side h3 {
  margin: 0 0 10px;
  font-size: 15px;
}

.report-sections {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 12px;
}

.report-exports {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.report-item {
  margin-bottom: 14px;
}

.report-item__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.report-item__title {
  font-weight: 600;
}

.samples-tip {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.samples-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.ev-ok {
  font-size: 12px;
  color: var(--el-color-success);
}

.ev-none {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}
</style>
