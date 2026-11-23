<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">需求</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <BaseExportMenu :resolve-url="resolveExportUrl" />
        <el-button :icon="Upload" @click="openImport">文档批量导入</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建需求</el-button>
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
        @row-click="openEdit"
      >
        <template #batch="{ rows }">
          <el-button size="small" type="danger" plain :loading="batchRemoving" @click="batchRemove(rows)">
            批量删除
          </el-button>
        </template>
        <template #priority="{ row }">
          <el-tag :type="priorityTag(row.priority)" size="small" effect="plain">{{ row.priority }}</el-tag>
        </template>
        <template #status="{ row }">
          <el-tag size="small" effect="plain">{{ statusLabel(row.status) }}</el-tag>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button>
          <el-button link type="success" @click.stop="openKnowledge(row)">加入知识库</el-button>
          <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
        </template>
      </BaseProTable>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑需求' : '新建需求'" width="640px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="例如 手机号+验证码登录" />
        </el-form-item>
        <el-form-item label="项目">
          <el-select v-model="form.projectId" placeholder="选择项目" clearable @change="onProjectChange">
            <el-option
              v-for="item in appStore.projects"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="版本">
          <el-select v-model="form.versionId" placeholder="选择版本" clearable style="width: 100%">
            <el-option v-for="item in formVersionOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority">
            <el-option v-for="p in CASE_PRIORITY_OPTIONS" :key="p.value" :label="p.label" :value="p.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status">
            <el-option
              v-for="item in requirementStatusOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="来源">
          <el-input v-model="form.source" placeholder="例如 客户访谈 / 需求文档" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="form.content" type="textarea" :rows="8" placeholder="粘贴需求原文" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="importVisible" title="文档批量拆分导入" width="880px" class="req-import-dialog">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="AI 只产出拆分建议，勾选后才落库；同一文档重复导入时，已采纳的条目会自动跳过"
      />
      <el-form label-width="92px" class="req-import-form">
        <el-form-item label="需求文档">
          <el-input
            v-model="importForm.text"
            class="req-import-text"
            type="textarea"
            :rows="8"
            placeholder="粘贴需求文档正文，或先上传 Word / Excel / PDF / Markdown 自动抽取文本"
          />
          <div class="req-import-actions">
            <el-upload
              :show-file-list="false"
              accept=".docx,.xlsx,.pdf,.md,.txt,.html,.json"
              :http-request="uploadImportDoc"
            >
              <el-button :loading="importUploading">上传文档</el-button>
            </el-upload>
            <span v-if="importForm.fileName" class="req-import-file">已载入：{{ importForm.fileName }}</span>
          </div>
        </el-form-item>
        <el-form-item label="最多拆几条">
          <el-input-number v-model="importForm.maxItems" :min="1" :max="50" />
        </el-form-item>
      </el-form>

      <el-alert
        v-if="importMessage"
        class="req-import-msg"
        :type="importResult && !importResult.generated ? 'warning' : 'success'"
        :closable="false"
        :title="importMessage"
      />

      <el-table
        v-if="importRows.length"
        :data="importRows"
        row-key="title"
        class="req-import-table"
        @selection-change="onImportSelection"
      >
        <el-table-column type="selection" width="44" />
        <el-table-column prop="title" label="需求标题" min-width="180" />
        <el-table-column prop="description" label="描述" min-width="240" show-overflow-tooltip />
        <el-table-column prop="modulePath" label="建议功能点" width="150" show-overflow-tooltip />
        <el-table-column prop="priority" label="优先级" width="80" />
      </el-table>

      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button type="primary" :loading="importSplitting" @click="runSplit">开始拆分</el-button>
        <el-button
          type="success"
          :loading="importAdopting"
          :disabled="!importSelection.length"
          @click="confirmImportAdopt"
        >
          采纳 {{ importSelection.length }} 条
        </el-button>
      </template>
    </el-dialog>
  </div>

    <AddToKnowledgeDialog
      v-model="knowledgeVisible"
      :title="knowledgePayload.title"
      :content="knowledgePayload.content"
      ref-type="requirement"
      :ref-id="knowledgePayload.refId"
      :ref-label="knowledgePayload.refLabel"
    />
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox, type UploadRequestOptions } from 'element-plus'
import { Plus, Upload } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import BaseExportMenu from '@/components/BaseExportMenu.vue'
import AddToKnowledgeDialog from '@/components/AddToKnowledgeDialog.vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { labApi, projectVersionApi, requirementApi } from '@/api'
import { CASE_PRIORITY_OPTIONS } from '@/constants/enums'
import type { Requirement, RequirementImportItem, RequirementImportSplit } from '@/api/types'
import { useProjectScope } from '@/composables/useProjectScope'
import { useProjectExtraQuery, useProjectVersionOptions } from '@/composables/useProjectOptions'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()

/** 列表只展示本页当前项目的需求。 */
const extraQuery = useProjectExtraQuery()

/** 列表筛选用的版本清单，跟随本页当前项目。 */
const { versionOptions, loadVersionOptions } = useProjectVersionOptions()

const knowledgeVisible = ref(false)
const knowledgePayload = reactive({
  title: '',
  content: '',
  refId: null as number | null,
  refLabel: '',
})

function openKnowledge(row: Requirement): void {
  knowledgePayload.title = row.title
  knowledgePayload.content = [`# ${row.title}`, row.content].filter(Boolean).join('\n\n')
  knowledgePayload.refId = Number(row.id)
  knowledgePayload.refLabel = row.title
  knowledgeVisible.value = true
}
const tableRef = ref<TableExpose | null>(null)

const requirementStatusOptions = [
  { label: '草稿', value: 'draft' },
  { label: '评审中', value: 'reviewing' },
  { label: '已通过', value: 'approved' },
  { label: '已实现', value: 'implemented' },
  { label: '已否决', value: 'rejected' },
]

const columns: ColumnDef[] = [
  { prop: 'reqNo', label: '编号', width: 110 },
  { prop: 'title', label: '标题', minWidth: 180 },
  { prop: 'priority', label: '优先级', width: 90, slot: 'priority' },
  { prop: 'status', label: '状态', width: 100, slot: 'status' },
  { prop: 'versionCode', label: '版本号', width: 110 },
  { prop: 'versionName', label: '版本名称', width: 130 },
  { prop: 'source', label: '来源', width: 120 },
  { prop: 'updatedAt', label: '更新时间', width: 170 },
  { prop: 'id', label: '操作', width: 210, slot: 'actions' },
]

const searchFields = computed<SearchField[]>(() => [
  { prop: 'keyword', label: '标题 / 编号' },
  { prop: 'status', label: '状态', type: 'select', options: requirementStatusOptions },
  { prop: 'priority', label: '优先级', type: 'select', options: CASE_PRIORITY_OPTIONS.map((p) => ({ label: p.label, value: p.value })) },
  { prop: 'versionId', label: '版本', type: 'select', options: versionOptions.value },
])

useProjectScope({
  load: () => void loadVersionOptions(),
  // 旧项目的版本筛选在新项目里不存在，留着会把列表筛成空。
  resetScopedFilters: () => tableRef.value?.clearFilter('versionId'),
})

function fetcher(query: Record<string, unknown>) {
  return requirementApi.list(query)
}

function resolveExportUrl(format: string): string {
  return requirementApi.exportUrl({
    format,
    projectId: appStore.currentProjectId,
    ...tableRef.value?.filterQuery(),
  })
}

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<Requirement | null>(null)
const form = reactive({
  title: '',
  content: '',
  projectId: null as number | null,
  versionId: null as number | null,
  priority: 'P1',
  status: 'draft',
  source: '',
})

/** 表单里的版本下拉跟随表单中选的项目。 */
const formVersionOptions = ref<Array<{ label: string; value: number }>>([])
const formVersionProjectId = ref<number | null>(null)

watch(() => form.projectId, async (pid) => {
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
        label: v.versionCode,
        value: v.id as number,
      }))
    }
  } catch {
    formVersionOptions.value = []
  }
}, { immediate: true })

/** 表单里换了项目，版本必须跟着换，否则会挂到别的项目的版本上。 */
function onProjectChange(): void {
  form.versionId = null
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

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    draft: '草稿',
    reviewing: '评审中',
    approved: '已通过',
    implemented: '已实现',
    rejected: '已否决',
  }
  return map[status] ?? status
}

function openCreate(): void {
  editing.value = null
  const fq = tableRef.value?.filterQuery() ?? {}
  Object.assign(form, {
    title: '',
    content: '',
    projectId: appStore.currentProjectId,
    // 列表正按版本筛选时，新建的需求直接挂到该版本下，保存后不会从列表里消失。
    versionId: fq.versionId ? Number(fq.versionId) : null,
    priority: 'P1',
    status: 'draft',
    source: '',
  })
  dialogVisible.value = true
}

function openEdit(row: Requirement): void {
  editing.value = row
  Object.assign(form, {
    title: row.title ?? '',
    content: row.content ?? '',
    projectId: row.projectId ?? null,
    versionId: row.versionId ?? null,
    priority: row.priority ?? 'P1',
    status: row.status ?? 'draft',
    source: row.source ?? '',
  })
  dialogVisible.value = true
}

async function save(): Promise<void> {
  if (!form.title.trim()) {
    ElMessage.warning('标题为必填项')
    return
  }
  saving.value = true
  try {
    const payload = { ...form, projectId: form.projectId ?? undefined }
    if (editing.value) {
      await requirementApi.update(editing.value.id, payload)
    } else {
      await requirementApi.create(payload)
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

async function remove(row: Requirement): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除需求「${row.title}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await requirementApi.remove(row.id)
    ElMessage.success('已删除')
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

const batchRemoving = ref(false)

async function batchRemove(rows: Record<string, any>[]): Promise<void> {
  const ids = rows.map((row) => Number(row.id)).filter((id) => Number.isInteger(id) && id > 0)
  if (!ids.length) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${ids.length} 条需求？关联的用例不会一起删除，但覆盖矩阵会失去这些行。`,
      '批量删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  batchRemoving.value = true
  try {
    const result = await requirementApi.batchRemove(ids)
    const skipped = result.skipped?.length ? `，跳过 ${result.skipped.length} 条` : ''
    ElMessage.success(`已删除 ${result.count} 条${skipped}`)
    tableRef.value?.clearSelection()
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量删除失败')
  } finally {
    batchRemoving.value = false
  }
}

// ---------------- 四期专项 D：文档批量拆分导入 ----------------

const importVisible = ref(false)
const importUploading = ref(false)
const importSplitting = ref(false)
const importAdopting = ref(false)
const importRows = ref<RequirementImportItem[]>([])
const importSelection = ref<RequirementImportItem[]>([])
const importMessage = ref('')
const importResult = ref<RequirementImportSplit | null>(null)
const importForm = reactive({ text: '', fileName: '', maxItems: 30 })

function openImport(): void {
  Object.assign(importForm, { text: '', fileName: '', maxItems: 30 })
  importRows.value = []
  importSelection.value = []
  importMessage.value = ''
  importResult.value = null
  importVisible.value = true
}

async function uploadImportDoc(options: UploadRequestOptions): Promise<void> {
  const form = new FormData()
  form.append('file', options.file)
  importUploading.value = true
  try {
    const result = await labApi.uploadDocument(form)
    importForm.text = result.text
    importForm.fileName = result.fileName
    ElMessage.success('文档已抽取为文本，可直接开始拆分')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '文档解析失败')
  } finally {
    importUploading.value = false
  }
}

async function runSplit(): Promise<void> {
  if (!importForm.text.trim()) {
    ElMessage.warning('请先粘贴或上传需求文档')
    return
  }
  if (!appStore.currentProjectId) {
    ElMessage.warning('请先在顶部选择项目')
    return
  }
  importSplitting.value = true
  try {
    const result = await requirementApi.splitImport({
      projectId: appStore.currentProjectId,
      text: importForm.text,
      fileName: importForm.fileName || undefined,
      maxItems: importForm.maxItems,
    })
    importResult.value = result
    importRows.value = result.items
    if (!result.generated) {
      importMessage.value = result.message || '未能拆出可用需求，请调整文档内容后重试'
      return
    }
    importMessage.value = result.duplicated
      ? result.message
      : `已拆出 ${result.items.length} 条需求，勾选后批量采纳`
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '拆分失败')
  } finally {
    importSplitting.value = false
  }
}

function onImportSelection(rows: RequirementImportItem[]): void {
  importSelection.value = rows
}

async function confirmImportAdopt(): Promise<void> {
  if (!importSelection.value.length || !appStore.currentProjectId) {
    return
  }
  importAdopting.value = true
  try {
    const result = await requirementApi.adoptImport({
      projectId: appStore.currentProjectId,
      items: importSelection.value,
      importId: importResult.value?.importId ?? undefined,
      fingerprint: importResult.value?.fingerprint || undefined,
      fileName: importForm.fileName || undefined,
    })
    ElMessage.success(result.message)
    importVisible.value = false
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量采纳失败')
  } finally {
    importAdopting.value = false
  }
}
</script>
