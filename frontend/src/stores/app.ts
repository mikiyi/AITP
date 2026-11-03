import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { projectApi } from '@/api'
import type { Project } from '@/api/types'

/** 全局应用状态：当前项目、侧栏、后端连通性。 */
export const useAppStore = defineStore('app', () => {
  const projects = ref<Project[]>([])
  const currentProjectId = ref<number | null>(null)
  const sidebarCollapsed = ref(false)
  const backendOnline = ref<boolean | null>(null)

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
    } catch {
      projects.value = []
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
    currentProject,
    sidebarCollapsed,
    backendOnline,
    loadProjects,
    selectProject,
    toggleSidebar,
  }
})
