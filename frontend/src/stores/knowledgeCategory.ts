import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { knowledgeApi } from '@/api'
import type { KnowledgeCategory } from '@/api/types'

/** 建库时的兜底分类；分类清单加载失败时也用它。 */
export const DEFAULT_KNOWLEDGE_CATEGORY = '通用'

/**
 * 知识分类清单：分类是中文名，内置分类 + 自定义分类共用一份清单。
 * 知识库、能力页「加入知识库」等多处下拉都读这里，新加的分类立即全局可见。
 */
export const useKnowledgeCategoryStore = defineStore('knowledgeCategory', () => {
  const list = ref<KnowledgeCategory[]>([])
  const loaded = ref(false)
  let loading: Promise<void> | null = null

  const options = computed(() =>
    list.value.map((item) => ({ label: item.name, value: item.name })),
  )
  const names = computed(() => list.value.map((item) => item.name))

  async function load(force = false): Promise<void> {
    if (loaded.value && !force) {
      return
    }
    if (loading) {
      return loading
    }
    loading = knowledgeApi
      .categories()
      .then((result) => {
        list.value = result.list
        loaded.value = true
      })
      .catch(() => {
        // 下拉不该因为分类接口失败而打不开，保留空清单即可
      })
      .finally(() => {
        loading = null
      })
    return loading
  }

  async function create(name: string): Promise<KnowledgeCategory> {
    const item = await knowledgeApi.createCategory(name.trim())
    if (!list.value.some((entry) => entry.name === item.name)) {
      list.value = [...list.value, item].sort((a, b) => a.sortOrder - b.sortOrder)
    }
    return item
  }

  async function remove(id: number): Promise<void> {
    await knowledgeApi.removeCategory(id)
    list.value = list.value.filter((item) => item.id !== id)
  }

  return { list, loaded, options, names, load, create, remove }
})
