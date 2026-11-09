<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">断言库</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新增断言</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="ar-hint"
      title="断言库把「目标 + 算子 + 期望」抽成可复用条目：接口用例的「断言规则」里直接写引用串（如 lib:AR-0001）即可展开执行。挂功能点的断言只对该功能点及其子节点可见，不挂功能点就是项目级共享。"
    />

    <div class="panel panel-pad">
      <div class="toolbar">
        <span class="ar-label">项目</span>
        <el-select
          v-model="projectId"
          class="ar-project"
          filterable
          placeholder="选择项目"
          @change="onProjectChange"
        >
          <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <span class="ar-label">功能点</span>
        <el-select v-model="moduleId" class="ar-module" clearable filterable placeholder="全部（只看项目级）">
          <el-option
            v-for="item in labStore.moduleOptions"
            :key="item.id"
            :label="item.path || item.name"
            :value="item.id"
          />
        </el-select>
        <div class="spacer" />
        <el-button size="small" :icon="Refresh" @click="reload">刷新</el-button>
      </div>

      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetchRules"
        :search-fields="[{ prop: 'keyword', label: '编号 / 名称 / 规则 / 说明' }]"
        :extra-query="scopeQuery"
        row-key="id"
        :page-size="20"
      >
        <template #ruleNo="{ row }">
          <span class="text-mono">{{ row.ruleNo }}</span>
        </template>
        <template #reference="{ row }">
          <el-link type="primary" :underline="false" @click="copyReference(row)">
            {{ row.reference }}
          </el-link>
        </template>
        <template #scope="{ row }">
          <el-tag v-if="row.moduleId" size="small" type="warning" effect="plain">功能点级</el-tag>
          <el-tag v-else size="small" type="info" effect="plain">项目级</el-tag>
        </template>
        <template #enabled="{ row }">
          <el-switch
            :model-value="row.enabled"
            size="small"
            @change="(value: boolean) => toggleEnabled(row, value)"
          />
        </template>
        <template #updatedAt="{ row }">{{ dateTimeText(row.updatedAt) }}</template>
        <template #ops="{ row }">
          <el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click.stop="removeRule(row)">删除</el-button>
        </template>
      </BaseProTable>
      <p class="ar-hint">
        引用串写在用例的「断言规则」里即可，多条用换行或分号分隔；停用的断言在执行时会直接报错提醒，
        schema 类断言保存时就会校验 JSON 是否合法。
      </p>
    </div>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑断言' : '新增断言'" width="680px">
      <el-form label-width="100px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="200" placeholder="例如：业务码为零" />
        </el-form-item>
        <el-form-item label="断言规则" required>
          <el-input
            v-model="form.rule"
            type="textarea"
            :rows="3"
            placeholder="例如 json:$.code = 0　或　schema:$ = {&quot;type&quot;:&quot;object&quot;,&quot;required&quot;:[&quot;code&quot;]}"
          />
          <div class="ar-under">
            与用例里的写法完全一致；schema 断言会当场校验 JSON 是否合法，坏写法直接给中文提示。
          </div>
        </el-form-item>
        <el-form-item label="共享范围">
          <el-select v-model="form.moduleId" clearable filterable placeholder="项目级（所有功能点可用）">
            <el-option
              v-for="item in labStore.moduleOptions"
              :key="item.id"
              :label="item.path || item.name"
              :value="item.id"
            />
          </el-select>
          <div class="ar-under">选了功能点，这条断言只在该功能点及其子节点下的用例里可见。</div>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="500" show-word-limit />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
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
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, Plus, Refresh } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type TableExpose } from '@/components/BaseProTable.vue'
import {
  createAssertionRule,
  deleteAssertionRule,
  listAssertionRules,
  updateAssertionRule,
} from '@/api/assertionRules'
import type { AssertionRule } from '@/api/assertionRules'
import type { PagePayload } from '@/api/request'
import { useAppStore } from '@/stores/app'
import { useLabStore } from '@/stores/lab'
import { dateTimeText } from '@/utils/format'

const router = useRouter()
const appStore = useAppStore()
const labStore = useLabStore()

const tableRef = ref<TableExpose | null>(null)
const projectId = ref<number | null>(appStore.currentProjectId ?? null)
const moduleId = ref<number | null>(null)
const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)

const form = reactive<{
  name: string
  rule: string
  description: string
  moduleId: number | null
  enabled: boolean
}>({ name: '', rule: '', description: '', moduleId: null, enabled: true })

const columns: ColumnDef[] = [
  { prop: 'ruleNo', label: '编号', width: 110 },
  { prop: 'name', label: '名称', minWidth: 150 },
  { prop: 'rule', label: '断言规则', minWidth: 240 },
  { prop: 'reference', label: '引用串', width: 140 },
  { prop: 'scope', label: '范围', width: 96 },
  { prop: 'description', label: '说明', minWidth: 160 },
  { prop: 'enabled', label: '启用', width: 80, slot: 'enabled' },
  { prop: 'updatedAt', label: '更新时间', width: 170 },
  { prop: 'ops', label: '操作', width: 120, fixed: 'right', slot: 'ops' },
]

/** 没选项目时不给取数：断言库按项目隔离，跨项目读取会把别的项目的断言带进来。 */
const scopeQuery = computed(() => ({
  projectId: projectId.value ?? '',
  moduleId: moduleId.value ?? '',
}))

function fetchRules(query: Record<string, unknown>): Promise<PagePayload<AssertionRule>> {
  if (!projectId.value) {
    return Promise.resolve({ list: [], pageNum: 1, pageSize: 20, total: 0, pages: 0 })
  }
  return listAssertionRules(query)
}

function reload(): void {
  tableRef.value?.reload()
}

async function loadModules(): Promise<void> {
  await labStore.loadModuleOptions(projectId.value ?? undefined)
}

function onProjectChange(): void {
  moduleId.value = null
  appStore.selectProject(projectId.value)
  void loadModules()
  reload()
}

watch(
  () => appStore.currentProjectId,
  (value) => {
    if (value && value !== projectId.value) {
      projectId.value = value
      moduleId.value = null
      void loadModules()
      reload()
    }
  },
)

watch(moduleId, () => reload())

function openCreate(): void {
  editingId.value = null
  form.name = ''
  form.rule = ''
  form.description = ''
  form.moduleId = moduleId.value
  form.enabled = true
  dialogVisible.value = true
}

function openEdit(row: AssertionRule): void {
  editingId.value = row.id
  form.name = row.name
  form.rule = row.rule
  form.description = row.description || ''
  form.moduleId = row.moduleId ?? null
  form.enabled = row.enabled
  dialogVisible.value = true
}

async function save(): Promise<void> {
  if (!projectId.value) {
    ElMessage.warning('请先选择项目')
    return
  }
  if (!form.name.trim() || !form.rule.trim()) {
    ElMessage.warning('名称与断言规则都不能为空')
    return
  }
  saving.value = true
  try {
    const payload = {
      projectId: projectId.value,
      moduleId: form.moduleId ?? null,
      name: form.name.trim(),
      rule: form.rule.trim(),
      description: form.description,
      enabled: form.enabled,
    }
    if (editingId.value) {
      await updateAssertionRule(editingId.value, payload)
      ElMessage.success('断言已更新')
    } else {
      await createAssertionRule(payload)
      ElMessage.success('断言已入库，用例里可直接写引用串')
    }
    dialogVisible.value = false
    reload()
  } catch (error) {
    ElMessage.error((error as Error).message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(row: AssertionRule, value: boolean): Promise<void> {
  try {
    await updateAssertionRule(row.id, { enabled: value })
    ElMessage.success(value ? `已启用 ${row.ruleNo}` : `已停用 ${row.ruleNo}`)
    reload()
  } catch (error) {
    ElMessage.error((error as Error).message || '操作失败')
    reload()
  }
}

async function removeRule(row: AssertionRule): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除后，用例里写 ${row.reference} 的断言执行时会直接报错。确定删除「${row.name}」？`,
      '删除断言',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteAssertionRule(row.id)
    ElMessage.success('断言已删除')
    reload()
  } catch (error) {
    ElMessage.error((error as Error).message || '删除失败')
  }
}

async function copyReference(row: AssertionRule): Promise<void> {
  try {
    await navigator.clipboard.writeText(row.reference)
    ElMessage.success(`已复制 ${row.reference}，粘贴到用例的「断言规则」里即可`)
  } catch {
    ElMessage.info(`引用串：${row.reference}`)
  }
}

onMounted(async () => {
  if (!appStore.projects.length) {
    await appStore.loadProjects()
  }
  if (!projectId.value) {
    projectId.value = appStore.currentProjectId ?? null
  }
  await loadModules()
  reload()
})
</script>

<style scoped>
.ar-hint {
  margin-bottom: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.ar-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-right: 6px;
}

.ar-project {
  width: 200px;
}

.ar-module {
  width: 220px;
}

.ar-under {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
</style>
