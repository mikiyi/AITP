<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">需求</h2>
      <div>
        <BaseExportMenu :resolve-url="resolveExportUrl" />
        <el-button type="primary" :icon="Plus" @click="openCreate">新建需求</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetcher"
        :search-fields="searchFields"
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
          <el-select v-model="form.projectId" placeholder="选择项目" clearable>
            <el-option
              v-for="item in appStore.projects"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority">
            <el-option v-for="item in ['P0', 'P1', 'P2', 'P3']" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status">
            <el-option
              v-for="item in requirementStatusOptions"
              :key="item"
              :label="item"
              :value="item"
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
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import BaseExportMenu from '@/components/BaseExportMenu.vue'
import { requirementApi } from '@/api'
import type { Requirement } from '@/api/types'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
const tableRef = ref<TableExpose | null>(null)

const requirementStatusOptions = ['draft', 'reviewing', 'approved', 'implemented', 'rejected']

const columns: ColumnDef[] = [
  { prop: 'reqNo', label: '编号', width: 110 },
  { prop: 'title', label: '标题', minWidth: 180 },
  { prop: 'priority', label: '优先级', width: 90, slot: 'priority' },
  { prop: 'status', label: '状态', width: 100, slot: 'status' },
  { prop: 'source', label: '来源', width: 120 },
  { prop: 'updatedAt', label: '更新时间', width: 170 },
  { prop: 'id', label: '操作', width: 130, slot: 'actions' },
]

const searchFields: SearchField[] = [
  { prop: 'keyword', label: '标题 / 编号' },
  { prop: 'status', label: '状态', type: 'select', options: requirementStatusOptions.map((s) => ({ label: s, value: s })) },
  { prop: 'priority', label: '优先级', type: 'select', options: ['P0', 'P1', 'P2', 'P3'].map((p) => ({ label: p, value: p })) },
]

function fetcher(query: Record<string, unknown>) {
  return requirementApi.list(query)
}

function resolveExportUrl(format: string): string {
  return requirementApi.exportUrl({ format, ...tableRef.value?.filterQuery() })
}

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<Requirement | null>(null)
const form = reactive({
  title: '',
  content: '',
  projectId: null as number | null,
  priority: 'P1',
  status: 'draft',
  source: '',
})

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
  Object.assign(form, {
    title: '',
    content: '',
    projectId: appStore.currentProjectId,
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
</script>
