<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">多入口顺序执行</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="MagicStick" @click="generateFromPreflight">预检生成步骤</el-button>
        <el-button :icon="FolderAdd" :disabled="!steps.length" @click="openSaveEntry">
          保存为编排
        </el-button>
        <el-button :icon="RefreshLeft" @click="resetSteps">清空</el-button>
        <el-switch
          v-model="continueOnError"
          active-text="失败继续"
          inactive-text="失败中断"
          inline-prompt
          style="margin: 0 8px"
        />
        <el-button type="primary" :icon="Promotion" :loading="running" @click="run">
          顺序执行
        </el-button>
        <el-button v-if="running" type="warning" :icon="CircleClose" :loading="stopping" @click="stop">
          停止
        </el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="pipeline-alert"
      title="多个入口按顺序跑成一个流水线：父任务串起子任务，每步都留轨迹，失败可选择继续或中断。"
    />

    <div class="pipeline-layout">
      <div class="pipeline-left">
        <div class="panel panel-pad">
          <h3 class="section-title">
            已保存编排
            <el-tag size="small" effect="plain">{{ entries.length }}</el-tag>
            <el-button link type="primary" class="saved-panel__refresh" :loading="entriesLoading" @click="loadEntries">
              刷新
            </el-button>
          </h3>
          <div v-loading="entriesLoading" class="saved-entry-list">
            <div
              v-for="entry in entries"
              :key="entry.id"
              class="saved-entry"
              :class="{ 'is-active': loadedEntry?.id === entry.id }"
            >
              <div class="saved-entry__main">
                <span class="saved-entry__name">{{ entry.name }}</span>
                <span class="muted saved-entry__meta">
                  {{ entry.workflow?.length ?? 0 }} 步 · v{{ entry.version }} ·
                  {{ entryStatusText(entry.status) }}
                </span>
              </div>
              <div class="saved-entry__actions">
                <el-button link type="primary" :disabled="entry.status === 'published'" @click="loadEntry(entry)">载入</el-button>
                <el-button link :disabled="entry.status === 'published'" @click="publishEntry(entry)">
                  发布
                </el-button>
                <el-button link type="danger" :disabled="entry.status === 'published'" @click="removeEntry(entry)">删除</el-button>
              </div>
            </div>
            <span v-if="!entries.length && !entriesLoading" class="muted saved-panel__empty">
              还没有保存的编排；配好步骤后点「保存为编排」
            </span>
          </div>
          <p class="muted saved-panel__hint">
            草稿可载入继续改参数再执行；发布即终态，锁定编辑与删除，如需调整请新建编排。
          </p>
        </div>

        <div class="panel panel-pad" :class="{ 'steps-fullscreen': stepsFullscreen }">
          <h3 class="section-title">
            执行步骤
            <el-tag size="small" effect="plain">{{ steps.length }} 步</el-tag>
            <el-tag v-if="loadedEntry" size="small" type="success" effect="plain">
              来自「{{ loadedEntry.name }}」
            </el-tag>
            <el-button
              link
              type="primary"
              class="steps-fullscreen-toggle"
              :icon="stepsFullscreen ? Close : FullScreen"
              @click="stepsFullscreen = !stepsFullscreen"
            >
              {{ stepsFullscreen ? '退出全屏' : '全屏编辑' }}
            </el-button>
          </h3>

          <div v-if="preflight || steps.length" class="preflight-box">
            <div class="preflight-box__head">
              <span>预检结论</span>
              <div class="preflight-box__actions">
                <el-button link type="primary" :loading="checking" @click="recheck">重新预检</el-button>
                <el-tag v-if="preflight" size="small" :type="preflight.ready ? 'success' : 'warning'">
                  {{ preflight.ready ? '可执行' : '有阻断项' }}
                </el-tag>
              </div>
            </div>
            <template v-if="preflight">
              <el-progress
                :percentage="preflight.overallCompleteness * 100"
                :format="percentText"
                :stroke-width="10"
                :status="preflight.ready ? 'success' : 'exception'"
              />
              <ul class="preflight-box__list">
                <li v-for="task in preflight.tasks" :key="task.capability">
                  <strong>{{ task.label }}</strong>
                  <span class="muted">完整度 {{ percentText(task.completeness * 100) }}</span>
                  <el-tag size="small" effect="plain">{{ task.role === 'primary' ? '主任务' : '辅助' }}</el-tag>
                  <span v-if="task.missing.length" class="missing">
                    缺：{{ task.missing.join('、') }}
                  </span>
                </li>
              </ul>
            </template>
            <p v-else class="muted preflight-box__hint">点「重新预检」看当前步骤还缺什么</p>
          </div>

          <el-empty v-if="!steps.length" description="还没有步骤，点「预检生成步骤」或「添加步骤」" :image-size="70" />

          <div v-for="(step, index) in steps" :key="step.key" class="step-card">
            <div class="step-card__head">
              <el-tag size="small" type="info">第 {{ index + 1 }} 步</el-tag>
              <el-select
                v-model="step.capability"
                placeholder="选择能力"
                style="width: 190px"
                @change="onCapabilityChange(step)"
              >
                <el-option
                  v-for="item in labStore.capabilities"
                  :key="item.code"
                  :label="item.label"
                  :value="item.code"
                />
              </el-select>
              <el-input v-model="step.name" placeholder="步骤名" style="flex: 1; min-width: 120px" />
              <el-button text :icon="Top" :disabled="index === 0" @click="move(index, -1)" />
              <el-button
                text
                :icon="Bottom"
                :disabled="index === steps.length - 1"
                @click="move(index, 1)"
              />
              <el-button text type="danger" :icon="Delete" @click="removeStep(index)" />
            </div>

            <div class="step-card__body">
              <el-form label-width="110px" label-position="right" size="small">
                <el-form-item v-for="name in requiredOf(step)" :key="`r-${name}`" :label="variableLabel(step.capability, name)">
                  <el-input
                    v-model="step.variables[name]"
                    type="textarea"
                    :rows="stepsFullscreen ? 6 : 3"
                    :placeholder="textareaHint(`请输入${variableLabel(step.capability, name)}（必填）`)"
                  />
                </el-form-item>
                <el-form-item v-for="name in optionalOf(step)" :key="`o-${name}`" :label="variableLabel(step.capability, name)">
                  <el-input
                    v-model="step.variables[name]"
                    :placeholder="`可选：${variableLabel(step.capability, name)}`"
                  />
                </el-form-item>
                <el-form-item label="功能点">
                  <el-select v-model="step.moduleId" clearable placeholder="继承全局功能点" style="width: 100%">
                    <el-option
                      v-for="item in labStore.moduleOptions"
                      :key="item.id"
                      :label="item.path"
                      :value="item.id"
                    />
                  </el-select>
                </el-form-item>
              </el-form>
            </div>
          </div>

          <el-button :icon="Plus" plain class="add-step" @click="addStep">添加步骤</el-button>
        </div>
      </div>

      <div class="panel panel-pad">
        <h3 class="section-title">
          执行轨迹
          <el-tag v-if="result" size="small" effect="plain">{{ statusText(result.status) }}</el-tag>
        </h3>

        <el-table v-if="result" :data="result.steps" border size="small">
          <el-table-column prop="order" label="#" width="52" align="center" />
          <el-table-column prop="stepName" label="步骤" min-width="150" />
          <el-table-column prop="label" label="能力" width="130" />
          <el-table-column label="状态" width="90">
            <template #default="scope">
              <el-tag size="small" :type="statusTagType(scope.row.status)">
                {{ statusText(scope.row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="model" label="模型" width="130">
            <template #default="scope">
              <span class="muted">{{ scope.row.model || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="86" align="right">
            <template #default="scope">{{ scope.row.latencyMs ?? '-' }}</template>
          </el-table-column>
          <el-table-column label="说明" min-width="220">
            <template #default="scope">
              <span class="muted">{{ scope.row.errorMessage || scope.row.summary || '-' }}</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="尚未执行" :image-size="80" />

        <div v-if="result" class="pipeline-output">
          <div class="pipeline-output__head">
            <span class="section-title" style="margin: 0">汇总产出</span>
            <el-button link type="primary" @click="copyOutput">复制</el-button>
          </div>
          <pre class="output-pre">{{ result.output || '（无产出）' }}</pre>
          <el-alert
            v-if="result.errorMessage"
            type="danger"
            show-icon
            :closable="false"
            :title="result.errorMessage"
          />
        </div>
      </div>
    </div>

    <el-dialog v-model="preflightVisible" title="预检生成步骤" width="620px">
      <el-form label-width="96px">
        <el-form-item label="需求描述">
          <el-input
            v-model="preflightText"
            type="textarea"
            :rows="4"
            :placeholder="textareaHint('描述要做的事，例如：支付回调重复推送导致金额翻倍')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="preflightVisible = false">取消</el-button>
        <el-button type="primary" :loading="preflighting" @click="confirmPreflight">预检</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="saveVisible"
      :title="loadedEntry ? '更新编排' : '保存为编排'"
      width="560px"
    >
      <el-alert
        v-if="loadedEntry"
        type="info"
        show-icon
        :closable="false"
        class="save-entry-alert"
        :title="`保存会更新「${loadedEntry.name}」并保留它的版本号；要另存为新编排请先点「清空」。`"
      />
      <el-form label-width="76px">
        <el-form-item label="编码" required>
          <el-input
            v-model="entryForm.code"
            :disabled="Boolean(loadedEntry)"
            placeholder="例如 requirement-to-cases"
          />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="entryForm.name" placeholder="例如 需求评审 → 用例生成" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="entryForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="步骤">
          <span class="muted">
            {{ steps.length }} 步：{{
              steps.map((step) => step.name || labelOf(step.capability)).join(' → ')
            }}
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="saveVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingEntry" @click="saveEntry">
          {{ loadedEntry ? '更新' : '保存' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Bottom,
  CircleClose,
  Close,
  Delete,
  FolderAdd,
  FullScreen,
  MagicStick,
  Plus,
  Promotion,
  RefreshLeft,
  Top,
} from '@element-plus/icons-vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { labApi, workbenchApi } from '@/api'
import { percentText } from '@/utils/format'
import { textareaHint } from '@/utils/inputHints'
import { variableLabel } from './capabilityForms'
import { useAiGuardStore } from '@/stores/aiGuard'
import type { PipelineResult, PreflightResult, WorkbenchEntry } from '@/api/types'
import { usePromptTemplates } from '@/composables/usePromptTemplates'
import { useAppStore } from '@/stores/app'
import { useLabStore } from '@/stores/lab'

interface StepForm {
  key: string
  capability: string
  name: string
  moduleId: number | null
  variables: Record<string, string>
}

const labStore = useLabStore()
const aiGuard = useAiGuardStore()
const appStore = useAppStore()
const { variablesOf, loadMany } = usePromptTemplates()

const steps = ref<StepForm[]>([])
const result = ref<PipelineResult | null>(null)
const running = ref(false)
const stopping = ref(false)
const continueOnError = ref(false)
const preflight = ref<PreflightResult | null>(null)
const preflightVisible = ref(false)
const preflightText = ref('')
const preflighting = ref(false)
const checking = ref(false)
const stepsFullscreen = ref(false)

const entries = ref<WorkbenchEntry[]>([])
const entriesLoading = ref(false)
const loadedEntry = ref<WorkbenchEntry | null>(null)
const saveVisible = ref(false)
const savingEntry = ref(false)
const entryForm = reactive({ code: '', name: '', description: '' })

/** 每个能力的变量清单，能力 → { 必填, 可选 }。 */
const varMap = reactive<Record<string, { required: string[]; optional: string[] }>>({})

function requiredOf(step: StepForm): string[] {
  return varMap[step.capability]?.required ?? []
}

function optionalOf(step: StepForm): string[] {
  return varMap[step.capability]?.optional ?? []
}

function labelOf(capability: string): string {
  return labStore.capabilities.find((item) => item.code === capability)?.label ?? capability
}

/** 变量值转可编辑文本：对象/数组序列化为 JSON，避免输入框露出 [object Object]。 */
function toText(value: unknown): string {
  if (value == null) {
    return ''
  }
  if (typeof value === 'object') {
    return JSON.stringify(value, null, 2)
  }
  return String(value)
}

function promptCodeOf(capability: string): string {
  return labStore.capabilities.find((item) => item.code === capability)?.promptCode ?? ''
}

async function refreshVars(capability: string): Promise<void> {
  if (!capability || varMap[capability]) {
    return
  }
  const code = promptCodeOf(capability)
  if (!code) {
    varMap[capability] = { required: [], optional: [] }
    return
  }
  varMap[capability] = await variablesOf(code)
}

function addStep(capability = ''): void {
  steps.value.push({
    key: `step-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    capability,
    name: capability ? labelOf(capability) : '',
    moduleId: null,
    variables: {},
  })
  if (capability) {
    void refreshVars(capability).then(() => {
      const step = steps.value[steps.value.length - 1]
      syncVariables(step)
    })
  }
}

function removeStep(index: number): void {
  steps.value.splice(index, 1)
}

function move(index: number, delta: number): void {
  const target = index + delta
  if (target < 0 || target >= steps.value.length) {
    return
  }
  const list = steps.value
  const [item] = list.splice(index, 1)
  list.splice(target, 0, item)
}

/** 能力变了就换一套变量；保留同名变量里已填的内容。 */
function syncVariables(step: StepForm): void {
  const names = [...requiredOf(step), ...optionalOf(step)]
  const next: Record<string, string> = {}
  names.forEach((name) => {
    next[name] = step.variables[name] ?? ''
  })
  step.variables = next
}

async function onCapabilityChange(step: StepForm): Promise<void> {
  step.name = labelOf(step.capability)
  await refreshVars(step.capability)
  syncVariables(step)
}

function resetSteps(): void {
  steps.value = []
  result.value = null
  preflight.value = null
  loadedEntry.value = null
}

function entryStatusText(status: string): string {
  if (status === 'published') return '已发布'
  if (status === 'archived') return '已归档'
  return '草稿'
}

async function loadEntries(): Promise<void> {
  entriesLoading.value = true
  try {
    const data = await workbenchApi.entries({ pageSize: 100 })
    entries.value = data.list ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载已保存编排失败')
  } finally {
    entriesLoading.value = false
  }
}

function openSaveEntry(): void {
  if (!steps.value.some((step) => step.capability)) {
    ElMessage.warning('至少要有一个已选能力的步骤')
    return
  }
  entryForm.code = loadedEntry.value?.code ?? ''
  entryForm.name =
    loadedEntry.value?.name ??
    steps.value
      .map((step) => step.name || labelOf(step.capability))
      .filter(Boolean)
      .join(' → ')
      .slice(0, 120)
  entryForm.description = loadedEntry.value?.description ?? ''
  saveVisible.value = true
}

async function saveEntry(): Promise<void> {
  const code = entryForm.code.trim()
  const name = entryForm.name.trim()
  if (!code || !name) {
    ElMessage.warning('编码和名称为必填')
    return
  }
  const usable = steps.value.filter((step) => step.capability)
  if (!usable.length) {
    ElMessage.warning('至少要有一个已选能力的步骤')
    return
  }
  const payload = {
    code,
    name,
    capability: usable[0].capability,
    description: entryForm.description,
    triggers: loadedEntry.value?.triggers ?? [],
    required: loadedEntry.value?.required ?? [],
    knowledge: loadedEntry.value?.knowledge ?? [],
    workflow: usable.map((step) => ({
      capability: step.capability,
      name: step.name || labelOf(step.capability),
      moduleId: step.moduleId,
      variables: Object.fromEntries(
        Object.entries(step.variables).filter(([, value]) => String(value ?? '').trim()),
      ),
    })),
  }
  savingEntry.value = true
  try {
    if (loadedEntry.value) {
      await workbenchApi.updateEntry(loadedEntry.value.id, payload)
      ElMessage.success('编排已更新')
    } else {
      await workbenchApi.createEntry(payload)
      ElMessage.success('编排已保存')
    }
    saveVisible.value = false
    await loadEntries()
    loadedEntry.value = entries.value.find((item) => item.code === code) ?? null
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    savingEntry.value = false
  }
}

function ensureVariables(step: StepForm): void {
  ;[...requiredOf(step), ...optionalOf(step)].forEach((name) => {
    if (!(name in step.variables)) {
      step.variables[name] = ''
    }
  })
}

async function loadEntry(entry: WorkbenchEntry): Promise<void> {
  if (entry.status === 'published') {
    ElMessage.warning('已发布的编排为终态，不能载入编辑；如需调整请新建编排')
    return
  }
  result.value = null
  preflight.value = null
  steps.value = (entry.workflow ?? []).map((item) => ({
    key: `step-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    capability: item.capability,
    name: item.name || labelOf(item.capability),
    moduleId: item.moduleId ?? null,
    variables: Object.fromEntries(
      Object.entries(item.variables ?? {}).map(([name, value]) => [name, toText(value)]),
    ),
  }))
  await Promise.all(steps.value.map((step) => refreshVars(step.capability)))
  steps.value.forEach(ensureVariables)
  loadedEntry.value = entry
  ElMessage.success(`已载入「${entry.name}」，可直接执行`)
}

async function publishEntry(entry: WorkbenchEntry): Promise<void> {
  try {
    const updated = await workbenchApi.publishEntry(entry.id)
    ElMessage.success(`已发布 v${updated.version}`)
    await loadEntries()
    if (loadedEntry.value?.id === entry.id) {
      loadedEntry.value = entries.value.find((item) => item.id === entry.id) ?? updated
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '发布失败')
  }
}

async function removeEntry(entry: WorkbenchEntry): Promise<void> {
  if (entry.status === 'published') {
    ElMessage.warning('已发布的编排为终态，不能删除')
    return
  }
  try {
    await ElMessageBox.confirm(
      `删除编排「${entry.name}」？已产生的执行记录不受影响。`,
      '删除编排',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await workbenchApi.removeEntry(entry.id)
    if (loadedEntry.value?.id === entry.id) {
      loadedEntry.value = null
    }
    ElMessage.success('已删除')
    await loadEntries()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

async function confirmPreflight(): Promise<void> {
  const text = preflightText.value.trim()
  if (!text) {
    ElMessage.warning('请先描述要做的事')
    return
  }
  preflighting.value = true
  try {
    const data = await labApi.preflight({
      text,
      projectId: appStore.currentProjectId ?? undefined,
      topK: 3,
    })
    preflight.value = data
    preflightVisible.value = false
    if (!data.tasks.length) {
      ElMessage.warning('没有匹配到候选能力')
      return
    }
    steps.value = []
    for (const task of data.tasks) {
      addStep(task.capability)
    }
    await Promise.all(steps.value.map((step) => refreshVars(step.capability)))
    steps.value.forEach((step) => {
      const task = data.tasks.find((item) => item.capability === step.capability)
      step.name = task?.label ?? step.name
      syncVariables(step)
      const missing = task?.missing ?? []
      missing.forEach((name) => {
        step.variables[name] = ''
      })
    })
    ElMessage.success(`预检完成：主任务 ${data.primary || '无'}，完整度 ${percentText(data.overallCompleteness * 100)}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '预检失败')
  } finally {
    preflighting.value = false
  }
}

async function generateFromPreflight(): Promise<void> {
  preflightText.value = preflightText.value || ''
  preflightVisible.value = true
}

/** 按当前步骤重新体检：各步变量合并后交回后端算一次，缺项与完整度立刻更新。 */
async function recheck(): Promise<void> {
  const capabilities = steps.value.map((step) => step.capability).filter(Boolean)
  if (!capabilities.length) {
    ElMessage.warning('还没有步骤')
    return
  }
  const variables: Record<string, string> = {}
  steps.value.forEach((step) => {
    Object.entries(step.variables).forEach(([name, value]) => {
      if (String(value ?? '').trim()) {
        variables[name] = String(value)
      }
    })
  })
  checking.value = true
  try {
    preflight.value = await labApi.preflight({
      capabilities,
      variables,
      projectId: appStore.currentProjectId ?? undefined,
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '预检失败')
  } finally {
    checking.value = false
  }
}

function statusText(status: string): string {
  if (status === 'success') return '成功'
  if (status === 'failed') return '失败'
  if (status === 'skipped') return '跳过'
  if (status === 'running') return '执行中'
  if (status === 'cancelled') return '已取消'
  return '待执行'
}

function statusTagType(status: string): 'success' | 'danger' | 'info' | 'warning' {
  if (status === 'success') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

async function stop(): Promise<void> {
  stopping.value = true
  try {
    const outcome = await labApi.compositeCancel()
    if (outcome.cancelled) {
      ElMessage.warning('已请求停止，当前步骤跑完后结束')
    } else {
      ElMessage.info('当前没有正在执行的流水线')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '停止失败')
  } finally {
    stopping.value = false
  }
}

async function run(): Promise<void> {
  if (!steps.value.length) {
    ElMessage.warning('至少要有一个步骤')
    return
  }
  const missing: string[] = []
  steps.value.forEach((step, index) => {
    requiredOf(step).forEach((name) => {
      if (!String(step.variables[name] ?? '').trim()) {
        missing.push(`第 ${index + 1} 步「${step.name || labelOf(step.capability)}」缺 ${name}`)
      }
    })
  })
  if (missing.length) {
    ElMessage.warning(missing.slice(0, 3).join('；'))
    return
  }
  if (!(await aiGuard.ensureExternalConfirmed())) {
    return
  }
  running.value = true
  try {
    const payload = steps.value.map((step) => ({
      capability: step.capability,
      name: step.name || labelOf(step.capability),
      moduleId: step.moduleId,
      variables: Object.fromEntries(
        Object.entries(step.variables).filter(([, value]) => String(value ?? '').trim()),
      ),
    }))
    result.value = await labApi.compositeRun({
      steps: payload,
      title: steps.value.map((step) => step.name || labelOf(step.capability)).join(' → '),
      moduleId: labStore.currentModuleId,
      continueOnError: continueOnError.value,
    })
    const status = result.value?.status
    if (status === 'success') {
      ElMessage.success('流水线执行完成')
    } else if (status === 'cancelled') {
      ElMessage.warning('流水线已取消')
    } else {
      const failedStep = result.value?.steps.find((step) => step.status === 'failed')
      ElMessage.error(
        failedStep
          ? `第 ${failedStep.order} 步「${failedStep.stepName || failedStep.label}」失败：${failedStep.errorMessage || '未返回原因'}`
          : `流水线执行失败：${result.value?.errorMessage || '存在失败步骤'}`,
      )
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '执行失败')
  } finally {
    running.value = false
  }
}

function copyOutput(): void {
  const text = result.value?.output ?? ''
  if (!text) {
    ElMessage.warning('暂无产出')
    return
  }
  void navigator.clipboard.writeText(text).then(
    () => ElMessage.success('已复制到剪贴板'),
    () => ElMessage.error('复制失败，请手动选择文本'),
  )
}

onMounted(async () => {
  await labStore.loadCapabilities()
  await labStore.loadModuleOptions(appStore.currentProjectId ?? undefined)
  const codes = labStore.capabilities
    .map((item) => item.promptCode)
    .filter((code): code is string => Boolean(code))
  await loadMany(codes)
  codes.forEach((code) => {
    void variablesOf(code).then((vars) => {
      const owner = labStore.capabilities.find((item) => item.promptCode === code)
      if (owner) {
        varMap[owner.code] = vars
      }
    })
  })
  await loadEntries()
})
</script>

<style scoped>
.pipeline-alert {
  margin-bottom: 12px;
}

.pipeline-layout {
  display: grid;
  grid-template-columns: minmax(380px, 520px) 1fr;
  gap: 12px;
  align-items: start;
}

.pipeline-left {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.saved-panel__refresh {
  margin-left: auto;
}

.saved-entry-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 220px;
  overflow-y: auto;
}

.saved-entry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 9px;
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
}

.saved-entry.is-active {
  border-color: var(--el-color-primary);
  background: var(--aitp-hover);
}

.saved-entry__main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.saved-entry__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
}

.saved-entry__meta {
  font-size: 12px;
}

.saved-entry__actions {
  display: flex;
  flex-shrink: 0;
  gap: 2px;
}

.saved-panel__empty {
  font-size: 12px;
}

.saved-panel__hint {
  margin: 8px 0 0;
  font-size: 12px;
}

.save-entry-alert {
  margin-bottom: 12px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  font-size: 15px;
}

.preflight-box {
  border: 1px solid var(--aitp-border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  background: var(--aitp-hover);
}

.preflight-box__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-weight: 600;
}

.preflight-box__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.preflight-box__hint {
  margin: 0;
  font-size: 13px;
}

.preflight-box__list {
  margin: 8px 0 0;
  padding-left: 18px;
  line-height: 1.9;
  font-size: 13px;
}

.preflight-box__list strong {
  margin-right: 8px;
}

.missing {
  color: var(--el-color-danger);
  margin-left: 8px;
}

.step-card {
  border: 1px solid var(--aitp-border);
  border-radius: 8px;
  margin-bottom: 10px;
  background: var(--aitp-panel);
}

.step-card__head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--aitp-border);
}

.step-card__body {
  padding: 10px 12px 2px;
}

.add-step {
  width: 100%;
}

.steps-fullscreen-toggle {
  margin-left: auto;
}

/* 全屏编辑：把执行步骤面板提升为覆盖视口的编辑区，仍编辑同一份 steps 状态。 */
.steps-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 2000;
  margin: 0;
  border-radius: 0;
  overflow-y: auto;
  background: var(--aitp-panel);
  padding: 20px 28px;
}

.steps-fullscreen .step-card__body :deep(textarea) {
  min-height: 120px;
}

.pipeline-output {
  margin-top: 14px;
}

.pipeline-output__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
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
  margin: 0 0 10px;
  max-height: 420px;
  overflow-y: auto;
}
</style>
