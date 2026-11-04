<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">编排入口</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建入口</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <el-table v-loading="loading" :data="entries" border size="small">
        <el-table-column prop="code" label="编码" width="140" class-name="text-mono" />
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column prop="capability" label="主能力" width="140">
          <template #default="scope">
            <el-tag size="small" effect="plain">{{ labelOf(scope.row.capability) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="工作流" width="90" align="center">
          <template #default="scope">{{ scope.row.workflow?.length ?? 0 }} 步</template>
        </el-table-column>
        <el-table-column prop="version" label="版本" width="80" align="center" />
        <el-table-column label="状态" width="100">
          <template #default="scope">
            <el-tag size="small" :type="statusTagType(scope.row.status)">
              {{ statusText(scope.row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click="openEdit(scope.row)">编辑</el-button>
            <el-button
              link
              type="success"
              :disabled="scope.row.status === 'published'"
              @click="publish(scope.row)"
            >
              发布
            </el-button>
            <el-button link type="warning" @click="openRun(scope.row)">运行</el-button>
            <el-button link type="danger" @click="remove(scope.row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">还没有编排入口</span>
        </template>
      </el-table>
    </div>

    <el-dialog v-model="editVisible" :title="form.id ? '编辑入口' : '新建入口'" width="760px">
      <el-form label-width="96px">
        <el-form-item label="编码" required>
          <el-input v-model="form.code" :disabled="Boolean(form.id)" placeholder="例如 pay-refund-review" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="面向用户的入口名称" />
        </el-form-item>
        <el-form-item label="主能力" required>
          <el-select v-model="form.capability" placeholder="选择主能力" style="width: 100%">
            <el-option
              v-for="item in labStore.capabilities"
              :key="item.code"
              :label="item.label"
              :value="item.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="触发词">
          <el-input v-model="triggersText" placeholder="逗号分隔，例如：退款,复核" />
        </el-form-item>
        <el-form-item label="必填资料">
          <el-input v-model="requiredText" placeholder="逗号分隔，例如：订单号,退款原因" />
        </el-form-item>
        <el-form-item label="关联知识">
          <el-input v-model="knowledgeText" placeholder="逗号分隔的知识标题" />
        </el-form-item>
        <el-form-item label="工作流">
          <div class="workflow-editor">
            <div v-for="(item, index) in form.workflow" :key="index" class="workflow-row">
              <el-select v-model="item.capability" placeholder="能力" style="width: 170px">
                <el-option
                  v-for="cap in labStore.capabilities"
                  :key="cap.code"
                  :label="cap.label"
                  :value="cap.code"
                />
              </el-select>
              <el-input v-model="item.name" placeholder="步骤名" style="flex: 1; min-width: 110px" />
              <el-button text type="danger" :icon="Delete" @click="form.workflow.splice(index, 1)" />
            </div>
            <el-button :icon="Plus" plain size="small" @click="addWorkflowStep">添加步骤</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="runVisible" title="运行入口" width="720px">
      <el-form label-width="96px">
        <el-form-item label="入口">
          <el-input :model-value="current?.name ?? ''" disabled />
        </el-form-item>
        <el-form-item label="参数">
          <el-input
            v-model="runVariablesText"
            type="textarea"
            :rows="8"
            class="text-mono"
            placeholder='{"requirement": "..."}'
          />
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        show-icon
        :closable="false"
        title="参数会以 JSON 对象传入每一步；缺必填变量时该步会失败并记进轨迹。"
        class="run-alert"
      />
      <template #footer>
        <el-button @click="runVisible = false">取消</el-button>
        <el-button type="primary" :loading="running" @click="run">运行</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resultVisible" title="执行轨迹" width="780px">
      <el-table v-if="runResult" :data="runResult.steps" border size="small">
        <el-table-column prop="order" label="#" width="52" align="center" />
        <el-table-column prop="stepName" label="步骤" min-width="150" />
        <el-table-column label="状态" width="90">
          <template #default="scope">
            <el-tag size="small" :type="scope.row.status === 'success' ? 'success' : 'danger'">
              {{ scope.row.status === 'success' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="86" align="right">
          <template #default="scope">{{ scope.row.latencyMs ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="说明" min-width="200">
          <template #default="scope">
            <span class="muted">{{ scope.row.errorMessage || scope.row.summary || '-' }}</span>
          </template>
        </el-table-column>
      </el-table>
      <pre v-if="runResult" class="output-pre">{{ runResult.output || '（无产出）' }}</pre>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, Plus, Refresh } from '@element-plus/icons-vue'
import { workbenchApi } from '@/api'
import type { PipelineResult, WorkbenchEntry } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useLabStore } from '@/stores/lab'

interface EntryForm {
  id: number | null
  code: string
  name: string
  capability: string
  description: string
  triggers: string[]
  required: string[]
  knowledge: string[]
  workflow: Array<{ capability: string; name: string; variables: Record<string, unknown> }>
}

const labStore = useLabStore()
const appStore = useAppStore()

const entries = ref<WorkbenchEntry[]>([])
const loading = ref(false)
const saving = ref(false)
const running = ref(false)
const editVisible = ref(false)
const runVisible = ref(false)
const resultVisible = ref(false)
const current = ref<WorkbenchEntry | null>(null)
const runResult = ref<PipelineResult | null>(null)
const runVariablesText = ref('{}')
const triggersText = ref('')
const requiredText = ref('')
const knowledgeText = ref('')

const form = reactive<EntryForm>({
  id: null,
  code: '',
  name: '',
  capability: '',
  description: '',
  triggers: [],
  required: [],
  knowledge: [],
  workflow: [],
})

function labelOf(capability: string): string {
  return labStore.capabilities.find((item) => item.code === capability)?.label ?? capability
}

function statusText(status: string): string {
  if (status === 'published') return '已发布'
  if (status === 'archived') return '已归档'
  return '草稿'
}

function statusTagType(status: string): 'success' | 'info' | 'warning' {
  if (status === 'published') return 'success'
  if (status === 'archived') return 'warning'
  return 'info'
}

function splitList(text: string): string[] {
  return text
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await workbenchApi.entries({})
    entries.value = data.list ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function resetForm(): void {
  form.id = null
  form.code = ''
  form.name = ''
  form.capability = labStore.capabilities[0]?.code ?? ''
  form.description = ''
  form.triggers = []
  form.required = []
  form.knowledge = []
  form.workflow = []
  triggersText.value = ''
  requiredText.value = ''
  knowledgeText.value = ''
}

function openCreate(): void {
  resetForm()
  editVisible.value = true
}

function openEdit(row: WorkbenchEntry): void {
  form.id = row.id
  form.code = row.code
  form.name = row.name
  form.capability = row.capability
  form.description = row.description
  form.triggers = [...(row.triggers ?? [])]
  form.required = [...(row.required ?? [])]
  form.knowledge = [...(row.knowledge ?? [])]
  form.workflow = (row.workflow ?? []).map((item) => ({
    capability: item.capability,
    name: item.name,
    variables: { ...(item.variables ?? {}) },
  }))
  triggersText.value = form.triggers.join(',')
  requiredText.value = form.required.join(',')
  knowledgeText.value = form.knowledge.join(',')
  editVisible.value = true
}

function addWorkflowStep(): void {
  form.workflow.push({
    capability: labStore.capabilities[0]?.code ?? '',
    name: '',
    variables: {},
  })
}

async function save(): Promise<void> {
  if (!form.code.trim() || !form.name.trim() || !form.capability) {
    ElMessage.warning('编码、名称、主能力为必填')
    return
  }
  const payload = {
    code: form.code.trim(),
    name: form.name.trim(),
    capability: form.capability,
    description: form.description,
    triggers: splitList(triggersText.value),
    required: splitList(requiredText.value),
    knowledge: splitList(knowledgeText.value),
    workflow: form.workflow
      .filter((item) => item.capability)
      .map((item) => ({
        capability: item.capability,
        name: item.name || labelOf(item.capability),
        variables: item.variables ?? {},
      })),
  }
  saving.value = true
  try {
    if (form.id) {
      await workbenchApi.updateEntry(form.id, payload)
    } else {
      await workbenchApi.createEntry(payload)
    }
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function publish(row: WorkbenchEntry): Promise<void> {
  try {
    await workbenchApi.publishEntry(row.id)
    ElMessage.success(`已发布 v${row.version + 1}`)
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '发布失败')
  }
}

async function remove(row: WorkbenchEntry): Promise<void> {
  try {
    await workbenchApi.removeEntry(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

function openRun(row: WorkbenchEntry): void {
  current.value = row
  runVariablesText.value = '{}'
  runResult.value = null
  runVisible.value = true
}

async function run(): Promise<void> {
  if (!current.value) {
    return
  }
  let variables: Record<string, unknown>
  try {
    const parsed = JSON.parse(runVariablesText.value || '{}') as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      ElMessage.warning('参数必须是 JSON 对象')
      return
    }
    variables = parsed as Record<string, unknown>
  } catch {
    ElMessage.warning('参数不是合法 JSON')
    return
  }
  running.value = true
  try {
    runResult.value = await workbenchApi.runEntry(current.value.id, {
      variables,
      moduleId: labStore.currentModuleId,
      continueOnError: true,
    })
    runVisible.value = false
    resultVisible.value = true
    ElMessage.success('入口执行完成')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '执行失败')
  } finally {
    running.value = false
  }
}

onMounted(async () => {
  await labStore.loadCapabilities()
  await labStore.loadModuleOptions(appStore.currentProjectId ?? undefined)
  await load()
})
</script>

<style scoped>
.workflow-editor {
  width: 100%;
}

.workflow-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.run-alert {
  margin-bottom: 10px;
}

.output-pre {
  background: var(--aitp-subtle);
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  margin: 12px 0 0;
  max-height: 360px;
  overflow-y: auto;
}
</style>
