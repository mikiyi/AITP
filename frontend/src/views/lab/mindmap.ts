/**
 * 脑图数据转换。
 *
 * 测试点脑图（testpoint-generation）的输出是 {name, children[]}，
 * 其中叶子节点带 priority / desc；xmind 解析结果已经是 {title, children[]}。
 */
import type { MindNode } from '@/api/types'

interface RawNode {
  name?: string
  title?: string
  children?: RawNode[]
  priority?: string
  desc?: string
}

function toMindNode(node: RawNode): MindNode {
  const title = node.name ?? node.title ?? ''
  const note = node.desc ? node.desc : undefined
  const priority = node.priority ? String(node.priority) : undefined
  return {
    title,
    note,
    priority,
    children: (node.children ?? []).map((child) => toMindNode(child)),
  }
}

/** 测试点 JSON 树 → 脑图节点。 */
export function testpointToMindNodes(parsed: unknown): MindNode[] {
  if (!parsed || typeof parsed !== 'object') {
    return []
  }
  const root = parsed as RawNode
  const children = (root.children ?? []).map((child) => toMindNode(child))
  if (!children.length) {
    return [toMindNode(root)]
  }
  return [{ title: root.name ?? root.title ?? '测试点', children }]
}

/** 取节点的子树文本（含自身），用于「按节点生成用例」。 */
export function subtreeText(node: MindNode): string {
  const lines: string[] = []
  const walk = (item: MindNode, depth: number): void => {
    lines.push('  '.repeat(depth) + item.title)
    for (const child of item.children ?? []) {
      walk(child, depth + 1)
    }
  }
  walk(node, 0)
  return lines.join('\n')
}

/** 按标题路径在树里定位节点。 */
export function findNodeByPath(nodes: MindNode[], path: string[]): MindNode | null {
  if (!path.length) {
    return null
  }
  let current: MindNode | undefined
  let rest = path
  for (const level of nodes) {
    if (level.title === rest[0]) {
      current = level
      rest = rest.slice(1)
      break
    }
  }
  if (!current) {
    return null
  }
  if (!rest.length) {
    return current
  }
  return findNodeByPath(current.children ?? [], rest)
}
