import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError, ErrorCode } from '@/api/request'
import type { KnowledgeRef } from '@/api/types'

/**
 * 引用保护提示：撤销 / 删除被下游引用的知识条目时，
 * 后端返回 140104 并在 data.refs 里带上引用清单，这里统一转成弹窗展示。
 */
export function useReferenceGuard() {
  const blockedVisible = ref(false)
  const blockedRefs = ref<KnowledgeRef[]>([])
  const blockedMessage = ref('')

  /** 处理写操作异常；被引用时弹清单，其它错误弹 toast。返回是否被引用拦截。 */
  function handleError(err: unknown, fallback: string): boolean {
    if (err instanceof ApiError && err.code === ErrorCode.RESOURCE_REFERENCED) {
      const payload = err.data as { refs?: KnowledgeRef[] } | null
      blockedRefs.value = payload?.refs ?? []
      blockedMessage.value = err.message || '该条目被下游引用，无法删除或撤销'
      blockedVisible.value = true
      return true
    }
    ElMessage.error(err instanceof Error ? err.message : fallback)
    return false
  }

  return { blockedVisible, blockedRefs, blockedMessage, handleError }
}
