<template>
  <div class="detail-missing-page">
    <div class="panel panel-pad detail-missing">
      <el-icon class="detail-missing__icon" :size="34"><WarningFilled /></el-icon>
      <h3>{{ title }}</h3>
      <p class="muted">{{ message }}</p>
      <div class="detail-missing__project">
        <span class="muted">当前项目</span>
        <ProjectSwitcher />
      </div>
      <div class="toolbar detail-missing__actions">
        <el-button @click="$emit('back')">返回列表</el-button>
        <el-button type="primary" @click="$emit('retry')">重试</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { WarningFilled } from '@element-plus/icons-vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'

withDefaults(
  defineProps<{
    title?: string
    message?: string
  }>(),
  {
    title: '目标不存在或不属于当前项目',
    message: '资源可能已被删除，或属于另一个项目。确认当前项目，或切换项目后重试。',
  },
)

defineEmits<{
  (event: 'back'): void
  (event: 'retry'): void
}>()
</script>

<style scoped>
.detail-missing-page {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 7vh;
}
.detail-missing {
  max-width: 560px;
  width: 100%;
  text-align: center;
}
.detail-missing__icon {
  color: var(--el-color-warning);
}
.detail-missing h3 {
  margin: 12px 0 8px;
}
.detail-missing__project {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin: 18px 0 8px;
}
.detail-missing__actions {
  justify-content: center;
  margin-top: 12px;
}
</style>
