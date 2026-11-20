/** 占位提示浮显：input/textarea 的 placeholder 被视觉截断时，悬停显示全文。
 *
 * 全站单例、事件委托，不改各页面 markup：
 *  - mouseover 委托命中 input[placeholder] / textarea[placeholder]，先量文本是否真被裁掉
 *    （单行按宽度，多行按估算行高），裁了才挂浮层，短提示不打扰；
 *  - 浮层只放 placeholder 提示文本，绝不放输入框里的值，无泄密面；
 *  - 鼠标移开 / 页面滚动 / 开始输入即隐藏。
 */

const TIP_CLASS = 'input-hint-tip'
const TIP_OFFSET = 6

let tipEl: HTMLDivElement | null = null
let currentTarget: HTMLInputElement | HTMLTextAreaElement | null = null
let measureCanvas: HTMLCanvasElement | null = null

function ensureTip(): HTMLDivElement {
  if (!tipEl) {
    tipEl = document.createElement('div')
    tipEl.className = TIP_CLASS
    tipEl.setAttribute('role', 'tooltip')
    tipEl.style.display = 'none'
    document.body.appendChild(tipEl)
  }
  return tipEl
}

function textWidth(text: string, font: string): number {
  measureCanvas ??= document.createElement('canvas')
  const ctx = measureCanvas.getContext('2d')
  if (!ctx) {
    return 0
  }
  ctx.font = font
  return ctx.measureText(text).width
}

/** 提示文本是否真的被控件可视区裁掉；量不出（隐藏元素等）一律当作没裁，不弹层。 */
function isPlaceholderClipped(el: HTMLInputElement | HTMLTextAreaElement): boolean {
  const placeholder = el.placeholder.trim()
  if (!placeholder || el.clientWidth === 0 || el.clientHeight === 0) {
    return false
  }
  const style = getComputedStyle(el)
  const contentWidth = el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
  if (contentWidth <= 0) {
    return false
  }
  const font = `${style.fontSize} ${style.fontFamily}`
  if (el instanceof HTMLTextAreaElement) {
    // 多行占位会自动换行：按内容宽折算行数，总行高超可视高才算截断
    const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.5
    const lines = placeholder
      .split('\n')
      .reduce((count, line) => count + Math.max(1, Math.ceil(textWidth(line, font) / contentWidth)), 0)
    const contentHeight = el.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)
    return lines * lineHeight > contentHeight + 1
  }
  return textWidth(placeholder, font) > contentWidth + 1
}

function show(el: HTMLInputElement | HTMLTextAreaElement): void {
  const tip = ensureTip()
  tip.textContent = el.placeholder
  tip.style.display = 'block'
  tip.style.visibility = 'hidden'
  const rect = el.getBoundingClientRect()
  const tipRect = tip.getBoundingClientRect()
  let left = rect.left
  left = Math.max(8, Math.min(left, window.innerWidth - tipRect.width - 8))
  let top = rect.bottom + TIP_OFFSET
  if (top + tipRect.height > window.innerHeight - 8) {
    top = Math.max(8, rect.top - tipRect.height - TIP_OFFSET)
  }
  tip.style.left = `${Math.round(left)}px`
  tip.style.top = `${Math.round(top)}px`
  tip.style.visibility = ''
}

function hide(): void {
  if (tipEl) {
    tipEl.style.display = 'none'
  }
  currentTarget = null
}

function onMouseOver(event: MouseEvent): void {
  const raw = event.target as HTMLElement | null
  const hit = raw?.closest?.('input[placeholder], textarea[placeholder]') ?? null
  if (hit === currentTarget) {
    return
  }
  hide()
  if (
    hit instanceof HTMLInputElement
    || hit instanceof HTMLTextAreaElement
  ) {
    if (isPlaceholderClipped(hit)) {
      currentTarget = hit
      show(hit)
    }
  }
}

/** 全局安装一次；返回卸载函数，仅测试用。 */
export function installInputHintTip(): () => void {
  document.addEventListener('mouseover', onMouseOver)
  const dismiss = (): void => hide()
  document.addEventListener('scroll', dismiss, true)
  document.addEventListener('input', dismiss, true)
  window.addEventListener('resize', dismiss)
  return () => {
    document.removeEventListener('mouseover', onMouseOver)
    document.removeEventListener('scroll', dismiss, true)
    document.removeEventListener('input', dismiss, true)
    window.removeEventListener('resize', dismiss)
    tipEl?.remove()
    tipEl = null
  }
}
