<template>
  <div class="metric-card" :class="`is-${tone}`">
    <div class="metric-icon">
      <el-icon :size="20"><component :is="icon" /></el-icon>
    </div>
    <div class="metric-body">
      <div class="metric-label">{{ label }}</div>
      <div class="metric-value">
        {{ display }}
        <span v-if="suffix" class="metric-suffix">{{ suffix }}</span>
      </div>
      <div v-if="hint" class="metric-hint">{{ hint }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

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
}

.metric-value {
  font-size: 22px;
  font-weight: 650;
  line-height: 1.3;
  white-space: nowrap;
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
