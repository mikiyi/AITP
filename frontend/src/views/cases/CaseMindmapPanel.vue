<template>
  <div class="mindmap-wrap">
    <el-empty
      v-if="!tree.length"
      description="当前范围没有可展示的功能点或用例"
      class="mindmap-empty"
    />
    <template v-else>
      <div class="mindmap-bar">
        <span class="muted">
          以功能点树为骨架、用例为叶子；点击用例节点跳到详情（只读视图）
        </span>
        <span v-if="truncated" class="mindmap-warn">
          用例超过 {{ caseLimit }} 条，脑图只展示前 {{ caseLimit }} 条
        </span>
      </div>
      <div ref="container" class="mindmap-canvas" data-testid="case-mindmap" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import type { EChartsOption } from 'echarts'
import { useEchart } from '@/composables/useEchart'
import type { CaseTreeNode, TestCase } from '@/api/types'

interface MindDatum {
  name: string
  value?: number
  caseNo?: string
  collapsed?: boolean
  children?: MindDatum[]
}

const props = defineProps<{
  nodes: CaseTreeNode[]
  cases: TestCase[]
  truncated?: boolean
  caseLimit?: number
}>()
const emit = defineEmits<{ (event: 'open', caseId: number): void }>()

const container = ref<HTMLElement | null>(null)
const option = shallowRef<EChartsOption | null>(null)
const bound = ref(false)
const { chart, render } = useEchart(container, option)

const caseLimit = computed(() => props.caseLimit ?? 300)

/** 用例按功能点分组；未关联功能点的挂在「未关联功能点」下。 */
function buildTree(): MindDatum[] {
  const byModule = new Map<string, MindDatum[]>()
  for (const item of props.cases) {
    const key = item.moduleId ? String(item.moduleId) : 'none'
    const list = byModule.get(key) ?? []
    list.push({
      name: `${item.caseNo} ${item.title}`,
      value: Number(item.id),
      caseNo: item.caseNo,
    })
    byModule.set(key, list)
  }

  const convert = (node: CaseTreeNode): MindDatum => ({
    name: node.name,
    children: [
      ...(node.children ?? []).map((child) => convert(child as CaseTreeNode)),
      ...(byModule.get(String(node.id)) ?? []),
    ],
  })

  const roots: MindDatum[] = props.nodes
    .filter((node) => Number(node.id) > 0)
    .map((node) => convert(node))
  const orphan = byModule.get('none') ?? []
  if (orphan.length) {
    roots.push({ name: '未关联功能点', children: orphan })
  }
  return roots
}

const tree = computed<MindDatum[]>(() => buildTree())

watch([tree, container], () => {
  if (!container.value || !tree.value.length) {
    option.value = null
    return
  }
  const total = countNodes(tree.value)
  option.value = {
    tooltip: {
      trigger: 'item',
      formatter: (params: unknown) => {
        const data = (params as { data?: MindDatum }).data
        if (!data) return ''
        return data.caseNo ? `用例 ${data.caseNo}<br/>${data.name}` : data.name
      },
    },
    series: [
      {
        type: 'tree',
        data: [{ name: '用例脑图', children: tree.value }],
        layout: 'orthogonal',
        orient: 'LR',
        symbol: 'circle',
        symbolSize: 8,
        // 超阈值自动折叠，按需展开，避免一次性铺满画布
        initialTreeDepth: total > 120 ? 2 : 3,
        expandAndCollapse: true,
        roam: true,
        label: {
          position: 'right',
          verticalAlign: 'middle',
          align: 'left',
          fontSize: 12,
          formatter: (params: unknown) => {
            const data = (params as { data?: MindDatum }).data
            if (!data) return ''
            const text = data.name ?? ''
            return text.length > 26 ? `${text.slice(0, 26)}…` : text
          },
        },
        leaves: { label: { position: 'right', align: 'left' } },
        lineStyle: { width: 1, curveness: 0.5 },
        emphasis: { focus: 'descendant' },
        animationDuration: 220,
      },
    ],
  }
}, { immediate: true, deep: false })

function countNodes(list: MindDatum[]): number {
  let total = 0
  for (const item of list) {
    total += 1
    if (item.children?.length) total += countNodes(item.children)
  }
  return total
}

onMounted(() => {
  render()
  if (chart.value && !bound.value) {
    chart.value.on('click', (params: unknown) => {
      const data = (params as { data?: MindDatum }).data
      const caseId = Number(data?.value ?? 0)
      if (Number.isInteger(caseId) && caseId > 0) {
        emit('open', caseId)
      }
    })
    bound.value = true
  }
})

defineExpose({ render })
</script>

<style scoped>
.mindmap-wrap {
  width: 100%;
}
.mindmap-empty {
  padding: 40px 0;
}
.mindmap-bar {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
  margin-bottom: 8px;
}
.mindmap-warn {
  color: var(--el-color-warning);
}
.mindmap-canvas {
  width: 100%;
  height: 560px;
  min-height: 400px;
}
</style>
