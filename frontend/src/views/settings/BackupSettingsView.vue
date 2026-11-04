<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">数据备份与还原设置</h2>
      <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
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
        <el-descriptions-item label="备份数量">{{ backups.length }} 份</el-descriptions-item>
        <el-descriptions-item label="最近备份">{{ backups[0]?.createdAt || '暂无' }}</el-descriptions-item>
      </el-descriptions>
      <p class="muted settings-block__hint">
        备份用 SQLite 快照生成单文件，包含项目、需求、用例、缺陷、知识、报告、AI 记录与配置；
        API Key 存在系统密钥库，不随数据库备份。
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
        <el-table-column prop="createdAt" label="时间" width="170" />
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
        title="恢复会用所选备份覆盖当前数据库，正在录入的内容都会回到备份时的状态"
        description="恢复前系统自动生成一份 pre-restore 安全备份；恢复后自动补齐缺失的数据库迁移。"
        class="settings-backup__alert"
      />
      <ul class="settings-list">
        <li>本机生成的备份：在列表里直接点「恢复」，输入备份文件名确认即可。</li>
        <li>从其他机器迁入：先「上传备份文件」，校验通过后会在列表里出现 upload- 开头的记录，再点「恢复」。</li>
        <li>上传文件必须是 AITP 的 SQLite 库（含 sys_schema_version 表），完整性校验不通过会被拒绝。</li>
        <li>恢复后无需重启服务，刷新页面即可看到恢复后的数据。</li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { Back, Refresh, Upload } from '@element-plus/icons-vue'
import { settingsApi } from '@/api'
import type { DbBackup } from '@/api/types'

const router = useRouter()

const backups = ref<DbBackup[]>([])
const backupDir = ref('')
const dbPath = ref('')
const loading = ref(false)
const backupCreating = ref(false)
const uploading = ref(false)

function formatSize(size: number): string {
  if (!size) {
    return '0 B'
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function backupType(row: DbBackup): string {
  if (row.isSafety) {
    return '恢复前自动备份'
  }
  return row.name.startsWith('upload-') ? '上传的备份' : '手动备份'
}

async function loadBackups(): Promise<void> {
  loading.value = true
  try {
    const result = await settingsApi.backups()
    backups.value = result.list ?? []
    backupDir.value = result.dir
    dbPath.value = result.dbPath
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载备份列表失败')
  } finally {
    loading.value = false
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
  let confirm = ''
  try {
    const result = await ElMessageBox.prompt(
      `恢复会用「${name}」覆盖当前数据库，正在录入的用例与缺陷都会回到备份时的状态。请输入备份文件名确认。`,
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
  try {
    const result = await settingsApi.restoreBackup(name, confirm)
    ElMessage.success(`已恢复，恢复前的数据保存在 ${result.safetyBackup}`)
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

.settings-list {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.9;
  color: var(--aitp-text-weak);
}
</style>
