import { defineStore } from 'pinia'
import { ElMessageBox } from 'element-plus'
import { settingsApi } from '@/api'

/**
 * 外部大模型调用前的人工确认。
 *
 * 单用户本地工具，但一旦用云端模型，用户输入（可能含 PII、内部 SQL、生产日志）
 * 就会离开本机。每次会触发外部调用的操作先弹一次确认：
 * - 「先检查数据是否脱敏」：取消本次任务，不调用模型；
 * - 「已确认数据脱敏」：继续调用。
 * 本地自托管模型（Ollama 等）不弹，避免无意义的打扰。
 */
export const useAiGuardStore = defineStore('aiGuard', () => {
  async function ensureExternalConfirmed(): Promise<boolean> {
    let isLocal = true
    let configured = true
    try {
      const status = await settingsApi.status()
      isLocal = Boolean(status.isLocal)
      configured = Boolean(status.configured)
    } catch {
      // 状态取不到时不阻断主流程，后续调用会自行报错
      return true
    }
    if (!configured || isLocal) {
      return true
    }
    try {
      await ElMessageBox.confirm(
        '准备调用外部大模型，请确保数据已经完成脱敏，避免数据泄密',
        '数据外发确认',
        {
          type: 'warning',
          distinguishCancelAndClose: true,
          confirmButtonText: '已确认数据脱敏',
          cancelButtonText: '先检查数据是否脱敏',
          confirmButtonClass: 'ai-egress-confirm',
          cancelButtonClass: 'ai-egress-cancel',
          customClass: 'ai-egress-box',
          closeOnClickModal: false,
        },
      )
      return true
    } catch {
      return false
    }
  }

  return { ensureExternalConfirmed }
})
