<template>
  <div class="assistant" :class="{ 'assistant--compact': compact }">
    <div class="assistant__head">
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="llm">基于大模型回答</el-radio-button>
        <el-radio-button value="kb">基于知识库回答</el-radio-button>
        <el-radio-button value="hybrid">知识库+大模型补强</el-radio-button>
      </el-radio-group>
      <span class="muted assistant__mode-hint">{{ modeHint }}</span>
      <div class="spacer" />
      <el-button v-if="messages.length" link size="small" @click="clearChat">清空对话</el-button>
    </div>

    <div v-if="messages.length" ref="listEl" class="assistant__messages">
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
        resize="none"
        placeholder="输入问题，Ctrl + Enter 发送"
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
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Promotion } from '@element-plus/icons-vue'
import MarkdownView from '@/components/MarkdownView.vue'
import { knowledgeApi } from '@/api'
import type { KnowledgeAnswer } from '@/api/types'
import { useAiGuardStore } from '@/stores/aiGuard'

withDefaults(defineProps<{ compact?: boolean }>(), { compact: false })

const router = useRouter()
const aiGuard = useAiGuardStore()

type Mode = 'kb' | 'llm' | 'hybrid'

interface ChatItem {
  role: 'user' | 'assistant'
  content: string
  refs?: KnowledgeAnswer['refs']
  egressWarning?: string
  meta?: {
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
  llm: '不检索知识库，直接由模型回答',
  kb: '只返回知识库检索结果，不调用模型',
  hybrid: '先检索知识库，再交给模型整合（默认）',
}

const mode = ref<Mode>('hybrid')
const question = ref('')
const sending = ref(false)
const messages = ref<ChatItem[]>([])
const listEl = ref<HTMLElement | null>(null)

const modeHint = computed(() => MODE_HINTS[mode.value])

function clearChat(): void {
  messages.value = []
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  if (listEl.value) {
    listEl.value.scrollTop = listEl.value.scrollHeight
  }
}

async function send(): Promise<void> {
  const text = question.value.trim()
  if (!text || sending.value) {
    return
  }
  // 纯知识库回答不调模型；其余两种会调用模型，云端模型先过全局数据外发确认
  if (mode.value !== 'kb' && !(await aiGuard.ensureExternalConfirmed())) {
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
        refs: result.refs ?? [],
        egressWarning: result.egressWarning,
        meta: {
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

.assistant__mode-hint {
  font-size: 12px;
}

.assistant__messages {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 520px;
  overflow-y: auto;
  padding: 4px 2px;
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
</style>
