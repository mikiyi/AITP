import { computed, ref } from 'vue'
import { moduleApi, projectVersionApi } from '@/api'
import type { ModuleOption } from '@/api/types'
import { useAppStore } from '@/stores/app'

export interface OptionItem {
  label: string
  value: number
}

/** 资产列表页统一的项目过滤条件。 */
export function useProjectExtraQuery() {
  const appStore = useAppStore()
  return computed(() => ({
    projectId: appStore.currentProjectId ?? undefined,
  }))
}

/** 当前项目的版本清单，供版本下拉筛选；换项目时由调用方的 useProjectScope 触发重拉。 */
export function useProjectVersionOptions() {
  const appStore = useAppStore()
  const versionOptions = ref<OptionItem[]>([])

  async function loadVersionOptions(): Promise<void> {
    const pid = appStore.currentProjectId
    if (!pid) {
      versionOptions.value = []
      return
    }
    try {
      const result = await projectVersionApi.list(pid)
      versionOptions.value = result.list.map((v) => ({
        label: v.versionCode,
        value: v.id as number,
      }))
    } catch {
      versionOptions.value = []
    }
  }

  return { versionOptions, loadVersionOptions }
}

/** 当前项目的功能点选项，供列表列渲染与筛选。 */
export function useProjectModuleOptions() {
  const appStore = useAppStore()
  const moduleOptions = ref<ModuleOption[]>([])

  async function loadModuleOptions(): Promise<void> {
    const pid = appStore.currentProjectId
    if (!pid) {
      moduleOptions.value = []
      return
    }
    try {
      moduleOptions.value = await moduleApi.options(pid, true)
    } catch {
      moduleOptions.value = []
    }
  }

  return { moduleOptions, loadModuleOptions }
}
