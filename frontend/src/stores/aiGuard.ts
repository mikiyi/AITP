import { defineStore } from 'pinia'
import { ElMessageBox } from 'element-plus'
import { settingsApi } from '@/api'

interface ExternalConfirmOptions {
  /**
   * 后端给出的「本次是否会把数据送出本机」，口径 = 实际会用的首选模型。
   * 传进来就不再看前端 status；不传时由 status.isLocal 推断。
   */
  externalPossible?: boolean
  /** 「先检查数据是否脱敏」：打开站内检查，返回的 Promise 在用户关闭检查后 resolve。 */
  onInspect?: () => Promise<void>
}

/**
 * 外部大模型调用前的人工确认。
 *
 * 单用户本地工具，但一旦用云端模型，用户输入（可能含 PII、内部 SQL、生产日志）
 * 就会离开本机。每次会触发外部调用的操作先弹一次确认：
 * - 「先检查数据是否脱敏」：打开脱敏检查（能做站内检查时不取消任务，检查完再确认）；
 * - 「已确认数据脱敏」：继续调用。
 * 本地自托管模型（Ollama 等）不弹，避免无意义的打扰。
 */
export const useAiGuardStore = defineStore('aiGuard', () => {
  async function ensureExternalConfirmed(options: ExternalConfirmOptions = {}): Promise<boolean> {
    let external = options.externalPossible
    if (external === undefined) {
      let isLocal = true
      let configured = true
      try {
        const status = await settingsApi.status()
        isLocal = Boolean(status.isLocal)
        configured = Boolean(status.configured)
        // 只看实际会用的首选模型：本地首选不打扰；真降级到云端时后端会自动脱敏，
        // 并由响应里的 egressWarning 事后如实提示这次确实外发。
        external = !isLocal
      } catch {
        // 状态取不到时不阻断主流程，后续调用会自行报错
        return true
      }
      if (!configured) {
        return true
      }
      external = Boolean(external)
    }
    if (!external) {
      return true
    }
    // 检查完再回到确认框：关闭检查窗口不等于放弃任务
    for (;;) {
      try {
        await ElMessageBox.confirm(
          '准备调用外部大模型。云端调用默认会在发送前自动脱敏手机号、身份证、邮箱、API Key 等敏感信息；'
          + '若你已在设置中关闭自动脱敏，请先自行确认数据已经脱敏。',
          '数据外发确认',
          {
            type: 'warning',
            distinguishCancelAndClose: true,
            confirmButtonText: '继续调用',
            cancelButtonText: '先检查数据是否脱敏',
            confirmButtonClass: 'ai-egress-confirm',
            cancelButtonClass: 'ai-egress-cancel',
            customClass: 'ai-egress-box',
            closeOnClickModal: false,
          },
        )
        return true
      } catch (action) {
        if (action === 'cancel' && options.onInspect) {
          await options.onInspect()
          continue
        }
        return false
      }
    }
  }

  return { ensureExternalConfirmed }
})
