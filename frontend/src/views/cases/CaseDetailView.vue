<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">测试用例</h2>
      <el-button :icon="Back" @click="router.back()">返回</el-button>
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
            <el-tag size="small" effect="plain">{{ detail.caseType }}</el-tag>
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
          <el-descriptions-item label="来源">{{ detail.source ?? '-' }}</el-descriptions-item>
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
          <el-table-column prop="action" label="操作步骤" min-width="220" />
          <el-table-column prop="expected" label="预期结果" min-width="220" />
        </el-table>

        <div class="section-title">整体预期结果</div>
        <div class="pre">{{ detail.expectedResult || '无' }}</div>
      </div>
    </div>

    <el-empty v-else description="用例不存在" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Back } from '@element-plus/icons-vue'
import { caseApi } from '@/api'
import type { TestCase } from '@/api/types'
import { dateTimeText } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const detail = ref<TestCase | null>(null)
const loading = ref(true)

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
</style>
