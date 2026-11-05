<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">缺陷</h2>
      <div>
        <BaseExportMenu :resolve-url="resolveExportUrl" />
        <el-button type="primary" :icon="Plus" @click="openCreate">新建缺陷</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetcher"
        :search-fields="searchFields"
        :extra-query="extraQuery"
        selectable
        sync-query
        @row-click="onRowClick"
      >
        <template #batch="{ rows }">
          <el-button size="small" type="danger" plain :loading="batchRemoving" @click="batchRemove(rows)">
            批量删除
          </el-button>
        </template>
        <template #severity="{ row }">
          <el-tag :type="severityTag(row.severity)" size="small" effect="plain">{{ row.severity }}</el-tag>
        </template>
        <template #status="{ row }">
          <el-tag :type="statusTag(row.status)" size="small" effect="plain">{{ statusLabel(row.status) }}</el-tag>
        </template>
        <template #moduleId="{ row }">
          {{ moduleName(row.moduleId) }}
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button>
          <el-button link type="success" @click.stop="openKnowledge(row)">加入知识库</el-button>
          <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
        </template>
      </BaseProTable>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑缺陷' : '新建缺陷'" width="680px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item label="项目" required>
          <el-select v-model="form.projectId">
            <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="功能点">
          <el-select v-model="form.moduleId" clearable filterable placeholder="选择功能点">
            <el-option v-for="item in moduleOptions" :key="item.id" :label="item.path" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="项目版本">
          <el-select v-model="form.versionId" clearable placeholder="选择版本" style="width: 100%">
            <el-option v-for="item in formVersionOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="严重程度">
          <el-select v-model="form.severity">
            <el-option v-for="item in ['P0', 'P1', 'P2', 'P3']" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status">
            <el-option v-for="item in bugStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="缺陷类型">
          <el-select v-model="form.bugType">
            <el-option v-for="item in bugTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="指派人">
          <el-input v-model="form.assignee" />
        </el-form-item>
        <el-form-item label="版本 / 环境">
          <el-input v-model="form.version" placeholder="版本" style="width: 48%" />
          <el-input v-model="form.env" placeholder="环境" style="width: 48%; margin-left: 2%" />
        </el-form-item>
        <el-form-item label="复现步骤">
          <el-input v-model="form.stepsText" type="textarea" :rows="4" placeholder="每行一步" />
        </el-form-item>
        <el-form-item label="实际结果">
          <el-input v-model="form.actualResult" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="预期结果">
          <el-input v-model="form.expectedResult" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>

    <AddToKnowledgeDialog
      v-model="knowledgeVisible"
      :title="knowledgePayload.title"
      :content="knowledgePayload.content"
      ref-type="bug"
      :ref-id="knowledgePayload.refId"
      :ref-label="knowledgePayload.refLabel"
      :module-id="knowledgePayload.moduleId"
    />
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import BaseExportMenu from '@/components/BaseExportMenu.vue'
import AddToKnowledgeDialog from '@/components/AddToKnowledgeDialog.vue'
import { bugApi, moduleApi, projectVersionApi } from '@/api'
import type { Bug, ModuleOption } from '@/api/types'
import { useAppStore } from '@/stores/app'

const router = useRouter()

const knowledgeVisible = ref(false)
const knowledgePayload = reactive({
  title: '',
  content: '',
  refId: null as number | null,
  refLabel: '',
  moduleId: null as number | null,
})

/** 缺陷正文整理成 Markdown：描述 → 复现步骤 → 预期/实际。 */
function bugMarkdown(row: Bug): string {
  const lines = [`# ${row.title}`]
  if (row.description) lines.push(row.description)
  if (row.steps?.length) {
    lines.push('**复现步骤**')
    row.steps.forEach((step, index) => lines.push(`${index + 1}. ${step}`))
  }
  if (row.expectedResult) lines.push(`**预期结果**：${row.expectedResult}`)
  if (row.actualResult) lines.push(`**实际结果**：${row.actualResult}`)
  if (row.env) lines.push(`**环境**：${row.env}`)
  if (row.version) lines.push(`**版本**：${row.version}`)
  return lines.join('\n\n')
}

function openKnowledge(row: Bug): void {
  knowledgePayload.title = row.title
  knowledgePayload.content = bugMarkdown(row)
  knowledgePayload.refId = Number(row.id)
  knowledgePayload.refLabel = row.title
  knowledgePayload.moduleId = row.moduleId ?? null
  knowledgeVisible.value = true
}
const appStore = useAppStore()
const tableRef = ref<TableExpose | null>(null)

const bugStatusOptions = [
  { label: '新建', value: 'open' },
  { label: '已指派', value: 'assigned' },
  { label: '已修复', value: 'fixed' },
  { label: '待验证', value: 'retest' },
  { label: '重新打开', value: 'reopened' },
  { label: '已关闭', value: 'closed' },
  { label: '已拒绝', value: 'rejected' },
]

const bugTypeOptions = [
  { label: '功能', value: 'functional' },
  { label: '接口', value: 'interface' },
  { label: '边界', value: 'boundary' },
  { label: '安全', value: 'security' },
  { label: 'UI', value: 'ui' },
  { label: '异常', value: 'exception' },
  { label: '性能', value: 'performance' },
  { label: '其他', value: 'other' },
]

const moduleOptions = ref<ModuleOption[]>([])

const extraQuery = computed(() => ({
  projectId: appStore.currentProjectId ?? undefined,
  versionId: selectedVersionId.value ?? undefined,
}))

/** 当前项目下的版本清单，供版本下拉筛选。 */
const versionOptions = ref<Array<{ label: string; value: number }>>([])
const selectedVersionId = ref<number | null>(null)

async function loadVersionOptions(): Promise<void> {
  const pid = appStore.currentProjectId
  if (!pid) {
    versionOptions.value = []
    return
  }
  try {
    const result = await projectVersionApi.list(pid)
    versionOptions.value = result.list.map((v) => ({
      label: v.versionCode + (v.name ? ` ${v.name}` : ''),
      value: v.id as number,
    }))
  } catch {
    versionOptions.value = []
  }
}

const columns: ColumnDef[] = [
  { prop: 'bugNo', label: '编号', width: 100 },
  { prop: 'title', label: '标题', minWidth: 220 },
  { prop: 'severity', label: '严重度', width: 90, slot: 'severity' },
  { prop: 'status', label: '状态', width: 100, slot: 'status' },
  { prop: 'assignee', label: '指派', width: 100 },
  { prop: 'moduleId', label: '功能点', minWidth: 140, slot: 'moduleId' },
  { prop: 'versionCode', label: '版本号', width: 110 },
  { prop: 'versionName', label: '版本名称', width: 120 },
  { prop: 'id', label: '操作', width: 210, slot: 'actions' },
]

const searchFields = computed<SearchField[]>(() => [
  { prop: 'keyword', label: '标题 / 编号' },
  { prop: 'status', label: '状态', type: 'select', options: bugStatusOptions },
  { prop: 'severity', label: '严重度', type: 'select', options: ['P0', 'P1', 'P2', 'P3'].map((p) => ({ label: p, value: p })) },
  { prop: 'versionId', label: '版本', type: 'select', options: versionOptions.value },
])

function fetcher(query: Record<string, unknown>) {
  return bugApi.list(query)
}

function resolveExportUrl(format: string): string {
  return bugApi.exportUrl({
    projectId: appStore.currentProjectId,
    format,
    ...tableRef.value?.filterQuery(),
  })
}

function statusLabel(value: string): string {
  return bugStatusOptions.find((item) => item.value === value)?.label ?? value
}

function severityTag(value: string): 'danger' | 'warning' | 'info' | 'primary' {
  if (value === 'P0') {
    return 'danger'
  }
  if (value === 'P1') {
    return 'warning'
  }
  if (value === 'P2') {
    return 'info'
  }
  return 'primary'
}

function statusTag(value: string): 'success' | 'danger' | 'warning' | 'info' | 'primary' {
  if (value === 'closed') {
    return 'success'
  }
  if (value === 'rejected') {
    return 'info'
  }
  if (value === 'fixed') {
    return 'primary'
  }
  if (value === 'reopened') {
    return 'danger'
  }
  return 'warning'
}

function moduleName(id: number | null): string {
  if (!id) {
    return '-'
  }
  return moduleOptions.value.find((item) => item.id === id)?.name ?? String(id)
}

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<Bug | null>(null)
const form = reactive({
  title: '',
  projectId: null as number | null,
  moduleId: null as number | null,
  versionId: null as number | null,
  severity: 'P2',
  status: 'open',
  bugType: 'functional',
  assignee: '',
  version: '',
  env: '',
  stepsText: '',
  actualResult: '',
  expectedResult: '',
  description: '',
})

function openCreate(): void {
  editing.value = null
  const fq = tableRef.value?.filterQuery() ?? {}
  pendingVersionId.value = fq.versionId ? Number(fq.versionId) : null
  Object.assign(form, {
    title: '',
    projectId: appStore.currentProjectId,
    moduleId: null,
    versionId: null,
    severity: 'P2',
    status: 'open',
    bugType: 'functional',
    assignee: '',
    version: '',
    env: '',
    stepsText: '',
    actualResult: '',
    expectedResult: '',
    description: '',
  })
  dialogVisible.value = true
}

async function openEdit(row: Bug): Promise<void> {
  try {
    const detail = await bugApi.get(row.id)
    editing.value = detail
    pendingVersionId.value = (detail as any).versionId ?? null
    Object.assign(form, {
      title: detail.title ?? '',
      projectId: detail.projectId ?? null,
      moduleId: detail.moduleId ?? null,
      severity: detail.severity ?? 'P2',
      status: detail.status ?? 'open',
      bugType: detail.bugType ?? 'functional',
      assignee: detail.assignee ?? '',
      version: detail.version ?? '',
      env: detail.env ?? '',
      stepsText: (detail.steps ?? []).map((s) => String(s)).join('\n'),
      actualResult: detail.actualResult ?? '',
      expectedResult: detail.expectedResult ?? '',
      description: detail.description ?? '',
    })
    dialogVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载缺陷失败')
  }
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
    const steps = form.stepsText.split('\n').map((line) => line.trim()).filter(Boolean)
    const payload = {
      ...form,
      steps,
      stepsText: undefined as unknown as string,
    }
    if (editing.value) {
      await bugApi.update(editing.value.id, payload)
    } else {
      await bugApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: Bug): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除缺陷「${row.title}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await bugApi.remove(row.id)
    ElMessage.success('已删除')
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

function onRowClick(row: Bug): void {
  void router.push(`/bugs/${row.id}`)
}

const batchRemoving = ref(false)

async function batchRemove(rows: Record<string, any>[]): Promise<void> {
  const ids = rows.map((row) => Number(row.id)).filter((id) => Number.isInteger(id) && id > 0)
  if (!ids.length) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${ids.length} 个缺陷？未闭环缺陷清零会影响首页指标。`,
      '批量删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  batchRemoving.value = true
  try {
    const result = await bugApi.batchRemove(ids)
    const skipped = result.skipped?.length ? `，跳过 ${result.skipped.length} 条` : ''
    ElMessage.success(`已删除 ${result.count} 个${skipped}`)
    tableRef.value?.clearSelection()
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量删除失败')
  } finally {
    batchRemoving.value = false
  }
}

async function loadOptions(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) {
    moduleOptions.value = []
    return
  }
  try {
    moduleOptions.value = await moduleApi.options(projectId, true)
  } catch {
    moduleOptions.value = []
  }
}

watch(() => appStore.currentProjectId, () => {
  void loadOptions()
  void loadVersionOptions()
  tableRef.value?.reload()
}, { immediate: true })

/** 表单里的版本下拉跟随表单中选的项目。 */
const formVersionOptions = ref<Array<{ label: string; value: number }>>([])
const formVersionProjectId = ref<number | null>(null)
const pendingVersionId = ref<number | null>(null)

watch(() => form.projectId, async (pid) => {
  form.versionId = pendingVersionId.value
  pendingVersionId.value = null
  if (!pid) {
    formVersionOptions.value = []
    formVersionProjectId.value = null
    return
  }
  formVersionProjectId.value = pid
  try {
    const result = await projectVersionApi.list(pid)
    if (formVersionProjectId.value === pid) {
      formVersionOptions.value = result.list.map((v) => ({
        label: v.versionCode + (v.name ? ` ${v.name}` : ''),
        value: v.id as number,
      }))
    }
  } catch {
    formVersionOptions.value = []
  }
}, { immediate: true })
</script>
