<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">项目版本管理</h2>
      <div class="toolbar" style="margin: 0">
        <el-select v-model="projectId" placeholder="选择项目" style="width: 200px" @change="loadVersions">
          <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建版本</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetcher"
        :extra-query="extraQuery"
      >
        <template #status="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small" effect="plain">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
        <template #assetCount="{ row }">{{ row.assetCount || 0 }}</template>
        <template #actions="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="removeVersion(row)">删除</el-button>
        </template>
      </BaseProTable>
    </div>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑版本' : '新建版本'" width="560px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="版本号" required>
          <el-input v-model="form.versionCode" placeholder="例如：v1.0、v2.0.1" maxlength="60" />
        </el-form-item>
        <el-form-item label="版本名称">
          <el-input v-model="form.name" placeholder="例如：首轮交付、灰度版本" maxlength="200" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status" style="width: 100%">
            <el-option v-for="item in STATUS_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" maxlength="500" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveVersion">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type TableExpose } from '@/components/BaseProTable.vue'
import { projectVersionApi } from '@/api'
import type { ProjectVersion } from '@/api/types'
import { useAppStore } from '@/stores/app'

const STATUS_OPTIONS = [
  { label: '规划中', value: 'planning' },
  { label: '进行中', value: 'in_progress' },
  { label: '已发布', value: 'released' },
]

const appStore = useAppStore()
const tableRef = ref<TableExpose | null>(null)

const projectId = ref<number | null>(null)

const columns: ColumnDef[] = [
  { prop: 'versionCode', label: '版本号', width: 140 },
  { prop: 'name', label: '名称', minWidth: 180 },
  { prop: 'status', label: '状态', width: 100, slot: 'status' },
  { prop: 'assetCount', label: '关联资产', width: 100, slot: 'assetCount' },
  { prop: 'description', label: '描述', minWidth: 200 },
  { prop: 'actions', label: '操作', width: 140, slot: 'actions', fixed: 'right' },
]

const extraQuery = computed(() => ({
  projectId: projectId.value ?? undefined,
}))

async function fetcher(_query: Record<string, unknown>) {
  if (!projectId.value) {
    return { list: [], pageNum: 1, pageSize: 20, total: 0, pages: 0 }
  }
  const result = await projectVersionApi.list(projectId.value)
  const total = result.list.length
  return {
    list: result.list,
    pageNum: 1,
    pageSize: Math.max(total, 1),
    total,
    pages: 1,
  }
}

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)
const form = reactive({
  versionCode: '',
  name: '',
  status: 'planning',
  description: '',
})

function statusLabel(status: string): string {
  return STATUS_OPTIONS.find((item) => item.value === status)?.label ?? status
}

function statusTagType(status: string): 'success' | 'warning' | 'info' {
  if (status === 'released') return 'success'
  if (status === 'in_progress') return 'warning'
  return 'info'
}

async function loadVersions(): Promise<void> {
  if (!projectId.value) {
    return
  }
  await tableRef.value?.reload()
}

function openCreate(): void {
  editingId.value = null
  Object.assign(form, {
    versionCode: '',
    name: '',
    status: 'planning',
    description: '',
  })
  dialogVisible.value = true
}

function openEdit(row: ProjectVersion): void {
  editingId.value = row.id
  Object.assign(form, {
    versionCode: row.versionCode,
    name: row.name,
    status: row.status,
    description: row.description ?? '',
  })
  dialogVisible.value = true
}

async function saveVersion(): Promise<void> {
  if (!form.versionCode.trim()) {
    ElMessage.warning('版本号为必填项')
    return
  }
  if (!projectId.value) {
    ElMessage.warning('请先选择项目')
    return
  }
  saving.value = true
  try {
    const payload = {
      projectId: projectId.value,
      versionCode: form.versionCode.trim(),
      name: form.name.trim(),
      status: form.status,
      description: form.description.trim(),
    }
    if (editingId.value) {
      await projectVersionApi.update(editingId.value, payload)
      ElMessage.success('已保存')
    } else {
      await projectVersionApi.create(payload)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    await loadVersions()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeVersion(row: ProjectVersion): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除版本「${row.versionCode}」？删除后已关联该版本的资产会变成未分配版本。`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await projectVersionApi.remove(row.id)
    ElMessage.success('已删除')
    await loadVersions()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

watch(
  () => appStore.currentProjectId,
  (value) => {
    if (value && !projectId.value) {
      projectId.value = value
      void loadVersions()
    }
  },
  { immediate: true },
)

onMounted(() => {
  if (!projectId.value && appStore.currentProjectId) {
    projectId.value = appStore.currentProjectId
  }
  void loadVersions()
})

onActivated(() => {
  void loadVersions()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
}
</style>
