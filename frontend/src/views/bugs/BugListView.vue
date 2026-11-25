<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">缺陷</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <BaseExportMenu :resolve-url="resolveExportUrl" />
        <el-button type="primary" :icon="Plus" @click="openCreate">新建缺陷</el-button>
      </div>
    </div>

    <div v-if="drafts.length" class="panel panel-pad drafts-panel">
      <div class="drafts-head">
        <h3>缺陷待审区</h3>
        <el-tag type="warning" effect="plain" size="small">{{ draftPendingCount }} 条待确认</el-tag>
        <span class="muted">自动化 / 场景 / MCP 提交的候选缺陷，确认后才生成正式缺陷</span>
        <el-button link type="primary" size="small" :loading="draftsLoading" @click="loadDrafts">
          刷新
        </el-button>
      </div>
      <el-table :data="drafts" size="small" border v-loading="draftsLoading">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="draft-detail">
              <div><strong>描述：</strong>{{ row.description || '-' }}</div>
              <div><strong>实际结果：</strong>{{ row.actualResult || '-' }}</div>
              <div><strong>来源：</strong>{{ row.source }}{{ row.sourceRef ? ` · ${row.sourceRef}` : '' }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ draftSourceLabel(row.source) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" min-width="260" show-overflow-tooltip />
        <el-table-column label="严重度" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="severityTag(row.severity)" size="small" effect="plain">{{ row.severity }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="createdAt" label="提交时间" width="180" />
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :loading="draftActionId === row.id" @click="confirmDraft(row)">
              确认转缺陷
            </el-button>
            <el-button link type="danger" :disabled="draftActionId === row.id" @click="ignoreDraft(row)">
              忽略
            </el-button>
          </template>
        </el-table-column>
      </el-table>
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
          <el-select v-model="form.projectId" @change="onProjectChange">
            <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="功能点">
          <el-select v-model="form.moduleId" clearable filterable placeholder="选择功能点" :loading="formModuleLoading">
            <el-option v-for="item in formModuleOptions" :key="item.id" :label="item.path" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="项目版本">
          <el-select v-model="form.versionId" clearable placeholder="选择版本" style="width: 100%">
            <el-option v-for="item in formVersionOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="严重程度">
          <el-select v-model="form.severity">
            <el-option v-for="item in BUG_SEVERITY_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority">
            <el-option v-for="item in BUG_SEVERITY_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <!-- 新建时后端强制 status=open（bugs.py create_bug），表单里选了也不生效，只在编辑态显示 -->
        <el-form-item v-if="editing" label="状态">
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
      <div class="bug-attach">
        <AttachmentManager
          ref="attachmentRef"
          owner-type="bug"
          :owner-id="editing?.id ?? null"
          title="附件"
          hint="新建时先选择文件，保存缺陷后自动上传；编辑时上传立即生效。支持文档、日志、接口报文、录屏与图片。"
        />
      </div>
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
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import BaseExportMenu from '@/components/BaseExportMenu.vue'
import AddToKnowledgeDialog from '@/components/AddToKnowledgeDialog.vue'
import AttachmentManager from '@/components/AttachmentManager.vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { BUG_SEVERITY_OPTIONS } from '@/constants/enums'
import { bugApi, moduleApi, projectVersionApi } from '@/api'
import type { Bug, BugDraft, ModuleOption } from '@/api/types'
import { useProjectScope } from '@/composables/useProjectScope'
import {
  useProjectExtraQuery,
  useProjectModuleOptions,
  useProjectVersionOptions,
} from '@/composables/useProjectOptions'
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

const drafts = ref<BugDraft[]>([])
const draftPendingCount = ref(0)
const draftsLoading = ref(false)
const draftActionId = ref<number | null>(null)

function draftSourceLabel(source: string): string {
  const labels: Record<string, string> = { automation: '自动化失败', scenario: '场景失败', mcp: 'MCP 提交' }
  return labels[source] ?? source
}

/** 待审区只展示待确认条目；确认或忽略后从列表消失，正式缺陷进主表。 */
async function loadDrafts(): Promise<void> {
  if (!appStore.currentProjectId) {
    drafts.value = []
    draftPendingCount.value = 0
    return
  }
  draftsLoading.value = true
  try {
    const payload = await bugApi.drafts({
      projectId: appStore.currentProjectId,
      status: 'pending',
    })
    drafts.value = payload.list
    draftPendingCount.value = payload.pendingCount
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载待审缺陷失败')
  } finally {
    draftsLoading.value = false
  }
}

async function confirmDraft(row: BugDraft): Promise<void> {
  draftActionId.value = row.id
  try {
    const result = await bugApi.confirmDraft(row.id, { reporter: 'manual' })
    ElMessage.success(`已生成缺陷 ${result.bugNo}`)
    await loadDrafts()
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '确认待审缺陷失败')
  } finally {
    draftActionId.value = null
  }
}

async function ignoreDraft(row: BugDraft): Promise<void> {
  let note = ''
  try {
    const result = await ElMessageBox.prompt('忽略后不会生成正式缺陷，可填写原因备查。', '忽略待审缺陷', {
      inputPlaceholder: '忽略原因（可选）',
      confirmButtonText: '忽略',
      cancelButtonText: '取消',
      type: 'warning',
    })
    note = result.value ?? ''
  } catch {
    return
  }
  draftActionId.value = row.id
  try {
    await bugApi.ignoreDraft(row.id, note)
    ElMessage.success('已忽略该待审缺陷')
    await loadDrafts()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '忽略待审缺陷失败')
  } finally {
    draftActionId.value = null
  }
}

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

const extraQuery = useProjectExtraQuery()
const { moduleOptions, loadModuleOptions } = useProjectModuleOptions()
const { versionOptions, loadVersionOptions } = useProjectVersionOptions()

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
  { prop: 'severity', label: '严重度', type: 'select', options: BUG_SEVERITY_OPTIONS.map((s) => ({ label: s.label, value: s.value })) },
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
const attachmentRef = ref<InstanceType<typeof AttachmentManager> | null>(null)
const form = reactive({
  title: '',
  projectId: null as number | null,
  moduleId: null as number | null,
  versionId: null as number | null,
  severity: 'P2',
  priority: 'P2',
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
  Object.assign(form, {
    title: '',
    projectId: appStore.currentProjectId,
    moduleId: null,
    // 列表正按版本筛选时，新建的缺陷直接挂到该版本下，保存后不会从列表里消失。
    versionId: fq.versionId ? Number(fq.versionId) : null,
    severity: 'P2',
    priority: 'P2',
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
  void nextTick(() => attachmentRef.value?.reset())
}

async function openEdit(row: Bug): Promise<void> {
  try {
    const detail = await bugApi.get(row.id)
    editing.value = detail
    Object.assign(form, {
      title: detail.title ?? '',
      projectId: detail.projectId ?? null,
      moduleId: detail.moduleId ?? null,
      versionId: detail.versionId ?? null,
      severity: detail.severity ?? 'P2',
      priority: detail.priority ?? 'P2',
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
    void nextTick(() => attachmentRef.value?.reset())
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
    const saved = editing.value
      ? await bugApi.update(editing.value.id, payload)
      : await bugApi.create(payload)
    const savedId = Number(saved?.id ?? editing.value?.id ?? 0)
    if (savedId) {
      try {
        await attachmentRef.value?.flush(savedId)
      } catch (err) {
        // 缺陷已保存，附件没传成功：留在弹窗里可直接重试，重试按内容去重不会重复
        editing.value = saved ?? editing.value
        ElMessage.error(err instanceof Error ? `缺陷已保存，但附件上传失败：${err.message}` : '缺陷已保存，但附件上传失败')
        return
      }
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

useProjectScope({
  load: () => {
    void loadModuleOptions()
    void loadVersionOptions()
    void loadDrafts()
  },
  // 旧项目的版本筛选在新项目里不存在，留着会把列表筛成空。
  resetScopedFilters: () => tableRef.value?.clearFilter('versionId'),
})

/** 表单里的版本、功能点下拉跟随表单中选的项目，切换项目时重新拉取该项目的清单。 */
const formVersionOptions = ref<Array<{ label: string; value: number }>>([])
const formModuleOptions = ref<ModuleOption[]>([])
const formModuleLoading = ref(false)
/** 请求回来时项目可能已经换过一次，只认最后一次请求的项目。 */
const formScopeProjectId = ref<number | null>(null)

watch(() => form.projectId, async (pid) => {
  formScopeProjectId.value = pid ?? null
  if (!pid) {
    formVersionOptions.value = []
    formModuleOptions.value = []
    return
  }
  const isStale = () => formScopeProjectId.value !== pid
  formModuleLoading.value = true
  try {
    try {
      const result = await projectVersionApi.list(pid)
      if (!isStale()) {
        formVersionOptions.value = result.list.map((v) => ({
          label: v.versionCode,
          value: v.id as number,
        }))
      }
    } catch {
      if (!isStale()) {
        formVersionOptions.value = []
      }
    }
    try {
      const modules = await moduleApi.options(pid, true)
      if (!isStale()) {
        formModuleOptions.value = modules
      }
    } catch {
      if (!isStale()) {
        formModuleOptions.value = []
      }
    }
  } finally {
    formModuleLoading.value = false
  }
}, { immediate: true })

/** 表单里换了项目，版本和功能点都会挂到别的项目上，必须一起清掉。 */
function onProjectChange(): void {
  form.versionId = null
  form.moduleId = null
}
</script>

<style scoped>
.bug-attach {
  margin-top: 4px;
}

.drafts-panel {
  margin-bottom: 16px;
}

.drafts-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.drafts-head h3 {
  margin: 0;
  font-size: 15px;
}

.draft-detail {
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
