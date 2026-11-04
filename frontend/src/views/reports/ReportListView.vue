<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">测试报告</h2>
      <el-button :icon="MagicStick" :loading="generating" @click="openGenerate">按实时数据生成</el-button>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建报告</el-button>
    </div>

    <div class="panel panel-pad">
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetcher"
        :search-fields="searchFields"
        :extra-query="extraQuery"
        sync-query
        @row-click="onRowClick"
      >
        <template #reportType="{ row }">{{ typeLabel(row.reportType) }}</template>
        <template #status="{ row }">
          <el-tag :type="row.status === 'published' ? 'success' : 'info'" size="small" effect="plain">
            {{ row.status === 'published' ? '已发布' : '草稿' }}
          </el-tag>
        </template>
        <template #period="{ row }">
          {{ row.periodStart || '-' }} ~ {{ row.periodEnd || '-' }}
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="onRowClick(row)">查看</el-button>
          <el-button link type="success" @click.stop="openKnowledge(row)">加入知识库</el-button>
          <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
        </template>
      </BaseProTable>
    </div>

    <el-dialog v-model="dialogVisible" title="新建报告" width="560px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item label="项目" required>
          <el-select v-model="form.projectId">
            <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="报告类型">
          <el-select v-model="form.reportType">
            <el-option v-for="item in typeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="统计周期">
          <el-date-picker
            v-model="period"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始"
            end-placeholder="结束"
          />
        </el-form-item>
        <el-form-item label="正文">
          <el-input v-model="form.content" type="textarea" :rows="8" placeholder="支持 Markdown" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">创建</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="generateVisible" title="按实时数据生成报告" width="560px">
      <el-form label-width="90px">
        <el-form-item label="项目" required>
          <el-select v-model="generateForm.projectId">
            <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="报告类型">
          <el-select v-model="generateForm.reportType">
            <el-option v-for="item in typeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="统计周期">
          <el-date-picker
            v-model="generatePeriod"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始"
            end-placeholder="结束"
          />
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="章节与指标取自项目当前用例、缺陷的实时统计，生成后为草稿，发布前请人工复核"
      />
      <template #footer>
        <el-button @click="generateVisible = false">取消</el-button>
        <el-button type="primary" :loading="generating" @click="confirmGenerate">生成</el-button>
      </template>
    </el-dialog>
  </div>

    <AddToKnowledgeDialog
      v-model="knowledgeVisible"
      :title="knowledgePayload.title"
      :content="knowledgePayload.content"
      ref-type="report"
      :ref-id="knowledgePayload.refId"
      :ref-label="knowledgePayload.refLabel"
    />
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { MagicStick, Plus } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import AddToKnowledgeDialog from '@/components/AddToKnowledgeDialog.vue'
import { reportApi } from '@/api'
import type { Report } from '@/api/types'
import { useAppStore } from '@/stores/app'

const router = useRouter()

const knowledgeVisible = ref(false)
const knowledgePayload = reactive({
  title: '',
  content: '',
  refId: null as number | null,
  refLabel: '',
})

function openKnowledge(row: Report): void {
  knowledgePayload.title = row.title
  knowledgePayload.content = [`# ${row.title}`, row.summary || row.content || ''].filter(Boolean).join('\n\n')
  knowledgePayload.refId = Number(row.id)
  knowledgePayload.refLabel = row.title
  knowledgeVisible.value = true
}
const appStore = useAppStore()
const tableRef = ref<TableExpose | null>(null)

const typeOptions = [
  { label: '测试总结', value: 'summary' },
  { label: '迭代报告', value: 'iteration' },
  { label: '回归报告', value: 'regression' },
  { label: '验收报告', value: 'acceptance' },
]

const columns: ColumnDef[] = [
  { prop: 'reportNo', label: '编号', width: 120 },
  { prop: 'title', label: '标题', minWidth: 200 },
  { prop: 'reportType', label: '类型', width: 110, slot: 'reportType' },
  { prop: 'period', label: '统计周期', width: 200, slot: 'period' },
  { prop: 'status', label: '状态', width: 90, slot: 'status' },
  { prop: 'id', label: '操作', width: 210, slot: 'actions' },
]

const searchFields: SearchField[] = [
  { prop: 'keyword', label: '标题 / 编号' },
  { prop: 'reportType', label: '类型', type: 'select', options: typeOptions },
  { prop: 'status', label: '状态', type: 'select', options: [
    { label: '草稿', value: 'draft' },
    { label: '已发布', value: 'published' },
  ] },
]

const extraQuery = computed(() => ({ projectId: appStore.currentProjectId ?? undefined }))

function fetcher(query: Record<string, unknown>) {
  return reportApi.list(query)
}

function typeLabel(value: string): string {
  return typeOptions.find((item) => item.value === value)?.label ?? value
}

const dialogVisible = ref(false)
const saving = ref(false)
const period = ref<[string, string] | null>(null)
const form = reactive({
  title: '',
  projectId: null as number | null,
  reportType: 'iteration',
  content: '',
})

function openCreate(): void {
  Object.assign(form, {
    title: '',
    projectId: appStore.currentProjectId,
    reportType: 'iteration',
    content: '',
  })
  period.value = null
  dialogVisible.value = true
}

async function save(): Promise<void> {
  if (!form.title.trim()) {
    ElMessage.warning('标题为必填项')
    return
  }
  if (!form.projectId) {
    ElMessage.warning('请先选择项目')
    return
  }
  saving.value = true
  try {
    await reportApi.create({
      ...form,
      periodStart: period.value?.[0] ?? '',
      periodEnd: period.value?.[1] ?? '',
      status: 'draft',
    })
    ElMessage.success('创建成功')
    dialogVisible.value = false
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: Report): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除报告「${row.title}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await reportApi.remove(row.id)
    ElMessage.success('已删除')
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

function onRowClick(row: Report): void {
  void router.push(`/reports/${row.id}`)
}

const generateVisible = ref(false)
const generating = ref(false)
const generatePeriod = ref<[string, string] | null>(null)
const generateForm = reactive({
  projectId: null as number | null,
  reportType: 'iteration',
})

function openGenerate(): void {
  generateForm.projectId = appStore.currentProjectId
  generateForm.reportType = 'iteration'
  generatePeriod.value = null
  generateVisible.value = true
}

async function confirmGenerate(): Promise<void> {
  if (!generateForm.projectId) {
    ElMessage.warning('请先选择项目')
    return
  }
  generating.value = true
  try {
    const report = await reportApi.generate({
      projectId: generateForm.projectId,
      reportType: generateForm.reportType,
      periodStart: generatePeriod.value?.[0] ?? '',
      periodEnd: generatePeriod.value?.[1] ?? '',
    })
    ElMessage.success('已生成报告草稿')
    generateVisible.value = false
    void router.push(`/reports/${report.id}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '生成失败')
  } finally {
    generating.value = false
  }
}
</script>
