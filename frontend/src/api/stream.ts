import { handleAuthRequired } from './auth'
import { ApiError, API_BASE, ErrorCode, explainCode, type ApiEnvelope } from './request'
import type { MaskInfo } from './types'

/** SSE 帧类型，与后端 api/sse.py 的常量一一对应。 */
export type SseFrameType =
  | 'start'
  | 'mask'
  | 'delta'
  | 'reasoning'
  | 'usage'
  | 'degrade'
  | 'done'
  | 'error'

export interface SseFrame {
  type: SseFrameType
  seq: number
  [key: string]: unknown
}

/** 模型降级信息：前端据此弹 3 秒提示，不阻塞主流程。 */
export interface DegradeInfo {
  degraded: boolean
  reason: string
  message: string
  fromConfig: string
  toConfig: string
}

export interface StreamHandlers {
  /** 每收到一段增量文本时调用。 */
  onDelta?: (content: string, frame: SseFrame) => void
  /** 收到推理过程时调用。 */
  onReasoning?: (content: string, frame: SseFrame) => void
  /** 收到 usage 时调用。 */
  onUsage?: (usage: Record<string, unknown>, frame: SseFrame) => void
  /** 首选模型不可用、已按优先级降级时调用。 */
  onDegrade?: (info: DegradeInfo, frame: SseFrame) => void
  /** 云端模型真正开始出字前告知脱敏结果（本地模型不会触发）。 */
  onMask?: (info: MaskInfo, frame: SseFrame) => void
  /** 流结束（done 帧）时调用。 */
  onDone?: (output: string, frame: SseFrame) => void
  /** 出错时调用。 */
  onError?: (message: string, frame: SseFrame) => void
  /** 连接建立后调用，拿到 taskId / sessionId。 */
  onStart?: (frame: SseFrame) => void
}

function parseFrame(raw: string): SseFrame | null {
  const line = raw.trim()
  if (!line.startsWith('data:')) {
    return null
  }
  const payload = line.slice(5).trim()
  if (!payload || payload === '[DONE]') {
    return null
  }
  try {
    return JSON.parse(payload) as SseFrame
  } catch {
    return null
  }
}

/**
 * POST + SSE 流式读取。
 * 网络异常与业务错误统一转成 ApiError，保证调用方只需处理一种失败形态。
 */
export async function streamPost(
  path: string,
  body: unknown,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      return
    }
    const message = '无法连接后端服务，请确认 AITP 已启动'
    handlers.onError?.(message, { type: 'error', seq: -1, errorMessage: message })
    return
  }

  const contentType = response.headers.get('Content-Type') || ''
  if (!contentType.includes('text/event-stream')) {
    const text = await response.text()
    let message = '流式响应异常'
    let code: number = ErrorCode.SUCCESS
    try {
      const envelope = JSON.parse(text) as ApiEnvelope
      code = envelope.code
      message = explainCode(envelope.code, envelope.message || message)
      if (code === ErrorCode.AUTH_REQUIRED) {
        handleAuthRequired()
      }
    } catch {
      message = text.slice(0, 200) || message
    }
    handlers.onError?.(message, { type: 'error', seq: -1, errorCode: code, errorMessage: message })
    return
  }

  const reader = response.body?.getReader()
  if (!reader) {
    const message = '浏览器不支持流式读取'
    handlers.onError?.(message, { type: 'error', seq: -1, errorMessage: message })
    return
  }

  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }
      buffer += decoder.decode(value, { stream: true })
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const raw = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const frame = parseFrame(raw)
        if (frame) {
          dispatch(frame, handlers)
        }
        boundary = buffer.indexOf('\n\n')
      }
    }
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      return
    }
    const message = err instanceof ApiError ? err.message : '流式连接中断'
    handlers.onError?.(message, { type: 'error', seq: -1, errorMessage: message })
  }
}

function dispatch(frame: SseFrame, handlers: StreamHandlers): void {
  switch (frame.type) {
    case 'start':
      handlers.onStart?.(frame)
      break
    case 'mask':
      handlers.onMask?.(
        {
          enabled: Boolean(frame.enabled ?? true),
          applied: Boolean(frame.applied),
          types: (frame.types ?? []) as MaskInfo['types'],
          count: Number(frame.count ?? 0),
        },
        frame,
      )
      break
    case 'delta':
      handlers.onDelta?.(String(frame.content ?? ''), frame)
      break
    case 'reasoning':
      handlers.onReasoning?.(String(frame.content ?? ''), frame)
      break
    case 'usage': {
      const usage = { ...(frame.usage ?? {}) } as Record<string, unknown>
      if (frame.model) {
        usage.model = frame.model
      }
      handlers.onUsage?.(usage, frame)
      break
    }
    case 'degrade':
      handlers.onDegrade?.(
        {
          degraded: true,
          reason: String(frame.reason ?? ''),
          message: String(frame.message ?? '模型已按优先级自动降级'),
          fromConfig: String(frame.fromConfig ?? ''),
          toConfig: String(frame.toConfig ?? ''),
        },
        frame,
      )
      break
    case 'done':
      handlers.onDone?.(String(frame.output ?? ''), frame)
      break
    case 'error': {
      const message = explainCode(
        Number(frame.errorCode ?? -1),
        String(frame.errorMessage ?? 'AI 调用失败'),
      )
      handlers.onError?.(message, frame)
      break
    }
    default:
      break
  }
}
