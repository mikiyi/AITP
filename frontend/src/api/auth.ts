/**
 * 本地访问口令接口与全局登录态。
 *
 * 登录态放在服务端 HttpOnly Cookie 里，前端不持有令牌：
 * 下载链接（window.open / a 标签）也能自动带上登录态，不用改导出逻辑。
 */

import { reactive } from 'vue'

const AUTH_BASE = '/api/v1/auth'
const SUCCESS = 200000

export const ErrorCode = {
  AUTH_REQUIRED: 150101,
  AUTH_FAILED: 150102,
  AUTH_LOCKED: 150103,
} as const

export interface AuthStatusData {
  enabled: boolean
  authenticated: boolean
  passwordUpdatedAt: string
  sessionTtlSeconds: number
  lockedSeconds: number
}

export interface LoginResult {
  authenticated: boolean
  expiresAt: string
  sessionTtlSeconds: number
}

export interface PasswordResult {
  enabled: boolean
  authenticated: boolean
  passwordUpdatedAt?: string
  expiresAt?: string
}

/** 全局登录态：enabled=是否启用口令，authenticated=当前浏览器是否已登录。 */
export const authState = reactive({
  enabled: false,
  authenticated: true,
  passwordUpdatedAt: '',
  sessionTtlSeconds: 12 * 3600,
  lockedSeconds: 0,
  /** 是否连得上后端；连不上时登录页给出重试入口而不是空转。 */
  online: true,
})

export class AuthError extends Error {
  readonly code: number

  constructor(message: string, code = -1) {
    super(message)
    this.name = 'AuthError'
    this.code = code
  }
}

interface Envelope<T> {
  code: number
  message: string
  data: T
}

async function call<T>(
  path: string,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  body?: unknown,
): Promise<T> {
  const init: RequestInit = { method, credentials: 'same-origin' }
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(body)
  }
  let response: Response
  try {
    response = await fetch(`${AUTH_BASE}${path}`, init)
  } catch {
    authState.online = false
    throw new AuthError('无法连接后端服务，请确认 AITP 已启动')
  }
  authState.online = true
  let envelope: Envelope<T>
  try {
    envelope = (await response.json()) as Envelope<T>
  } catch {
    throw new AuthError('后端返回了非 JSON 内容')
  }
  if (envelope.code !== SUCCESS) {
    throw new AuthError(envelope.message || '请求失败', envelope.code)
  }
  return envelope.data
}

function applyStatus(data: AuthStatusData): void {
  authState.enabled = data.enabled
  authState.authenticated = data.authenticated
  authState.passwordUpdatedAt = data.passwordUpdatedAt
  authState.sessionTtlSeconds = data.sessionTtlSeconds
  authState.lockedSeconds = data.lockedSeconds
}

export async function fetchAuthStatus(): Promise<void> {
  const data = await call<AuthStatusData>('/status', 'GET')
  applyStatus(data)
}

export async function login(password: string): Promise<LoginResult> {
  const result = await call<LoginResult>('/login', 'POST', { password })
  authState.enabled = true
  authState.authenticated = result.authenticated
  authState.lockedSeconds = 0
  return result
}

export async function logout(): Promise<void> {
  await call<{ loggedOut: boolean }>('/logout', 'POST')
  authState.authenticated = false
}

export async function savePassword(payload: {
  currentPassword?: string
  password: string
  confirmPassword: string
}): Promise<PasswordResult> {
  const result = await call<PasswordResult>('/password', 'PUT', payload)
  authState.enabled = true
  authState.authenticated = true
  authState.passwordUpdatedAt = result.passwordUpdatedAt ?? authState.passwordUpdatedAt
  return result
}

export async function removePassword(password: string): Promise<void> {
  await call<{ enabled: boolean }>('/password', 'DELETE', { password })
  authState.enabled = false
  authState.authenticated = true
  authState.passwordUpdatedAt = ''
}

/** 任意请求收到「需要口令」时调用：把界面切回登录卡片。 */
export function handleAuthRequired(): void {
  if (authState.enabled) {
    authState.authenticated = false
  }
}
