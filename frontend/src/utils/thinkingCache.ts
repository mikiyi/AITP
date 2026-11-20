/**
 * 问答助手思考过程瞬态缓存（P0）。
 *
 * 语义：不跨设备、不保证跨会话。默认放 sessionStorage（本标签页有效、关标签即失），
 * 按 message 键控（sessionId|answeredAt|model），LRU 上限防超配额；
 * 敏感场景（回答走过脱敏）仅放内存，不落 sessionStorage。
 * 思考默认不进数据库；仅用户 opt-in「保存思考」才随消息落库（后端 reasoning 列）。
 */

export interface ThinkingRecord {
  text: string
  seconds: number
  chars: number
  done: boolean
  interrupted: boolean
  saved: boolean
  ts: number
}

const PREFIX = 'aitp.assistant.thinking.'
const MAX_ENTRIES = 40
const MAX_BYTES = 1_500_000

/** 敏感场景仅内存，不写 sessionStorage */
const memory = new Map<string, ThinkingRecord>()
let sensitiveMode = false

export function setSensitiveMode(on: boolean): void {
  sensitiveMode = on
}

export function thinkingKey(sessionId: number | null, answeredAt: string, model: string): string {
  return `${sessionId ?? 0}|${answeredAt}|${model}`
}

function readSession(key: string): ThinkingRecord | null {
  try {
    const raw = sessionStorage.getItem(PREFIX + key)
    return raw ? (JSON.parse(raw) as ThinkingRecord) : null
  } catch {
    return null
  }
}

function trimSession(): void {
  try {
    const keys = Object.keys(sessionStorage).filter((k) => k.startsWith(PREFIX))
    const entries = keys
      .map((k) => ({ k, rec: readSession(k.slice(PREFIX.length)) }))
      .filter((e): e is { k: string; rec: ThinkingRecord } => Boolean(e.rec))
      .sort((a, b) => a.rec.ts - b.rec.ts)
    let bytes = entries.reduce((s, e) => s + e.k.length + JSON.stringify(e.rec).length, 0)
    while (entries.length > MAX_ENTRIES || bytes > MAX_BYTES) {
      const oldest = entries.shift()
      if (!oldest) break
      sessionStorage.removeItem(oldest.k)
      bytes -= oldest.k.length + JSON.stringify(oldest.rec).length
    }
  } catch {
    /* 配额/隐私模式异常时静默降级为不缓存 */
  }
}

export function setThinking(key: string, rec: ThinkingRecord): void {
  if (sensitiveMode) {
    memory.set(key, rec)
    if (memory.size > MAX_ENTRIES) {
      const oldest = [...memory.entries()].sort((a, b) => a[1].ts - b[1].ts)[0]
      if (oldest) memory.delete(oldest[0])
    }
    return
  }
  try {
    sessionStorage.setItem(PREFIX + key, JSON.stringify(rec))
    trimSession()
  } catch {
    /* 超配额：放弃本条缓存，不影响问答主流程 */
  }
}

export function clearThinkingCache(): void {
  memory.clear()
  try {
    Object.keys(sessionStorage)
      .filter((k) => k.startsWith(PREFIX))
      .forEach((k) => sessionStorage.removeItem(k))
  } catch {
    /* ignore */
  }
}

/** 思考展示脱敏（P1）：复用输入脱敏同类敏感模式，避免回显口令/证件/联系方式 */
export function maskThinking(text: string): string {
  return text
    .replace(/(1[3-9]\d)\d{4}(\d{4})/g, '$1****$2')
    .replace(/([\w.+-]+)@([\w-]+\.[\w.]+)/g, '***@***')
    .replace(/\b(\d{6})\d{8}(\d{4})\b/g, '$1********$2')
    .replace(/\b(\d{4})\d{10,13}(\d{4})\b/g, '$1********$2')
    .replace(/((?:password|passwd|pwd|token|secret|api[_-]?key|口令|密码|凭证)\s*[:=]\s*)(\S+)/gi, '$1******')
}