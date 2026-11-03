<template>
  <el-dialog v-model="visible" title="操作被拒绝：条目被下游引用" width="560px">
    <el-alert type="warning" :closable="false" :title="message" show-icon />
    <el-table v-if="refs.length" :data="refs" size="small" border class="ref-table">
      <el-table-column prop="refType" label="引用类型" width="120" />
      <el-table-column prop="label" label="引用来源" min-width="240" />
      <el-table-column prop="refId" label="标识" width="90" />
    </el-table>
    <p class="ref-hint">请先到引用来源处解除引用，再执行撤销或删除。</p>
    <template #footer>
      <el-button type="primary" @click="visible = false">知道了</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import type { KnowledgeRef } from '@/api/types'

const visible = defineModel<boolean>({ required: true })

defineProps<{
  message: string
  refs: KnowledgeRef[]
}>()
</script>

<style scoped>
.ref-table {
  margin-top: 12px;
}

.ref-hint {
  margin: 12px 0 0;
  color: var(--aitp-text-secondary, #909399);
  font-size: 12px;
}
</style>
