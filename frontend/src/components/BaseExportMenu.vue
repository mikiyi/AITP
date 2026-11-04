<template>
  <el-dropdown trigger="click" @command="onCommand">
    <el-button :icon="Download">
      导出
      <el-icon class="el-icon--right"><ArrowDown /></el-icon>
    </el-button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item v-for="item in formats" :key="item" :command="item">
          {{ LABELS[item] || item }}
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup lang="ts">
import { ArrowDown, Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const LABELS: Record<string, string> = {
  xlsx: 'Excel（xlsx）',
  csv: 'CSV',
  json: 'JSON',
}

const props = withDefaults(
  defineProps<{
    formats?: string[]
    /** 由父组件按当前筛选拼出下载地址；返回空字符串表示条件不满足。 */
    resolveUrl: (format: string) => string
    /** 导出前的前置校验，返回错误文案则中断。 */
    guard?: () => string | ''
  }>(),
  {
    formats: () => ['csv', 'json'],
    guard: undefined,
  },
)

function onCommand(format: string): void {
  const blocked = props.guard ? props.guard() : ''
  if (blocked) {
    ElMessage.warning(blocked)
    return
  }
  const url = props.resolveUrl(format)
  if (!url) {
    ElMessage.warning('暂无可导出的数据')
    return
  }
  window.open(url, '_blank')
}
</script>
