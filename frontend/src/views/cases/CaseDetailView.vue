<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">测试用例</h2>
      <div class="toolbar" style="margin: 0">
        <el-button
          v-if="detail?.caseType === 'interface'"
          type="primary"
          :icon="VideoPlay"
          :loading="running"
          @click="runInterface"
        >
          执行接口用例
        </el-button>
        <el-button :icon="Back" @click="router.back()">返回</el-button>
      </div>
    </div>

    <el-skeleton v-if="loading" :rows="6" animated />

    <div v-else-if="detail" class="case-detail">
      <div class="panel panel-pad">
        <div class="case-detail__head">
          <div>
            <div class="case-detail__no muted">{{ detail.caseNo }}</div>
            <h3 class="case-detail__title">{{ detail.title }}</h3>
          </div>
          <div class="case-detail__tags">
            <el-tag size="small" effect="plain">{{ caseTypeText(detail.caseType) }}</el-tag>
            <el-tag :type="priorityTag(detail.priority)" size="small" effect="plain">{{ detail.priority }}</el-tag>
            <el-tag :type="statusTag(detail.status)" size="small" effect="plain">
              {{ statusLabel(detail.status) }}
            </el-tag>
          </div>
        </div>

        <el-descriptions :column="3" border size="small" class="case-detail__desc">
          <el-descriptions-item label="项目">{{ detail.projectId }}</el-descriptions-item>
          <el-descriptions-item label="功能点">{{ detail.moduleId ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="需求">{{ detail.requirementId ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="来源">{{ caseSourceText(detail.source ?? '') || '-' }}</el-descriptions-item>
          <el-descriptions-item label="标签">{{ detail.tags ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ dateTimeText(detail.createdAt) }}</el-descriptions-item>
        </el-descriptions>
      </div>

      <div class="panel panel-pad">
        <div class="section-title">前置条件</div>
        <div class="pre">{{ detail.preconditions || '无' }}</div>

        <div class="section-title">执行步骤</div>
        <el-table :data="detail.steps || []" size="small" border>
          <el-table-column type="index" label="#" width="50" />
          <el-table-column label="操作步骤" min-width="220">
            <template #default="{ row }"><span class="pre-line">{{ row.action }}</span></template>
          </el-table-column>
          <el-table-column label="预期结果" min-width="220">
            <template #default="{ row }"><span class="pre-line">{{ row.expected }}</span></template>
          </el-table-column>
        </el-table>

        <div class="section-title">整体预期结果</div>
        <div class="pre">{{ detail.expectedResult || '无' }}</div>

        <template v-if="testDataRaw">
          <div class="section-title">测试数据</div>
          <div class="pre">{{ testDataRaw }}</div>
          <div class="test-data-meta">
            <el-button size="small" :loading="previewLoading" @click="loadTestDataPreview">解析预览</el-button>
            <el-tag v-if="testDataPreview" size="small" effect="plain" :type="testDataPreview.kind === 'empty' ? 'info' : 'success'">
              {{ testDataPreviewLabel }}
            </el-tag>
            <span class="muted test-data-meta__hint">自动化 / 性能执行自动消费该数据</span>
          </div>
          <div v-if="testDataPreview && testDataPreview.sampleRows.length" class="pre text-mono">
            {{ testDataPreview.sampleRows.map((r) => r.join(' | ')).join('\n') }}
          </div>
          <div v-else-if="testDataPreview && testDataPreview.textSample" class="pre text-mono">
            {{ testDataPreview.textSample }}
          </div>
        </template>
      </div>

      <div v-if="detail.caseType === 'interface'" class="panel panel-pad">
        <div class="section-title">接口定义</div>
        <el-descriptions v-if="interfaceEntries.length" :column="2" border size="small">
          <el-descriptions-item v-for="item in interfaceEntries" :key="item.key" :label="item.key">
            <span class="iface-value">{{ item.value || '-' }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <el-empty v-else description="还没有填写接口URL、请求参数与断言" :image-size="60" />
      </div>
    </div>

    <el-empty v-else description="用例不存在" />

    <InterfaceResultDialog v-model="resultVisible" :result="result" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back, VideoPlay } from '@element-plus/icons-vue'
import { caseApi } from '@/api'
import type { InterfaceExecution, TestCase, TestDataPreview } from '@/api/types'
import InterfaceResultDialog from '@/components/InterfaceResultDialog.vue'
import { dateTimeText, caseSourceText, caseTypeText } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const detail = ref<TestCase | null>(null)
const loading = ref(true)
const running = ref(false)
const result = ref<InterfaceExecution | null>(null)
const resultVisible = ref(false)

const interfaceEntries = computed(() =>
  Object.entries(detail.value?.extra ?? {}).map(([key, value]) => ({ key, value })),
)

const testDataRaw = computed(() => String(detail.value?.extra?.['测试数据'] ?? ''))
const previewLoading = ref(false)
const testDataPreview = ref<TestDataPreview | null>(null)
const testDataPreviewLabel = computed(() => {
  const p = testDataPreview.value
  if (!p) return ''
  if (p.kind === 'empty') return '空数据'
  const labels: Record<string, string> = {
    excel: 'Excel', json: 'JSON', 'inline-json': 'JSON', sql: 'SQL', txt: '文本', 'inline-text': '文本',
  }
  const base = labels[p.kind] ?? p.kind
  return p.rowCount > 0 ? `${base} · ${p.rowCount} 条记录` : `${base} · 文本`
})

async function loadTestDataPreview(): Promise<void> {
  previewLoading.value = true
  try {
    testDataPreview.value = await caseApi.previewTestData(testDataRaw.value)
  } catch (err) {
    testDataPreview.value = null
    ElMessage.error(err instanceof Error ? err.message : '测试数据解析失败')
  } finally {
    previewLoading.value = false
  }
}

function priorityTag(priority: string): 'danger' | 'warning' | 'info' | 'primary' {
  if (priority === 'P0') {
    return 'danger'
  }
  if (priority === 'P1') {
    return 'warning'
  }
  if (priority === 'P2') {
    return 'info'
  }
  return 'primary'
}

function statusTag(value: string): 'success' | 'danger' | 'warning' | 'info' {
  if (value === 'pass') {
    return 'success'
  }
  if (value === 'fail') {
    return 'danger'
  }
  if (value === 'blocked') {
    return 'warning'
  }
  return 'info'
}

function statusLabel(value: string): string {
  const map: Record<string, string> = {
    not_executed: '未执行',
    pass: '通过',
    fail: '失败',
    blocked: '阻塞',
  }
  return map[value] ?? value
}

async function runInterface(): Promise<void> {
  if (!detail.value) return
  running.value = true
  try {
    result.value = await caseApi.runInterface(detail.value.id)
    resultVisible.value = true
    detail.value = await caseApi.get(detail.value.id)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '接口执行失败')
  } finally {
    running.value = false
  }
}

onMounted(async () => {
  try {
    detail.value = await caseApi.get(Number(route.params.id))
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.case-detail__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.case-detail__title {
  margin: 4px 0 0;
  font-size: 17px;
}

.case-detail__tags {
  display: flex;
  gap: 6px;
}

.case-detail__desc {
  margin-bottom: 4px;
}

.section-title {
  font-weight: 600;
  font-size: 13px;
  margin: 14px 0 8px;
}

.pre {
  white-space: pre-wrap;
  line-height: 1.7;
  background: var(--aitp-subtle);
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  padding: 10px 12px;
}

.iface-value {
  white-space: pre-wrap;
  word-break: break-all;
}

.pre-line {
  white-space: pre-line;
  word-break: break-word;
}

.test-data-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 8px 0;
}

.test-data-meta__hint {
  font-size: 12px;
}
</style>
