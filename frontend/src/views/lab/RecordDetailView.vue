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
        <h3 class="section-title">
          风险点
          <el-tag size="small" effect="plain">{{ record.risks.length }} 条</el-tag>
        </h3>
        <ul v-if="record.risks.length" class="plain-list">
          <li v-for="(item, index) in record.risks" :key="index">
            <el-tag size="small" :type="item.level === 'high' ? 'danger' : 'warning'">
              {{ item.title }}
            </el-tag>
            <span class="muted">{{ item.detail }}</span>
          </li>
        </ul>
        <p v-else class="muted">未命中行为安全规则。</p>
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
import { onMounted, ref } from 'vue'
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
</style>
