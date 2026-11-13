<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">数据留存与归档</h2>
      <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>留存期配置</h3>
        <div class="settings-block__actions retention-actions">
          <el-switch v-model="settings.autoCleanup" active-text="启动自动清理" />
          <el-button @click="loadPreview">到期预览</el-button>
          <el-button :loading="dryRunning" @click="runDry">干跑统计</el-button>
          <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
          <el-button type="warning" plain :loading="cleaning" @click="runCleanup">立即执行清理</el-button>
        </div>
      </div>
      <el-alert type="info" :closable="false" show-icon
                title="到期数据按动作处理：归档=压缩打包到 data/archive 并软删 / 直接清理=软删 / 仅留摘要=保留头字段清正文。保留天数 0 表示永久保留。被引用数据（缺陷引用的证据、矩阵引用的执行记录与轮次）到期不清。"
                style="margin-bottom: 12px" />
      <el-table v-loading="loading" :data="settings.items" border stripe class="retention-table">
        <el-table-column prop="dataTypeLabel" label="数据类型" width="150" />
        <el-table-column label="保留天数" width="180">
          <template #default="scope">
            <el-input-number v-model="scope.row.days" :min="0" :max="3650" size="default" style="width: 100%" />
          </template>
        </el-table-column>
        <el-table-column label="到期动作" min-width="240">
          <template #default="scope">
            <el-select v-model="scope.row.action" size="default" style="width: 100%">
              <el-option v-for="opt in scope.row.actions" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="当前到期" width="90">
          <template #default="scope">
            <span v-if="scope.row.dueCount !== undefined">{{ scope.row.dueCount }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="引用保护提示" min-width="220">
          <template #default="scope">
            <div v-if="scope.row.protected?.length" class="protected-list">
              <div v-for="item in scope.row.protected.slice(0, 3)" :key="item.id" class="protected-item">
                <el-tag size="small" type="warning" effect="plain">{{ item.referencedBy }}</el-tag>
                <span class="muted">{{ item.title }}</span>
              </div>
              <span v-if="scope.row.protected.length > 3" class="muted">… 共 {{ scope.row.protected.length }} 条</span>
            </div>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="lastResult" class="cleanup-result">
        <el-alert
          :type="lastResult.totalCleaned ? 'success' : 'info'"
          :closable="false"
          show-icon
          :title="`清理完成：处理 ${lastResult.totalCleaned ?? 0} 条，引用保护跳过 ${lastResult.totalProtected ?? 0} 条`"
        />
      </div>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>归档区</h3>
        <div class="settings-block__actions">
          <el-input v-model="archiveKeyword" placeholder="检索标题 / 摘要" clearable style="width: 220px"
                    @keyup.enter="loadArchives" @clear="loadArchives" />
          <el-select v-model="archiveType" placeholder="类型" clearable style="width: 150px" @change="loadArchives">
            <el-option v-for="opt in archiveTypes" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
          <el-button size="small" @click="loadArchives">检索</el-button>
        </div>
      </div>
      <el-table v-loading="archiveLoading" :data="bundles" border stripe size="small">
        <el-table-column prop="bundleNo" label="包裹号" width="110" />
        <el-table-column label="类型" width="120">
          <template #default="scope">{{ typeLabel(scope.row.dataType) }}</template>
        </el-table-column>
        <el-table-column prop="action" label="动作" width="100">
          <template #default="scope">{{ actionLabel(scope.row.action) }}</template>
        </el-table-column>
        <el-table-column prop="itemCount" label="条数" width="70" />
        <el-table-column label="时间跨度" min-width="150">
          <template #default="scope">
            <span class="muted">{{ shortDate(scope.row.periodFrom) }} ~ {{ shortDate(scope.row.periodTo) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="90">
          <template #default="scope">{{ fileSizeText(scope.row.fileSize) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="165">
          <template #default="scope">{{ dateTimeText(scope.row.createdAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click="openBundle(scope.row)">浏览</el-button>
            <el-button link type="danger" @click="removeBundle(scope.row)">移除</el-button>
          </template>
        </el-table-column>
        <template #empty><span class="muted">暂无归档，清理到期数据后自动生成</span></template>
      </el-table>
      <div class="pager">
        <el-pagination v-model:current-page="archivePage" :page-size="20" :total="archiveTotal"
                       layout="total, prev, pager, next" @current-change="loadArchives" />
      </div>
    </div>

    <el-drawer v-model="drawerVisible" :title="`归档浏览 · ${currentBundle?.bundle.bundleNo || ''}`" size="620px">
      <div v-if="currentBundle" class="bundle-meta">
        <el-tag size="small">{{ typeLabel(currentBundle.bundle.dataType) }}</el-tag>
        <el-tag size="small" type="info" effect="plain">{{ currentBundle.bundle.itemCount }} 条</el-tag>
        <span class="muted">{{ currentBundle.bundle.filePath }}</span>
      </div>
      <el-table :data="currentBundle?.items || []" border stripe size="small" height="420">
        <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
        <el-table-column prop="summary" label="摘要" min-width="160" show-overflow-tooltip />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click="viewOriginal(scope.row)">原文</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-drawer v-model="originalVisible" title="归档原文（可复盘）" size="520px" append-to-body>
        <pre class="original-json">{{ originalJson }}</pre>
      </el-drawer>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back } from '@element-plus/icons-vue'
import { retentionApi } from '@/api'
import { dateTimeText } from '@/utils/format'
import type { ArchiveBundle, ArchiveBundleDetail, RetentionItem } from '@/api/types'

const router = useRouter()
const loading = ref(false)
const saving = ref(false)
const dryRunning = ref(false)
const cleaning = ref(false)
const settings = reactive({ items: [] as RetentionItem[], autoCleanup: false, lastRunAt: '' })
const lastResult = ref<{ totalCleaned?: number; totalProtected?: number } | null>(null)

const archiveLoading = ref(false)
const bundles = ref<ArchiveBundle[]>([])
const archiveTypes = ref<{ value: string; label: string }[]>([])
const archiveKeyword = ref('')
const archiveType = ref('')
const archivePage = ref(1)
const archiveTotal = ref(0)

const drawerVisible = ref(false)
const originalVisible = ref(false)
const currentBundle = ref<ArchiveBundleDetail | null>(null)
const originalJson = ref('')

const ACTION_LABELS: Record<string, string> = {
  archive: '归档后清理',
  delete: '直接清理',
  summary: '仅留摘要',
}

function typeLabel(value: string): string {
  return settings.items.find((item) => item.dataType === value)?.dataTypeLabel || value
}

function actionLabel(value: string): string {
  return ACTION_LABELS[value] || value
}

function shortDate(value: string): string {
  return value ? value.slice(0, 10) : '-'
}

function fileSizeText(size: number): string {
  if (!size) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

async function loadSettings(): Promise<void> {
  loading.value = true
  try {
    const payload = await retentionApi.settings()
    settings.items = payload.items
    settings.autoCleanup = payload.autoCleanup
    settings.lastRunAt = payload.lastRunAt
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载配置失败')
  } finally {
    loading.value = false
  }
}

async function loadPreview(): Promise<void> {
  loading.value = true
  try {
    const payload = await retentionApi.preview()
    settings.items = payload.items
    settings.autoCleanup = payload.autoCleanup
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '预览失败')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  try {
    const payload = await retentionApi.saveSettings({
      items: settings.items.map((item) => ({ dataType: item.dataType, days: item.days, action: item.action })),
      autoCleanup: settings.autoCleanup,
    })
    settings.items = payload.items
    ElMessage.success('留存配置已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function runDry(): Promise<void> {
  dryRunning.value = true
  try {
    const result = await retentionApi.cleanup({ dryRun: true })
    if (result.types) {
      settings.items = settings.items.map((item) => {
        const hit = result.types?.find((entry) => entry.dataType === item.dataType)
        return hit ? { ...item, dueCount: hit.due, protected: hit.protected } : item
      })
    }
    ElMessage.success('干跑统计完成（未改动数据）')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '干跑失败')
  } finally {
    dryRunning.value = false
  }
}

async function runCleanup(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '按当前配置处理到期数据：归档/清理/仅留摘要，被引用数据会跳过。是否继续？',
      '清理确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  cleaning.value = true
  try {
    await retentionApi.cleanup({})
    lastResult.value = {}
    ElMessage.success('已投递清理任务，到「任务队列」看进度')
    void loadArchives()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '清理失败')
  } finally {
    cleaning.value = false
  }
}

async function loadArchives(): Promise<void> {
  archiveLoading.value = true
  try {
    const payload = await retentionApi.archives({
      pageNum: archivePage.value,
      pageSize: 20,
      keyword: archiveKeyword.value || undefined,
      dataType: archiveType.value || undefined,
    })
    bundles.value = payload.list
    archiveTotal.value = payload.total
    archiveTypes.value = payload.dataTypes ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载归档区失败')
  } finally {
    archiveLoading.value = false
  }
}

async function openBundle(row: ArchiveBundle): Promise<void> {
  try {
    currentBundle.value = await retentionApi.bundle(row.id)
    drawerVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '打开归档失败')
  }
}

async function viewOriginal(item: { refId: number }): Promise<void> {
  if (!currentBundle.value) return
  try {
    const result = await retentionApi.original(currentBundle.value.bundle.id, item.refId)
    originalJson.value = JSON.stringify(result.original, null, 2)
    originalVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '读取原文失败')
  }
}

async function removeBundle(row: ArchiveBundle): Promise<void> {
  try {
    await ElMessageBox.confirm(`把归档包裹「${row.bundleNo}」移出检索区？（本机 zip 不删）`, '移除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await retentionApi.removeBundle(row.id)
    ElMessage.success('已移除')
    void loadArchives()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '移除失败')
  }
}

onMounted(() => {
  void loadSettings()
  void loadArchives()
})
</script>

<style scoped>
/* 启动自动清理 与 到期预览 之间留出更宽的呼吸间距 */
.retention-actions {
  gap: 14px;
}

.retention-actions :deep(.el-switch) {
  margin-right: 28px;
}

.retention-actions :deep(.el-switch__label) {
  font-size: 14px;
}

/* 留存配置表：控件与字号整体放大一档，行高随之适配 */
.retention-table {
  font-size: 14px;
}

.retention-table :deep(.el-table__header th) {
  font-size: 14px;
  padding: 12px 0;
}

.retention-table :deep(.el-table__row td) {
  padding: 12px 0;
}

.retention-table :deep(.el-input-number .el-input__inner),
.retention-table :deep(.el-select .el-input__inner) {
  font-size: 14px;
  height: 36px;
}

.retention-table :deep(.el-input-number__increase),
.retention-table :deep(.el-input-number__decrease) {
  width: 34px;
  font-size: 14px;
}

.retention-table :deep(.el-select .el-select__wrapper) {
  min-height: 36px;
  font-size: 14px;
}

.protected-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.protected-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.cleanup-result {
  margin-top: 12px;
}

.bundle-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}

.original-json {
  background: var(--el-fill-color-light);
  padding: 12px;
  border-radius: 6px;
  font-size: 12px;
  max-height: 70vh;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
</style>
