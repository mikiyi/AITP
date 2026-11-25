<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">缺陷详情</h2>
      <el-button :icon="Back" @click="router.back()">返回</el-button>
    </div>

    <el-skeleton v-if="loading" :rows="6" animated />

    <DetailNotFound
      v-else-if="notFound"
      title="缺陷不存在或不属于当前项目"
      message="缺陷可能已删除，或属于另一个项目。确认当前项目，或切换项目后重试。"
      @back="router.back()"
      @retry="loadDetail"
    />

    <div v-else-if="detail" class="bug-detail">
      <div class="panel panel-pad">
        <div class="bug-detail__head">
          <div>
            <div class="muted">{{ detail.bugNo }}</div>
            <h3 class="bug-detail__title">{{ detail.title }}</h3>
          </div>
          <div class="bug-detail__tags">
            <el-tag :type="severityTag(detail.severity)" size="small" effect="plain">{{ detail.severity }}</el-tag>
            <el-tag :type="statusTag(detail.status)" size="small" effect="plain">
              {{ statusLabel(detail.status) }}
            </el-tag>
          </div>
        </div>

        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="类型">{{ detail.bugType ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="指派">{{ detail.assignee || '-' }}</el-descriptions-item>
          <el-descriptions-item label="报告人">{{ detail.reporter || '-' }}</el-descriptions-item>
          <el-descriptions-item label="版本">{{ detail.version || '-' }}</el-descriptions-item>
          <el-descriptions-item label="环境">{{ detail.env || '-' }}</el-descriptions-item>
          <el-descriptions-item label="功能点">{{ detail.moduleId ?? '-' }}</el-descriptions-item>
        </el-descriptions>
      </div>

      <div class="panel panel-pad">
        <div class="section-title">描述</div>
        <div class="pre">{{ detail.description || '无' }}</div>
        <div class="section-title">复现步骤</div>
        <div class="pre">{{ (detail.steps || []).join('\n') || '无' }}</div>
        <div class="section-title">实际结果 / 预期结果</div>
        <div class="pre">{{ detail.actualResult || '无' }}</div>
        <div class="pre">{{ detail.expectedResult || '无' }}</div>
      </div>

      <div class="panel panel-pad">
        <div class="section-title screenshot-head">
          <span>
            缺陷截图
            <el-tag size="small" effect="plain">{{ screenshots.length }} 张</el-tag>
          </span>
          <div>
            <el-button size="small" :loading="uploading" :disabled="screenshots.length >= maxShots" @click="pickScreenshots">
              上传截图
            </el-button>
            <input ref="fileInput" class="hidden-file" type="file" multiple accept="image/png,image/jpeg,image/gif,image/webp,image/bmp" @change="onPickScreenshots" />
          </div>
        </div>
        <p class="muted screenshot-hint">
          截图存放在 data/bug-screenshots/&lt;缺陷编号&gt;/，文件名按 pic_&lt;缺陷编号&gt;_001 递增；单张 ≤ 10MB、每个缺陷最多 {{ maxShots }} 张。
          默认不进备份/项目资产包/留存归档，需要时在「备份设置」打开「备份包含缺陷截图」。
        </p>
        <el-empty v-if="!screenshots.length" description="还没有截图" :image-size="56" />
        <div v-else class="screenshot-grid">
          <div v-for="shot in screenshots" :key="shot.id" class="screenshot-item">
            <el-image
              :src="bugApi.screenshotUrl(detail.id, shot.id)"
              :preview-src-list="previewUrls"
              :initial-index="previewIndex(shot.id)"
              fit="cover"
              preview-teleported
              class="screenshot-item__img"
            >
              <template #error>
                <div class="screenshot-item__error">加载失败</div>
              </template>
            </el-image>
            <div class="screenshot-item__meta" :title="shot.fileName">{{ shot.fileName }}</div>
            <div class="screenshot-item__actions">
              <el-button link type="primary" size="small" @click="downloadScreenshot(shot)">下载</el-button>
              <el-button link type="danger" size="small" @click="removeScreenshot(shot)">删除</el-button>
            </div>
          </div>
        </div>
      </div>

      <div class="panel panel-pad">
        <AttachmentManager
          owner-type="bug"
          :owner-id="detail.id"
          title="缺陷附件"
          hint="文档、日志、接口报文、录屏与图片都可以挂在这里，随缺陷长期保留；仅图片证据也可直接用上方截图。"
        />
      </div>

      <div v-if="detail.linkedCases?.length" class="panel panel-pad">
        <div class="section-title">关联用例</div>
        <el-table :data="detail.linkedCases" size="small" border>
          <el-table-column label="编号" width="120">
            <template #default="{ row }">
              <el-button link type="primary" @click="openCase(row.id)">{{ row.caseNo }}</el-button>
            </template>
          </el-table-column>
          <el-table-column prop="title" label="用例标题" min-width="240" show-overflow-tooltip />
          <el-table-column prop="priority" label="优先级" width="90" />
          <el-table-column label="当前状态" width="110">
            <template #default="{ row }">{{ caseStatusLabel(row.status) }}</template>
          </el-table-column>
        </el-table>
      </div>

      <div class="panel panel-pad">
        <div class="section-title">
          执行证据
          <el-tag size="small" effect="plain">{{ evidences.length }} 条</el-tag>
        </div>
        <el-table :data="evidences" size="small" border>
          <el-table-column label="证据编号" width="104">
            <template #default="{ row }">EV-{{ row.evidenceId ?? '-' }}</template>
          </el-table-column>
          <el-table-column label="来源" width="106">
            <template #default="{ row }">{{ row.sourceTypeLabel || row.sourceType || '-' }}</template>
          </el-table-column>
          <el-table-column prop="title" label="用例" min-width="180" show-overflow-tooltip />
          <el-table-column label="结论" width="86">
            <template #default="{ row }">
              <el-tag :type="row.result === 'passed' ? 'success' : row.result === 'failed' ? 'danger' : 'warning'" size="small" effect="plain">
                {{ row.resultLabel || row.result || '-' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="断言" width="104">
            <template #default="{ row }">
              {{ row.assertionSummary?.passed ?? 0 }}/{{ row.assertionSummary?.total ?? 0 }} 通过
            </template>
          </el-table-column>
          <el-table-column label="执行时间" width="160">
            <template #default="{ row }">{{ dateTimeText(row.executedAt) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="104" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openEvidence(row)">查看证据</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <span class="muted">{{ evidenceHint }}</span>
          </template>
        </el-table>
      </div>

      <div class="panel panel-pad">
        <div class="section-title">状态流转</div>
        <div class="toolbar">
          <el-select v-model="nextStatus" placeholder="选择目标状态" style="width: 180px">
            <el-option
              v-for="item in allowedTransitions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <el-input v-model="comment" placeholder="流转说明（选填）" style="flex: 1" />
          <el-button type="primary" :loading="transitioning" @click="doTransition">流转</el-button>
        </div>

        <el-table :data="logs" size="small" border empty-text="暂无流转记录">
          <el-table-column prop="fromStatus" label="原状态" width="110" />
          <el-table-column prop="toStatus" label="新状态" width="110" />
          <el-table-column prop="operator" label="操作人" width="120" />
          <el-table-column prop="comment" label="说明" min-width="200" />
          <el-table-column label="时间" width="170">
            <template #default="{ row }">{{ dateTimeText(row.createdAt) }}</template>
          </el-table-column>
        </el-table>
      </div>
      <EvidenceDrawer v-model="evidenceVisible" :locator="evidenceLocator" title="执行证据" />
    </div>

    <el-empty v-else description="缺陷不存在" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back } from '@element-plus/icons-vue'
import EvidenceDrawer from '@/components/EvidenceDrawer.vue'
import AttachmentManager from '@/components/AttachmentManager.vue'
import { bugApi } from '@/api'
import DetailNotFound from '@/components/DetailNotFound.vue'
import { ApiError, ErrorCode } from '@/api/request'
import type { Bug, BugLog, BugScreenshot, ExecutionEvidence } from '@/api/types'
import { dateTimeText } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const detail = ref<Bug | null>(null)
const logs = ref<BugLog[]>([])
const screenshots = ref<BugScreenshot[]>([])
const evidences = ref<ExecutionEvidence[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const maxShots = 30
const maxShotBytes = 10 * 1024 * 1024
const evidenceHint = ref('')
const evidenceVisible = ref(false)
const notFound = ref(false)
const evidenceLocator = ref<Record<string, string | number>>({})
const loading = ref(true)
const nextStatus = ref('')
const comment = ref('')
const transitioning = ref(false)

const transitions: Record<string, string[]> = {
  open: ['assigned', 'fixed', 'rejected', 'closed'],
  assigned: ['open', 'fixed', 'rejected', 'closed'],
  fixed: ['retest', 'reopened', 'closed'],
  retest: ['fixed', 'closed', 'reopened'],
  reopened: ['assigned', 'fixed', 'closed'],
  closed: ['reopened'],
  rejected: ['open', 'closed'],
}

const labels: Record<string, string> = {
  open: '新建',
  assigned: '已指派',
  fixed: '已修复',
  retest: '待验证',
  reopened: '重新打开',
  closed: '已关闭',
  rejected: '已拒绝',
}

const allowedTransitions = computed(() =>
  (transitions[detail.value?.status ?? 'open'] ?? []).map((value) => ({
    value,
    label: labels[value] ?? value,
  })),
)

function statusLabel(value: string): string {
  return labels[value] ?? value
}

function caseStatusLabel(value: string): string {
  return { not_executed: '未执行', pass: '通过', fail: '失败', blocked: '阻塞' }[value] ?? value
}

function openCase(id: number): void {
  void router.push({ name: 'test-case-detail', params: { id } })
}

function severityTag(value: string): 'danger' | 'warning' | 'info' | 'primary' {
  if (value === 'P0') {
    return 'danger'
  }
  if (value === 'P1') {
    return 'warning'
  }
  if (value === 'P2') {
    return 'info'
  }
  return 'primary'
}

function statusTag(value: string): 'success' | 'danger' | 'warning' | 'info' | 'primary' {
  if (value === 'closed') {
    return 'success'
  }
  if (value === 'rejected') {
    return 'info'
  }
  if (value === 'reopened') {
    return 'danger'
  }
  if (value === 'fixed') {
    return 'primary'
  }
  return 'warning'
}

async function loadLogs(): Promise<void> {
  try {
    logs.value = await bugApi.logs(Number(route.params.id))
  } catch {
    logs.value = []
  }
}

async function loadEvidences(): Promise<void> {
  try {
    const data = await bugApi.evidence(Number(route.params.id))
    evidences.value = data.list ?? []
    evidenceHint.value =
      data.hint || '这条缺陷还没有关联的执行证据，可从失败的轮次用例用「转缺陷」重新建立关联。'
  } catch {
    evidences.value = []
    evidenceHint.value = '执行证据暂时读不到，其余信息不受影响。'
  }
}

function openEvidence(row: ExecutionEvidence): void {
  evidenceLocator.value = row.evidenceId
    ? { evidenceId: Number(row.evidenceId) }
    : { bugId: Number(route.params.id) }
  evidenceVisible.value = true
}

const previewUrls = computed(() =>
  screenshots.value.map((shot) => bugApi.screenshotUrl(Number(detail.value?.id ?? 0), shot.id)),
)

function previewIndex(id: number): number {
  return Math.max(0, screenshots.value.findIndex((shot) => shot.id === id))
}

function pickScreenshots(): void {
  fileInput.value?.click()
}

async function onPickScreenshots(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (!detail.value || !files.length) {
    return
  }
  if (screenshots.value.length + files.length > maxShots) {
    ElMessage.warning(`每个缺陷最多 ${maxShots} 张截图（当前已有 ${screenshots.value.length} 张）`)
    return
  }
  const oversize = files.find((file) => file.size > maxShotBytes)
  if (oversize) {
    ElMessage.warning(`截图「${oversize.name}」超过 10MB 限制`)
    return
  }
  uploading.value = true
  try {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    const res = await bugApi.uploadScreenshots(detail.value.id, form)
    screenshots.value = [...screenshots.value, ...res.list].sort((a, b) => a.seq - b.seq)
    ElMessage.success(`已上传 ${res.list.length} 张截图`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '上传截图失败')
  } finally {
    uploading.value = false
  }
}

function downloadScreenshot(shot: BugScreenshot): void {
  const link = document.createElement('a')
  link.href = bugApi.screenshotUrl(Number(detail.value?.id ?? 0), shot.id)
  link.download = shot.fileName
  document.body.appendChild(link)
  link.click()
  link.remove()
}

async function removeScreenshot(shot: BugScreenshot): Promise<void> {
  if (!detail.value) {
    return
  }
  try {
    await bugApi.removeScreenshot(detail.value.id, shot.id)
    screenshots.value = screenshots.value.filter((item) => item.id !== shot.id)
    ElMessage.success(`已删除 ${shot.fileName}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除截图失败')
  }
}

async function doTransition(): Promise<void> {
  if (!nextStatus.value) {
    ElMessage.warning('请选择目标状态')
    return
  }
  transitioning.value = true
  try {
    await bugApi.transition(Number(route.params.id), {
      toStatus: nextStatus.value,
      comment: comment.value,
    })
    ElMessage.success('流转成功')
    nextStatus.value = ''
    comment.value = ''
    detail.value = await bugApi.get(Number(route.params.id))
    await loadLogs()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '流转失败')
  } finally {
    transitioning.value = false
  }
}

async function loadDetail(): Promise<void> {
  loading.value = true
  notFound.value = false
  try {
    detail.value = await bugApi.get(Number(route.params.id))
    screenshots.value = detail.value.screenshots ?? []
    await loadLogs()
    await loadEvidences()
  } catch (err) {
    if (err instanceof ApiError && err.code === ErrorCode.RESOURCE_NOT_FOUND) {
      detail.value = null
      notFound.value = true
      return
    }
    ElMessage.error(err instanceof Error ? err.message : '加载缺陷失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadDetail()
})
</script>

<style scoped>
.bug-detail__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.bug-detail__title {
  margin: 4px 0 0;
  font-size: 17px;
}

.bug-detail__tags {
  display: flex;
  gap: 6px;
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

.screenshot-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 0;
}

.screenshot-hint {
  margin: 0 0 10px;
  line-height: 1.6;
}

.hidden-file {
  display: none;
}

.screenshot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
}

.screenshot-item {
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  overflow: hidden;
  background: var(--aitp-subtle);
}

.screenshot-item__img {
  width: 100%;
  height: 110px;
  display: block;
  cursor: zoom-in;
}

.screenshot-item__error {
  height: 110px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.screenshot-item__meta {
  padding: 6px 8px 0;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.screenshot-item__actions {
  display: flex;
  justify-content: flex-end;
  padding: 2px 6px 6px;
}
</style>
