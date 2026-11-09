import { handleAuthRequired } from './auth'
import { ApiError, API_BASE, ErrorCode, explainCode, get, type ApiEnvelope } from './request'
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

/** 轮询上限：比后端单次调用的最长等待再宽一点，超时后让用户去历史产出看结果。 */
const LONG_POLL_MS = 660_000

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
    let envelope: ApiEnvelope | null = null
    try {
      envelope = JSON.parse(text) as ApiEnvelope
      code = envelope.code
      message = explainCode(envelope.code, envelope.message || message)
      if (code === ErrorCode.AUTH_REQUIRED) {
        handleAuthRequired()
        return
      }
    } catch {
      message = text.slice(0, 200) || message
    }
    const poll = pollTargetOf(envelope)
    if (poll) {
      // GAP-26：后端不再挂长连接，改成发起 + 增量取帧，帧语义与 SSE 完全一致
      await pollFrames(poll.framesUrl, poll.intervalMs, poll.start, handlers, signal)
      return
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

/** 轮询承载的每帧增量返回体，与后端 api/lab.py 的 frames 接口一一对应。 */
interface FramesPayload {
  frames?: SseFrame[]
  lastSeq?: number
  status?: string
  finished?: boolean
  truncated?: boolean
  intervalMs?: number
  expired?: boolean
}

/** 取帧地址已经带上了 /api/v1 前缀，接 get() 前要削成相对路径。 */
function framesPathOf(framesUrl: string): string {
  return framesUrl.startsWith(API_BASE) ? framesUrl.slice(API_BASE.length) : framesUrl
}

/** 发起响应是不是「改为轮询」的形态；不是就还按老的错误包封处理。 */
function pollTargetOf(
  envelope: ApiEnvelope | null,
): { start: SseFrame; framesUrl: string; intervalMs: number } | null {
  const data = envelope?.data
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return null
  }
  const record = data as Record<string, unknown>
  if (record.mode !== 'poll' || typeof record.framesUrl !== 'string' || !record.framesUrl) {
    return null
  }
  return {
    // 长连接时代由服务端下发 start 帧，轮询时代这里补上，停止按钮才知道要取消哪个任务
    start: { ...record, type: 'start', seq: 0 } as SseFrame,
    framesUrl: record.framesUrl,
    intervalMs: Number(record.intervalMs ?? 0) || 250,
  }
}

/** 轮询取帧直到终局帧；帧派发与 SSE 分支共用一个 dispatch。 */
async function pollFrames(
  framesUrl: string,
  intervalMs: number,
  start: SseFrame,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const path = framesPathOf(framesUrl)
  const step = Math.max(100, intervalMs)
  const deadline = Date.now() + LONG_POLL_MS
  handlers.onStart?.(start)
  let after = 0
  let quiet = 0
  let terminal = false
  while (!terminal && !signal?.aborted && Date.now() < deadline) {
    let payload: FramesPayload
    try {
      payload = await get<FramesPayload>(path, { after })
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '取流式帧失败'
      handlers.onError?.(message, { type: 'error', seq: -1, errorMessage: message })
      return
    }
    const frames = payload.frames ?? []
    for (const frame of frames) {
      if (frame.type === 'done' || frame.type === 'error') {
        terminal = true
      }
      dispatch(frame, handlers)
      after = Math.max(after, Number(frame.seq ?? after))
    }
    after = Math.max(after, Number(payload.lastSeq ?? after))
    quiet = frames.length ? 0 : quiet + 1
    if (payload.finished && (terminal || quiet >= 2)) {
      if (!terminal && !signal?.aborted) {
        const message = '流式执行已结束，但没有取到产出帧'
        handlers.onError?.(message, { type: 'error', seq: -1, errorMessage: message })
      }
      return
    }
    await new Promise<void>((resolve) => {
      setTimeout(resolve, step)
    })
  }
  if (!terminal && !signal?.aborted) {
    const message = '流式执行等待超时，可在历史产出里查看结果'
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
