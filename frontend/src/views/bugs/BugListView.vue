<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">缺陷</h2>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建缺陷</el-button>
    </div>

    <div class="panel panel-pad">
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetcher"
        :search-fields="searchFields"
        :extra-query="extraQuery"
        @row-click="onRowClick"
      >
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
            <el-option v-for="item in bugTypeOptions" :key="item" :label="item" :value="item" />
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
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import { bugApi, moduleApi } from '@/api'
import type { Bug, ModuleOption } from '@/api/types'
import { useAppStore } from '@/stores/app'

const router = useRouter()
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

const bugTypeOptions = ['functional', 'interface', 'boundary', 'security', 'ui', 'exception', 'performance', 'other']

const moduleOptions = ref<ModuleOption[]>([])

const extraQuery = computed(() => ({ projectId: appStore.currentProjectId ?? undefined }))

const columns: ColumnDef[] = [
  { prop: 'bugNo', label: '编号', width: 100 },
  { prop: 'title', label: '标题', minWidth: 220 },
  { prop: 'severity', label: '严重度', width: 90, slot: 'severity' },
  { prop: 'status', label: '状态', width: 100, slot: 'status' },
  { prop: 'assignee', label: '指派', width: 100 },
  { prop: 'moduleId', label: '功能点', minWidth: 140, slot: 'moduleId' },
  { prop: 'id', label: '操作', width: 130, slot: 'actions' },
]

const searchFields: SearchField[] = [
  { prop: 'keyword', label: '标题 / 编号' },
  { prop: 'status', label: '状态', type: 'select', options: bugStatusOptions },
  { prop: 'severity', label: '严重度', type: 'select', options: ['P0', 'P1', 'P2', 'P3'].map((p) => ({ label: p, value: p })) },
]

function fetcher(query: Record<string, unknown>) {
  return bugApi.list(query)
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
  Object.assign(form, {
    title: '',
    projectId: appStore.currentProjectId,
    moduleId: null,
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
  tableRef.value?.reload()
}, { immediate: true })
</script>
