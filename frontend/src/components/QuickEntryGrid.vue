<template>
  <div class="quick-entry">
    <el-button
      v-for="item in entries"
      :key="item.key"
      class="quick-entry-item"
      @click="go(item)"
    >
      <span class="quick-entry-label">{{ item.label }}</span>
      <span class="quick-entry-group">（{{ item.group }}）</span>
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { workbenchApi } from '@/api'
import type { QuickEntry } from '@/api/types'

defineProps<{
  entries: QuickEntry[]
}>()

const router = useRouter()

function go(item: QuickEntry): void {
  // 上报点击次数，首页按使用频率取前 10；上报失败不阻塞跳转
  void workbenchApi.reportShortcut(item.key).catch(() => undefined)
  void router.push(item.path)
}
</script>

<style scoped>
.quick-entry {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}

.quick-entry-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  height: 62px;
  padding: 10px 14px;
  margin: 0;
  border: 1px solid var(--aitp-border);
  border-radius: var(--aitp-radius);
  background: var(--aitp-panel);
  color: var(--aitp-text);
  transition: all 0.15s;
}

.quick-entry-item:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
  background: var(--aitp-hover);
}

.quick-entry-label {
  font-size: 14px;
  font-weight: 600;
}

.quick-entry-group {
  font-size: 12px;
  color: var(--aitp-text-weak);
}
</style>
