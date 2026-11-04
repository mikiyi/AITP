/** AI 能力入口的多行输入框统一追加的换行快捷键提示。 */
const NEWLINE_HINT = '（Enter 换行）'

/**
 * 多行输入框的回车只换行、不会触发执行，但聊天类产品的习惯让用户担心按错，
 * 所以占位文案统一补一句快捷键说明；没有原文案时给一个兜底描述。
 */
export function textareaHint(placeholder?: string): string {
  return `${placeholder?.trim() || '可输入或粘贴多行文本'}${NEWLINE_HINT}`
}
