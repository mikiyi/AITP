<template>
  <div class="mindmap" :style="{ height: `${height}px` }">
    <svg ref="svgRef" class="mindmap__svg"></svg>
    <div v-if="!nodes.length" class="mindmap__empty">暂无可渲染的节点</div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Markmap } from 'markmap-view'
import type { IPureNode } from 'markmap-common'
import type { MindNode } from '@/api/types'

const props = withDefaults(
  defineProps<{
    nodes: MindNode[]
    height?: number
  }>(),
  { height: 420 },
)

const emit = defineEmits<{
  (event: 'node-context', payload: { title: string; path: string[]; text: string }): void
}>()

const svgRef = ref<SVGSVGElement | null>(null)
let instance: Markmap | null = null

function buildPure(node: MindNode, path: string[]): IPureNode {
  const title = node.title || '未命名'
  const suffix = [node.priority ? `[${node.priority}]` : '', node.note ? `：${node.note}` : '']
    .filter(Boolean)
    .join('')
  const nextPath = [...path, title]
  return {
    content: `${title}${suffix}`,
    payload: { path: nextPath },
    children: (node.children ?? []).map((child) => buildPure(child, nextPath)),
  }
}

function buildData(): IPureNode | null {
  const nodes = props.nodes ?? []
  if (!nodes.length) {
    return null
  }
  if (nodes.length === 1) {
    return buildPure(nodes[0], [])
  }
  return {
    content: '大纲',
    payload: { path: ['大纲'] },
    children: nodes.map((node) => buildPure(node, ['大纲'])),
  }
}

async function render(): Promise<void> {
  if (!svgRef.value) {
    return
  }
  const data = buildData()
  if (!data) {
    return
  }
  if (!instance) {
    instance = Markmap.create(svgRef.value, {
      embedGlobalCSS: false,
      autoFit: true,
      initialExpandLevel: 3,
      pan: true,
      zoom: true,
    })
    svgRef.value.addEventListener('contextmenu', onContextMenu)
  }
  await instance.setData(data)
}

function onContextMenu(event: MouseEvent): void {
  const target = event.target as HTMLElement | null
  const group = target?.closest('g') as (SVGGElement & { __data__?: { payload?: { path?: string[] } } }) | null
  const path = group?.__data__?.payload?.path
  if (!path || !path.length) {
    return
  }
  event.preventDefault()
  emit('node-context', {
    title: path[path.length - 1],
    path,
    text: path.join('\n'),
  })
}

onMounted(() => {
  void render()
})

watch(
  () => props.nodes,
  () => {
    void render()
  },
  { deep: true },
)

onBeforeUnmount(() => {
  if (svgRef.value) {
    svgRef.value.removeEventListener('contextmenu', onContextMenu)
  }
  instance?.destroy()
  instance = null
})
</script>

<style scoped>
.mindmap {
  position: relative;
  width: 100%;
  border: 1px solid var(--aitp-border);
  border-radius: var(--aitp-radius);
  background: #fff;
  overflow: hidden;
}

.mindmap__svg {
  width: 100%;
  height: 100%;
}

.mindmap__empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--aitp-text-weak);
  font-size: 13px;
}
</style>
