<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <h2 class="page-title">执行记录详情</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="ArrowLeft" @click="router.push('/lab/records')">返回列表</el-button>
      </div>
    </div>

    <div v-if="record" class="record-detail">
      <div class="panel panel-pad">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="能力">
            {{ record.capabilityLabel || record.capability }}
          </el-descriptions-item>
          <el-descriptions-item label="生效模型">{{ record.model || '-' }}</el-descriptions-item>
          <el-descriptions-item label="风险等级">
            <el-tag size="small" :type="riskTagType(record.riskLevel)">
              {{ riskText(record.riskLevel) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="复核状态">
            <el-tag size="small" :type="reviewTagType(record)">{{ reviewText(record) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="结论摘要" :span="2">
            {{ record.summary || '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="执行时间">{{ record.createdAt }}</el-descriptions-item>
          <el-descriptions-item label="最近更新">{{ record.updatedAt }}</el-descriptions-item>
        </el-descriptions>
      </div>

      <div class="panel panel-pad">
        <h3 class="section-title">
          证据核对
          <el-tag size="small" effect="plain">{{ record.evidence.length }} 条</el-tag>
        </h3>
        <el-table v-if="record.evidence.length" :data="record.evidence" border size="small">
          <el-table-column prop="kind" label="类型" width="100" />
          <el-table-column prop="ref" label="引用" width="140" class-name="text-mono" />
          <el-table-column prop="title" label="标题" min-width="200">
            <template #default="scope">
              {{ scope.row.title || '（库里查不到，疑似编造）' }}
            </template>
          </el-table-column>
          <el-table-column prop="verified" label="核对" width="100">
            <template #default="scope">
              <el-tag size="small" :type="scope.row.verified ? 'success' : 'danger'">
                {{ scope.row.verified ? '真实存在' : '无法核实' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="detail" label="说明" min-width="200" />
        </el-table>
        <p v-else class="muted">本次产出没有引用任何条目。</p>
      </div>

      <div class="panel panel-pad">
        <h3 class="section-title">
          信息缺项
          <el-tag size="small" effect="plain">{{ record.missing.length }} 条</el-tag>
        </h3>
        <ul v-if="record.missing.length" class="plain-list">
          <li v-for="(item, index) in record.missing" :key="index">
            <strong>{{ item.label }}</strong>
            <span class="muted">{{ item.detail }}</span>
          </li>
        </ul>
        <p v-else class="muted">未发现信息不足的表述。</p>
      </div>

      <div class="panel panel-pad">
        <h3 class="section-title">风险点</h3>
        <el-table v-if="record.risks.length" :data="record.risks" border size="small">
          <el-table-column prop="title" label="风险" width="200">
            <template #default="scope">
              <el-tag size="small" :type="scope.row.level === 'high' ? 'danger' : 'warning'">
                {{ scope.row.title }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="rule" label="规则" width="180" class-name="text-mono" />
          <el-table-column prop="detail" label="说明" min-width="200" />
        </el-table>
        <p v-else class="muted">未命中行为安全规则。</p>
      </div>

      <div class="panel panel-pad">
        <h3 class="section-title">原始输入</h3>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="提示词">
            <span class="text-mono">{{ record.promptCode || '-' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="耗时">
            {{ record.latencyMs != null ? `${record.latencyMs} ms` : '-' }}
          </el-descriptions-item>
        </el-descriptions>
        <pre v-if="hasInput" class="record-pre">{{ prettyInput }}</pre>
        <p v-else class="muted">本次执行没有记录输入变量。</p>
      </div>

      <div class="panel panel-pad">
        <h3 class="section-title">
          路由命中
          <el-tag size="small" effect="plain">{{ record.route.length }} 条</el-tag>
        </h3>
        <el-table v-if="record.route.length" :data="record.route" border size="small">
          <el-table-column prop="capability" label="能力" width="160" class-name="text-mono" />
          <el-table-column label="分" width="80" align="right">
            <template #default="scope">
              {{ scope.row.score != null ? Math.round(scope.row.score * 100) : '-' }}
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="理由" min-width="220" />
        </el-table>
        <p v-else class="muted">本次执行没有走智能路由（直接指定的能力）。</p>
      </div>

      <div class="panel panel-pad">
        <h3 class="section-title">
          步骤轨迹
          <el-tag size="small" effect="plain">{{ record.steps.length }} 步</el-tag>
        </h3>
        <el-table v-if="record.steps.length" :data="record.steps" border size="small">
          <el-table-column prop="order" label="#" width="56" align="center" />
          <el-table-column prop="stepName" label="步骤" min-width="150" />
          <el-table-column prop="label" label="能力" width="140" />
          <el-table-column label="状态" width="90">
            <template #default="scope">
              <el-tag size="small" :type="scope.row.status === 'success' ? 'success' : 'danger'">
                {{ scope.row.status === 'success' ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="model" label="模型" width="130" />
          <el-table-column label="耗时" width="86" align="right">
            <template #default="scope">{{ scope.row.latencyMs ?? '-' }}</template>
          </el-table-column>
          <el-table-column prop="summary" label="摘要" min-width="180" show-overflow-tooltip />
        </el-table>
        <p v-else class="muted">单次能力执行没有子步骤。</p>
      </div>

      <div class="panel panel-pad">
        <h3 class="section-title">模型用量</h3>
        <el-descriptions v-if="hasUsage" :column="3" border>
          <el-descriptions-item
            v-for="(value, key) in usageEntries"
            :key="key"
            :label="key"
          >
            {{ value }}
          </el-descriptions-item>
        </el-descriptions>
        <p v-else class="muted">本次执行没有记录用量（离线规则或本地模型不返回用量）。</p>
      </div>

      <div class="panel panel-pad">
        <h3 class="section-title">人工复核</h3>
        <el-alert
          type="warning"
          show-icon
          :closable="false"
          title="AI 的结论只作参考，上线与否由人工决定；复核结论会写入审计日志。"
          style="margin-bottom: 12px"
        />
        <el-form label-width="90px">
          <el-form-item label="复核结论">
            <el-radio-group v-model="reviewStatus">
              <el-radio value="approved">采纳</el-radio>
              <el-radio value="rejected">驳回</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="复核备注">
            <el-input
              v-model="reviewNote"
              type="textarea"
              :rows="3"
              maxlength="1000"
              show-word-limit
              placeholder="写下采纳或驳回的理由，便于后续追溯"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitReview">
              提交复核结论
            </el-button>
            <span v-if="record.reviewedAt" class="muted" style="margin-left: 12px">
              最近复核：{{ record.reviewedAt }}
            </span>
          </el-form-item>
        </el-form>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { recordApi } from '@/api'
import type { AiRecord } from '@/api/types'

const route = useRoute()
const router = useRouter()

const record = ref<AiRecord | null>(null)
const loading = ref(false)
const submitting = ref(false)
const reviewStatus = ref<'approved' | 'rejected'>('approved')
const reviewNote = ref('')

const hasInput = computed(() => Object.keys(record.value?.input ?? {}).length > 0)
const prettyInput = computed(() => JSON.stringify(record.value?.input ?? {}, null, 2))

const hasUsage = computed(() => Object.keys(record.value?.usage ?? {}).length > 0)
const usageEntries = computed(() =>
  Object.entries(record.value?.usage ?? {}).map(([key, value]) => ({
    key,
    value: typeof value === 'object' ? JSON.stringify(value) : String(value),
  })),
)

function riskText(level: string): string {
  if (level === 'high') return '高风险'
  if (level === 'low') return '低风险'
  return '无风险'
}

function riskTagType(level: string): 'danger' | 'warning' | 'info' {
  if (level === 'high') return 'danger'
  if (level === 'low') return 'warning'
  return 'info'
}

function reviewText(row: AiRecord): string {
  if (row.reviewStatus === 'approved') return '已采纳'
  if (row.reviewStatus === 'rejected') return '已驳回'
  return row.reviewRequired ? '待复核' : '无需复核'
}

function reviewTagType(row: AiRecord): 'success' | 'danger' | 'warning' | 'info' {
  if (row.reviewStatus === 'approved') return 'success'
  if (row.reviewStatus === 'rejected') return 'danger'
  return row.reviewRequired ? 'warning' : 'info'
}

async function load(): Promise<void> {
  const id = Number(route.params.id)
  loading.value = true
  try {
    record.value = await recordApi.get(id)
    reviewStatus.value = record.value.reviewStatus === 'rejected' ? 'rejected' : 'approved'
    reviewNote.value = record.value.reviewNote ?? ''
  } catch (err) {
    record.value = null
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function submitReview(): Promise<void> {
  if (!record.value) {
    return
  }
  submitting.value = true
  try {
    await recordApi.review(record.value.id, reviewStatus.value, reviewNote.value)
    ElMessage.success('复核结论已保存')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<style scoped>
.record-detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  font-size: 15px;
}

.plain-list {
  margin: 0;
  padding-left: 18px;
  line-height: 1.9;
}

.plain-list strong {
  margin-right: 8px;
}

.record-pre {
  background: var(--aitp-subtle);
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  margin: 12px 0 0;
  max-height: 320px;
  overflow-y: auto;
}
</style>
