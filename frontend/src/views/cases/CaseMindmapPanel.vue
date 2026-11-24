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
import { useAppStore } from '@/stores/app'
import { useThemeStore } from '@/stores/theme'
import type { CaseTreeNode, TestCase } from '@/api/types'

interface MindDatum {
  name: string
  value?: number
  caseNo?: string
  symbol?: string
  symbolSize?: number
  itemStyle?: { color?: string; borderWidth?: number }
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

const appStore = useAppStore()
const themeStore = useThemeStore()

/** 读当前皮肤下的 CSS 变量，保证画布颜色跟随主题。 */
function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') {
    return fallback
  }
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

/** 把主色转成带透明度的 rgba，用作 +/− 徽标底色。 */
function withAlpha(color: string, alpha: number): string {
  const text = color.trim()
  const hex6 = /^#([0-9a-fA-F]{6})$/.exec(text)
  if (hex6) {
    const value = hex6[1]
    const r = parseInt(value.slice(0, 2), 16)
    const g = parseInt(value.slice(2, 4), 16)
    const b = parseInt(value.slice(4, 6), 16)
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }
  const hex3 = /^#([0-9a-fA-F]{3})$/.exec(text)
  if (hex3) {
    const value = hex3[1]
    const r = parseInt(value[0] + value[0], 16)
    const g = parseInt(value[1] + value[1], 16)
    const b = parseInt(value[2] + value[2], 16)
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }
  const rgb = /^rgba?\(([^)]+)\)$/.exec(text)
  if (rgb) {
    return `rgba(${rgb[1].split(',').slice(0, 3).map((item) => item.trim()).join(', ')}, ${alpha})`
  }
  return text
}

const container = ref<HTMLElement | null>(null)
const option = shallowRef<EChartsOption | null>(null)
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
      symbol: 'circle',
      symbolSize: 6,
    })
    byModule.set(key, list)
  }

  const convert = (node: CaseTreeNode): MindDatum => {
    const children = [
      ...(node.children ?? []).map((child) => convert(child as CaseTreeNode)),
      ...(byModule.get(String(node.id)) ?? []),
    ]
    return {
      name: node.name,
      // 父节点的「展开 / 收起」徽标即节点图标：节点圆改为透明填充，只保留点击热区与标签
      symbol: 'circle',
      symbolSize: children.length ? 16 : 6,
      itemStyle: children.length ? { color: 'transparent', borderWidth: 0 } : undefined,
      children,
    }
  }

  const roots: MindDatum[] = props.nodes
    .filter((node) => Number(node.id) > 0)
    .map((node) => convert(node))
  const orphan = byModule.get('none') ?? []
  if (orphan.length) {
    roots.push({
      name: '未关联功能点',
      symbol: 'circle',
      symbolSize: 16,
      itemStyle: { color: 'transparent', borderWidth: 0 },
      children: orphan,
    })
  }
  return roots
}

const tree = computed<MindDatum[]>(() => buildTree())

/** 根节点名称：当前项目名 + 用例脑图。 */
const rootName = computed(() => {
  const name = appStore.currentProject?.name?.trim()
  return name ? `${name}用例脑图` : '用例脑图'
})

watch([tree, container, () => themeStore.theme, rootName], () => {
  if (!container.value || !tree.value.length) {
    option.value = null
    return
  }
  // 画布不继承 CSS 变量，构建配置时读取当前皮肤的实际颜色
  const primary = cssVar('--el-color-primary', '#409eff')
  const border = cssVar('--el-border-color', '#c0c4cc')
  const text = cssVar('--el-text-color-regular', '#303133')
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
        data: [{
          name: rootName.value,
          value: -1,
          symbol: 'circle',
          symbolSize: 10,
          itemStyle: { color: primary, borderColor: primary },
          label: { offset: [0, 0] },
          children: tree.value,
        }],
        layout: 'orthogonal',
        orient: 'LR',
        symbol: 'circle',
        symbolSize: 6,
        itemStyle: { color: primary, borderColor: primary },
        // 超阈值自动折叠，按需展开，避免一次性铺满画布
        initialTreeDepth: total > 120 ? 2 : 3,
        expandAndCollapse: true,
        roam: true,
        label: {
          position: 'right',
          verticalAlign: 'middle',
          align: 'left',
          fontSize: 12,
          // 让 +/− 徽标盖在透明点击热区上，视觉上只有一个节点图标
          offset: [-20, 0],
          rich: {
            arrow: {
              color: primary,
              fontSize: 12,
              fontWeight: 'bold',
              width: 15,
              height: 15,
              lineHeight: 14,
              align: 'center',
              borderRadius: 8,
              borderWidth: 1,
              borderColor: primary,
              backgroundColor: withAlpha(primary, 0.1),
            },
            text: { fontSize: 12, color: text },
          },
          formatter: (params: unknown) => {
            // params.collapsed 由 ECharts 树节点实时状态给出：展开 − / 收起 ＋
            const p = params as { data?: MindDatum; collapsed?: boolean }
            const data = p.data
            if (!data) return ''
            const text = data.name ?? ''
            const short = text.length > 26 ? `${text.slice(0, 26)}…` : text
            // 根节点是项目标题：只显示圆点，不显示展开/收起徽标
            if (data.value === -1) return `{text|${short}}`
            if (!data.children?.length) return `{text|${short}}`
            return `{arrow|${p.collapsed ? '＋' : '−'}}{text| ${short}}`
          },
        },
        leaves: { label: { position: 'right', align: 'left', offset: [0, 0] } },
        lineStyle: { width: 1, curveness: 0.5, color: border },
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

function onChartClick(params: unknown): void {
  const p = params as { data?: MindDatum; seriesIndex?: number; dataIndex?: number }
  const data = p.data
  // 根节点是视图标题（value=-1）：ECharts 收起根节点会隐藏整棵树，这里立即还原
  if (data?.value === -1) {
    if (Number.isInteger(p.dataIndex)) {
      chart.value?.dispatchAction({
        type: 'treeExpandAndCollapse',
        seriesIndex: p.seriesIndex ?? 0,
        dataIndex: p.dataIndex,
      })
    }
    return
  }
  const caseId = Number(data?.value ?? 0)
  if (Number.isInteger(caseId) && caseId > 0) {
    emit('open', caseId)
  }
}

// 图表实例由 useEchart 在容器就绪后创建，创建完成再绑事件，避免挂载时机早于实例
watch(chart, (instance) => {
  if (!instance) {
    return
  }
  instance.off('click')
  instance.on('click', onChartClick)
}, { immediate: true })

onMounted(() => {
  render()
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
