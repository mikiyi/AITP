<template>
  <div
    class="assistant"
    :class="[{ 'assistant--compact': compact }, `is-mode-${mode}`]"
  >
    <div class="assistant__head">
      <el-radio-group v-model="mode" size="small" class="assistant__modes">
        <el-radio-button value="llm">基于大模型回答</el-radio-button>
        <el-radio-button value="kb">基于知识库回答</el-radio-button>
        <el-radio-button value="hybrid">知识库+大模型补强</el-radio-button>
      </el-radio-group>
      <div class="spacer" />
      <el-button
        v-if="archiveCount > 0"
        link
        size="small"
        :icon="Folder"
        title="查看历史对话归档"
        @click="openArchiveDrawer"
      >
        历史对话
        <el-badge :value="archiveCount" :max="99" class="assistant__archive-badge" />
      </el-button>
      <el-button
        v-if="messages.length"
        link
        size="small"
        title="另起一条会话；问过的内容仍保留在「会话记录」里"
        @click="clearChat"
      >
        清空对话
      </el-button>
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
      <div class="assistant__cache-bar">
        <el-button link size="small" @click="clearThinking">清理思考缓存</el-button>
      </div>
      <div v-if="archiveCount > 0" class="assistant__history-note" role="note">
        当前显示最近 50 轮对话，已归档 {{ archiveCount }} 批历史对话，点击右上角「历史对话」查看。
      </div>
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
          :class="[`is-${item.role}`, `is-tone-${toneOf(item)}`]"
        >
          <div class="assistant__bubble">
            <div
              v-if="item.thinking && (item.thinking.text || item.thinking.interrupted)"
              class="assistant__thinking"
            >
              <button type="button" class="assistant__thinking-head" @click="toggleThinking(item)">
                <el-icon>
                  <ArrowRight v-if="item.thinking.collapsed" />
                  <ArrowDown v-else />
                </el-icon>
                <span v-if="!item.thinking.done">思考中…</span>
                <span v-else>已思考 {{ item.thinking.seconds }} 秒 / {{ item.thinking.chars }} 字</span>
                <el-tag v-if="item.thinking.interrupted" size="small" type="warning" effect="plain">未完成</el-tag>
                <el-tag v-if="item.thinking.saved" size="small" type="success" effect="plain">思考已保存</el-tag>
              </button>
              <pre v-show="!item.thinking.collapsed" class="assistant__thinking-body">{{ thinkingDisplay(item) }}</pre>
              <div v-if="item.thinking.done && !item.thinking.saved && item.thinking.text" class="assistant__thinking-actions">
                <el-button link type="primary" size="small" :loading="savingThinking" @click="saveThinking(item)">
                  保存思考
                </el-button>
                <span class="muted">默认仅本标签页缓存，不入库</span>
              </div>
            </div>
            <MarkdownView v-if="item.role === 'assistant'" :source="item.content" />
            <span v-else>{{ item.content }}</span>
            <!-- 流式进行中且还没吐出思考/正文：给一个明确的等待指示，不让气泡空白 -->
            <div
              v-if="item.role === 'assistant' && !item.answeredAt && !item.content && !item.thinking?.text"
              class="assistant__pending"
            >
              <span class="assistant__pending-dots" aria-hidden="true"><i /><i /><i /></span>
              正在思考…
            </div>
            <div
              v-if="item.role === 'assistant' && item.meta && item.answeredAt && item.meta.mode !== 'kb' && !item.thinking"
              class="muted assistant__thinking-note"
            >
              思考过程未保存（仅当次会话可见）
            </div>
            <!-- 元信息与「加入知识库」只在回答完成后出现：流式未完成就放入库入口，
                 用户会把半成品提交进待审池 -->
            <div
              v-if="item.role === 'assistant' && item.meta && item.answeredAt"
              class="assistant__meta"
            >
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
              <span class="muted">参考来源 · {{ item.refs.length }} 条（正文中的 [K#] 对应此处）</span>
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
      <div class="assistant__composer">
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
        <!-- 附件入口收进输入框内：导入文档 / 上传图片 成为问题框自带的工具行 -->
        <div class="assistant__attachments">
          <AiAttachmentInput
            ref="attachmentInput"
            v-model:attachment-ids="attachmentIds"
            compact
            inline
          />
        </div>
      </div>
      <el-button
        type="primary"
        :icon="Promotion"
        :loading="sending"
        :disabled="!question.trim() && !attachmentIds.length"
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

    <!-- 历史对话归档抽屉 -->
    <el-drawer
      v-model="archiveDrawerVisible"
      title="历史对话归档"
      size="600px"
      append-to-body
      @open="onArchiveDrawerOpen"
      @close="onArchiveDrawerClose"
    >
      <div v-if="!archiveDetail" class="assistant__archive-list">
        <div v-loading="archiveListLoading" class="assistant__archive-list-body">
          <el-empty
            v-if="!archiveListLoading && archiveList.length === 0"
            description="暂无历史对话归档"
            :image-size="80"
          />
          <div
            v-for="item in archiveList"
            :key="item.id"
            class="assistant__archive-item"
            @click="viewArchive(item.id)"
          >
            <div class="assistant__archive-item-name">{{ item.archiveName }}</div>
            <div class="assistant__archive-item-meta">
              <span>{{ item.turnCount }} 轮</span>
              <span class="assistant__archive-item-sep">·</span>
              <span>{{ item.messageCount }} 条消息</span>
              <span class="assistant__archive-item-sep">·</span>
              <span>{{ formatArchivePeriod(item.periodFrom, item.periodTo) }}</span>
            </div>
            <el-icon class="assistant__archive-item-arrow"><ArrowRight /></el-icon>
          </div>
        </div>
      </div>
      <div v-else class="assistant__archive-detail">
        <div class="assistant__archive-detail-header">
          <el-button link :icon="ArrowLeft" @click="clearArchiveDetail">返回列表</el-button>
          <span class="assistant__archive-detail-title">{{ archiveDetail.archiveName }}</span>
          <span class="muted">{{ archiveDetail.turnCount }} 轮 · {{ archiveDetail.messageCount }} 条</span>
        </div>
        <div class="assistant__archive-detail-body" v-loading="archiveDetailLoading">
          <div
            v-for="msg in archiveDetail.messages"
            :key="msg.id"
            class="assistant__archive-msg"
            :class="[`is-${msg.role}`]"
          >
            <div class="assistant__archive-msg-role">
              {{ msg.role === 'user' ? '提问' : '回答' }}
            </div>
            <MarkdownView v-if="msg.role === 'assistant'" :source="msg.content" />
            <span v-else class="assistant__archive-msg-text">{{ msg.content }}</span>
            <div v-if="msg.createdAt" class="assistant__archive-msg-time muted">
              {{ formatArchiveTime(msg.createdAt) }}
            </div>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { storeToRefs } from 'pinia'
import {
  ArrowDown, ArrowLeft, ArrowRight, Collection, Cpu, Folder, MagicStick, Promotion,
} from '@element-plus/icons-vue'
import MarkdownView from '@/components/MarkdownView.vue'
import AiAttachmentInput from '@/components/AiAttachmentInput.vue'
import { aiApi, knowledgeApi } from '@/api'
import type { MaskInfo } from '@/api/types'
import { useAiGuardStore } from '@/stores/aiGuard'
import { useKnowledgeAssistantStore } from '@/stores/knowledgeAssistant'
import type { AssistantChatItem as ChatItem, AssistantMode as Mode } from '@/stores/knowledgeAssistant'
import { textareaHint } from '@/utils/inputHints'
import {
  clearThinkingCache, maskThinking, setSensitiveMode, setThinking, thinkingKey,
} from '@/utils/thinkingCache'

withDefaults(defineProps<{ compact?: boolean }>(), { compact: false })

const router = useRouter()
const aiGuard = useAiGuardStore()
const assistantStore = useKnowledgeAssistantStore()

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
/** 本次提问携带的附件编号：文档已在选择时并入提问，图片由后端转多模态消息 */
const attachmentIds = ref<string[]>([])
const attachmentInput = ref<InstanceType<typeof AiAttachmentInput> | null>(null)
const sending = ref(false)
// 对话与会话 id 都在 store 上：换菜单把组件挤掉、或首页卡片与问答页并存，看到的都是同一条线
const {
  messages, archiveCount, archiveList, archiveListLoading,
  archiveDetail, archiveDetailLoading,
} = storeToRefs(assistantStore)
const listEl = ref<HTMLElement | null>(null)

/** 历史对话抽屉可见性。 */
const archiveDrawerVisible = ref(false)

// 默认模式可能来自设置页；加载完成前用户已经手动切过，就不再覆盖他的选择
let modeTouched = false
watch(mode, () => {
  modeTouched = true
})

onMounted(async () => {
  const touched = modeTouched
  await Promise.all([assistantStore.load(), assistantStore.loadHistory()])
  if (!touched) {
    mode.value = assistantStore.defaultMode
  }
  if (messages.value.length) {
    await scrollToBottom()
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

/** 每条消息按它自己的模式染色：问题用提问时的模式，回复用回答时的模式。 */
function toneOf(item: ChatItem): Mode {
  if (item.role === 'user') {
    return item.mode ?? mode.value
  }
  return item.meta?.mode ?? mode.value
}

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
  // 另起一条会话：清掉当前线程，问过的内容仍在库里，去「会话记录」按「知识库问答」可查
  assistantStore.resetThread()
  messagesHeight.value = null
}

// ---------------- 历史对话归档 ----------------

function openArchiveDrawer(): void {
  archiveDrawerVisible.value = true
}

async function onArchiveDrawerOpen(): Promise<void> {
  assistantStore.clearArchiveDetail()
  await assistantStore.loadArchives()
}

function onArchiveDrawerClose(): void {
  assistantStore.clearArchiveDetail()
}

async function viewArchive(archiveId: number): Promise<void> {
  await assistantStore.viewArchive(archiveId)
}

function clearArchiveDetail(): void {
  assistantStore.clearArchiveDetail()
}

/** 格式化归档的时间段展示。 */
function formatArchivePeriod(periodFrom: string, periodTo: string): string {
  if (!periodFrom || !periodTo) return ''
  const from = formatArchiveTime(periodFrom)
  const to = formatArchiveTime(periodTo)
  return `${from} ~ ${to}`
}

/** 格式化 ISO 时间为可读的本地时间。 */
function formatArchiveTime(iso: string): string {
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    const h = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    return `${y}-${m}-${day} ${h}:${min}`
  } catch {
    return iso
  }
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
    // 以后端返回的首选模型为准：本地模型问答不弹外发确认，云端首选才弹
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
  const ids = [...attachmentIds.value]
  if ((!text && !ids.length) || sending.value) {
    return
  }
  const promptText = text || '请阅读并处理附件内容'
  // 纯知识库回答不调模型；其余两种会调用模型，云端模型先过全局数据外发确认
  if (!(await confirmExternal(mode.value, promptText))) {
    return
  }
  sending.value = true
  messages.value = [...messages.value, { role: 'user', content: promptText, mode: mode.value }]
  question.value = ''
  attachmentInput.value?.reset()
  await scrollToBottom()
  try {
    if (mode.value === 'kb') {
      await sendKb(promptText, ids)
    } else {
      await sendStream(promptText, mode.value, ids)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '问答失败')
  } finally {
    sending.value = false
    // 附件已在请求发出前读取，回答结束后再删除，避免和请求抢文件
    void attachmentInput.value?.dispose(ids)
  }
}

/** 纯知识库直答：不调模型、无思考过程。 */
async function sendKb(text: string, attachmentIds: string[] = []): Promise<void> {
  const result = await knowledgeApi.ask({
    question: text, mode: 'kb', sessionId: assistantStore.sessionId, attachmentIds,
  })
  assistantStore.sessionId = result.sessionId ?? null
  messages.value = [
    ...messages.value,
    {
      role: 'assistant',
      content: result.answer || '（模型没有返回内容）',
      question: text,
      refs: result.refs ?? [],
      egressWarning: result.egressWarning,
      answeredAt: result.answeredAt || new Date().toISOString(),
      mask: result.mask,
      submitState: 'idle',
      meta: {
        mode: result.mode, modeLabel: MODE_LABELS[result.mode], model: result.model,
        latencyMs: result.latencyMs, reviewRequired: result.reviewRequired,
      },
    },
  ]
  await scrollToBottom()
}

/** 流式问答：reasoning / delta 双通道；思考默认只到浏览器瞬态缓存，不落库。 */
async function sendStream(text: string, m: Mode, attachmentIds: string[] = []): Promise<void> {
  const item: ChatItem = {
    role: 'assistant',
    content: '',
    question: text,
    refs: [],
    egressWarning: '',
    answeredAt: '',
    submitState: 'idle',
    thinking: {
      text: '', seconds: 0, chars: 0, done: false, interrupted: false,
      collapsed: false, saved: false, fromHistory: false,
    },
    meta: { mode: m, modeLabel: MODE_LABELS[m], model: '', latencyMs: 0, reviewRequired: false },
  }
  messages.value = [...messages.value, item]
  const resp = await fetch('/api/v1/knowledge/assistant/ask-stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question: text, mode: m, sessionId: assistantStore.sessionId, attachmentIds,
    }),
  })
  if (!resp.ok || !resp.body) {
    throw new Error('问答流启动失败')
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let reasoning = ''
  let answer = ''
  let doneMeta: Record<string, any> | null = null
  let errored = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let sep = buf.indexOf('\n\n')
    while (sep >= 0) {
      const chunk = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      const ev = /event: (.+)/.exec(chunk)?.[1]?.trim()
      const dataLine = /data: (.+)/.exec(chunk)?.[1]
      sep = buf.indexOf('\n\n')
      if (!ev || !dataLine) continue
      const payload = JSON.parse(dataLine)
      if (ev === 'reasoning') {
        reasoning += payload.content
        if (item.thinking) {
          item.thinking.text = reasoning
          item.thinking.chars = reasoning.length
        }
        await scrollToBottom()
      } else if (ev === 'delta') {
        answer += payload.content
        item.content = answer
        await scrollToBottom()
      } else if (ev === 'mask') {
        item.mask = payload as MaskInfo
      } else if (ev === 'degrade') {
        ElMessage.warning(String(payload.message || '已降级'))
      } else if (ev === 'done') {
        doneMeta = payload
      } else if (ev === 'error') {
        errored = String(payload.message || '模型调用失败')
      }
    }
  }
  const th = item.thinking
  if (errored) {
    if (th) {
      th.interrupted = true
      th.done = true
    }
    item.content = item.content || '（模型调用失败）'
    ElMessage.error(errored)
    await scrollToBottom()
    return
  }
  if (!doneMeta) {
    if (th) th.interrupted = true
    item.content = item.content || '（模型没有返回内容）'
    await scrollToBottom()
    return
  }
  assistantStore.sessionId = doneMeta.sessionId ?? null
  const answeredAt = String(doneMeta.answeredAt || new Date().toISOString())
  item.content = answer || '（模型没有返回内容）'
  item.refs = doneMeta.refs ?? []
  item.egressWarning = doneMeta.egressWarning
  item.answeredAt = answeredAt
  item.mask = doneMeta.mask
  item.meta = {
    mode: m, modeLabel: MODE_LABELS[m], model: doneMeta.model,
    latencyMs: doneMeta.latencyMs, reviewRequired: doneMeta.reviewRequired,
  }
  // 敏感（走过脱敏）→ 思考仅内存缓存，不写 sessionStorage
  setSensitiveMode(Boolean(item.mask && Object.keys(item.mask).length))
  if (th) {
    if (reasoning) {
      th.done = true
      th.seconds = Number(doneMeta.reasoningSeconds ?? 0)
      th.chars = Number(doneMeta.reasoningChars ?? reasoning.length)
      th.collapsed = collapsePref.value
      setThinking(thinkingKey(doneMeta.sessionId, answeredAt, doneMeta.model), {
        text: reasoning, seconds: th.seconds, chars: th.chars,
        done: true, interrupted: false, saved: false, ts: Date.now(),
      })
    } else {
      // 模型没返回思考：不显示空框（P0 降级）
      item.thinking = undefined
    }
  }
  await scrollToBottom()
}

/** 折叠偏好：回答完成后默认自动折叠为一行摘要，可再展开；偏好记浏览器。 */
const collapsePref = ref(localStorage.getItem('aitp.assistant.thinking.collapsed') !== '0')
const savingThinking = ref(false)

function thinkingDisplay(item: ChatItem): string {
  return maskThinking(item.thinking?.text ?? '')
}

function toggleThinking(item: ChatItem): void {
  if (!item.thinking) return
  item.thinking.collapsed = !item.thinking.collapsed
  collapsePref.value = item.thinking.collapsed
  localStorage.setItem('aitp.assistant.thinking.collapsed', item.thinking.collapsed ? '1' : '0')
}

/** opt-in：把思考随消息保存（默认不存）。 */
async function saveThinking(item: ChatItem): Promise<void> {
  if (!item.thinking || !item.answeredAt || assistantStore.sessionId == null) return
  savingThinking.value = true
  try {
    await knowledgeApi.assistantSaveThinking({
      sessionId: assistantStore.sessionId,
      answeredAt: item.answeredAt,
      text: item.thinking.text,
    })
    item.thinking.saved = true
    ElMessage.success('思考已随本条回答保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存思考失败')
  } finally {
    savingThinking.value = false
  }
}

function clearThinking(): void {
  clearThinkingCache()
  ElMessage.success('已清理本标签页的思考缓存')
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
  /* 三原色：红=大模型 / 绿=知识库 / 蓝=知识库+大模型。色相固定以保持三原色识别，
     底纹 / 描边 / 文字再与当前皮肤的 panel / text 混色，做到浅色深色皮肤都适配。 */
  --ka-red: #d32f2f;
  --ka-green: #2e7d32;
  --ka-blue: #1976d2;
  --mode-tone: var(--ka-blue);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.assistant.is-mode-llm {
  --mode-tone: var(--ka-red);
}

.assistant.is-mode-kb {
  --mode-tone: var(--ka-green);
}

.assistant.is-mode-hybrid {
  --mode-tone: var(--ka-blue);
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
  border: 1px solid color-mix(in srgb, var(--mode-tone) 16%, transparent);
  border-left: 3px solid var(--mode-tone);
  border-radius: 8px;
  background: var(--aitp-subtle);
  /* 用面板色混出淡色底：浅色皮肤接近白底轻染，深色皮肤保持暗底，不出现白块 */
  background: color-mix(in srgb, var(--mode-tone) 5%, var(--aitp-panel));
  font-size: 13px;
  line-height: 1.5;
  animation: assistant-mode-in 0.32s ease;
}

.assistant__mode-banner.is-llm {
  --mode-tone: var(--ka-red);
}

.assistant__mode-banner.is-kb {
  --mode-tone: var(--ka-green);
}

.assistant__mode-banner.is-hybrid {
  --mode-tone: var(--ka-blue);
}

/* 模式页签：未选中时文字带各自色相，选中时整块填充该模式主色 */
.assistant__modes :deep(.el-radio-button__inner) {
  color: var(--aitp-text);
  border-color: var(--aitp-border);
  background: var(--aitp-panel);
}

.assistant__modes :deep(.el-radio-button__original-radio[value='llm'] + .el-radio-button__inner) {
  color: color-mix(in srgb, var(--ka-red) 72%, var(--aitp-text));
}

.assistant__modes :deep(.el-radio-button__original-radio[value='kb'] + .el-radio-button__inner) {
  color: color-mix(in srgb, var(--ka-green) 72%, var(--aitp-text));
}

.assistant__modes :deep(.el-radio-button__original-radio[value='hybrid'] + .el-radio-button__inner) {
  color: color-mix(in srgb, var(--ka-blue) 72%, var(--aitp-text));
}

.assistant__modes :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  color: #fff;
  background: var(--mode-tone);
  border-color: var(--mode-tone);
  box-shadow: -1px 0 0 0 var(--mode-tone);
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

/* 历史只回放一部分时给一句去向说明，别让用户以为更早的问答丢了 */
.assistant__history-note {
  margin-bottom: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--aitp-text-weak);
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
  background: var(--mode-tone);
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

/* 每条消息按自身模式染底纹：问题用提问模式色，回复用回答模式色 */
.is-tone-llm {
  --bubble-tone: var(--ka-red);
}

.is-tone-kb {
  --bubble-tone: var(--ka-green);
}

.is-tone-hybrid {
  --bubble-tone: var(--ka-blue);
}

.is-user .assistant__bubble {
  background: color-mix(in srgb, var(--bubble-tone) 12%, var(--aitp-panel));
  border: 1px solid color-mix(in srgb, var(--bubble-tone) 38%, transparent);
  color: var(--aitp-text);
}

.is-assistant .assistant__bubble {
  background: color-mix(in srgb, var(--bubble-tone) 7%, var(--aitp-panel));
  border: 1px solid color-mix(in srgb, var(--bubble-tone) 26%, var(--aitp-border));
}

/* 深色皮肤底纹加重一档，保持与浅色皮肤相近的辨识度 */
html[data-theme='black'] .is-user .assistant__bubble,
html[data-theme='tech'] .is-user .assistant__bubble,
html[data-theme='industry'] .is-user .assistant__bubble {
  background: color-mix(in srgb, var(--bubble-tone) 22%, var(--aitp-panel));
}

html[data-theme='black'] .is-assistant .assistant__bubble,
html[data-theme='tech'] .is-assistant .assistant__bubble,
html[data-theme='industry'] .is-assistant .assistant__bubble {
  background: color-mix(in srgb, var(--bubble-tone) 14%, var(--aitp-panel));
}

.assistant__cache-bar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 4px;
}

.assistant__thinking {
  margin-bottom: 8px;
  border: 1px solid var(--aitp-border, #e4e7ed);
  border-left: 3px solid var(--el-color-primary-light-5, #79bbff);
  border-radius: 6px;
  background: var(--aitp-subtle, #f5f7fa);
  padding: 6px 8px;
}

.assistant__thinking-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 12px;
  color: var(--aitp-text-secondary, #606266);
}

.assistant__thinking-head:hover {
  color: var(--el-color-primary, #409eff);
}

.assistant__thinking-body {
  margin: 6px 0 0;
  max-height: 220px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.6;
  color: var(--aitp-text-secondary, #606266);
  scrollbar-width: thin;
  scrollbar-color: var(--el-color-primary-light-5, #79bbff) var(--aitp-subtle, #f5f7fa);
}

.assistant__thinking-body::-webkit-scrollbar {
  width: 6px;
}

.assistant__thinking-body::-webkit-scrollbar-thumb {
  background: var(--el-color-primary-light-5, #79bbff);
  border-radius: 3px;
}

.assistant__thinking-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
}

.assistant__thinking-note {
  margin-top: 4px;
  font-size: 12px;
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
  align-items: stretch;
  gap: 8px;
}

/* 提问按钮贴齐输入框底边，与附件工具行同高 */
.assistant__input > :deep(.el-button) {
  align-self: flex-end;
}

/* 输入框 + 附件工具行合成一个 composer：附件入口收进问题框内 */
.assistant__composer {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid color-mix(in srgb, var(--mode-tone) 45%, var(--aitp-border));
  border-radius: 8px;
  background: color-mix(in srgb, var(--mode-tone) 4%, var(--aitp-panel));
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

/* 描边 / 聚焦 / 底色随当前模式主色；textarea 自身去描边，统一由 composer 出 */
.assistant__composer:hover {
  border-color: color-mix(in srgb, var(--mode-tone) 65%, var(--aitp-border));
}

.assistant__composer:focus-within {
  border-color: var(--mode-tone);
  box-shadow: 0 0 0 1px var(--mode-tone) inset;
}

.assistant__composer :deep(.el-textarea__inner) {
  background: transparent;
  color: var(--aitp-text);
  box-shadow: none;
}

.assistant__composer .assistant__attachments {
  border-top: 1px dashed color-mix(in srgb, var(--mode-tone) 25%, var(--aitp-border));
  padding: 2px 8px 4px;
}

/* 流式等待指示：模型还没吐出思考与正文时的三点动画 */
.assistant__pending {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--aitp-text-secondary, #606266);
}

.assistant__pending-dots {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.assistant__pending-dots i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--mode-tone);
  animation: assistant-pending-blink 1.2s infinite ease-in-out;
}

.assistant__pending-dots i:nth-child(2) {
  animation-delay: 0.15s;
}

.assistant__pending-dots i:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes assistant-pending-blink {
  0%,
  60%,
  100% {
    opacity: 0.25;
    transform: scale(0.8);
  }
  30% {
    opacity: 1;
    transform: scale(1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .assistant__pending-dots i {
    animation: none;
    opacity: 0.6;
  }
}

/* 提问按钮填充当前模式主色 */
.assistant__input :deep(.el-button--primary) {
  --el-button-bg-color: var(--mode-tone);
  --el-button-border-color: var(--mode-tone);
  --el-button-text-color: #fff;
  --el-button-hover-bg-color: color-mix(in srgb, var(--mode-tone) 84%, #fff);
  --el-button-hover-border-color: color-mix(in srgb, var(--mode-tone) 84%, #fff);
  --el-button-hover-text-color: #fff;
  --el-button-active-bg-color: color-mix(in srgb, var(--mode-tone) 84%, #000);
  --el-button-active-border-color: color-mix(in srgb, var(--mode-tone) 84%, #000);
  --el-button-disabled-bg-color: color-mix(in srgb, var(--mode-tone) 38%, var(--aitp-panel));
  --el-button-disabled-border-color: color-mix(in srgb, var(--mode-tone) 38%, var(--aitp-border));
  --el-button-disabled-text-color: color-mix(in srgb, var(--mode-tone) 30%, var(--aitp-text));
}

/* 回复里的「加入知识库」链接也跟随模式主色 */
.assistant :deep(.el-button.is-link.el-button--primary) {
  --el-button-text-color: var(--mode-tone);
  --el-button-hover-text-color: color-mix(in srgb, var(--mode-tone) 80%, var(--aitp-text));
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

/* ---------------- 历史对话归档 ---------------- */

.assistant__archive-badge {
  margin-left: 4px;
}

.assistant__archive-list {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.assistant__archive-list-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 0;
}

.assistant__archive-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--aitp-border);
  border-radius: 8px;
  background: var(--aitp-panel);
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.assistant__archive-item:hover {
  border-color: var(--el-color-primary);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.assistant__archive-item-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--aitp-text);
}

.assistant__archive-item-meta {
  font-size: 12px;
  color: var(--aitp-text-secondary);
  margin-top: 2px;
}

.assistant__archive-item-sep {
  margin: 0 6px;
  opacity: 0.5;
}

.assistant__archive-item-arrow {
  margin-left: auto;
  color: var(--aitp-text-weak);
}

.assistant__archive-detail {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.assistant__archive-detail-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--aitp-border);
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.assistant__archive-detail-title {
  font-weight: 600;
  font-size: 15px;
}

.assistant__archive-detail-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
  flex: 1;
  padding-right: 4px;
}

.assistant__archive-msg {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.7;
}

.assistant__archive-msg.is-user {
  background: color-mix(in srgb, var(--ka-blue) 8%, var(--aitp-panel));
  border-left: 3px solid var(--ka-blue);
}

.assistant__archive-msg.is-assistant {
  background: var(--aitp-subtle);
  border-left: 3px solid var(--ka-green);
}

.assistant__archive-msg-role {
  font-size: 12px;
  font-weight: 600;
  opacity: 0.7;
}

.assistant__archive-msg-text {
  word-break: break-word;
}

.assistant__archive-msg-time {
  font-size: 11px;
  margin-top: 2px;
}
</style>
