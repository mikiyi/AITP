<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">{{ report?.title ?? '报告详情' }}</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Back" @click="router.push('/reports')">返回列表</el-button>
        <el-button :icon="Edit" @click="openEdit">编辑</el-button>
        <el-button :icon="Refresh" :loading="statsLoading" @click="loadStats">刷新数据</el-button>
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
        :precision="1"
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

    <div v-loading="loading" class="report-layout">
      <div class="panel panel-pad report-main">
        <el-descriptions v-if="report" :column="3" border size="small">
          <el-descriptions-item label="编号">{{ report.reportNo ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ report.reportType ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ report.status ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="统计开始">{{ report.periodStart || '-' }}</el-descriptions-item>
          <el-descriptions-item label="统计结束">{{ report.periodEnd || '-' }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ report.updatedAt ?? '-' }}</el-descriptions-item>
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
          <el-button size="small" @click="openExport('pdf')">PDF</el-button>
          <el-button size="small" @click="openExport('print')">打印视图</el-button>
        </div>
      </div>
    </div>

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
import { ElMessage } from 'element-plus'
import { Back, Edit, Refresh } from '@element-plus/icons-vue'
import MarkdownView from '@/components/MarkdownView.vue'
import MetricCard from '@/components/MetricCard.vue'
import ReportCharts from '@/components/ReportCharts.vue'
import { reportApi } from '@/api'
import type { Report, ReportItem, ReportStats } from '@/api/types'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const savingItems = ref(false)
const statsLoading = ref(false)
const report = ref<Report | null>(null)
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

const editVisible = ref(false)
const editForm = reactive({ title: '', periodStart: '', periodEnd: '', content: '' })

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

function openExport(format: 'md' | 'html' | 'pdf' | 'print'): void {
  if (!report.value) {
    return
  }
  window.open(reportApi.exportUrl(report.value.id, format), '_blank')
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
</style>
