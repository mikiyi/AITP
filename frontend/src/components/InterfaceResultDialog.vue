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

      <div v-if="result.attribution || result.flaky" class="iface-attr">
        <el-tooltip
          v-if="result.attribution"
          :content="attributionTip(result.attribution)"
          placement="top"
        >
          <el-tag class="attribution-tag" :type="attributionTagType(result.attribution.kind)" size="small">
            {{ result.attribution.label }}
            <template v-if="result.attribution.confidence">
              · {{ Math.round(result.attribution.confidence * 100) }}%
            </template>
          </el-tag>
        </el-tooltip>
        <el-tag v-if="result.flaky" class="flaky-tag" type="warning" size="small" effect="plain">
          flaky · 重试 {{ result.retryCount ?? 0 }} 次
        </el-tag>
        <span v-else-if="result.retryCount" class="muted">重试 {{ result.retryCount }} 次</span>
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
    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
      <el-button
        v-if="result && !result.passed && result.executionId"
        type="primary"
        :loading="generating"
        @click="emitGenerate"
      >生成修复建议</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { FailureAttribution, InterfaceExecution } from '@/api/types'

const props = defineProps<{
  modelValue: boolean
  result: InterfaceExecution | null
  generating?: boolean
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'generateFix', executionId: number): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

function emitGenerate(): void {
  const executionId = props.result?.executionId
  if (executionId) {
    emit('generateFix', executionId)
  }
}

/** 归因配色：缺陷红、断言黄、环境蓝、数据/未知灰。 */
function attributionTagType(kind: string): 'danger' | 'warning' | 'primary' | 'info' {
  if (kind === 'bug') return 'danger'
  if (kind === 'assertion') return 'warning'
  if (kind === 'environment') return 'primary'
  return 'info'
}

function attributionTip(attribution: FailureAttribution): string {
  const parts = [attribution.reason, attribution.action].filter((item) => item && item.trim())
  return parts.join('；') || attribution.label
}

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

.iface-attr {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
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
