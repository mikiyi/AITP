<script setup lang="ts">
import { computed } from 'vue'

/** 轻量资源曲线：0-100% 固定纵轴；不给时间窗口时按点数均分，给了就按时间落点。 */
const props = withDefaults(defineProps<{
  points: number[]
  timestamps?: number[]
  windowMs?: number
  color?: string
  height?: number
}>(), {
  color: 'var(--el-color-primary)',
  height: 38,
  windowMs: 0,
})

const WIDTH = 100
const HEIGHT = 40
const MAX_POINTS = 30
const TICKS = [0, 20, 40, 60, 80, 100]

interface Sample {
  value: number
  x: number
}

function yOf(value: number): number {
  const clamped = Math.max(0, Math.min(100, value))
  return HEIGHT - (clamped / 100) * HEIGHT
}

const samples = computed<Sample[]>(() => {
  const values = props.points
  const times = props.timestamps
  if (props.windowMs > 0 && times && times.length === values.length) {
    // 时间窗口模式：右端永远是「现在」，窗口外样本直接丢弃
    const right = Date.now()
    const left = right - props.windowMs
    const list: Sample[] = []
    for (let i = 0; i < values.length; i += 1) {
      const value = values[i]
      const ts = times[i]
      if (!Number.isFinite(value) || !Number.isFinite(ts) || ts < left) {
        continue
      }
      list.push({ value, x: ((ts - left) / props.windowMs) * WIDTH })
    }
    return list
  }
  const tail = values.slice(-MAX_POINTS)
  return tail.map((value, index) => ({
    value,
    x: tail.length > 1 ? (index / (tail.length - 1)) * WIDTH : WIDTH,
  }))
})

const polyline = computed(() => {
  const list = samples.value
  if (!list.length) return ''
  if (list.length === 1) {
    const { value, x } = list[0]
    const y = yOf(value).toFixed(2)
    const start = Math.max(0, x - 1.2).toFixed(2)
    return `${start},${y} ${x.toFixed(2)},${y}`
  }
  return list.map((item) => `${item.x.toFixed(2)},${yOf(item.value).toFixed(2)}`).join(' ')
})

const areaPath = computed(() => {
  const list = samples.value
  if (!list.length) return ''
  const first = list[0]
  const last = list[list.length - 1]
  let path = `M${first.x.toFixed(2)},${HEIGHT} L${first.x.toFixed(2)},${yOf(first.value).toFixed(2)}`
  for (const item of list.slice(1)) {
    path += ` L${item.x.toFixed(2)},${yOf(item.value).toFixed(2)}`
  }
  return `${path} L${last.x.toFixed(2)},${HEIGHT} Z`
})

const latest = computed(() => samples.value[samples.value.length - 1] ?? null)
</script>

<template>
  <div class="metric-spark" :style="{ color }">
    <div class="metric-spark__plot" :style="{ height: `${height}px` }">
      <span
        v-for="tick in TICKS"
        :key="tick"
        class="metric-spark__grid"
        :style="{ top: `${100 - tick}%` }"
      />
      <svg
        class="metric-spark__svg"
        :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
        preserveAspectRatio="none"
        role="img"
        aria-hidden="true"
      >
        <path v-if="areaPath" :d="areaPath" class="metric-spark__area" fill="currentColor" />
        <polyline
          v-if="polyline"
          :points="polyline"
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
          stroke-linecap="round"
          stroke-linejoin="round"
          vector-effect="non-scaling-stroke"
        />
        <line
          v-if="latest"
          :x1="latest.x.toFixed(2)"
          :x2="latest.x.toFixed(2)"
          :y1="Math.max(0, yOf(latest.value) - 1.6).toFixed(2)"
          :y2="Math.min(HEIGHT, yOf(latest.value) + 1.6).toFixed(2)"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          vector-effect="non-scaling-stroke"
          class="metric-spark__latest"
        />
      </svg>
    </div>
    <div class="metric-spark__scale" :style="{ height: `${height}px` }" aria-hidden="true">
      <span
        v-for="tick in TICKS"
        :key="tick"
        class="metric-spark__scale-label"
        :style="{ top: `${100 - tick}%` }"
      >
        {{ tick }}%
      </span>
    </div>
  </div>
</template>

<style scoped>
.metric-spark {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 6px 0;
}

.metric-spark__plot {
  position: relative;
  flex: 1;
  min-width: 0;
}

.metric-spark__svg {
  display: block;
  width: 100%;
  height: 100%;
}

.metric-spark__grid {
  position: absolute;
  left: 0;
  right: 0;
  height: 1px;
  background: var(--aitp-border);
  transform: translateY(-50%);
  pointer-events: none;
}

.metric-spark__area {
  opacity: 0.14;
}

.metric-spark__scale {
  position: relative;
  flex: none;
  width: 30px;
}

.metric-spark__scale-label {
  position: absolute;
  right: 0;
  transform: translateY(-50%);
  font-size: 10px;
  line-height: 1;
  white-space: nowrap;
  color: var(--aitp-text-weak);
  font-variant-numeric: tabular-nums;
}
</style>
