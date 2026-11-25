import { defineStore } from 'pinia'
import { ref } from 'vue'
import { knowledgeApi } from '@/api'
import type {
  AssistantArchiveDetail, AssistantArchiveItem, AssistantHistoryMessage,
  AssistantRef, AssistantThinking, MaskInfo,
} from '@/api/types'

export type AssistantMode = 'kb' | 'llm' | 'hybrid'

/** 与后端 DEFAULT_ASSISTANT_MODE 保持一致。 */
export const DEFAULT_ASSISTANT_MODE: AssistantMode = 'hybrid'

const MODES: AssistantMode[] = ['kb', 'llm', 'hybrid']

/** 对话里的一条气泡：状态放 store，组件被 keep-alive 淘汰后重建也不会丢。 */
export interface AssistantChatItem {
  role: 'user' | 'assistant'
  content: string
  /** 提问时所选模式，用于给问题气泡染上对应主题色 */
  mode?: AssistantMode
  /** 对应提问，加入知识库时要一起留档 */
  question?: string
  refs?: AssistantRef[]
  egressWarning?: string
  answeredAt?: string
  /** 本次回答的外发脱敏结果 */
  mask?: MaskInfo
  /** 思考过程展示态（瞬态缓存 / opt-in 已保存 / 历史回放） */
  thinking?: AssistantThinking
  submitState?: 'idle' | 'submitting' | 'submitted'
  meta?: {
    mode: AssistantMode
    modeLabel: string
    model: string
    latencyMs: number
    reviewRequired: boolean
  }
}

function normalize(raw: unknown): AssistantMode {
  return MODES.includes(raw as AssistantMode) ? (raw as AssistantMode) : DEFAULT_ASSISTANT_MODE
}

/** 后端回放的消息转成气泡；mask 为 {} 表示当时没走脱敏（知识库直答），不显示徽标。 */
function toChatItem(item: AssistantHistoryMessage): AssistantChatItem {
  const itemMode = normalize(item.mode)
  if (item.role !== 'assistant') {
    return { role: 'user', content: item.content, mode: itemMode }
  }
  const mask = item.mask && Object.keys(item.mask).length ? item.mask : undefined
  const savedThinking = String(item.thinkingText ?? '')
  return {
    role: 'assistant',
    content: item.content,
    question: item.question ?? '',
    refs: item.refs ?? [],
    egressWarning: item.egressWarning ?? '',
    answeredAt: item.answeredAt,
    mask,
    thinking: savedThinking
      ? {
          text: savedThinking, seconds: 0, chars: savedThinking.length,
          done: true, interrupted: false, collapsed: true, saved: true, fromHistory: true,
        }
      : undefined,
    submitState: item.submitState === 'submitted' ? 'submitted' : 'idle',
    meta: {
      mode: normalize(item.meta?.mode ?? item.mode),
      modeLabel: item.meta?.modeLabel ?? '',
      model: item.meta?.model ?? '',
      latencyMs: Number(item.meta?.latencyMs ?? 0),
      reviewRequired: Boolean(item.meta?.reviewRequired),
    },
  }
}

/**
 * 问答助手：默认回答模式 + 当前这条对话。
 *
 * 对话本身也放 store：后端按会话留痕，页面挂载时回放，再加上 store 是进程级单例，
 * 于是「换个菜单再回来问题历史就没了」这条路被堵死——首页卡片与问答页也共用同一份。
 *
 * 归档：会话超过 50 轮时，老对话自动归档到 biz_assistant_archive，
 * 页面只展示最近 50 轮；归档列表和详情通过独立 API 获取。
 */
export const useKnowledgeAssistantStore = defineStore('knowledgeAssistant', () => {
  const defaultMode = ref<AssistantMode>(DEFAULT_ASSISTANT_MODE)
  const loaded = ref(false)
  let loading: Promise<void> | null = null

  const messages = ref<AssistantChatItem[]>([])
  const sessionId = ref<number | null>(null)
  /** 历史超过后端回放上限（50 轮）：更早的自动归档，去历史对话查看。 */
  const truncated = ref(false)
  /** 当前会话的归档批次数（超过 50 轮后自动归档产生）。 */
  const archiveCount = ref(0)
  const historyLoaded = ref(false)
  let historyLoading: Promise<void> | null = null

  /** 历史对话抽屉：归档列表与选中的归档详情。 */
  const archiveList = ref<AssistantArchiveItem[]>([])
  const archiveListLoading = ref(false)
  const archiveDetail = ref<AssistantArchiveDetail | null>(null)
  const archiveDetailLoading = ref(false)

  async function load(force = false): Promise<void> {
    if (loaded.value && !force) {
      return
    }
    if (loading) {
      return loading
    }
    loading = knowledgeApi
      .storage()
      .then((info) => {
        defaultMode.value = normalize(info.assistantDefaultMode)
        loaded.value = true
      })
      .catch(() => {
        // 读取失败时沿用内置默认 hybrid，不阻断问答
      })
      .finally(() => {
        loading = null
      })
    return loading
  }

  function setDefaultMode(mode: string): void {
    defaultMode.value = normalize(mode)
    loaded.value = true
  }

  /** 回放历史：加载过一次就不再覆盖当前对话，除非显式 force。 */
  async function loadHistory(force = false): Promise<void> {
    if (historyLoaded.value && !force) {
      return
    }
    if (historyLoading) {
      return historyLoading
    }
    historyLoading = knowledgeApi
      .assistantHistory(sessionId.value)
      .then((history) => {
        sessionId.value = history.sessionId ?? null
        messages.value = (history.messages ?? []).map(toChatItem)
        truncated.value = Boolean(history.truncated)
        archiveCount.value = Number(history.archiveCount ?? 0)
        historyLoaded.value = true
      })
      .catch(() => {
        // 读不到历史就按空对话处理，提问本身不受影响
        historyLoaded.value = true
      })
      .finally(() => {
        historyLoading = null
      })
    return historyLoading
  }

  /** 加载当前会话的归档列表。 */
  async function loadArchives(): Promise<void> {
    if (!sessionId.value) {
      archiveList.value = []
      return
    }
    archiveListLoading.value = true
    try {
      const resp = await knowledgeApi.assistantArchives(sessionId.value)
      archiveList.value = resp.list ?? []
    } catch {
      archiveList.value = []
    } finally {
      archiveListLoading.value = false
    }
  }

  /** 查看某个归档的完整对话。 */
  async function viewArchive(archiveId: number): Promise<void> {
    archiveDetailLoading.value = true
    try {
      archiveDetail.value = await knowledgeApi.assistantArchiveDetail(archiveId)
    } catch {
      archiveDetail.value = null
    } finally {
      archiveDetailLoading.value = false
    }
  }

  /** 清除归档详情（关闭抽屉时）。 */
  function clearArchiveDetail(): void {
    archiveDetail.value = null
  }

  /**
   * 「清空对话」= 另起一条会话：当前线程清空，已经问过的仍留在库里，
   * 在「会话记录」页按「知识库问答」继续可查。
   */
  function resetThread(): void {
    messages.value = []
    sessionId.value = null
    truncated.value = false
    archiveCount.value = 0
    archiveList.value = []
    archiveDetail.value = null
    historyLoaded.value = true
  }

  return {
    defaultMode,
    loaded,
    load,
    setDefaultMode,
    messages,
    sessionId,
    truncated,
    archiveCount,
    historyLoaded,
    loadHistory,
    loadArchives,
    viewArchive,
    clearArchiveDetail,
    archiveList,
    archiveListLoading,
    archiveDetail,
    archiveDetailLoading,
    resetThread,
  }
})
