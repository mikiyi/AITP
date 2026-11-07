<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">版本记录</h2>
      <el-button :icon="Back" @click="router.back()">返回</el-button>
    </div>

    <el-skeleton v-if="loading" :rows="5" animated />

    <div v-else class="version-page">
      <div class="panel panel-pad">
        <el-table :data="versions" size="small" border>
          <el-table-column prop="version" label="版本" width="80">
            <template #default="scope">v{{ scope.row.version }}</template>
          </el-table-column>
          <el-table-column prop="title" label="标题" min-width="200" />
          <el-table-column prop="reviewStatus" label="状态" width="110">
            <template #default="scope">
              <el-tag :type="statusType(scope.row.reviewStatus)" size="small" effect="plain">
                {{ statusLabel(scope.row.reviewStatus) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reviewer" label="审核人" width="110" />
          <el-table-column prop="changeLog" label="变更说明" min-width="200">
            <template #default="scope">
              <span>{{ scope.row.changeLog }}</span>
              <el-tag
                v-if="scope.row.mergeTargets?.length"
                size="small"
                type="warning"
                effect="plain"
                class="merge-tag"
              >
                合并 {{ scope.row.mergeTargets.length }} 条
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="170">
            <template #default="{ row }">{{ dateTimeText(row.createdAt) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="190">
            <template #default="scope">
              <el-button link type="primary" @click="viewVersion(scope.row.version)">查看</el-button>
              <el-button link type="primary" @click="showDiff(scope.row.version)">与当前对比</el-button>
              <el-button
                v-if="scope.row.reviewStatus === 'pending'"
                link
                type="success"
                @click="reviewVersion(scope.row.version, 'approve')"
              >
                通过
              </el-button>
              <el-button
                v-if="scope.row.reviewStatus === 'pending'"
                link
                type="danger"
                @click="reviewVersion(scope.row.version, 'reject')"
              >
                驳回
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="diffRows.length" class="panel panel-pad">
        <div class="section-title">版本差异{{ diffLabel ? `（${diffLabel}）` : '' }}</div>
        <pre class="diff-view">{{ diffText }}</pre>
      </div>
    </div>

    <el-drawer v-model="viewerVisible" :title="`版本 v${viewingVersion}`" size="46%">
      <MarkdownView v-if="viewingContent" :source="viewingContent" />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back } from '@element-plus/icons-vue'
import MarkdownView from '@/components/MarkdownView.vue'
import { knowledgeApi } from '@/api'
import type { KnowledgeVersion } from '@/api/types'
import { dateTimeText } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const id = computed(() => Number(route.params.id))

const versions = ref<KnowledgeVersion[]>([])
const loading = ref(true)
const diffRows = ref<string[]>([])
const diffLabel = ref('')

const viewerVisible = ref(false)
const viewingVersion = ref(0)
const viewingContent = ref('')

const diffText = computed(() => diffRows.value.join('\n'))

function statusLabel(status: string): string {
  if (status === 'published') {
    return '已发布'
  }
  return status === 'rejected' ? '已驳回' : '待审核'
}

function statusType(status: string): 'success' | 'warning' | 'danger' {
  if (status === 'published') {
    return 'success'
  }
  return status === 'rejected' ? 'danger' : 'warning'
}

async function load(): Promise<void> {
  try {
    versions.value = await knowledgeApi.listVersions(id.value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载版本失败')
  } finally {
    loading.value = false
  }
}

let viewSeq = 0

async function viewVersion(version: number): Promise<void> {
  const seq = ++viewSeq
  try {
    const payload = await knowledgeApi.getVersion(id.value, version)
    if (seq !== viewSeq) {
      return
    }
    viewingVersion.value = version
    viewingContent.value = payload.content ?? ''
    viewerVisible.value = true
  } catch (err) {
    if (seq === viewSeq) {
      ElMessage.error(err instanceof Error ? err.message : '加载版本失败')
    }
  }
}

async function showDiff(version: number): Promise<void> {
  const current = versions.value.find((v) => v.reviewStatus === 'published')
  const from = current?.version ?? version
  if (from === version) {
    ElMessage.info('该版本即当前生效版本，无差异')
    return
  }
  try {
    const payload = (await knowledgeApi.diffVersions(id.value, from, version)) as {
      diff?: string[]
    }
    diffRows.value = payload?.diff ?? []
    diffLabel.value = `v${from} → v${version}`
    if (!diffRows.value.length) {
      ElMessage.info('两个版本内容一致')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '生成差异失败')
  }
}

async function reviewVersion(version: number, action: 'approve' | 'reject'): Promise<void> {
  try {
    await knowledgeApi.reviewVersion(id.value, version, action)
    ElMessage.success(action === 'approve' ? '版本已发布' : '版本已驳回')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '审核失败')
  }
}

onMounted(() => {
  void load()
})
</script>

<style scoped>
.merge-tag {
  margin-left: 6px;
}

.section-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 8px;
}

.diff-view {
  background: var(--aitp-subtle);
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  padding: 10px 12px;
  max-height: 460px;
  overflow: auto;
  white-space: pre-wrap;
  line-height: 1.6;
}
</style>
