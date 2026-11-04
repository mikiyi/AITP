<template>
  <el-tooltip
    effect="dark"
    placement="top"
    :show-after="120"
    :disabled="!overflowing"
  >
    <template #content>
      <div>
        <div>{{ label }}：{{ display }}{{ suffix }}</div>
        <div v-if="hint">{{ hint }}</div>
      </div>
    </template>
    <div class="metric-card" :class="`is-${tone}`" @mouseenter="detectOverflow">
      <div class="metric-icon">
        <el-icon :size="20"><component :is="icon" /></el-icon>
      </div>
      <div class="metric-body">
        <div ref="labelEl" class="metric-label">{{ label }}</div>
        <div ref="valueEl" class="metric-value">
          {{ display }}
          <span v-if="suffix" class="metric-suffix">{{ suffix }}</span>
        </div>
        <div v-if="hint" ref="hintEl" class="metric-hint">{{ hint }}</div>
      </div>
    </div>
  </el-tooltip>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    label: string
    value: number | string
    icon?: string
    suffix?: string
    hint?: string
    tone?: 'primary' | 'success' | 'warning' | 'danger' | 'info'
    precision?: number
  }>(),
  {
    icon: 'DataLine',
    suffix: '',
    hint: '',
    tone: 'primary',
    precision: 0,
  },
)

const display = computed(() => {
  if (typeof props.value === 'string') {
    return props.value
  }
  if (props.precision > 0) {
    return props.value.toFixed(props.precision)
  }
  return String(props.value)
})

const labelEl = ref<HTMLElement | null>(null)
const valueEl = ref<HTMLElement | null>(null)
const hintEl = ref<HTMLElement | null>(null)
const overflowing = ref(false)

/** 窄卡片里三行文本都会被截断，只有真的截到内容才弹浮层，避免正常宽度下多一个提示。 */
function detectOverflow(): void {
  overflowing.value = [labelEl.value, valueEl.value, hintEl.value].some(
    (el) => !!el && el.scrollWidth > el.clientWidth + 1,
  )
}
</script>

<style scoped>
.metric-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: var(--aitp-panel);
  border: 1px solid var(--aitp-border);
  border-radius: var(--aitp-radius);
  box-shadow: var(--aitp-shadow);
}

.metric-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--aitp-primary-soft);
  color: var(--el-color-primary);
  flex-shrink: 0;
}

.is-success .metric-icon {
  background: rgba(103, 194, 58, 0.12);
  color: #67c23a;
}

.is-warning .metric-icon {
  background: rgba(230, 162, 60, 0.14);
  color: #e6a23c;
}

.is-danger .metric-icon {
  background: rgba(245, 108, 108, 0.12);
  color: #f56c6c;
}

.is-info .metric-icon {
  background: rgba(144, 147, 153, 0.14);
  color: #909399;
}

.metric-body {
  min-width: 0;
}

.metric-label {
  font-size: 12px;
  color: var(--aitp-text-weak);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-value {
  font-size: 22px;
  font-weight: 650;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.metric-suffix {
  font-size: 12px;
  font-weight: 400;
  color: var(--aitp-text-weak);
  margin-left: 2px;
}

.metric-hint {
  font-size: 12px;
  color: var(--aitp-text-weak);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
