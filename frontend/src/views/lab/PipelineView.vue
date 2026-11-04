<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">多入口顺序执行</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="MagicStick" @click="generateFromPreflight">预检生成步骤</el-button>
        <el-button :icon="RefreshLeft" @click="resetSteps">清空</el-button>
        <el-button type="primary" :icon="Promotion" :loading="running" @click="run">
          顺序执行
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
      <div class="panel panel-pad">
        <h3 class="section-title">
          执行步骤
          <el-tag size="small" effect="plain">{{ steps.length }} 步</el-tag>
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
              <el-form-item v-for="name in requiredOf(step)" :key="`r-${name}`" :label="name">
                <el-input
                  v-model="step.variables[name]"
                  type="textarea"
                  :rows="2"
                  :placeholder="`必填：${name}`"
                />
              </el-form-item>
              <el-form-item v-for="name in optionalOf(step)" :key="`o-${name}`" :label="name">
                <el-input v-model="step.variables[name]" :placeholder="`可选：${name}`" />
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
            placeholder="描述要做的事，例如：支付回调重复推送导致金额翻倍"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="preflightVisible = false">取消</el-button>
        <el-button type="primary" :loading="preflighting" @click="confirmPreflight">预检</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Bottom,
  Delete,
  MagicStick,
  Plus,
  Promotion,
  RefreshLeft,
  Top,
} from '@element-plus/icons-vue'
import { labApi } from '@/api'
import { percentText } from '@/utils/format'
import type { PipelineResult, PreflightResult } from '@/api/types'
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
const appStore = useAppStore()
const { variablesOf, loadMany } = usePromptTemplates()

const steps = ref<StepForm[]>([])
const result = ref<PipelineResult | null>(null)
const running = ref(false)
const preflight = ref<PreflightResult | null>(null)
const preflightVisible = ref(false)
const preflightText = ref('')
const preflighting = ref(false)
const checking = ref(false)

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
  return '待执行'
}

function statusTagType(status: string): 'success' | 'danger' | 'info' | 'warning' {
  if (status === 'success') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
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
      continueOnError: true,
    })
    ElMessage.success('流水线执行完成')
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
