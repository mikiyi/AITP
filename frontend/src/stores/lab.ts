import { defineStore } from 'pinia'
import { ref } from 'vue'
import { labApi, moduleApi } from '@/api'
import type { CapabilitySpec, ModuleOption } from '@/api/types'

/** AI 实验室状态：能力目录、功能点选项、当前会话。 */
export const useLabStore = defineStore('lab', () => {
  const capabilities = ref<CapabilitySpec[]>([])
  /** 全部能力（含知识问答），按需加载。 */
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
  }
})
