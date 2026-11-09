/** CI 令牌访问边界 API（GAP-24）。
 *  单独导出，避免污染 index.ts / types.ts；类型就近定义。
 */
import { del, get, post } from './request'

export type CiTokenStatus = 'active' | 'revoked' | 'expired' | 'inactive'

export interface CiSettings {
  enabled: boolean
  name: string
  tokenPrefix: string
  projectId: number | null
  lastUsedAt: string | null
  createdAt: string | null
  expiresAt: string | null
  revokedAt: string | null
  expired: boolean
  status: CiTokenStatus
  baseUrl: string
  endpoint: string
  events: { value: string; label: string }[]
  token?: string
}

export interface CiTokenItem {
  id: number
  name: string | null
  tokenPrefix: string | null
  projectId: number | null
  enabled: boolean
  status: CiTokenStatus
  statusLabel: string
  expiresAt: string | null
  revokedAt: string | null
  expired: boolean
  revoked: boolean
  lastUsedAt: string | null
  createdAt: string | null
  updatedAt: string | null
}

export interface IssueCiTokenPayload {
  name?: string
  projectId?: number | null
  /** 有效期天数（1~3650）；不传且 expiresAt 也不传即不过期。 */
  expiresInDays?: number | null
  /** 直接指定 ISO8601 到期时间，优先于 expiresInDays。 */
  expiresAt?: string | null
}

/** 读取当前 CI 设置（含到期/吊销状态）。 */
export function getCiSettings(): Promise<CiSettings> {
  return get<CiSettings>('/settings/ci')
}

/** 近期令牌列表，展示有效期与吊销状态。 */
export function listCiTokens(): Promise<{ list: CiTokenItem[] }> {
  return get<{ list: CiTokenItem[] }>('/settings/ci/tokens')
}

/** 生成令牌；可选有效期；响应里回传一次明文。 */
export function issueCiToken(payload: IssueCiTokenPayload): Promise<CiSettings> {
  return post<CiSettings>('/settings/ci/token', payload)
}

/** 一键吊销全部启用令牌。 */
export function revokeAllCiTokens(): Promise<{ revoked: number; enabled: boolean }> {
  return del<{ revoked: number; enabled: boolean }>('/settings/ci/token')
}

/** 按 id 吊销单个令牌，立即失效。 */
export function revokeCiToken(id: number): Promise<{ id: number; revoked: number; enabled: boolean }> {
  return post<{ id: number; revoked: number; enabled: boolean }>(`/settings/ci/token/${id}/revoke`)
}
