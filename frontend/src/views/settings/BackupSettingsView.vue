<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">数据备份与还原设置</h2>
      <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>自动备份</h3>
        <div class="settings-block__actions">
          <el-button size="small" :loading="autoRunning" @click="runAutoBackupNow">
            立即执行一次
          </el-button>
          <el-button type="primary" size="small" :loading="autoSaving" @click="saveAutoPlan">
            保存计划
          </el-button>
        </div>
      </div>
      <el-form :model="autoForm" label-width="110px" class="auto-backup__form">
        <el-form-item label="启用自动备份">
          <el-switch v-model="autoForm.enabled" />
          <span class="muted auto-backup__inline">关闭后只保留手动备份</span>
        </el-form-item>
        <el-form-item label="备份频率">
          <el-radio-group v-model="autoForm.frequency">
            <el-radio value="daily">每天</el-radio>
            <el-radio value="weekly">每周</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="autoForm.frequency === 'weekly'" label="星期">
          <el-select v-model="autoForm.weekday" style="width: 120px">
            <el-option
              v-for="item in WEEKDAYS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="执行时间">
          <el-time-select
            v-model="autoForm.time"
            start="00:00"
            step="00:30"
            end="23:30"
            style="width: 160px"
          />
          <span class="muted auto-backup__inline">按本机时间</span>
        </el-form-item>
        <el-form-item label="保留份数">
          <el-input-number v-model="autoForm.retention" :min="1" :max="90" />
          <span class="muted auto-backup__inline">超出后只删除最旧的自动备份</span>
        </el-form-item>
        <el-form-item label="磁盘保留空间">
          <el-input-number v-model="autoForm.minFreeMb" :min="0" :max="102400" :step="128" />
          <span class="muted auto-backup__inline">MB，剩余空间低于该值时跳过并告警</span>
        </el-form-item>
      </el-form>
      <el-descriptions :column="3" border size="small" class="auto-backup__status">
        <el-descriptions-item label="下次执行">
          {{ autoPlan?.nextRunAt || (autoPlan?.enabled ? '未计算' : '已关闭') }}
        </el-descriptions-item>
        <el-descriptions-item label="最近执行">{{ autoPlan?.lastRunAt || '尚未执行' }}</el-descriptions-item>
        <el-descriptions-item label="最近结果">
          <el-tag size="small" effect="plain" :type="autoStatusType">
            {{ autoPlan?.lastStatusLabel || '尚未执行' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="自动备份">
          {{ autoPlan?.autoCount ?? 0 }} 份 / {{ formatSize(autoPlan?.autoBytes ?? 0) }}
        </el-descriptions-item>
        <el-descriptions-item label="磁盘剩余">
          {{ formatDiskFree(autoPlan?.diskFreeBytes ?? -1) }}
        </el-descriptions-item>
        <el-descriptions-item label="最近说明">{{ autoPlan?.lastMessage || '-' }}</el-descriptions-item>
      </el-descriptions>
      <p class="muted settings-block__hint">
        定时任务在后台队列执行，结果写审计与通知；服务停机错过的时间点会在下次启动时补跑一次。
        磁盘剩余空间不足时跳过本次备份并发出告警，不会删除任何已有备份。
      </p>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>备份概览</h3>
        <el-button type="primary" size="small" :loading="backupCreating" @click="createBackup">
          立即备份
        </el-button>
      </div>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="数据库文件">{{ dbPath || '-' }}</el-descriptions-item>
        <el-descriptions-item label="备份目录">{{ backupDir || '-' }}</el-descriptions-item>
        <el-descriptions-item label="库备份数量">{{ backups.length }} 份</el-descriptions-item>
        <el-descriptions-item label="整包数量">{{ bundles.length }} 份</el-descriptions-item>
        <el-descriptions-item label="最近备份">{{ backups.length ? dateTimeText(backups[0].createdAt) : '暂无' }}</el-descriptions-item>
      </el-descriptions>
      <p class="muted settings-block__hint">
        备份用 SQLite 快照生成单文件，包含项目、需求、用例、缺陷、知识、报告、AI 记录与配置；
        API Key 存在系统密钥库，不随数据库备份。换机迁移请用下方「整包备份」。
      </p>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>整包备份</h3>
        <div class="settings-block__actions">
          <el-button type="primary" size="small" plain :loading="bundleCreating" @click="createBundleBackup">
            立即整包备份
          </el-button>
          <el-button size="small" :icon="Refresh" :loading="loading" @click="loadBackups">刷新</el-button>
        </div>
      </div>

      <el-table v-loading="loading" :data="bundles" size="small" border max-height="300">
        <el-table-column prop="name" label="整包文件" min-width="240" />
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ dateTimeText(row.createdAt) }}</template>
        </el-table-column>
        <el-table-column label="大小" width="100" align="right">
          <template #default="{ row }">{{ formatSize(row.sizeBytes) }}</template>
        </el-table-column>
        <el-table-column label="类型" width="130">
          <template #default="{ row }">
            <el-tag :type="row.isAuto ? 'primary' : row.isSafety ? 'warning' : 'success'" size="small" effect="plain">
              {{ row.isAuto ? '自动整包' : row.isSafety ? '恢复前自动整包' : '手动整包' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="downloadBackup(row.name)">下载</el-button>
            <el-button link type="warning" @click="restore(row.name)">恢复</el-button>
            <el-button link type="danger" @click="removeBackup(row.name)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">还没有整包备份，点「立即整包备份」生成第一份</span>
        </template>
      </el-table>
      <p class="muted settings-block__hint">
        整包在数据库快照之外，还会打包附件、自动化脚本、生成物、密钥密文与知识库 Markdown 正文，
        一个 zip 带走全部资产，适合换机迁移和长期归档。
        密钥在当前 Windows 用户下加密，整包换到别的机器或别的用户账号后解不开，需要在「模型与密钥」里重新填一次。
      </p>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>备份列表</h3>
        <div class="settings-block__actions">
          <el-upload
            :show-file-list="false"
            :http-request="uploadBackup"
            accept=".db,.sqlite,.sqlite3"
          >
            <el-button size="small" :icon="Upload" :loading="uploading">上传备份文件</el-button>
          </el-upload>
          <el-button size="small" :icon="Refresh" :loading="loading" @click="loadBackups">刷新</el-button>
        </div>
      </div>

      <el-table v-loading="loading" :data="backups" size="small" border max-height="380">
        <el-table-column prop="name" label="备份文件" min-width="240" />
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ dateTimeText(row.createdAt) }}</template>
        </el-table-column>
        <el-table-column label="大小" width="100" align="right">
          <template #default="{ row }">{{ formatSize(row.sizeBytes) }}</template>
        </el-table-column>
        <el-table-column label="类型" width="130">
          <template #default="{ row }">
            <el-tag :type="row.isSafety ? 'warning' : row.name.startsWith('upload-') ? 'success' : 'info'" size="small" effect="plain">
              {{ backupType(row) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="downloadBackup(row.name)">下载</el-button>
            <el-button link type="warning" @click="restore(row.name)">恢复</el-button>
            <el-button link type="danger" @click="removeBackup(row.name)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">还没有备份文件，点「立即备份」生成第一份</span>
        </template>
      </el-table>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>还原设置</h3>
      </div>
      <el-alert
        type="warning"
        show-icon
        :closable="false"
        title="恢复会用所选备份覆盖当前数据，正在录入的内容都会回到备份时的状态"
        description="恢复前系统自动生成一份 pre-restore 安全备份（整包恢复还会额外存一份 pre-bundle 整包）；恢复后自动补齐缺失的数据库迁移。"
        class="settings-backup__alert"
      />
      <ul class="settings-list">
        <li>本机生成的备份：在列表里直接点「恢复」，输入备份文件名确认即可。</li>
        <li>从其他机器迁入：先「上传备份文件」，校验通过后会在列表里出现 upload- 开头的记录，再点「恢复」。</li>
        <li>上传文件必须是 AITP 的 SQLite 库（含 sys_schema_version 表），完整性校验不通过会被拒绝。</li>
        <li>整包恢复除数据库外还会整体覆盖附件、脚本、生成物、密钥与知识库正文目录，需要额外输入「覆盖本机文件」才会执行。</li>
        <li>恢复后无需重启服务，刷新页面即可看到恢复后的数据。</li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { Back, Refresh, Upload } from '@element-plus/icons-vue'
import { settingsApi } from '@/api'
import type { AutoBackupForm, AutoBackupPlan, DbBackup } from '@/api/types'
import { dateTimeText } from '@/utils/format'

const router = useRouter()

const backups = ref<DbBackup[]>([])
const bundles = ref<DbBackup[]>([])
const backupDir = ref('')
const dbPath = ref('')
const loading = ref(false)
const backupCreating = ref(false)
const bundleCreating = ref(false)
const uploading = ref(false)

const WEEKDAYS = [
  { value: 1, label: '周一' },
  { value: 2, label: '周二' },
  { value: 3, label: '周三' },
  { value: 4, label: '周四' },
  { value: 5, label: '周五' },
  { value: 6, label: '周六' },
  { value: 7, label: '周日' },
]

const autoPlan = ref<AutoBackupPlan | null>(null)
const autoForm = ref<AutoBackupForm>({
  enabled: true,
  frequency: 'daily',
  time: '02:00',
  weekday: 1,
  retention: 7,
  minFreeMb: 512,
})
const autoSaving = ref(false)
const autoRunning = ref(false)

const autoStatusType = computed<'success' | 'warning' | 'danger' | 'info'>(() => {
  switch (autoPlan.value?.lastStatus) {
    case 'success':
      return 'success'
    case 'skipped':
      return 'warning'
    case 'failed':
      return 'danger'
    default:
      return 'info'
  }
})

const BUNDLE_CONFIRM_TEXT = '覆盖本机文件'

function formatSize(size: number): string {
  if (!size) {
    return '0 B'
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function formatDiskFree(size: number): string {
  return size < 0 ? '读取失败' : formatSize(size)
}

function backupType(row: DbBackup): string {
  if (row.isSafety) {
    return '恢复前自动备份'
  }
  return row.name.startsWith('upload-') ? '上传的备份' : '手动备份'
}

function applyPlan(plan: AutoBackupPlan): void {
  autoPlan.value = plan
  autoForm.value = {
    enabled: plan.enabled,
    frequency: plan.frequency,
    time: plan.time,
    weekday: plan.weekday,
    retention: plan.retention,
    minFreeMb: plan.minFreeMb,
  }
}

async function loadBackups(): Promise<void> {
  loading.value = true
  try {
    const result = await settingsApi.backups()
    backups.value = result.list ?? []
    bundles.value = result.bundles ?? []
    backupDir.value = result.dir
    dbPath.value = result.dbPath
    if (result.auto) {
      applyPlan(result.auto)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载备份列表失败')
  } finally {
    loading.value = false
  }
}

async function saveAutoPlan(): Promise<void> {
  autoSaving.value = true
  try {
    const plan = await settingsApi.saveAutoBackup({ ...autoForm.value })
    applyPlan(plan)
    ElMessage.success(plan.enabled ? `已保存，下次执行：${plan.nextRunAt}` : '已保存，自动备份已关闭')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存自动备份计划失败')
  } finally {
    autoSaving.value = false
  }
}

async function runAutoBackupNow(): Promise<void> {
  autoRunning.value = true
  try {
    const task = await settingsApi.runAutoBackupNow()
    ElMessage.success(`已提交备份任务（${task.statusLabel || '排队中'}），完成后可在本页刷新查看`)
    window.setTimeout(() => {
      void loadBackups()
    }, 2500)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交自动备份失败')
  } finally {
    autoRunning.value = false
  }
}

async function createBackup(): Promise<void> {
  backupCreating.value = true
  try {
    const info = await settingsApi.createBackup()
    ElMessage.success(`已备份：${info.name}`)
    await loadBackups()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '备份失败')
  } finally {
    backupCreating.value = false
  }
}

async function createBundleBackup(): Promise<void> {
  bundleCreating.value = true
  try {
    const info = await settingsApi.createBundleBackup()
    ElMessage.success(`已整包备份：${info.name}（${info.files ?? 0} 个文件）`)
    await loadBackups()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '整包备份失败')
  } finally {
    bundleCreating.value = false
  }
}

async function uploadBackup(options: UploadRequestOptions): Promise<void> {
  uploading.value = true
  try {
    const form = new FormData()
    form.append('file', options.file)
    const info = await settingsApi.uploadBackup(form)
    ElMessage.success(`已上传：${info.name}，可在列表中选择恢复`)
    await loadBackups()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '上传失败')
  } finally {
    uploading.value = false
  }
}

function downloadBackup(name: string): void {
  window.open(settingsApi.backupDownloadUrl(name), '_blank')
}

async function restore(name: string): Promise<void> {
  const isBundle = name.endsWith('.zip')
  let confirm = ''
  try {
    const result = await ElMessageBox.prompt(
      isBundle
        ? `恢复会用整包「${name}」覆盖当前数据库，并整体替换附件、脚本、生成物、密钥与知识库正文。请输入备份文件名确认。`
        : `恢复会用「${name}」覆盖当前数据库，正在录入的用例与缺陷都会回到备份时的状态。请输入备份文件名确认。`,
      '恢复确认',
      {
        inputPlaceholder: name,
        type: 'warning',
        inputValidator: (value) => (value === name ? true : '请完整输入备份文件名'),
      },
    )
    confirm = String(result.value ?? '')
  } catch {
    return
  }
  let confirmFiles = ''
  if (isBundle) {
    try {
      const result = await ElMessageBox.prompt(
        `整包恢复还会覆盖本机的附件与知识库正文目录，无法回退。请输入「${BUNDLE_CONFIRM_TEXT}」放行。`,
        '整包恢复确认',
        {
          inputPlaceholder: BUNDLE_CONFIRM_TEXT,
          type: 'warning',
          inputValidator: (value) => (value === BUNDLE_CONFIRM_TEXT ? true : `请完整输入「${BUNDLE_CONFIRM_TEXT}」`),
        },
      )
      confirmFiles = String(result.value ?? '')
    } catch {
      return
    }
  }
  try {
    const result = await settingsApi.restoreBackup(name, confirm, confirmFiles || undefined)
    const files = Object.entries(result.restoredFiles ?? {}).map(([key, count]) => `${key} ${count}`)
    ElMessage.success(
      isBundle
        ? `已整包恢复，恢复前的数据保存在 ${result.safetyBackup}${files.length ? `，共覆盖 ${files.join('、')}` : ''}`
        : `已恢复，恢复前的数据保存在 ${result.safetyBackup}`,
    )
    await loadBackups()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '恢复失败')
  }
}

async function removeBackup(name: string): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除备份文件「${name}」？删除后无法找回。`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await settingsApi.deleteBackup(name)
    ElMessage.success('已删除')
    await loadBackups()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

onMounted(() => {
  void loadBackups()
})
</script>

<style scoped>
.settings-block {
  margin-bottom: 16px;
}

.settings-block__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.settings-block__head h3 {
  margin: 0;
  font-size: 15px;
}

.settings-block__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.settings-block__hint {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.6;
}

.settings-backup__alert {
  margin-bottom: 10px;
}

.auto-backup__form {
  max-width: 760px;
}

.auto-backup__inline {
  margin-left: 10px;
  font-size: 12px;
}

.auto-backup__status {
  margin-top: 6px;
}

.settings-list {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.9;
  color: var(--aitp-text-weak);
}
</style>
