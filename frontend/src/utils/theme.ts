/**
 * 读取 <html> 上的 CSS 变量实际值。
 *
 * 画布（ECharts、思维导图）里拿不到 CSS 变量——canvas 只认颜色字面量，
 * 所以这类地方要把变量读成真实颜色再用。
 */
export function cssVar(name: string): string {
  if (typeof window === 'undefined') {
    return ''
  }
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}
