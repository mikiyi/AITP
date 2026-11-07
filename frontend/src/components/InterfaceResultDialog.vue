<template>
  <el-dialog
    :model-value="modelValue"
    title="接口执行结果"
    width="760px"
    @update:model-value="visible = $event"
  >
    <template v-if="result">
      <div class="iface-head">
        <el-tag :type="result.passed ? 'success' : 'danger'" size="small">
          {{ result.passed ? '通过' : '失败' }}
        </el-tag>
        <span class="iface-method">{{ result.request.method }}</span>
        <span class="iface-url">{{ result.request.url }}</span>
      </div>
      <div class="iface-meta">
        <span>HTTP {{ result.statusCode ?? '-' }}</span>
        <span>耗时 {{ result.durationMs }}ms</span>
        <span v-if="result.envName">环境：{{ result.envName }}</span>
        <span>{{ result.message }}</span>
      </div>

      <div class="iface-title">断言明细</div>
      <el-table :data="result.assertions" size="small" border>
        <el-table-column prop="name" label="断言" min-width="150" show-overflow-tooltip />
        <el-table-column label="结果" width="76">
          <template #default="scope">
            <el-tag :type="scope.row.passed ? 'success' : 'danger'" size="small" effect="plain">
              {{ scope.row.passed ? '通过' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="expected" label="期望" min-width="120" show-overflow-tooltip />
        <el-table-column prop="actual" label="实际" min-width="140" show-overflow-tooltip />
        <el-table-column prop="message" label="说明" min-width="140" show-overflow-tooltip />
        <template #empty>
          <span class="muted">没有配置断言</span>
        </template>
      </el-table>

      <div class="iface-title">响应体</div>
      <pre class="iface-pre">{{ result.response.body || '（空响应）' }}</pre>
      <div v-if="result.response.bodyTruncated" class="muted">响应体过长，已截断显示</div>

      <div class="iface-title">请求头</div>
      <pre class="iface-pre">{{ requestHeaders }}</pre>
      <template v-if="result.request.body">
        <div class="iface-title">请求体</div>
        <pre class="iface-pre">{{ result.request.body }}</pre>
      </template>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { InterfaceExecution } from '@/api/types'

const props = defineProps<{
  modelValue: boolean
  result: InterfaceExecution | null
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const requestHeaders = computed(() => {
  const headers = props.result?.request.headers ?? {}
  return Object.keys(headers).length ? JSON.stringify(headers, null, 2) : '（无）'
})
</script>

<style scoped>
.iface-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.iface-method {
  font-weight: 600;
  color: var(--el-color-primary);
}

.iface-url {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--aitp-font-mono, monospace);
  font-size: 12px;
}

.iface-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.iface-title {
  margin: 14px 0 6px;
  font-size: 13px;
  font-weight: 600;
}

.iface-pre {
  max-height: 220px;
  margin: 0;
  padding: 8px 10px;
  overflow: auto;
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
  font-family: var(--aitp-font-mono, monospace);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
