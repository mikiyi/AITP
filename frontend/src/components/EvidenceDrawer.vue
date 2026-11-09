<template>
  <el-drawer
    :model-value="modelValue"
    :title="drawerTitle"
    size="760px"
    append-to-body
    class="evidence-drawer"
    @update:model-value="visible = $event"
  >
    <div v-loading="loading">
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

      <el-empty
        v-else-if="!loading && !items.length"
        :description="EMPTY_TEXT"
        :image-size="72"
      />

      <div v-for="(item, index) in items" :key="itemKey(item, index)" class="ev-block">
        <div class="ev-head">
          <el-tag :type="resultTone(item.result)" size="small">{{ resultLabel(item) }}</el-tag>
          <span class="ev-no">证据编号 {{ evidenceNo(item) }}</span>
          <el-tag size="small" effect="plain">{{ item.sourceTypeLabel || item.sourceType || '未知来源' }}</el-tag>
          <span v-if="item.durationMs" class="ev-muted">耗时 {{ item.durationMs }}ms</span>
          <span v-else class="ev-muted">耗时未记录</span>
        </div>

        <div class="ev-meta">
          <span v-if="item.envName">环境：{{ item.envName }}</span>
          <span v-if="item.executedBy">执行人：{{ item.executedBy }}</span>
          <span v-if="item.executedAt">执行时间：{{ dateTimeText(item.executedAt) }}</span>
          <span v-if="item.traceId">追踪号：{{ item.traceId }}</span>
        </div>

        <p v-if="item.errorMessage" class="ev-error">{{ item.errorMessage }}</p>

        <template v-if="items.length > 1">
          <div class="ev-title">断言明细（{{ index + 1 }}/{{ items.length }}）</div>
        </template>
        <div v-else class="ev-title">断言明细</div>
        <el-table :data="item.assertions ?? []" size="small" border>
          <el-table-column prop="name" label="断言" min-width="140" show-overflow-tooltip />
          <el-table-column label="结果" width="72">
            <template #default="scope">
              <el-tag :type="scope.row.passed ? 'success' : 'danger'" size="small" effect="plain">
                {{ scope.row.passed ? '通过' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="expected" label="期望" min-width="110" show-overflow-tooltip />
          <el-table-column prop="actual" label="实际" min-width="130" show-overflow-tooltip />
          <el-table-column prop="message" label="说明" min-width="130" show-overflow-tooltip />
          <template #empty>
            <span class="ev-muted">这条证据没有断言明细</span>
          </template>
        </el-table>

        <div class="ev-title">请求摘要</div>
        <pre class="ev-pre">{{ item.requestSummary || '无请求快照' }}</pre>

        <div class="ev-title">响应摘要</div>
        <pre class="ev-pre">{{ item.responseSummary || '无响应快照' }}</pre>

        <template v-if="item.logSummary">
          <div class="ev-title">日志摘要</div>
          <pre class="ev-pre">{{ item.logSummary }}</pre>
        </template>

        <div class="ev-title">附件快照</div>
        <div v-if="(item.attachments ?? []).length" class="ev-files">
          <a
            v-for="file in item.attachments"
            :key="`att-${file.attachmentId}`"
            :href="file.previewUrl"
            target="_blank"
            rel="noopener"
          >
            {{ file.fileName || `附件 ${file.attachmentId}` }}
            <span class="ev-muted">（{{ file.suffix || file.kind || '文件' }}）</span>
          </a>
        </div>
        <p v-else class="ev-muted">这条证据没有附件快照。</p>
      </div>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { testRunApi } from '@/api'
import { dateTimeText } from '@/utils/format'
import type { ExecutionEvidence } from '@/api/types'

const NO_EVIDENCE_TEXT = '没有可下钻的执行证据快照。'
const EMPTY_TEXT = `${NO_EVIDENCE_TEXT}可能这条记录是手工执行的、或快照已过保留期。`

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    /** 定位参数，一次只认一个：evidenceId / runCaseId / bugId / regressionItemId / caseId。 */
    locator?: Record<string, string | number>
    title?: string
  }>(),
  { locator: () => ({}), title: '执行证据' },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
}>()

const loading = ref(false)
const error = ref('')
const items = ref<ExecutionEvidence[]>([])

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const drawerTitle = computed(() => {
  const head = props.title || '执行证据'
  if (!items.value.length) {
    return head
  }
  const first = items.value[0]
  return `${head} · ${first.title || evidenceNo(first)}`
})

function evidenceNo(item: ExecutionEvidence): string {
  return item.evidenceId ? `EV-${item.evidenceId}` : '无证据编号'
}

function itemKey(item: ExecutionEvidence, index: number): string {
  return `ev-${item.evidenceId ?? 'x'}-${index}`
}

function resultLabel(item: ExecutionEvidence): string {
  return item.resultLabel || item.result || '未执行'
}

function resultTone(result: string | undefined): 'success' | 'danger' | 'warning' | 'info' {
  if (result === 'passed') {
    return 'success'
  }
  if (result === 'failed') {
    return 'danger'
  }
  return result === 'blocked' ? 'warning' : 'info'
}

async function load(): Promise<void> {
  const keys = Object.keys(props.locator ?? {}).filter((key) => props.locator[key] !== undefined && props.locator[key] !== '')
  items.value = []
  error.value = ''
  if (!keys.length) {
    error.value = NO_EVIDENCE_TEXT
    return
  }
  loading.value = true
  try {
    const data = await testRunApi.evidence(props.locator)
    if (!data) {
      items.value = []
    } else if (Array.isArray((data as { list?: unknown }).list)) {
      items.value = (data as { list: ExecutionEvidence[] }).list
    } else {
      items.value = [data as ExecutionEvidence]
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '证据查询失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.modelValue, JSON.stringify(props.locator ?? {})] as const,
  ([open]) => {
    if (open) {
      void load()
    }
  },
  { immediate: true },
)

defineExpose({ reload: load })
</script>

<style scoped>
.evidence-drawer {
  padding-bottom: 24px;
}
.ev-block + .ev-block {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px dashed var(--el-border-color);
}
.ev-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.ev-no {
  font-weight: 600;
}
.ev-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.ev-error {
  margin: 8px 0 0;
  color: var(--el-color-danger);
  font-size: 13px;
  word-break: break-all;
}
.ev-title {
  margin-top: 14px;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.ev-pre {
  max-height: 180px;
  margin: 0;
  padding: 8px 10px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}
.ev-files {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ev-muted {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
