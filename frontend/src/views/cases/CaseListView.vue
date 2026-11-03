<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">测试用例</h2>
      <div>
        <el-button :icon="Upload" @click="importVisible = true">导入 xlsx</el-button>
        <el-button :icon="Download" @click="exportXlsx">导出 xlsx</el-button>
        <el-button :icon="DataAnalysis" @click="openCoverage">覆盖矩阵</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建用例</el-button>
      </div>
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
        <template #caseType="{ row }">
          <el-tag size="small" effect="plain">{{ caseTypeLabel(row.caseType) }}</el-tag>
        </template>
        <template #priority="{ row }">
          <el-tag :type="priorityTag(row.priority)" size="small" effect="plain">{{ row.priority }}</el-tag>
        </template>
        <template #status="{ row }">
          <el-tag :type="statusTag(row.status)" size="small" effect="plain">
            {{ statusLabel(row.status) }}
          </el-tag>
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

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑用例' : '新建用例'" width="720px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="格式：场景-条件-预期" />
        </el-form-item>
        <el-form-item label="项目" required>
          <el-select v-model="form.projectId" placeholder="选择项目">
            <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="功能点">
          <el-select v-model="form.moduleId" placeholder="选择功能点" clearable filterable>
            <el-option v-for="item in moduleOptions" :key="item.id" :label="item.path" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="需求">
          <el-select v-model="form.requirementId" placeholder="选择需求" clearable filterable>
            <el-option v-for="item in requirementOptions" :key="item.id" :label="item.title" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="用例类型">
          <el-select v-model="form.caseType">
            <el-option v-for="item in caseTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority">
            <el-option v-for="item in ['P0', 'P1', 'P2', 'P3']" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="执行状态">
          <el-select v-model="form.status">
            <el-option v-for="item in caseStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="前置条件">
          <el-input v-model="form.preconditions" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="步骤 / 预期">
          <div class="step-editor">
            <div v-for="(step, index) in form.steps" :key="index" class="step-row">
              <el-input v-model="step.action" placeholder="操作步骤" style="flex: 1" />
              <el-input v-model="step.expected" placeholder="预期结果" style="flex: 1" />
              <el-button link type="danger" @click="form.steps.splice(index, 1)">删除</el-button>
            </div>
            <el-button size="small" :icon="Plus" @click="form.steps.push({ action: '', expected: '' })">
              添加步骤
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="整体预期">
          <el-input v-model="form.expectedResult" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="form.tags" placeholder="逗号分隔" />
        </el-form-item>
        <el-form-item v-if="extraFields.length" label="扩展字段">
          <div class="extra-editor">
            <div v-for="field in extraFields" :key="field.key" class="step-row">
              <span class="extra-label">{{ field.label }}</span>
              <el-input v-model="form.extra[field.key]" :placeholder="field.placeholder" style="flex: 1" />
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="importVisible" title="导入 xlsx 用例" width="520px">
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept=".xlsx"
        :on-change="onFileChange"
        :file-list="fileList"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽 xlsx 文件到此处，或<em>点击选择</em></div>
      </el-upload>
      <p class="muted import-hint">
        导入前请确保已在「项目」中选择目标项目；编号冲突的用例会被跳过。
      </p>
      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="doImport">开始导入</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="coverageVisible" title="功能点覆盖矩阵" width="900px">
      <el-alert
        v-if="coverageRows.length"
        type="info"
        :closable="false"
        show-icon
        title="用例数以「功能点」归属统计，与用例列表中的 moduleId 一致；无覆盖的功能点用例数为 0。"
      />
      <el-table :data="coverageRows" size="small" border max-height="520" class="coverage-table">
        <el-table-column prop="path" label="功能点" min-width="260" show-overflow-tooltip />
        <el-table-column prop="caseCount" label="用例数" width="80" align="right" />
        <el-table-column prop="p0Count" label="P0" width="70" align="right" />
        <el-table-column prop="p1Count" label="P1" width="70" align="right" />
        <el-table-column prop="passCount" label="通过" width="70" align="right" />
        <el-table-column prop="failCount" label="失败" width="70" align="right" />
        <el-table-column prop="blockedCount" label="阻塞" width="70" align="right" />
        <el-table-column prop="notExecutedCount" label="未执行" width="80" align="right" />
      </el-table>
      <el-empty v-if="!coverageLoading && !coverageRows.length" description="当前项目还没有功能点数据" />
      <template #footer>
        <el-button @click="coverageVisible = false">关闭</el-button>
        <el-button type="primary" :loading="coverageLoading" @click="loadCoverage">刷新</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, DataAnalysis, Plus, Upload, UploadFilled } from '@element-plus/icons-vue'
import type { UploadFile, UploadFiles } from 'element-plus'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import { caseApi, moduleApi, requirementApi } from '@/api'
import type { CaseStep, ModuleCoverage, ModuleOption, Requirement, TestCase } from '@/api/types'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const appStore = useAppStore()
const tableRef = ref<TableExpose | null>(null)

const caseTypeOptions = [
  { label: '功能测试', value: 'functional' },
  { label: '接口测试', value: 'interface' },
  { label: '边界测试', value: 'boundary' },
  { label: '安全测试', value: 'security' },
  { label: 'UI 测试', value: 'ui' },
  { label: '异常测试', value: 'exception' },
]

const caseStatusOptions = [
  { label: '未执行', value: 'not_executed' },
  { label: '通过', value: 'pass' },
  { label: '失败', value: 'fail' },
  { label: '阻塞', value: 'blocked' },
]

interface ExtraField {
  key: string
  label: string
  placeholder: string
}

/** 各用例类型在 xlsx 里的专属列，对应导出器读取的 extra 字段名。 */
const EXTRA_FIELDS: Record<string, ExtraField[]> = {
  functional: [
    { key: '测试数据', label: '测试数据', placeholder: '' },
    { key: '执行时间', label: '执行时间', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
  interface: [
    { key: '模块/接口名', label: '模块/接口名', placeholder: '' },
    { key: '接口URL', label: '接口URL', placeholder: 'https://' },
    { key: '请求方式', label: '请求方式', placeholder: 'GET/POST' },
    { key: 'token/鉴权方式', label: '鉴权方式', placeholder: 'Bearer Token' },
    { key: '请求头', label: '请求头', placeholder: 'JSON' },
    { key: '请求参数', label: '请求参数', placeholder: 'Query 参数' },
    { key: '请求体', label: '请求体', placeholder: 'JSON 请求体' },
    { key: '预期状态码', label: '预期状态码', placeholder: '200' },
    { key: '预期响应体', label: '预期响应体', placeholder: '' },
    { key: '断言规则', label: '断言规则', placeholder: '' },
    { key: '幂等性校验', label: '幂等性校验', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
  boundary: [
    { key: '边界类型', label: '边界类型', placeholder: '如：等价类边界' },
    { key: '边界描述', label: '边界描述', placeholder: '' },
    { key: '测试数据', label: '测试数据', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
  security: [
    { key: '权限类型', label: '权限类型', placeholder: '如：越权访问' },
    { key: '测试数据', label: '测试数据', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
  ui: [
    { key: '测试数据', label: '测试数据', placeholder: '' },
    { key: '执行时间', label: '执行时间', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
  exception: [
    { key: '异常类型', label: '异常类型', placeholder: '如：网络超时' },
    { key: '测试数据', label: '测试数据', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
}

const moduleOptions = ref<ModuleOption[]>([])
const requirementOptions = ref<Requirement[]>([])

const extraQuery = computed(() => ({
  projectId: appStore.currentProjectId ?? undefined,
}))

const columns: ColumnDef[] = [
  { prop: 'caseNo', label: '编号', width: 100 },
  { prop: 'title', label: '标题', minWidth: 220 },
  { prop: 'caseType', label: '类型', width: 100, slot: 'caseType' },
  { prop: 'priority', label: '优先级', width: 90, slot: 'priority' },
  { prop: 'status', label: '执行状态', width: 100, slot: 'status' },
  { prop: 'moduleId', label: '功能点', minWidth: 160, slot: 'moduleId' },
  { prop: 'id', label: '操作', width: 130, slot: 'actions' },
]

const searchFields: SearchField[] = [
  { prop: 'keyword', label: '标题 / 编号' },
  { prop: 'caseType', label: '类型', type: 'select', options: caseTypeOptions },
  { prop: 'priority', label: '优先级', type: 'select', options: ['P0', 'P1', 'P2', 'P3'].map((p) => ({ label: p, value: p })) },
  { prop: 'status', label: '执行状态', type: 'select', options: caseStatusOptions },
]

function fetcher(query: Record<string, unknown>) {
  return caseApi.list(query)
}

function caseTypeLabel(value: string): string {
  return caseTypeOptions.find((item) => item.value === value)?.label ?? value
}

function statusLabel(value: string): string {
  return caseStatusOptions.find((item) => item.value === value)?.label ?? value
}

function priorityTag(priority: string): 'danger' | 'warning' | 'info' | 'primary' {
  if (priority === 'P0') {
    return 'danger'
  }
  if (priority === 'P1') {
    return 'warning'
  }
  if (priority === 'P2') {
    return 'info'
  }
  return 'primary'
}

function statusTag(value: string): 'success' | 'danger' | 'warning' | 'info' {
  if (value === 'pass') {
    return 'success'
  }
  if (value === 'fail') {
    return 'danger'
  }
  if (value === 'blocked') {
    return 'warning'
  }
  return 'info'
}

function moduleName(id: number | null): string {
  if (!id) {
    return '-'
  }
  return moduleOptions.value.find((item) => item.id === id)?.name ?? String(id)
}

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<TestCase | null>(null)
const form = reactive({
  title: '',
  projectId: null as number | null,
  moduleId: null as number | null,
  requirementId: null as number | null,
  caseType: 'functional',
  priority: 'P2',
  status: 'not_executed',
  preconditions: '',
  steps: [] as CaseStep[],
  expectedResult: '',
  tags: '',
  extra: {} as Record<string, string>,
})

const extraFields = computed<ExtraField[]>(() => EXTRA_FIELDS[form.caseType] ?? [])

function openCreate(): void {
  editing.value = null
  Object.assign(form, {
    title: '',
    projectId: appStore.currentProjectId,
    moduleId: null,
    requirementId: null,
    caseType: 'functional',
    priority: 'P2',
    status: 'not_executed',
    preconditions: '',
    steps: [{ action: '', expected: '' }],
    expectedResult: '',
    tags: '',
    extra: {},
  })
  dialogVisible.value = true
}

async function openEdit(row: TestCase): Promise<void> {
  try {
    const detail = await caseApi.get(row.id)
    editing.value = detail
    Object.assign(form, {
      title: detail.title ?? '',
      projectId: detail.projectId ?? null,
      moduleId: detail.moduleId ?? null,
      requirementId: detail.requirementId ?? null,
      caseType: detail.caseType ?? 'functional',
      priority: detail.priority ?? 'P2',
      status: detail.status ?? 'not_executed',
      preconditions: detail.preconditions ?? '',
      steps: detail.steps?.length ? detail.steps : [{ action: '', expected: '' }],
      expectedResult: detail.expectedResult ?? '',
      tags: detail.tags ?? '',
      extra: { ...(detail.extra ?? {}) },
    })
    dialogVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载用例失败')
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
    const payload = {
      ...form,
      steps: form.steps.filter((item) => item.action.trim() || item.expected.trim()),
    }
    if (editing.value) {
      await caseApi.update(editing.value.id, payload)
    } else {
      await caseApi.create(payload)
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

async function remove(row: TestCase): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除用例「${row.title}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await caseApi.remove(row.id)
    ElMessage.success('已删除')
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

function onRowClick(row: TestCase): void {
  void router.push(`/test-cases/${row.id}`)
}

const importVisible = ref(false)
const importing = ref(false)
const fileList = ref<UploadFiles>([])
let pickedFile: UploadFile | null = null

function onFileChange(file: UploadFile, files: UploadFiles): void {
  pickedFile = file
  fileList.value = files.slice(-1)
}

async function doImport(): Promise<void> {
  if (!pickedFile?.raw) {
    ElMessage.warning('请先选择 xlsx 文件')
    return
  }
  if (!appStore.currentProjectId) {
    ElMessage.warning('请先在右上角选择项目')
    return
  }
  importing.value = true
  try {
    const data = new FormData()
    data.append('file', pickedFile.raw)
    data.append('projectId', String(appStore.currentProjectId))
    const result = await caseApi.importExcel(data)
    ElMessage.success(`导入完成：新增 ${result.created ?? 0} 条，跳过 ${result.skipped ?? 0} 条`)
    importVisible.value = false
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '导入失败')
  } finally {
    importing.value = false
  }
}

function exportXlsx(): void {
  if (!appStore.currentProjectId) {
    ElMessage.warning('请先在右上角选择项目')
    return
  }
  window.open(caseApi.exportUrl({ projectId: appStore.currentProjectId }), '_blank')
}

const coverageVisible = ref(false)
const coverageLoading = ref(false)
const coverageRows = ref<ModuleCoverage[]>([])

async function loadCoverage(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) {
    coverageRows.value = []
    return
  }
  coverageLoading.value = true
  try {
    const result = await caseApi.coverageModules(projectId)
    coverageRows.value = result.list ?? []
  } catch (err) {
    coverageRows.value = []
    ElMessage.error(err instanceof Error ? err.message : '加载覆盖矩阵失败')
  } finally {
    coverageLoading.value = false
  }
}

function openCoverage(): void {
  coverageVisible.value = true
  void loadCoverage()
}

async function loadOptions(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) {
    moduleOptions.value = []
    requirementOptions.value = []
    return
  }
  try {
    moduleOptions.value = await moduleApi.options(projectId, true)
  } catch {
    moduleOptions.value = []
  }
  try {
    requirementOptions.value = await requirementApi.list({ projectId, pageSize: 100 }).then((p) => p.list)
  } catch {
    requirementOptions.value = []
  }
}

watch(() => appStore.currentProjectId, () => {
  void loadOptions()
  tableRef.value?.reload()
}, { immediate: true })
</script>

<style scoped>
.step-editor {
  width: 100%;
}

.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.import-hint {
  font-size: 12px;
  margin: 10px 0 0;
}

.extra-editor {
  width: 100%;
}

.extra-label {
  width: 110px;
  flex: none;
  font-size: 12px;
  color: var(--aitp-text-secondary, #606266);
}

.coverage-table {
  margin-top: 12px;
}
</style>
