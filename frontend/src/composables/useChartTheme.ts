import { computed } from 'vue'
import { useThemeStore } from '@/stores/theme'
import { cssVar } from '@/utils/theme'

/**
 * 图表配色跟随皮肤。
 *
 * 画布只认颜色字面量（CSS 变量在 canvas 里不生效），
 * 所以这里把 CSS 变量读成真实颜色；依赖主题状态，
 * 皮肤一变就重新求值，图表跟着重画。
 */
export function useChartTheme() {
  const store = useThemeStore()

  function read(name: string, fallback: string): string {
    void store.theme
    return cssVar(name) || fallback
  }

  const panel = computed(() => read('--aitp-panel', '#ffffff'))
  const text = computed(() => read('--aitp-text', '#303133'))
  const weak = computed(() => read('--aitp-text-weak', '#909399'))
  const primary = computed(() => read('--aitp-primary', '#2f6fed'))
  const border = computed(() => read('--aitp-border', '#e4e7ed'))

  return { panel, text, weak, primary, border }
}
