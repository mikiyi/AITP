import { defineStore } from 'pinia'
import { ref } from 'vue'
import { labApi, moduleApi } from '@/api'
import type { CapabilitySpec, ModuleOption } from '@/api/types'

/** 非单能力的能力/会话类型中文名：它们不在能力目录里，页面展示时要能兜底。 */
const EXTRA_CAPABILITY_LABELS: Record<string, string> = {
  lab: '实验室',
  composite: '多入口顺序执行',
  'knowledge-assistant': '知识库问答',
}

/** AI 实验室状态：能力目录、功能点选项、当前会话。 */
export const useLabStore = defineStore('lab', () => {
  const capabilities = ref<CapabilitySpec[]>([])
  /** 完整能力目录：记录/会话列表的能力筛选与标签回显用它，不只首页卡片。 */
  const allCapabilities = ref<CapabilitySpec[]>([])
  const moduleOptions = ref<ModuleOption[]>([])
  const currentSessionId = ref<number | null>(null)
  const currentModuleId = ref<number | null>(null)
  const loading = ref(false)

  async function loadCapabilities(): Promise<void> {
    if (capabilities.value.length) {
      return
    }
    loading.value = true
    try {
      capabilities.value = await labApi.mainCapabilities()
    } finally {
      loading.value = false
    }
  }

  async function loadAllCapabilities(): Promise<void> {
    if (allCapabilities.value.length) {
      return
    }
    allCapabilities.value = await labApi.capabilities()
  }

  async function loadModuleOptions(projectId: number | undefined): Promise<void> {
    if (!projectId) {
      moduleOptions.value = []
      return
    }
    try {
      moduleOptions.value = await moduleApi.options(projectId, true)
    } catch {
      moduleOptions.value = []
    }
  }

  function reset(): void {
    currentSessionId.value = null
    currentModuleId.value = null
  }

  /** 能力编码 → 中文名：未知编码原样返回，避免记录/会话页直接露出英文编号。 */
  function capabilityLabel(code: string | null | undefined): string {
    if (!code) {
      return '-'
    }
    const hit = allCapabilities.value.find((item) => item.code === code)
      ?? capabilities.value.find((item) => item.code === code)
    return hit?.label ?? EXTRA_CAPABILITY_LABELS[code] ?? code
  }

  return {
    capabilities,
    allCapabilities,
    moduleOptions,
    currentSessionId,
    currentModuleId,
    loading,
    loadCapabilities,
    loadAllCapabilities,
    loadModuleOptions,
    reset,
    capabilityLabel,
  }
})
