<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">项目</h2>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建项目</el-button>
    </div>

    <div class="panel panel-pad">
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetcher"
        :search-fields="searchFields"
        @row-click="onRowClick"
      >
        <template #status="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small" effect="plain">
            {{ row.status }}
          </el-tag>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
        </template>
      </BaseProTable>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑项目' : '新建项目'" width="480px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="编号" required>
          <el-input v-model="form.code" placeholder="例如 ECOM" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="例如 示例电商项目" />
        </el-form-item>
        <el-form-item label="负责人">
          <el-input v-model="form.owner" placeholder="选填" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status">
            <el-option label="启用" value="active" />
            <el-option label="归档" value="archived" />
          </el-select>
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
import { projectApi } from '@/api'
import type { Project } from '@/api/types'

const tableRef = ref<TableExpose | null>(null)

const columns: ColumnDef[] = [
  { prop: 'code', label: '编号', width: 130 },
  { prop: 'name', label: '名称', minWidth: 160 },
  { prop: 'owner', label: '负责人', width: 110 },
  { prop: 'status', label: '状态', width: 90, slot: 'status' },
  { prop: 'updatedAt', label: '更新时间', width: 170 },
  { prop: 'id', label: '操作', width: 130, slot: 'actions' },
]

const searchFields: SearchField[] = [{ prop: 'keyword', label: '名称 / 编号' }]

function fetcher(query: Record<string, unknown>) {
  return projectApi.list(query)
}

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<Project | null>(null)
const form = reactive({
  code: '',
  name: '',
  owner: '',
  description: '',
  status: 'active',
})

function openCreate(): void {
  editing.value = null
  Object.assign(form, { code: '', name: '', owner: '', description: '', status: 'active' })
  dialogVisible.value = true
}

function openEdit(row: Project): void {
  editing.value = row
  Object.assign(form, {
    code: row.code,
    name: row.name,
    owner: row.owner ?? '',
    description: row.description ?? '',
    status: row.status ?? 'active',
  })
  dialogVisible.value = true
}

async function save(): Promise<void> {
  if (!form.code.trim() || !form.name.trim()) {
    ElMessage.warning('编号与名称为必填项')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await projectApi.update(editing.value.id, { ...form })
    } else {
      await projectApi.create({ ...form })
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

async function remove(row: Project): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除项目「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await projectApi.remove(row.id)
    ElMessage.success('已删除')
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

function onRowClick(row: Project): void {
  openEdit(row)
}
</script>
