/**
 * 未捕获异常的全局兜底（GAP-26 功能点 4）。
 *
 * 单个页面里的 try/catch 只能救自己看得见的调用：模板渲染、生命周期钩子、
 * 忘记 catch 的 Promise 一路抛上去时，用户看到的是白屏或者「转圈很久后没结果」。
 * 这里把这三类出口一次收口：登记成一条可读的错误态，由 GlobalErrorBoundary
 * 铺一层兜底页，同时把它打进控制台并带上追踪号，便于对着 data/aitp.log 复查。
 */

import { computed, ref, type App } from 'vue'

export interface GlobalErrorState {
  message: string
  /** 后端包封返回的追踪号；纯前端异常没有它。 */
  traceId: string
  /** 本页生成的追踪号，用来把界面提示和控制台日志对上。 */
  clientTraceId: string
  source: string
  at: string
}

const current = ref<GlobalErrorState | null>(null)

function clientTrace(): string {
  const rand = Math.random().toString(16).slice(2, 10)
  return `fe-${rand}${Date.now().toString(16).slice(-6)}`
}

function messageOf(reason: unknown): string {
  if (reason instanceof Error) {
    return reason.message || reason.name
  }
  if (typeof reason === 'string' && reason.trim()) {
    return reason
  }
  return '发生了未预期的错误'
}

/** 后端追踪号只在 ApiError 上带；用结构判断而不是 import 类型，避免这里反向依赖 api 层。 */
function traceIdOf(reason: unknown): string {
  if (reason && typeof reason === 'object' && 'traceId' in reason) {
    return String((reason as { traceId?: unknown }).traceId ?? '')
  }
  return ''
}

export function reportGlobalError(reason: unknown, source = '未知位置'): void {
  const state: GlobalErrorState = {
    message: messageOf(reason),
    traceId: traceIdOf(reason),
    clientTraceId: clientTrace(),
    source,
    at: new Date().toLocaleString(),
  }
  current.value = state
  const trace = state.traceId ? `${state.clientTraceId} / 后端 ${state.traceId}` : state.clientTraceId
  console.error(`[AITP 未捕获异常 ${trace}]`, reason)
}

export function dismissGlobalError(): void {
  current.value = null
}

export function useGlobalError() {
  return {
    globalError: computed(() => current.value),
    dismiss: dismissGlobalError,
  }
}

/**
 * 挂三类出口：Vue 渲染/生命周期错误走 errorHandler，脚本错误与 Promise 拒绝走 window 事件。
 * 资源加载失败（图片/脚本 404）也会触发 window 的 error 事件，但那不是 ErrorEvent，
 * 兜底页不该因为一张图挂了就把整个界面盖住，所以只收 ErrorEvent。
 */
export function installGlobalErrorGuard(app: App): void {
  app.config.errorHandler = (err, _instance, info) => {
    reportGlobalError(err, `页面渲染（${info}）`)
  }
  window.addEventListener('error', (event) => {
    if (event instanceof ErrorEvent) {
      reportGlobalError(event.error ?? event.message, '浏览器脚本')
    }
  })
  window.addEventListener('unhandledrejection', (event) => {
    reportGlobalError(event.reason, '异步任务')
  })
}
