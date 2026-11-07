<template>
  <div class="assistant" :class="{ 'assistant--compact': compact }">
    <div class="assistant__head">
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="llm">基于大模型回答</el-radio-button>
        <el-radio-button value="kb">基于知识库回答</el-radio-button>
        <el-radio-button value="hybrid">知识库+大模型补强</el-radio-button>
      </el-radio-group>
      <div class="spacer" />
      <el-button v-if="messages.length" link size="small" @click="clearChat">清空对话</el-button>
    </div>

    <div
      :key="mode"
      class="assistant__mode-banner"
      :class="`is-${mode}`"
      role="status"
      aria-live="polite"
    >
      <el-icon class="assistant__mode-icon" :size="15">
        <Cpu v-if="mode === 'llm'" />
        <Collection v-else-if="mode === 'kb'" />
        <MagicStick v-else />
      </el-icon>
      <div class="assistant__mode-copy">
        <span class="assistant__mode-name">{{ MODE_LABELS[mode] }}</span>
        <span class="assistant__mode-desc">{{ modeHint }}</span>
      </div>
      <span v-if="mode === defaultMode" class="assistant__mode-default">默认</span>
    </div>

    <div v-if="messages.length" class="assistant__messages-wrap">
      <div
        ref="listEl"
        class="assistant__messages"
        :class="{ 'is-height-manual': messagesHeight !== null }"
        :style="messagesHeight !== null ? { height: `${messagesHeight}px` } : undefined"
      >
        <div
          v-for="(item, index) in messages"
          :key="index"
          class="assistant__msg"
          :class="`is-${item.role}`"
        >
          <div class="assistant__bubble">
            <MarkdownView v-if="item.role === 'assistant'" :source="item.content" />
            <span v-else>{{ item.content }}</span>
            <div v-if="item.role === 'assistant' && item.meta" class="assistant__meta">
              <el-tag size="small" effect="plain" type="info">{{ item.meta.modeLabel }}</el-tag>
              <span v-if="item.meta.model" class="muted">模型：{{ item.meta.model }}</span>
              <span v-if="item.meta.latencyMs" class="muted">耗时 {{ item.meta.latencyMs }} ms</span>
              <el-tag v-if="item.meta.reviewRequired" size="small" type="warning" effect="plain">
                需人工复核
              </el-tag>
              <el-tag
                v-if="item.mask?.applied"
                size="small"
                type="success"
                effect="plain"
                :title="item.mask.types.map((mask) => `${mask.label}×${mask.count}`).join('、')"
              >
                已自动脱敏 {{ item.mask.count }} 处
              </el-tag>
              <!-- 知识库直答的正文本来就取自正式库，再加回去等于自我复制 -->
              <el-button
                v-if="item.meta.mode !== 'kb'"
                link
                type="primary"
                size="small"
                :loading="item.submitState === 'submitting'"
                :disabled="item.submitState === 'submitted'"
                @click.stop="addToKnowledge(index, item)"
              >
                {{ item.submitState === 'submitted' ? '已提交待审' : '加入知识库' }}
              </el-button>
            </div>
            <div v-if="item.refs?.length" class="assistant__refs">
              <span class="muted">参考来源</span>
              <el-tag
                v-for="ref in item.refs"
                :key="ref.marker"
                size="small"
                effect="plain"
                class="assistant__ref"
                @click="router.push(`/knowledge/${ref.articleId}`)"
              >
                [{{ ref.marker }}] {{ ref.title }}
              </el-tag>
            </div>
            <el-alert
              v-if="item.egressWarning"
              type="warning"
              :closable="false"
              show-icon
              :title="item.egressWarning"
              class="assistant__egress"
            />
          </div>
        </div>
      </div>
      <div
        v-if="!compact"
        class="assistant__resizer"
        role="separator"
        aria-orientation="horizontal"
        aria-label="拖动调整对话区高度"
        title="拖动调整对话区高度"
        @pointerdown="startResize"
      />
    </div>

    <div v-else class="assistant__empty">
      <el-empty
        :description="compact ? '问点什么，例如：支付回调重复推送怎么排查？' : '输入测试相关问题，助手会按所选方式回答'"
        :image-size="compact ? 64 : 90"
      />
    </div>

    <div class="assistant__input">
      <el-input
        v-model="question"
        type="textarea"
        :rows="2"
        maxlength="2000"
        resize="vertical"
        :placeholder="textareaHint('输入问题，Ctrl + Enter 发送')"
        @keydown.ctrl.enter.prevent="send"
        @keydown.meta.enter.prevent="send"
      />
      <el-button
        type="primary"
        :icon="Promotion"
        :loading="sending"
        :disabled="!question.trim()"
        @click="send"
      >
        提问
      </el-button>
    </div>

    <el-drawer v-model="inspectVisible" title="外发前数据检查：本次提问" size="420px" append-to-body>
      <div v-loading="inspectMaskLoading" class="assistant__inspect">
        <div class="muted">即将发送给外部模型的内容</div>
        <pre class="assistant__inspect-body">{{ inspectText }}</pre>
        <div class="muted">回答模式：{{ MODE_LABELS[inspectMode] }}</div>
        <div v-if="inspectMode === 'hybrid'" class="muted">
          另会附带知识库检索到的相关片段，回答后可在「参考来源」查看具体条目。
        </div>
        <template v-if="inspectMask">
          <el-alert
            v-if="inspectMask.count > 0"
            type="warning"
            :closable="false"
            show-icon
            :title="`识别到 ${inspectMask.count} 处敏感信息，发送前会自动替换`"
          />
          <el-alert
            v-else
            type="success"
            :closable="false"
            show-icon
            title="本次提问未识别到敏感信息"
          />
          <div v-if="inspectMask.types.length" class="assistant__inspect-types">
            <el-tag
              v-for="item in inspectMask.types"
              :key="item.code"
              size="small"
              type="warning"
              effect="plain"
            >
              {{ item.label }} × {{ item.count }}
            </el-tag>
          </div>
        </template>
        <div class="muted">
          云端调用默认自动脱敏手机号、身份证、邮箱、API Key 等；问答文本与检索片段都会经过同一层。关闭本窗口会回到外发确认框。
        </div>
        <el-button type="primary" @click="inspectVisible = false">关闭</el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Collection, Cpu, MagicStick, Promotion } from '@element-plus/icons-vue'
import MarkdownView from '@/components/MarkdownView.vue'
import { aiApi, knowledgeApi } from '@/api'
import type { AssistantRef, MaskInfo } from '@/api/types'
import { useAiGuardStore } from '@/stores/aiGuard'
import { useKnowledgeAssistantStore } from '@/stores/knowledgeAssistant'
import { textareaHint } from '@/utils/inputHints'

withDefaults(defineProps<{ compact?: boolean }>(), { compact: false })

const router = useRouter()
const aiGuard = useAiGuardStore()
const assistantStore = useKnowledgeAssistantStore()

type Mode = 'kb' | 'llm' | 'hybrid'

interface ChatItem {
  role: 'user' | 'assistant'
  content: string
  /** 对应提问，加入知识库时要一起留档 */
  question?: string
  refs?: AssistantRef[]
  egressWarning?: string
  answeredAt?: string
  /** 本次回答的外发脱敏结果 */
  mask?: MaskInfo
  submitState?: 'idle' | 'submitting' | 'submitted'
  meta?: {
    mode: Mode
    modeLabel: string
    model: string
    latencyMs: number
    reviewRequired: boolean
  }
}

const MODE_LABELS: Record<Mode, string> = {
  llm: '大模型回答',
  kb: '知识库回答',
  hybrid: '知识库+大模型补强',
}

const MODE_HINTS: Record<Mode, string> = {
  llm: '不检索知识库，直接由模型生成回答，内容可能存在推测',
  kb: '只返回知识库检索到的原文，不调用模型，来源可追溯',
  hybrid: '先检索知识库，再交给模型整合，兼顾准确与完整',
}

const mode = ref<Mode>(assistantStore.defaultMode)
const defaultMode = computed(() => assistantStore.defaultMode)
const question = ref('')
const sending = ref(false)
const messages = ref<ChatItem[]>([])
const listEl = ref<HTMLElement | null>(null)

// 默认模式可能来自设置页；加载完成前用户已经手动切过，就不再覆盖他的选择
let modeTouched = false
watch(mode, () => {
  modeTouched = true
})

onMounted(async () => {
  const touched = modeTouched
  await assistantStore.load()
  if (!touched) {
    mode.value = assistantStore.defaultMode
  }
})

/** 对话区被手动拖拽后的高度；null 表示沿用 CSS 默认上限 */
const messagesHeight = ref<number | null>(null)
let resizeStartY = 0
let resizeStartHeight = 0
let resizing = false

function onResizeMove(event: PointerEvent): void {
  if (!resizing) {
    return
  }
  const next = resizeStartHeight + (event.clientY - resizeStartY)
  messagesHeight.value = Math.min(Math.max(next, 120), Math.round(window.innerHeight * 0.9))
}

function onResizeEnd(): void {
  resizing = false
  window.removeEventListener('pointermove', onResizeMove)
  window.removeEventListener('pointerup', onResizeEnd)
  document.body.style.userSelect = ''
}

function startResize(event: PointerEvent): void {
  if (!listEl.value) {
    return
  }
  event.preventDefault()
  resizing = true
  resizeStartY = event.clientY
  resizeStartHeight = listEl.value.getBoundingClientRect().height
  messagesHeight.value = resizeStartHeight
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', onResizeMove)
  window.addEventListener('pointerup', onResizeEnd)
}

onUnmounted(onResizeEnd)

const modeHint = computed(() => MODE_HINTS[mode.value])

const inspectVisible = ref(false)
const inspectText = ref('')
const inspectMode = ref<Mode>('hybrid')
const inspectMask = ref<MaskInfo | null>(null)
const inspectMaskLoading = ref(false)
let inspectResolve: (() => void) | null = null

watch(inspectVisible, (visible) => {
  if (!visible) {
    inspectResolve?.()
    inspectResolve = null
  }
})

/** 打开前检查抽屉；关闭后 resolve，让确认弹窗继续等待用户决定。 */
async function openInspect(text: string, nextMode: Mode): Promise<void> {
  inspectText.value = text
  inspectMode.value = nextMode
  inspectMask.value = null
  inspectVisible.value = true
  const closed = new Promise<void>((resolve) => {
    inspectResolve = resolve
  })
  inspectMaskLoading.value = true
  try {
    inspectMask.value = await aiApi.maskPreview(text)
  } catch {
    // 检查失败不阻断；发送时后端仍会脱敏
  } finally {
    inspectMaskLoading.value = false
  }
  await closed
}

function clearChat(): void {
  messages.value = []
  messagesHeight.value = null
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  if (listEl.value) {
    listEl.value.scrollTop = listEl.value.scrollHeight
  }
}

async function confirmExternal(nextMode: Mode, text: string): Promise<boolean> {
  if (nextMode === 'kb') {
    return true
  }
  let externalPossible: boolean | undefined
  try {
    // 以服务端候选链为准：本地首选失败会降级到云端，只看前端 isLocal 会漏判
    externalPossible = (await knowledgeApi.assistantEgress(nextMode)).externalPossible
  } catch {
    externalPossible = undefined
  }
  return aiGuard.ensureExternalConfirmed({
    externalPossible,
    // 关闭检查抽屉后回到确认框，而不是直接取消提问
    onInspect: () => openInspect(text, nextMode),
  })
}

async function send(): Promise<void> {
  const text = question.value.trim()
  if (!text || sending.value) {
    return
  }
  // 纯知识库回答不调模型；其余两种会调用模型，云端模型先过全局数据外发确认
  if (!(await confirmExternal(mode.value, text))) {
    return
  }
  sending.value = true
  messages.value = [...messages.value, { role: 'user', content: text }]
  question.value = ''
  await scrollToBottom()
  try {
    const result = await knowledgeApi.ask({ question: text, mode: mode.value })
    messages.value = [
      ...messages.value,
      {
        role: 'assistant',
        content: result.answer || '（模型没有返回内容）',
        question: text,
        refs: result.refs ?? [],
        egressWarning: result.egressWarning,
        answeredAt: new Date().toISOString(),
        mask: result.mask,
        submitState: 'idle',
        meta: {
          mode: result.mode,
          modeLabel: MODE_LABELS[result.mode],
          model: result.model,
          latencyMs: result.latencyMs,
          reviewRequired: result.reviewRequired,
        },
      },
    ]
    await scrollToBottom()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '问答失败')
  } finally {
    sending.value = false
  }
}

async function addToKnowledge(index: number, item: ChatItem): Promise<void> {
  const meta = item.meta
  if (!meta || !item.question || item.submitState === 'submitting') {
    return
  }
  messages.value[index] = { ...item, submitState: 'submitting' }
  try {
    const draft = await knowledgeApi.addAssistantAnswer({
      question: item.question,
      answer: item.content,
      mode: meta.mode,
      model: meta.model,
      answeredAt: item.answeredAt,
      refs: item.refs ?? [],
      reviewRequired: meta.reviewRequired,
    })
    messages.value[index] = { ...item, submitState: 'submitted' }
    ElMessage.success(
      draft.duplicated
        ? '这条问答已经在待审池里，无需重复提交'
        : '已提交到待审池，审核通过后进入正式知识库',
    )
  } catch (err) {
    messages.value[index] = { ...item, submitState: 'idle' }
    ElMessage.error(err instanceof Error ? err.message : '加入知识库失败')
  }
}
</script>

<style scoped>
.assistant {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.assistant__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
}

.assistant__mode-banner {
  --mode-tone: var(--el-color-primary);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border: 1px solid var(--aitp-border);
  border-left: 3px solid var(--mode-tone);
  border-radius: 8px;
  background: var(--aitp-subtle);
  /* 用面板色混出淡色底：浅色皮肤接近白底轻染，深色皮肤保持暗底，不出现白块 */
  background: color-mix(in srgb, var(--mode-tone) 9%, var(--aitp-panel));
  font-size: 13px;
  line-height: 1.5;
  animation: assistant-mode-in 0.32s ease;
}

.assistant__mode-banner.is-llm {
  --mode-tone: var(--el-color-warning);
}

.assistant__mode-banner.is-kb {
  --mode-tone: var(--el-color-success);
}

.assistant__mode-banner.is-hybrid {
  --mode-tone: var(--el-color-primary);
}

.assistant__mode-icon {
  flex: 0 0 auto;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  color: var(--mode-tone);
  background: color-mix(in srgb, var(--mode-tone) 16%, var(--aitp-panel));
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--mode-tone) 10%, transparent);
}

.assistant__mode-copy {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 2px 8px;
  min-width: 0;
}

.assistant__mode-name {
  font-weight: 600;
  /* 和正文色混合出深浅适中的同色系文字：浅色皮肤更深，深色皮肤更亮 */
  color: color-mix(in srgb, var(--mode-tone) 50%, var(--aitp-text));
}

.assistant__mode-desc {
  color: var(--aitp-text);
}

.assistant__mode-default {
  flex: 0 0 auto;
  margin-left: auto;
  padding: 0 7px;
  height: 20px;
  line-height: 18px;
  font-size: 11px;
  border-radius: 10px;
  color: color-mix(in srgb, var(--mode-tone) 50%, var(--aitp-text));
  border: 1px solid color-mix(in srgb, var(--mode-tone) 45%, transparent);
  background: color-mix(in srgb, var(--mode-tone) 12%, transparent);
}

@keyframes assistant-mode-in {
  from {
    opacity: 0;
    transform: translateY(-3px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .assistant__mode-banner {
    animation: none;
  }
}

.assistant--compact .assistant__mode-banner {
  gap: 8px;
  padding: 6px 10px;
  font-size: 12px;
}

.assistant--compact .assistant__mode-icon {
  width: 22px;
  height: 22px;
}

/* 深色底上 9% 的混色几乎看不出来，单独加重一档，保持和浅色皮肤相近的辨识度 */
html[data-theme='black'] .assistant__mode-banner,
html[data-theme='tech'] .assistant__mode-banner,
html[data-theme='industry'] .assistant__mode-banner {
  background: color-mix(in srgb, var(--mode-tone) 16%, var(--aitp-panel));
}

.assistant__messages-wrap {
  display: flex;
  flex-direction: column;
}

.assistant__messages {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 520px;
  overflow-y: auto;
  padding: 4px 2px;
}

/* 手动拖过高度后解除默认上限，改由行内 height 控制 */
.assistant__messages.is-height-manual {
  max-height: none;
}

.assistant__resizer {
  align-self: center;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 12px;
  margin-top: 2px;
  cursor: ns-resize;
  touch-action: none;
}

.assistant__resizer::before {
  content: '';
  width: 36px;
  height: 3px;
  border-radius: 2px;
  background: var(--aitp-border);
}

.assistant__resizer:hover::before {
  background: var(--el-color-primary);
}

.assistant--compact .assistant__messages {
  max-height: 300px;
}

.assistant__msg {
  display: flex;
}

.assistant__msg.is-user {
  justify-content: flex-end;
}

.assistant__bubble {
  max-width: 86%;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.7;
  word-break: break-word;
}

.is-user .assistant__bubble {
  background: var(--aitp-primary-soft);
  color: var(--aitp-text);
}

.is-assistant .assistant__bubble {
  background: var(--aitp-subtle);
  border: 1px solid var(--aitp-border);
}

.assistant__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
}

.assistant__refs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  font-size: 12px;
}

.assistant__ref {
  cursor: pointer;
}

.assistant__egress {
  margin-top: 8px;
}

.assistant__empty {
  padding: 8px 0;
}

.assistant__input {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.assistant__input :deep(.el-textarea) {
  flex: 1;
}

.assistant__inspect {
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-size: 13px;
}

.assistant__inspect-body {
  margin: 0;
  padding: 10px;
  max-height: 46vh;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-family: inherit;
}

.assistant__inspect-types {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
