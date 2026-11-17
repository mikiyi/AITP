import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { projectApi } from '@/api'
import { setRequestProjectId } from '@/api/request'
import type { Project } from '@/api/types'

const PROJECT_STORAGE_KEY = 'aitp.currentProjectId'
const SIDEBAR_STORAGE_KEY = 'aitp.sidebarCollapsed'

function storedProjectId(): number | null {
  try {
    const raw = window.localStorage.getItem(PROJECT_STORAGE_KEY)
    const value = Number(raw)
    return Number.isInteger(value) && value > 0 ? value : null
  } catch {
    return null
  }
}

function storedSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

/** 全局应用状态：当前项目、侧栏、后端连通性。 */
export const useAppStore = defineStore('app', () => {
  const projects = ref<Project[]>([])
  const currentProjectId = ref<number | null>(storedProjectId())
  const projectsLoaded = ref(false)
  const sidebarCollapsed = ref(storedSidebarCollapsed())
  const backendOnline = ref<boolean | null>(null)

  watch(currentProjectId, (value) => {
    setRequestProjectId(value)
    try {
      if (value) {
        window.localStorage.setItem(PROJECT_STORAGE_KEY, String(value))
      } else {
        window.localStorage.removeItem(PROJECT_STORAGE_KEY)
      }
    } catch {
      // 隐私模式禁用存储时只影响记住项目，不影响本次会话
    }
  }, { immediate: true })

  watch(sidebarCollapsed, (value) => {
    try {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, value ? '1' : '0')
    } catch {
      // 隐私模式禁用存储时只影响记住折叠态，不影响本次会话
    }
  })

  const currentProject = computed(
    () => projects.value.find((item) => item.id === currentProjectId.value) ?? null,
  )

  async function loadProjects(): Promise<void> {
    try {
      const list = await projectApi.options()
      projects.value = list
      if (list.length && !list.some((item) => item.id === currentProjectId.value)) {
        currentProjectId.value = list[0].id
      }
      if (!list.length) {
        currentProjectId.value = null
      }
      projectsLoaded.value = true
    } catch {
      projects.value = []
    }
  }

  async function ensureProjects(): Promise<void> {
    if (!projectsLoaded.value) {
      await loadProjects()
    }
  }

  function selectProject(id: number | null): void {
    currentProjectId.value = id
  }

  function toggleSidebar(): void {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  return {
    projects,
    currentProjectId,
    projectsLoaded,
    currentProject,
    sidebarCollapsed,
    backendOnline,
    loadProjects,
    ensureProjects,
    selectProject,
    toggleSidebar,
  }
})
