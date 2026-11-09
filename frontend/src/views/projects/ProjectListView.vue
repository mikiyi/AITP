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
        sync-query
        @row-click="onRowClick"
      >
        <template #status="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small" effect="plain">
            {{ row.status === 'archived' ? '归档' : '启用' }}
          </el-tag>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
        </template>
        <template #empty>
          <div class="project-empty">
            <p class="muted">还没有项目。新建一个，或载入内置示例项目快速体验主链路。</p>
            <el-button type="primary" size="small" @click="openCreate">新建项目</el-button>
            <el-button size="small" :loading="injectingDemo" @click="injectDemo">载入示例项目</el-button>
          </div>
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
          <el-select v-model="form.status" popper-class="project-status-popper">
            <el-option label="启用" value="active">
              <div class="status-option">
                <span class="status-option__label">启用</span>
                <span class="status-option__desc">在用项目。出现在页头「项目」下拉与首页概览里，需求 / 用例 / 缺陷 / 报告等资产照常新建、统计和 AI 取数。</span>
              </div>
            </el-option>
            <el-option label="归档" value="archived">
              <div class="status-option">
                <span class="status-option__label">归档</span>
                <span class="status-option__desc">收尾停用的项目。列表标签变灰、状态栏显示「归档」，历史数据全部保留可查可导出；归档只是状态标记，不会删除数据，也不会阻止继续录入。</span>
              </div>
            </el-option>
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
import { onboardingApi, projectApi } from '@/api'
import type { Project } from '@/api/types'

const tableRef = ref<TableExpose | null>(null)
const injectingDemo = ref(false)

async function injectDemo(): Promise<void> {
  injectingDemo.value = true
  try {
    await onboardingApi.injectDemo()
    ElMessage.success('示例项目 DEMO-EC 已载入，点击行即可查看')
    tableRef.value?.reload()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '示例注入失败')
  } finally {
    injectingDemo.value = false
  }
}

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

<style scoped>
.status-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.status-option__label {
  font-size: 14px;
}

.status-option__desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
  white-space: normal;
}

/* 下拉面板挂在 body 上，且选项默认固定 34px 高，两行文案会被裁掉 */
:global(.project-status-popper .el-select-dropdown__item) {
  height: auto;
  padding: 8px 12px;
  line-height: 1.4;
}

:global(.project-status-popper .el-select-dropdown__item.is-selected .status-option__label) {
  color: var(--el-color-primary);
  font-weight: 600;
}

.project-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 24px 0;
}

.project-empty p {
  margin: 0;
}
</style>
