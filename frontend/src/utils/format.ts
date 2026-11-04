/** 百分数统一格式：入参是 0~100 的百分数，页面展示保留两位小数。 */
export function percentText(value: number): string {
  const num = Number(value)
  return `${(Number.isFinite(num) ? num : 0).toFixed(2)}%`
}

/**
 * 接口返回的时间统一是 UTC ISO（`2026-11-03T10:13:16.275Z`），
 * 页面一律显示成本地时间 `2026-11-03 18:13:16`，不要把 ISO 串直接丢给用户。
 */
export function dateTimeText(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '-'
  }
  const raw = String(value)
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) {
    return raw
  }
  const pad = (num: number) => String(num).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}
