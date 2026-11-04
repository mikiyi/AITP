import { ref } from 'vue'
import { labApi } from '@/api'
import type { PromptTemplate } from '@/api/types'

/**
 * 提示词模板缓存。
 *
 * 能力 → 提示词 code → 变量清单，是「按变量渲染表单」的唯一依据：
 * 后端 registry.render() 会校验必填变量，前端必须用同一份变量名，
 * 否则填了也报 120102。
 */
const cache = ref<Record<string, PromptTemplate>>({})
const loading = ref(false)

async function load(code: string): Promise<PromptTemplate | null> {
  if (!code) {
    return null
  }
  const cached = cache.value[code]
  if (cached) {
    return cached
  }
  try {
    const template = await labApi.prompt(code)
    cache.value = { ...cache.value, [code]: template }
    return template
  } catch {
    return null
  }
}

/** 取提示词变量清单；失败时退回空列表。 */
async function variablesOf(code: string): Promise<{ required: string[]; optional: string[] }> {
  const template = await load(code)
  if (!template) {
    return { required: [], optional: [] }
  }
  const optional = template.optional ?? []
  const required = (template.variables ?? []).filter((name) => !optional.includes(name))
  return { required, optional }
}

/** 一次取回多个提示词的变量清单。 */
async function loadMany(codes: string[]): Promise<void> {
  const missing = codes.filter((code) => code && !cache.value[code])
  if (!missing.length) {
    return
  }
  loading.value = true
  try {
    const results = await Promise.all(missing.map((code) => load(code)))
    const next = { ...cache.value }
    results.forEach((template) => {
      if (template) {
        next[template.code] = template
      }
    })
    cache.value = next
  } finally {
    loading.value = false
  }
}

export function usePromptTemplates() {
  return { cache, loading, load, loadMany, variablesOf }
}
