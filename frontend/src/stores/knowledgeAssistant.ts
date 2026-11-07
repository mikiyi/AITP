import { defineStore } from 'pinia'
import { ref } from 'vue'
import { knowledgeApi } from '@/api'

export type AssistantMode = 'kb' | 'llm' | 'hybrid'

/** 与后端 DEFAULT_ASSISTANT_MODE 保持一致。 */
export const DEFAULT_ASSISTANT_MODE: AssistantMode = 'hybrid'

const MODES: AssistantMode[] = ['kb', 'llm', 'hybrid']

function normalize(raw: unknown): AssistantMode {
  return MODES.includes(raw as AssistantMode) ? (raw as AssistantMode) : DEFAULT_ASSISTANT_MODE
}

/**
 * 问答助手默认回答模式：设置页写入，首页卡片与问答助手页共用。
 * 用 store 而不是各自请求，设置页保存后已挂载的助手徽标能立刻跟着变。
 */
export const useKnowledgeAssistantStore = defineStore('knowledgeAssistant', () => {
  const defaultMode = ref<AssistantMode>(DEFAULT_ASSISTANT_MODE)
  const loaded = ref(false)
  let loading: Promise<void> | null = null

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

  return { defaultMode, loaded, load, setDefaultMode }
})
