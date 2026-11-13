<template>
  <div class="rsub" role="tablist">
    <button
      v-for="item in items"
      :key="item.path"
      type="button"
      role="tab"
      class="rsub__item"
      :class="{ 'is-active': route.path === item.path }"
      :aria-selected="route.path === item.path"
      @click="go(item.path)"
    >
      {{ item.label }}
    </button>
  </div>
</template>

<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'

defineProps<{ items: Array<{ path: string; label: string }> }>()

const route = useRoute()
const router = useRouter()

function go(path: string): void {
  if (route.path !== path) void router.push(path)
}
</script>

<style scoped>
.rsub {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--aitp-border);
  border-radius: 9px;
  background: var(--aitp-subtle);
  margin-bottom: 12px;
}

.rsub__item {
  border: none;
  background: transparent;
  padding: 5px 14px;
  border-radius: 7px;
  font-size: 13px;
  color: var(--aitp-text-weak);
  cursor: pointer;
  transition: background .15s ease, color .15s ease;
}

.rsub__item:hover {
  color: var(--aitp-text);
}

.rsub__item.is-active {
  background: var(--aitp-panel);
  color: var(--el-color-primary);
  font-weight: 600;
  box-shadow: var(--aitp-shadow);
}
</style>