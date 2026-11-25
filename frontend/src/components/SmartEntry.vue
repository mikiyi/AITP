<template>
  <div class="smart-entry panel panel-pad">
    <div class="smart-entry__row">
      <div class="smart-entry__field">
        <el-input
          v-model="text"
          type="textarea"
          :rows="2"
          resize="vertical"
          :placeholder="textareaHint('描述你要做的事（例：支付回调重复推送导致金额翻倍），或上传需求文档/截图，自动匹配 AI 能力与功能点')"
          @keyup.ctrl.enter="route"
        />
        <AiAttachmentInput v-model:attachment-ids="attachmentIds" compact />
      </div>
      <div class="smart-entry__actions">
        <el-button type="primary" :icon="Search" :loading="loading" @click="route">智能匹配</el-button>
        <span class="muted smart-entry__hint">Ctrl + Enter 快捷匹配</span>
      </div>
    </div>

    <div v-if="result" class="smart-entry__result">
      <div v-if="hasAttachmentInput" class="smart-entry__transcript">
        <el-tag size="small" type="success" effect="plain">附件已转文本参与匹配</el-tag>
        <span v-if="result.transcription" class="muted">
          图片转写 {{ result.transcription.length }} 字
        </span>
        <span v-if="result.truncated" class="muted">长文本已截取前 4000 字参与匹配</span>
        <ul v-if="result.input?.notes?.length" class="smart-entry__notes">
          <li v-for="(note, index) in result.input.notes" :key="index">{{ note }}</li>
        </ul>
      </div>
      <div class="smart-entry__group">
        <div class="smart-entry__group-title">
          匹配的 AI 能力
          <el-tag v-if="result.source === 'fallback'" size="small" type="info" effect="plain">
            离线关键词匹配
          </el-tag>
        </div>
        <div class="smart-entry__cards">
          <button
            v-for="item in result.capabilityRoutes"
            :key="item.capability"
            class="smart-entry__card"
            type="button"
            @click="openCapability(item)"
          >
            <span class="smart-entry__card-title">{{ item.label }}</span>
            <span class="smart-entry__card-reason">{{ item.reason }}</span>
            <span class="smart-entry__card-score">{{ Math.round(item.score * 100) }} 分</span>
          </button>
          <span v-if="!result.capabilityRoutes.length" class="muted">未匹配到能力候选</span>
        </div>
      </div>

      <div class="smart-entry__group">
        <div class="smart-entry__group-title">归属功能点</div>
        <div class="smart-entry__cards">
          <button
            v-for="item in result.moduleRoutes"
            :key="item.moduleId"
            class="smart-entry__card"
            type="button"
            @click="openModule(item)"
          >
            <span class="smart-entry__card-title">{{ item.path }}</span>
            <span class="smart-entry__card-reason">{{ item.reason }}</span>
            <span class="smart-entry__card-score">{{ Math.round(item.score * 100) }} 分</span>
          </button>
          <span v-if="!result.moduleRoutes.length" class="muted">未匹配到功能点候选</span>
        </div>
      </div>

      <div class="smart-entry__group">
        <div class="smart-entry__group-title">
          执行前预检
          <el-tag size="small" :type="preflight?.ready ? 'success' : 'warning'" effect="plain">
            {{ preflight?.ready ? '可执行' : '有阻断项' }}
          </el-tag>
        </div>
        <div v-if="preflight" class="preflight">
          <div class="preflight__bar">
            <span class="muted">整体完整度</span>
            <el-progress
              :percentage="preflight.overallCompleteness * 100"
              :format="percentText"
              :stroke-width="10"
              :status="preflight.ready ? 'success' : 'exception'"
              style="flex: 1"
            />
            <el-tag size="small" :type="preflight.modelReady ? 'success' : 'danger'" effect="plain">
              {{ preflight.modelReady ? '模型可用' : '无可用模型' }}
            </el-tag>
          </div>

          <div class="preflight__order">
            <span class="muted">执行顺序</span>
            <template v-for="(code, index) in preflight.order" :key="code">
              <el-tag size="small" effect="plain">{{ index + 1 }}. {{ labelOf(code) }}</el-tag>
              <span v-if="index < preflight.order.length - 1" class="arrow">→</span>
            </template>
            <span v-if="!preflight.order.length" class="muted">无</span>
          </div>

          <el-table :data="preflight.tasks" size="small" border>
            <el-table-column prop="label" label="任务" width="130" />
            <el-table-column label="角色" width="76" align="center">
              <template #default="scope">
                <el-tag size="small" :type="scope.row.role === 'primary' ? 'primary' : 'info'" effect="plain">
                  {{ scope.row.role === 'primary' ? '主任务' : '辅助' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="完整度" width="130">
              <template #default="scope">
                <el-progress
                  :percentage="scope.row.completeness * 100"
                  :format="percentText"
                  :stroke-width="8"
                  :status="scope.row.ready ? 'success' : 'exception'"
                />
              </template>
            </el-table-column>
            <el-table-column label="缺项" min-width="180">
              <template #default="scope">
                <span v-if="scope.row.missing.length" class="missing">
                  {{ missingLabels(scope.row.capability, scope.row.missing) }}
                </span>
                <span v-else class="muted">必填齐全</span>
                <div v-if="scope.row.missingOptional.length" class="muted optional-line">
                  可选未填：{{ missingLabels(scope.row.capability, scope.row.missingOptional) }}
                </div>
              </template>
            </el-table-column>
          </el-table>

          <ul v-if="preflight.blockers.length" class="preflight__notes">
            <li v-for="(item, index) in preflight.blockers" :key="`b-${index}`" class="blocker">
              {{ localizedNote(item) }}
            </li>
          </ul>
          <ul v-if="preflight.warnings.length" class="preflight__notes">
            <li v-for="(item, index) in preflight.warnings" :key="`w-${index}`" class="warning">
              {{ localizedNote(item) }}
            </li>
          </ul>
        </div>
        <span v-else class="muted">点击「智能匹配」后给出缺项与执行顺序</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { labApi } from '@/api'
import type { ModuleRouteResult, PreflightResult, RouteResponse, RouteResult } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { percentText } from '@/utils/format'
import { textareaHint } from '@/utils/inputHints'
import { variableLabel } from '@/views/lab/capabilityForms'
import { useAiGuardStore } from '@/stores/aiGuard'
import AiAttachmentInput from '@/components/AiAttachmentInput.vue'

const router = useRouter()
const aiGuard = useAiGuardStore()
const appStore = useAppStore()

const text = ref('')
const loading = ref(false)
const result = ref<RouteResponse | null>(null)
const preflight = ref<PreflightResult | null>(null)
const attachmentIds = ref<string[]>([])

/** 本轮匹配是否带过附件：用于展示「已转文本」与降级说明。 */
const hasAttachmentInput = computed(() => {
  const input = result.value?.input
  if (!input) return false
  return input.documents.length > 0 || input.images.length > 0
})

/** 进入能力页时优先带转写后的完整文本，纯文本输入则等价于原文本。 */
function navText(): string {
  return (result.value?.effectiveText || text.value).trim()
}

/**
 * 能力名以服务端预检结果为准（PreflightTask.label 来自后端能力目录），
 * 前端不再另写一份映射，避免与后端目录对不上、把中文名显示成英文编号。
 */
function labelOf(code: string): string {
  return preflight.value?.tasks.find((item) => item.capability === code)?.label ?? code
}

/** 缺项里的变量名转中文标签，避免表格直接露出英文 key。 */
function missingLabels(capability: string, names: string[]): string {
  return names.map((name) => variableLabel(capability, name)).join('、')
}

/** 阻断/提示文案由后端拼接，里面的英文变量名展示前统一换成中文标签。 */
function localizedNote(text: string): string {
  let out = text
  ;(preflight.value?.tasks ?? []).forEach((task) => {
    ;[...task.required, ...task.optional].forEach((name) => {
      out = out.split(name).join(variableLabel(task.capability, name))
    })
  })
  return out
}

async function route(): Promise<void> {
  const value = text.value.trim()
  const ids = [...attachmentIds.value]
  if (!value && !ids.length) {
    ElMessage.warning('请先描述你的需求，或上传文档/截图')
    return
  }
  // 智能路由会调用模型，先确认数据已脱敏
  if (!(await aiGuard.ensureExternalConfirmed())) {
    return
  }
  loading.value = true
  preflight.value = null
  try {
    result.value = await labApi.route({
      text: value,
      attachmentIds: ids,
      projectId: appStore.currentProjectId ?? undefined,
      topK: 3,
    })
    await loadPreflight(navText())
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '匹配失败')
  } finally {
    loading.value = false
  }
}

/** 预检与匹配并行做，纯本地计算，不调模型。 */
async function loadPreflight(value: string): Promise<void> {
  try {
    preflight.value = await labApi.preflight({
      text: value,
      projectId: appStore.currentProjectId ?? undefined,
      topK: 3,
    })
  } catch {
    preflight.value = null
  }
}

function openCapability(item: RouteResult): void {
  const query: Record<string, string> = { text: navText() }
  if (item.targetModuleId) {
    query.moduleId = String(item.targetModuleId)
  }
  void router.push({ path: `/lab/${item.capability}`, query })
}

function openModule(item: ModuleRouteResult): void {
  const capability = result.value?.capabilityRoutes[0]
  const query: Record<string, string> = { text: navText(), moduleId: String(item.moduleId) }
  void router.push({
    path: capability ? `/lab/${capability.capability}` : '/lab',
    query,
  })
}

defineExpose({ route, text })
</script>

<style scoped>
.smart-entry__row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.smart-entry__field {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.smart-entry__transcript {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px dashed var(--aitp-border);
  border-radius: 8px;
  font-size: 12px;
}

.smart-entry__notes {
  margin: 0;
  padding-left: 18px;
  color: var(--aitp-text-weak);
  flex-basis: 100%;
}

.smart-entry__actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
}

.smart-entry__hint {
  font-size: 12px;
  white-space: nowrap;
}

.smart-entry__result {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.smart-entry__group-title {
  font-size: 13px;
  color: var(--aitp-text-weak);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.smart-entry__cards {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.smart-entry__card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 8px 12px;
  border: 1px solid var(--aitp-border);
  border-radius: 8px;
  background: var(--aitp-hover);
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
}

.smart-entry__card:hover {
  border-color: var(--aitp-primary);
}

.smart-entry__card-title {
  font-weight: 600;
}

.smart-entry__card-reason {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.smart-entry__card-score {
  font-size: 12px;
  color: var(--aitp-primary);
}

.preflight {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--aitp-border);
  border-radius: 8px;
  background: var(--aitp-hover);
}

.preflight__bar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.preflight__order {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 13px;
}

.arrow {
  color: var(--aitp-text-weak);
}

.missing {
  color: var(--el-color-danger);
  font-size: 12px;
}

.optional-line {
  font-size: 12px;
  margin-top: 2px;
}

.preflight__notes {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.8;
}

.blocker {
  color: var(--el-color-danger);
}

.warning {
  color: var(--el-color-warning);
}
</style>
