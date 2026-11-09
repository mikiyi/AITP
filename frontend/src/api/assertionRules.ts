/** 断言库 API（GAP-34）。
 *  单独导出，避免污染 index.ts / types.ts；类型就近定义。
 *  后端路由挂在用例资源下：/test-cases/assertion-rules。
 */
import { del, get, post, put } from './request'
import type { PagePayload } from './request'

/** 断言库条目：reference 就是用例「断言规则」里能直接写的引用串（lib:AR-0001）。 */
export interface AssertionRule {
  id: number
  projectId: number
  moduleId: number | null
  ruleNo: string
  name: string
  rule: string
  description: string
  enabled: boolean
  reference: string
  createdAt: string | null
  updatedAt: string | null
}

export interface AssertionRuleForm {
  projectId: number
  moduleId?: number | null
  name: string
  rule: string
  description?: string
  enabled?: boolean
}

/** 可见范围：不传 moduleId 只给项目级；传了则含该功能点的祖先链。 */
export function listAssertionRules(
  query: Record<string, unknown>,
): Promise<PagePayload<AssertionRule>> {
  // 列表组件统一吐 Record<string, unknown>，与 api/index.ts 里的列表口径一致地转给请求层
  return get<PagePayload<AssertionRule>>('/test-cases/assertion-rules', query as never)
}

export function createAssertionRule(body: AssertionRuleForm): Promise<AssertionRule> {
  return post<AssertionRule>('/test-cases/assertion-rules', body)
}

export function updateAssertionRule(
  id: number,
  body: Partial<AssertionRuleForm>,
): Promise<AssertionRule> {
  return put<AssertionRule>(`/test-cases/assertion-rules/${id}`, body)
}

export function deleteAssertionRule(id: number): Promise<{ id: number; deleted: boolean }> {
  return del<{ id: number; deleted: boolean }>(`/test-cases/assertion-rules/${id}`)
}
