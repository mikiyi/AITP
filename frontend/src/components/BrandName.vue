<template>
  <span class="brand-name" :class="`is-${size}`">
    <span class="brand-name__ai">AI</span><span class="brand-name__test">Test</span><span class="brand-name__pilot">Pilot</span>
  </span>
</template>

<script setup lang="ts">
/** 产品名分色：AI 蓝、Test 绿、Pilot 红，整体加粗。 */
withDefaults(
  defineProps<{
    size?: 'normal' | 'small'
  }>(),
  { size: 'normal' },
)
</script>

<style scoped>
.brand-name {
  font-weight: 700;
  letter-spacing: 0.2px;
  white-space: nowrap;
}

.brand-name.is-small {
  font-size: 14px;
}

.brand-name__ai {
  color: #2f6fed;
}

.brand-name__test {
  color: #67c23a;
}

.brand-name__pilot {
  color: #f56c6c;
}
</style>
