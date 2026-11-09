<template>
  <div class="module-tree">
    <VirtualScroller
      :items="rows"
      :item-height="ROW_HEIGHT"
      :max-height="maxHeight"
      :loading="loading"
      :key-of="rowKey"
    >
      <template #empty>
        <span class="muted">{{ emptyText }}</span>
      </template>
      <template #default="{ item }">
        <div
          class="module-tree__row"
          :class="{ 'is-active': item.kind === 'node' && item.active }"
          :style="{ paddingLeft: `${4 + item.indent * INDENT}px` }"
        >
          <template v-if="item.kind === 'node'">
            <button
              v-if="hasChildren(item)"
              type="button"
              class="module-tree__caret"
              :title="isCollapsed(item.nodeKey) ? '展开下级' : '收起下级'"
              @click.stop="toggleBranch(item.nodeKey)"
            >
              <el-icon>
                <ArrowRight v-if="isCollapsed(item.nodeKey)" />
                <ArrowDown v-else />
              </el-icon>
            </button>
            <span v-else class="module-tree__caret module-tree__caret--void" />
            <el-tag :type="typeTag(nodeOf(item).nodeType)" size="small" effect="plain">
              {{ typeLabel(nodeOf(item).nodeType) }}
            </el-tag>
            <span class="module-tree__name" :title="item.title || nodeOf(item).path" @click="onSelectNode(item)">
              {{ item.label }}
            </span>
            <span class="module-tree__refs">
              <button
                v-if="drillable && item.count > 0"
                type="button"
                class="module-tree__drill"
                :class="{ 'is-open': item.expanded }"
                :title="item.expanded ? '收起该节点的用例' : `展开该节点挂的 ${item.count} 条用例（分页续取）`"
                @click.stop="toggleCases(item)"
              >
                用例 {{ item.count }}
              </button>
              <span v-else-if="nodeOf(item).references">用例 {{ item.count }}</span>
              <span v-if="nodeOf(item).references?.bugs !== undefined">
                · 缺陷 {{ nodeOf(item).references?.bugs ?? 0 }}
              </span>
            </span>
          </template>

          <template v-else-if="item.kind === 'case' && item.caseItem">
            <span class="module-tree__case-no">{{ item.caseItem.caseNo }}</span>
            <span class="module-tree__case-title" :title="item.title" @click="emit('open-case', item.caseItem)">
              {{ item.label }}
            </span>
          </template>

          <template v-else-if="item.kind === 'more'">
            <el-button
              link
              type="primary"
              size="small"
              :loading="item.loading && !isBulk(item.nodeKey)"
              @click.stop="onMore(item)"
            >
              还有 {{ item.remaining }} 条，点击加载
            </el-button>
            <el-button link size="small" @click.stop="onLoadAll(item)">加载全部</el-button>
            <span class="module-tree__loaded muted">已取 {{ item.loaded }}</span>
          </template>

          <template v-else-if="item.kind === 'error'">
            <el-button link type="danger" size="small" @click.stop="onMore(item)">
              {{ item.message || '用例加载失败' }}，点击重试
            </el-button>
          </template>
        </div>
      </template>
    </VirtualScroller>
    <p v-if="drillable" class="module-tree__hint muted">
      点「用例 N」展开该节点挂的用例，一次取 100 条，剩下的点「还有 N 条」或「加载全部」续取
    </p>
  </div>
</template>

<script setup lang="ts">
/**
 * 功能点 / 模块树（GAP-25 前端）。
 *
 * 原来用 el-tree 整树渲染：节点一多、再把节点挂的用例摊开就是几千上万个 DOM。
 * 这里把「节点 + 下级 + 节点用例 + 续取入口」摊平成定高行交给 VirtualScroller，
 * DOM 只保留视口内的几十行；树节点用例被接口限量时，点「还有 N 条」按 pageNum 继续取，
 * 用户仍然能拿到该节点的全部用例。
 */
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowRight } from '@element-plus/icons-vue'
import VirtualScroller from '@/components/VirtualScroller.vue'
import { useTreeCaseDrilldown, type DrillNode, type DrillRow } from '@/composables/useTreeCaseDrilldown'
import type { PagePayload } from '@/api/request'
import type { ModuleNode, TestCase } from '@/api/types'

/** 面板对外方法：外层换项目 / 改用例后要清缓存。 */
export interface ModuleTreeExpose {
  resetDrilldown(): void
  invalidateCases(): void
  isBulk(nodeKey: string): boolean
}

const props = withDefaults(
  defineProps<{
    nodes: ModuleNode[]
    loading?: boolean
    autoExpand?: boolean
    /** 面板高度上限（像素）。 */
    maxHeight?: number
    /** 传入后节点可以就地展开自己的用例并按页续取；不传保持纯结构树。 */
    loadCases?: (node: ModuleNode, pageNum: number) => Promise<PagePayload<TestCase>>
    /** 用例总量口径；默认取接口回传的本级引用数，左侧筛选面板会换成「本级 + 下级」。 */
    countOf?: (node: ModuleNode) => number
    /** 展开用例 / 联动列表时用的 moduleId 串；默认节点自身 id。 */
    queryOf?: (node: ModuleNode) => string
    /** 外部驱动的选中态（一般是列表当前的 moduleId 筛选）；不传则按面板内最后一次点击高亮。 */
    activeQuery?: string
    /** 外部关键字：与 el-tree 的 filter-node-method 同语义，命中项保留祖先。 */
    keyword?: string
    /** 空态文案。 */
    emptyText?: string
  }>(),
  {
    loading: false,
    autoExpand: true,
    maxHeight: 420,
    loadCases: undefined,
    countOf: undefined,
    queryOf: undefined,
    activeQuery: undefined,
    keyword: '',
    emptyText: '暂无数据',
  },
)

const emit = defineEmits<{
  (event: 'select', node: ModuleNode): void
  (event: 'open-case', item: TestCase): void
}>()

const ROW_HEIGHT = 28
const INDENT = 14

const drillable = computed(() => Boolean(props.loadCases))
const activeKey = ref<string>('')
/** 收起的下级分支；autoExpand 时默认可见。 */
const collapsed = ref<Set<string>>(new Set())
const collapseSeeded = ref(false)

/** autoExpand=false 的面板：首次拿到树时把非顶级分支收成折叠态。 */
function seedCollapse(): void {
  if (collapseSeeded.value || !props.nodes?.length) {
    return
  }
  collapseSeeded.value = true
  if (props.autoExpand) {
    return
  }
  const walk = (list: ModuleNode[], depth: number): void => {
    list.forEach((node) => {
      const children = node.children ?? []
      if (children.length) {
        if (depth > 0) {
          collapsed.value.add(String(node.id))
        }
        walk(children, depth + 1)
      }
    })
  }
  walk(props.nodes, 0)
}

watch(() => props.nodes, seedCollapse, { immediate: true })

const flatNodes = computed(() => {
  const out: Array<{ node: ModuleNode; depth: number; hasChildren: boolean }> = []
  const walk = (list: ModuleNode[], depth: number): void => {
    list.forEach((node) => {
      const children = node.children ?? []
      out.push({ node, depth, hasChildren: children.length > 0 })
      if (children.length && !collapsed.value.has(String(node.id))) {
        walk(children, depth + 1)
      }
    })
  }
  walk(props.nodes ?? [], 0)
  return out
})

/** 摊平后的可钻取节点：count / query 口径可由外层覆写，默认用接口引用数与节点 id。 */
const drillNodes = computed<DrillNode[]>(() =>
  flatNodes.value.map(({ node, depth }) => {
    const count = props.countOf ? props.countOf(node) : Number(node.references?.cases ?? 0)
    return {
      key: String(node.id),
      depth,
      label: node.name,
      title: node.path ?? node.name,
      path: node.path ?? node.name,
      count,
      expandable: count > 0,
      query: props.queryOf ? props.queryOf(node) : String(node.id),
      source: node,
    }
  }),
)

const childFlags = computed(() => {
  const map = new Map<string, boolean>()
  flatNodes.value.forEach((item) => map.set(String(item.node.id), item.hasChildren))
  return map
})

const drilldown = useTreeCaseDrilldown({
  nodes: () => drillNodes.value,
  fetch: (node, pageNum) => {
    const source = node.source as ModuleNode
    if (!props.loadCases) {
      return Promise.reject(new Error('该面板未开启用例钻取'))
    }
    return props.loadCases(source, pageNum)
  },
  isActive: (node) =>
    props.activeQuery === undefined ? node.key === activeKey.value : node.query === props.activeQuery,
})

/** 关键字由外层输入框驱动，过滤语义与 el-tree 的 filter-node-method 一致。 */
watch(
  () => props.keyword,
  (value) => {
    drilldown.keyword.value = value ?? ''
  },
  { immediate: true },
)

const rows = computed<DrillRow[]>(() => drilldown.rows.value)

function nodeOf(item: DrillRow): ModuleNode {
  return (item.source as ModuleNode) ?? ({ nodeType: 'module', references: null } as unknown as ModuleNode)
}

function hasChildren(item: DrillRow): boolean {
  return childFlags.value.get(item.nodeKey) === true
}

function isCollapsed(nodeKey: string): boolean {
  return collapsed.value.has(nodeKey)
}

function toggleBranch(nodeKey: string): void {
  if (collapsed.value.has(nodeKey)) {
    collapsed.value.delete(nodeKey)
  } else {
    collapsed.value.add(nodeKey)
  }
}

function toggleCases(item: DrillRow): void {
  drilldown.toggle(item.nodeKey)
}

function onMore(item: DrillRow): void {
  drilldown.loadMore(item.nodeKey)
}

function onLoadAll(item: DrillRow): void {
  void drilldown.loadAll(item.nodeKey)
}

function isBulk(nodeKey: string): boolean {
  return drilldown.isBulk(nodeKey)
}

function onSelectNode(item: DrillRow): void {
  activeKey.value = item.nodeKey
  emit('select', nodeOf(item))
}

function rowKey(item: DrillRow): string {
  return item.key
}

function typeLabel(nodeType: string): string {
  if (nodeType === 'module') {
    return '模块'
  }
  if (nodeType === 'submodule') {
    return '子模块'
  }
  return '功能'
}

function typeTag(nodeType: string): 'primary' | 'success' | 'warning' {
  if (nodeType === 'module') {
    return 'primary'
  }
  if (nodeType === 'submodule') {
    return 'success'
  }
  return 'warning'
}

/** 换项目 / 重拉树之后清掉钻取缓存与收起状态，避免旧项目的用例挂在同名节点下。 */
function resetDrilldown(): void {
  drilldown.reset()
  collapsed.value = new Set()
  collapseSeeded.value = false
  activeKey.value = ''
  seedCollapse()
}

/** 用例被增删改后：缓存全部作废，展开着的节点重取第一页，剩下的靠「还有 N 条」续。 */
function invalidateCases(): void {
  drillNodes.value.forEach((node) => drilldown.invalidate(node.key))
}

defineExpose({ resetDrilldown, invalidateCases, isBulk })
</script>

<style scoped>
.module-tree__row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding-right: 6px;
  font-size: 13px;
  line-height: 20px;
}

.module-tree__row.is-active .module-tree__name {
  color: var(--el-color-primary);
  font-weight: 600;
}

.module-tree__caret {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex: none;
  padding: 0;
  color: var(--el-text-color-secondary);
  background: none;
  border: none;
  cursor: pointer;
}

.module-tree__caret--void {
  cursor: default;
}

.module-tree__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.module-tree__refs {
  flex: none;
  margin-left: auto;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.module-tree__drill {
  padding: 0 2px;
  color: inherit;
  font-size: 12px;
  background: none;
  border: none;
  border-bottom: 1px dashed var(--el-border-color);
  cursor: pointer;
}

.module-tree__drill.is-open {
  color: var(--el-color-primary);
  border-bottom-color: var(--el-color-primary);
}

.module-tree__case-no {
  flex: none;
  color: var(--aitp-text-weak);
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
}

.module-tree__case-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.module-tree__case-title:hover {
  color: var(--el-color-primary);
}

.module-tree__loaded {
  font-size: 12px;
}

.module-tree__hint {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.5;
}
</style>
