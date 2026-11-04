/** 百分数统一格式：入参是 0~100 的百分数，页面展示保留两位小数。 */
export function percentText(value: number): string {
  const num = Number(value)
  return `${(Number.isFinite(num) ? num : 0).toFixed(2)}%`
}
