<template>
  <div class="project-switcher">
    <span class="project-switcher__label">{{ label }}</span>
    <el-select
      v-if="appStore.projects.length"
      :model-value="appStore.currentProjectId"
      class="project-switcher__select"
      :style="{ width }"
      placeholder="选择项目"
      size="small"
      filterable
      @update:model-value="onSelect"
    >
      <el-option
        v-for="item in appStore.projects"
        :key="item.id"
        :label="item.name"
        :value="item.id"
      />
    </el-select>
    <!-- 一个项目都没有时给条出路，否则页面只会反复提示"请先选择项目" -->
    <RouterLink v-else class="project-switcher__empty" to="/projects">还没有项目，去新建</RouterLink>
  </div>
</template>

<script setup lang="ts">
import { useAppStore } from '@/stores/app'

withDefaults(
  defineProps<{
    /** 下拉宽度，页面工具栏拥挤时可以调窄。 */
    width?: string
    /** 前缀文字，默认「项目」。 */
    label?: string
  }>(),
  {
    width: '180px',
    label: '项目',
  },
)

const appStore = useAppStore()

function onSelect(value: number | null): void {
  appStore.selectProject(value ?? null)
}
</script>

<style scoped>
.project-switcher {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.project-switcher__label {
  font-size: 12px;
  color: var(--aitp-text-weak);
  white-space: nowrap;
}

.project-switcher__select {
  min-width: 120px;
}

.project-switcher__empty {
  font-size: 12px;
  color: var(--el-color-primary);
  white-space: nowrap;
}
</style>
