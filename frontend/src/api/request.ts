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
} as const

/** 携带业务码与 traceId 的错误对象，便于前端区分提示文案。 */
export class ApiError extends Error {
  readonly code: number
  readonly traceId: string
  readonly isNetwork: boolean
  /** 业务包封的 data 段，引用保护等错误会在这里回传引用清单。 */
  readonly data: unknown

  constructor(message: string, code = -1, traceId = '', isNetwork = false, data: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.traceId = traceId
    this.isNetwork = isNetwork
    this.data = data
  }
}

export const API_BASE = '/api/v1'

/** 业务码 → 用户可读提示。 */
export function explainCode(code: number, fallback: string): string {
  switch (code) {
    case ErrorCode.PARAM_ERROR:
      return '参数不合法，请检查后重试'
    case ErrorCode.RESOURCE_NOT_FOUND:
      return '目标不存在或已被删除'
    case ErrorCode.RESOURCE_REFERENCED:
      return fallback.includes('引用') ? fallback : '该条目被下游引用，无法删除或撤销'
    case ErrorCode.ILLEGAL_OPERATION:
      return '当前状态不允许该操作'
    case ErrorCode.AI_CREDENTIAL_MISSING:
      return '未配置可用的 AI 模型，请到「设置 → AI 配置」添加'
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

  const init: RequestInit = { method, headers: {} }
  if (options.body !== undefined) {
    if (options.body instanceof FormData) {
      init.body = options.body
    } else {
      init.headers = { 'Content-Type': 'application/json' }
      init.body = JSON.stringify(options.body)
    }
  }
  if (options.signal) {
    init.signal = options.signal
  }

  let response: Response
  try {
    response = await fetch(url, init)
  } catch (err) {
    const aborted = err instanceof DOMException && err.name === 'AbortError'
    if (aborted) {
      throw err
    }
    throw new ApiError('无法连接后端服务，请确认 AITP 已启动', -1, '', true)
  }

  const envelope = await parseBody(response)
  if (envelope.code !== ErrorCode.SUCCESS) {
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

/**
 * POST 下载二进制文件（Excel 等）。
 * 成功时返回 Blob；失败时后端仍返回 JSON 包封，这里按业务错误抛出。
 */
export async function postBlob(path: string, body: unknown): Promise<BlobResult> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    })
  } catch {
    throw new ApiError('无法连接后端服务，请确认 AITP 已启动', -1, '', true)
  }

  const contentType = response.headers.get('Content-Type') ?? ''
  if (contentType.includes('application/json')) {
    const envelope = await parseBody(response)
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
  return {
    blob: await response.blob(),
    filename: filenameOf(response.headers.get('Content-Disposition') ?? ''),
  }
}
