import { onUnmounted } from 'vue'

/**
 * 输入型筛选条件的「停手即查」：打字过程中不发请求，停下来再查一次。
 * 下拉、清空这类一步到位的筛选仍应直接重查，不要用这个。
 */
export function useAutoQuery(reload: () => void, delay = 400): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined

  function schedule(): void {
    if (timer !== undefined) {
      clearTimeout(timer)
    }
    timer = setTimeout(() => {
      timer = undefined
      reload()
    }, delay)
  }

  onUnmounted(() => {
    if (timer !== undefined) {
      clearTimeout(timer)
    }
  })

  return schedule
}
