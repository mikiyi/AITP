import { defineStore } from 'pinia'
import { ref } from 'vue'
import { badgeApi } from '@/api'

/** 导航路径 → 角标分区；详情页按前缀命中同一分区。 */
export const SECTION_BY_PATH: Record<string, string> = {
  '/requirements': 'requirements',
  '/test-cases': 'cases',
  '/bugs': 'bugs',
  '/sql-analyses': 'sql_analyses',
  '/regressions': 'regressions',
  '/reports': 'reports',
  '/automation/web': 'automation_web',
  '/automation/app': 'automation_app',
  '/automation/api': 'automation_api',
  '/automation/performance': 'automation_performance',
  '/knowledge': 'knowledge',
}

/**
 * 不随「进入页面」清零的分区：它们的数字是实时状态而非未读累加。
 * knowledge = 待审池真实条数，只有真正审核掉才会变，点进页面不该归零。
 */
const PERSISTENT_SECTIONS = new Set(['knowledge'])

export function sectionOfPath(path: string): string {
  for (const [prefix, section] of Object.entries(SECTION_BY_PATH)) {
    if (path === prefix || path.startsWith(`${prefix}/`)) {
      return section
    }
  }
  return ''
}

/**
 * 导航未读角标：AI 产出一键添加后 +N，进入对应列表页查看后清零。
 */
export const useBadgeStore = defineStore('badges', () => {
  const items = ref<Record<string, number>>({})
  let loading: Promise<void> | null = null

  const total = () => Object.values(items.value).reduce((sum, value) => sum + value, 0)

  async function load(): Promise<void> {
    if (loading) {
      return loading
    }
    loading = badgeApi
      .list()
      .then((result) => {
        items.value = result.items
      })
      .catch(() => {
        // 角标失败不影响主流程
      })
      .finally(() => {
        loading = null
      })
    return loading
  }

  function countOf(path: string): number {
    const section = SECTION_BY_PATH[path]
    return section ? items.value[section] ?? 0 : 0
  }

  /** 进入某分区页面时清零；持久分区（实时状态）与无未读的跳过。 */
  async function clearForPath(path: string): Promise<void> {
    const section = sectionOfPath(path)
    if (!section || PERSISTENT_SECTIONS.has(section) || !(items.value[section] ?? 0)) {
      return
    }
    try {
      await badgeApi.clear(section)
      items.value = { ...items.value, [section]: 0 }
    } catch {
      await load()
    }
  }

  return { items, total, load, countOf, clearForPath }
})
