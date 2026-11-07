import { handleAuthRequired } from './auth'

/** 后端统一包封：HTTP 恒 200，业务码在 data 里。 */
export interface ApiEnvelope<T = unknown> {
  code: number
  message: string
  data: T
  traceId: string
  timestamp: string
}

/** 分页载荷。 */
export interface PagePayload<T> {
  list: T[]
  pageNum: number
  pageSize: number
  total: number
  pages: number
}

/** 业务错误码。 */
export const ErrorCode = {
  SUCCESS: 200000,
  PARAM_ERROR: 100100,
  RESOURCE_NOT_FOUND: 140100,
  RESOURCE_REFERENCED: 140104,
  ILLEGAL_OPERATION: 140105,
  AI_CREDENTIAL_MISSING: 110102,
  AUTH_REQUIRED: 150101,
  AUTH_FAILED: 150102,
  AUTH_LOCKED: 150103,
} as const

/** 携带业务码与 traceId 的错误对象，便于前端区分提示文案。 */
export class ApiError extends Error {
  readonly code: number
  readonly traceId: string
  readonly isNetwork: boolean
  /** 请求是否因前端超时被中止（区别于用户主动取消）。 */
  readonly isTimeout: boolean
  /** 业务包封的 data 段，引用保护等错误会在这里回传引用清单。 */
  readonly data: unknown

  constructor(
    message: string,
    code = -1,
    traceId = '',
    isNetwork = false,
    data: unknown = null,
    isTimeout = false,
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.traceId = traceId
    this.isNetwork = isNetwork
    this.isTimeout = isTimeout
    this.data = data
  }
}

export const API_BASE = '/api/v1'

/** 普通接口默认超时；后端卡死或无响应时页面不再无限等待。 */
export const DEFAULT_TIMEOUT_MS = 60_000
/** AI 生成、批量执行等长耗时接口的放宽超时。 */
export const LONG_TIMEOUT_MS = 600_000
/** 导出/下载类接口的超时。 */
export const DOWNLOAD_TIMEOUT_MS = 300_000

/** 业务码 → 用户可读提示。 */
export function explainCode(code: number, fallback: string): string {
  switch (code) {
    case ErrorCode.PARAM_ERROR:
      // 后端通常已返回具体原因（如「环境变量名只能…」），优先展示，别用笼统文案盖掉
      return fallback && fallback !== '请求失败' ? fallback : '参数不合法，请检查后重试'
    case ErrorCode.RESOURCE_NOT_FOUND:
      return '目标不存在或已被删除'
    case ErrorCode.RESOURCE_REFERENCED:
      return fallback.includes('引用') ? fallback : '该条目被下游引用，无法删除或撤销'
    case ErrorCode.ILLEGAL_OPERATION:
      return '当前状态不允许该操作'
    case ErrorCode.AI_CREDENTIAL_MISSING:
      return '未配置可用的 AI 模型，请到「设置 → AI 配置」添加'
    case ErrorCode.AUTH_REQUIRED:
      return '登录状态已失效，请重新输入访问口令'
    case ErrorCode.AUTH_FAILED:
      return fallback || '口令不正确'
    case ErrorCode.AUTH_LOCKED:
      return fallback || '口令尝试次数过多，请稍后再试'
    default:
      return fallback
  }
}

async function parseBody(response: Response): Promise<ApiEnvelope> {
  const text = await response.text()
  if (!text) {
    throw new ApiError('后端返回空响应', -1, '', true)
  }
  try {
    return JSON.parse(text) as ApiEnvelope
  } catch {
    throw new ApiError('后端返回了非 JSON 内容', -1, '', true)
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  query?: Record<string, string | number | boolean | undefined | null>
  body?: unknown
  signal?: AbortSignal
  /** 覆盖默认超时（毫秒），传 0 表示不超时。 */
  timeoutMs?: number
}

function timeoutMessage(ms: number): string {
  const label = ms >= 1000 ? `${Math.round(ms / 1000)} 秒` : `${ms} 毫秒`
  return `请求超时（${label}），请稍后重试或检查后端服务`
}

/** 给请求套上默认超时；调用方自己的 signal 仍然可以主动取消。 */
function withTimeout(
  external: AbortSignal | undefined,
  timeoutMs: number,
): { signal: AbortSignal; timedOut: () => boolean; done: () => void } {
  const controller = new AbortController()
  let timedOut = false
  const timer =
    timeoutMs > 0
      ? window.setTimeout(() => {
          timedOut = true
          controller.abort()
        }, timeoutMs)
      : 0
  const forward = () => controller.abort()
  if (external) {
    if (external.aborted) {
      controller.abort()
    } else {
      external.addEventListener('abort', forward, { once: true })
    }
  }
  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    done: () => {
      if (timer) {
        window.clearTimeout(timer)
      }
      external?.removeEventListener('abort', forward)
    },
  }
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

/**
 * 统一请求入口。
 * 所有网络异常都会转成 ApiError（isNetwork=true），调用方只需 catch 一次。
 */
export async function request<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET'
  let url = `${API_BASE}${path}`
  if (options.query) {
    const params = new URLSearchParams()
    Object.entries(options.query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params.append(key, String(value))
      }
    })
    const qs = params.toString()
    if (qs) {
      url += (url.includes('?') ? '&' : '?') + qs
    }
  }

  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const guard = withTimeout(options.signal, timeoutMs)
  const init: RequestInit = { method, headers: {}, signal: guard.signal }
  if (options.body !== undefined) {
    if (options.body instanceof FormData) {
      init.body = options.body
    } else {
      init.headers = { 'Content-Type': 'application/json' }
      init.body = JSON.stringify(options.body)
    }
  }

  let envelope: ApiEnvelope
  try {
    const response = await fetch(url, init)
    envelope = await parseBody(response)
  } catch (err) {
    if (err instanceof ApiError) {
      throw err
    }
    if (guard.timedOut()) {
      throw new ApiError(timeoutMessage(timeoutMs), -1, '', true, null, true)
    }
    if (isAbortError(err)) {
      throw err
    }
    throw new ApiError('无法连接后端服务，请确认 AITP 已启动', -1, '', true)
  } finally {
    guard.done()
  }

  if (envelope.code !== ErrorCode.SUCCESS) {
    if (envelope.code === ErrorCode.AUTH_REQUIRED) {
      handleAuthRequired()
    }
    throw new ApiError(
      explainCode(envelope.code, envelope.message || '请求失败'),
      envelope.code,
      envelope.traceId,
      false,
      envelope.data,
    )
  }
  return envelope.data as T
}

/** GET 便捷方法。 */
export function get<T = unknown>(path: string, query?: RequestOptions['query']): Promise<T> {
  return request<T>(path, { method: 'GET', query })
}

/** POST 便捷方法。 */
export function post<T = unknown>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body })
}

/** 长耗时 POST（AI 生成、批量执行、外部探测），默认 10 分钟超时。 */
export function postLong<T = unknown>(path: string, body?: unknown, timeoutMs = LONG_TIMEOUT_MS): Promise<T> {
  return request<T>(path, { method: 'POST', body, timeoutMs })
}

/** PUT 便捷方法。 */
export function put<T = unknown>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'PUT', body })
}

/** DELETE 便捷方法。 */
export function del<T = unknown>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'DELETE', body })
}

export interface BlobResult {
  blob: Blob
  /** 服务端 Content-Disposition 里的文件名（已解码），取不到时为空串。 */
  filename: string
  /** 服务端标记的导出行数（X-AITP-Export-Count），没有时为空。 */
  count: number | null
  /** 服务端标记的中文字体降级（X-AITP-Font-Warning），PDF 导出用。 */
  fontWarning: boolean
}

/** 从 Content-Disposition 里取文件名；后端用 filename*=UTF-8'' 编码中文。 */
function filenameOf(disposition: string): string {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1])
    } catch {
      return encoded[1]
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition)
  return plain ? plain[1] : ''
}

/** 从二进制响应里取出文件名与服务端标记。 */
async function blobResultOf(response: Response): Promise<BlobResult> {
  const rawCount = response.headers.get('X-AITP-Export-Count')
  const count = rawCount && !Number.isNaN(Number(rawCount)) ? Number(rawCount) : null
  return {
    blob: await response.blob(),
    filename: filenameOf(response.headers.get('Content-Disposition') ?? ''),
    count,
    fontWarning: response.headers.get('X-AITP-Font-Warning') === '1',
  }
}

/**
 * POST 下载二进制文件（Excel 等）。
 * 成功时返回 Blob；失败时后端仍返回 JSON 包封，这里按业务错误抛出。
 */
export async function postBlob(
  path: string,
  body: unknown,
  timeoutMs = DOWNLOAD_TIMEOUT_MS,
): Promise<BlobResult> {
  const guard = withTimeout(undefined, timeoutMs)
  let response: Response
  let envelope: ApiEnvelope | null = null
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
      signal: guard.signal,
    })
    if ((response.headers.get('Content-Type') ?? '').includes('application/json')) {
      envelope = await parseBody(response)
    }
  } catch (err) {
    if (err instanceof ApiError) {
      throw err
    }
    if (guard.timedOut()) {
      throw new ApiError(timeoutMessage(timeoutMs), -1, '', true, null, true)
    }
    if (isAbortError(err)) {
      throw err
    }
    throw new ApiError('无法连接后端服务，请确认 AITP 已启动', -1, '', true)
  } finally {
    guard.done()
  }

  if (envelope) {
    if (envelope.code === ErrorCode.AUTH_REQUIRED) {
      handleAuthRequired()
    }
    throw new ApiError(
      explainCode(envelope.code, envelope.message || '导出失败'),
      envelope.code,
      envelope.traceId,
      false,
      envelope.data,
    )
  }
  if (!response.ok) {
    throw new ApiError('导出失败，请稍后重试', -1, '', true)
  }
  return blobResultOf(response)
}

/** GET 下载二进制文件（报告导出等），与 postBlob 同样回传服务端标记。 */
export async function getBlob(
  path: string,
  query?: RequestOptions['query'],
  timeoutMs = DOWNLOAD_TIMEOUT_MS,
): Promise<BlobResult> {
  const params = new URLSearchParams()
  Object.entries(query ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.append(key, String(value))
    }
  })
  const qs = params.toString()
  const guard = withTimeout(undefined, timeoutMs)
  let response: Response
  let envelope: ApiEnvelope | null = null
  try {
    response = await fetch(`${API_BASE}${path}${qs ? `?${qs}` : ''}`, {
      method: 'GET',
      signal: guard.signal,
    })
    if ((response.headers.get('Content-Type') ?? '').includes('application/json')) {
      envelope = await parseBody(response)
    }
  } catch (err) {
    if (err instanceof ApiError) {
      throw err
    }
    if (guard.timedOut()) {
      throw new ApiError(timeoutMessage(timeoutMs), -1, '', true, null, true)
    }
    if (isAbortError(err)) {
      throw err
    }
    throw new ApiError('无法连接后端服务，请确认 AITP 已启动', -1, '', true)
  } finally {
    guard.done()
  }

  if (envelope) {
    if (envelope.code === ErrorCode.AUTH_REQUIRED) {
      handleAuthRequired()
    }
    throw new ApiError(
      explainCode(envelope.code, envelope.message || '导出失败'),
      envelope.code,
      envelope.traceId,
      false,
      envelope.data,
    )
  }
  if (!response.ok) {
    throw new ApiError('导出失败，请稍后重试', -1, '', true)
  }
  return blobResultOf(response)
}
