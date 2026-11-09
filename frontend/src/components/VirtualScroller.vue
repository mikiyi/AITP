<template>
  <div
    ref="scrollerRef"
    class="virtual-scroller"
    :style="containerStyle"
    @scroll.passive="onScroll"
  >
    <div v-if="loading && !items.length" class="virtual-scroller__skeleton" :style="{ minHeight: skeletonHeight }">
      <el-skeleton :rows="skeletonRows" animated />
    </div>
    <div v-else-if="!items.length" class="virtual-scroller__empty" :style="{ minHeight: skeletonHeight }">
      <slot name="empty">
        <span class="muted">暂无数据</span>
      </slot>
    </div>
    <div v-else class="virtual-scroller__track" :style="{ height: `${totalHeight}px` }">
      <div
        v-for="(item, offset) in windowItems"
        :key="itemKey(item, start + offset)"
        class="virtual-scroller__row"
        :style="{ transform: `translateY(${(start + offset) * itemHeight}px)`, height: `${itemHeight}px` }"
      >
        <slot :item="item" :index="start + offset" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts" generic="T extends Record<string, any>">
/**
 * 定行高窗口列表（GAP-25 前端 · 长列表虚拟滚动）。
 *
 * 只渲染视口内（含上下缓冲）的若干行，其余行用轨道总高度顶出滚动条，
 * 所以万级条目的面板里 DOM 行数恒定在几十行以内。
 * 树形面板把「节点 + 展开出来的用例 + 续取入口」摊平成同一种行再交进来，
 * 行高固定，缩进与样式由外层插槽自己画。
 */
import { computed, nextTick, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 摊平后的全部行。 */
    items: T[]
    /** 单行高度（像素），窗口大小按它算。 */
    itemHeight?: number
    /** 容器高度（像素）。 */
    height?: number
    /** 容器最大高度（像素），内容不足时自动塌下来。 */
    maxHeight?: number
    /** 上下各多渲染几行，滚动时不露白。 */
    overscan?: number
    /** 首屏骨架行数与空态最小高度用的容器高度。 */
    loading?: boolean
    /** 取行唯一键；默认用下标，展开/续取时建议给稳定键避免整窗重挂。 */
    keyOf?: (item: T, index: number) => string | number
  }>(),
  {
    itemHeight: 28,
    height: undefined,
    maxHeight: 360,
    overscan: 6,
    loading: false,
    keyOf: undefined,
  },
)

const scrollerRef = ref<HTMLElement | null>(null)
const scrollTop = ref(0)

const containerHeight = computed(() => props.height ?? props.maxHeight ?? 360)

const containerStyle = computed(() => ({
  height: props.height ? `${props.height}px` : undefined,
  maxHeight: props.maxHeight ? `${props.maxHeight}px` : undefined,
}))

const skeletonRows = computed(() => Math.max(3, Math.min(12, Math.floor(containerHeight.value / props.itemHeight))))
const skeletonHeight = computed(() => `${Math.min(containerHeight.value, props.itemHeight * 4)}px`)

const totalHeight = computed(() => props.items.length * props.itemHeight)

const start = computed(() => {
  const raw = Math.floor(scrollTop.value / props.itemHeight) - props.overscan
  return Math.min(Math.max(raw, 0), Math.max(0, props.items.length - 1))
})

const end = computed(() => {
  const visible = Math.ceil(containerHeight.value / props.itemHeight) + props.overscan * 2
  return Math.min(props.items.length, start.value + visible)
})

const windowItems = computed(() => props.items.slice(start.value, end.value))

function itemKey(item: T, index: number): string | number {
  return props.keyOf ? props.keyOf(item, index) : index
}

function onScroll(event: Event): void {
  const el = event.target as HTMLElement | null
  scrollTop.value = el && el.scrollTop > 0 ? el.scrollTop : 0
}

/** 换数据 / 换项目后回到顶部，避免旧滚动位置落在新列表中间。 */
function resetScroll(): void {
  scrollTop.value = 0
  if (scrollerRef.value) {
    scrollerRef.value.scrollTop = 0
  }
}

watch(() => props.items.length, (length, previous) => {
  // 续取是往后追加，滚动位置该留着；整表替换（换项目、换筛选）才回到顶部
  if (previous !== undefined && length > previous) {
    return
  }
  void nextTick(() => {
    if (scrollTop.value > totalHeight.value) {
      resetScroll()
    }
  })
})

defineExpose({ resetScroll })
</script>

<style scoped>
.virtual-scroller {
  position: relative;
  overflow-y: auto;
  overflow-x: hidden;
}

.virtual-scroller__track {
  position: relative;
  width: 100%;
}

.virtual-scroller__row {
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  display: flex;
  align-items: center;
  overflow: hidden;
}

.virtual-scroller__skeleton,
.virtual-scroller__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 6px 2px;
}

.virtual-scroller__skeleton {
  align-items: stretch;
  flex-direction: column;
  justify-content: flex-start;
}
</style>
