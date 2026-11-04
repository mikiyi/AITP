<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">系统设置</h2>
    </div>

    <div class="settings-grid">
      <button class="settings-card panel" type="button" @click="router.push('/settings/ai')">
        <span class="settings-card__title">AI 设置</span>
        <span class="settings-card__desc">供应商档案、API Key、优先级与降级</span>
      </button>
      <button class="settings-card panel" type="button" @click="router.push('/settings/knowledge')">
        <span class="settings-card__title">知识库设置</span>
        <span class="settings-card__desc">存储路径、文件模式、索引策略</span>
      </button>
      <button class="settings-card panel" type="button" @click="router.push('/settings/modules')">
        <span class="settings-card__title">模块树设置</span>
        <span class="settings-card__desc">四级结构维护与功能点 AI 能力绑定</span>
      </button>
      <button class="settings-card panel" type="button" @click="router.push('/settings/automation')">
        <span class="settings-card__title">自动化管理设置</span>
        <span class="settings-card__desc">Python 解释器路径与用例执行超时</span>
      </button>
      <button class="settings-card panel" type="button" @click="router.push('/settings/performance')">
        <span class="settings-card__title">性能测试管理设置</span>
        <span class="settings-card__desc">JMeter 安装目录与启动参数</span>
      </button>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>系统配置</h3>
        <el-button type="primary" size="small" :loading="saving" @click="saveConfig">保存修改</el-button>
      </div>
      <el-form label-width="220px">
        <el-form-item v-for="item in configs" :key="item.key" :label="item.key">
          <el-input v-model="item.value" :placeholder="item.description" />
          <div class="muted settings-block__desc">{{ item.description }}</div>
        </el-form-item>
      </el-form>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>数据备份</h3>
        <div class="settings-block__actions">
          <span class="muted">{{ backupDir || '备份目录加载中…' }}</span>
          <el-button size="small" :loading="backupCreating" @click="createBackup">立即备份</el-button>
        </div>
      </div>
      <p class="muted settings-block__hint">
        数据库文件：{{ dbPath || '-' }}。恢复会用所选备份覆盖当前库，覆盖前系统自动先备份一份当前数据。
      </p>
      <el-table :data="backups" size="small" border max-height="320">
        <el-table-column prop="name" label="备份文件" min-width="220" />
        <el-table-column prop="createdAt" label="时间" width="160" />
        <el-table-column label="大小" width="100" align="right">
          <template #default="{ row }">{{ formatSize(row.sizeBytes) }}</template>
        </el-table-column>
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <el-tag :type="row.isSafety ? 'warning' : 'info'" size="small" effect="plain">
              {{ row.isSafety ? '恢复前自动备份' : '手动备份' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200">
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
        <h3>审计日志</h3>
        <span class="muted">关键写操作都会留痕，可按资源追溯</span>
      </div>
      <BaseProTable
        :columns="auditColumns"
        :fetcher="auditFetcher"
        :search-fields="auditSearchFields"
        :max-height="420"
        sync-query
        query-prefix="audit_"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import BaseProTable, { type ColumnDef, type SearchField } from '@/components/BaseProTable.vue'
import { settingsApi } from '@/api'
import type { DbBackup, SystemConfigItem } from '@/api/types'

const router = useRouter()

const configs = ref<SystemConfigItem[]>([])
/**
 * 自动化与性能相关的配置项有专门的设置卡片（解释器路径、JMeter 目录），
 * 这里不再重复列出，避免两处入口改同一个键互相覆盖。
 */
const DEDICATED_PREFIXES = ['automation.', 'performance.']
const saving = ref(false)

const auditColumns: ColumnDef[] = [
  { prop: 'createdAt', label: '时间', width: 170, formatter: (row) => formatAuditTime(row.createdAt) },
  { prop: 'actor', label: '操作者', width: 110 },
  { prop: 'action', label: '动作', width: 120 },
  { prop: 'resourceType', label: '资源类型', width: 140 },
  { prop: 'resourceLabel', label: '资源', minWidth: 200 },
  { prop: 'result', label: '结果', width: 90 },
  { prop: 'traceId', label: 'TraceId', width: 200 },
]

/** 审计时间落库是 ISO 字符串，表格里换成本地可读时间，避免整列都被截断。 */
function formatAuditTime(raw: unknown): string {
  if (!raw) {
    return '-'
  }
  const date = new Date(String(raw))
  if (Number.isNaN(date.getTime())) {
    return String(raw)
  }
  const pad = (value: number): string => String(value).padStart(2, '0')
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  return `${day} ${time}`
}

const auditSearchFields: SearchField[] = [
  { prop: 'action', label: '动作' },
  { prop: 'resourceType', label: '资源类型' },
]

async function loadConfig(): Promise<void> {
  try {
    const all = await settingsApi.config()
    configs.value = all.filter(
      (item) => !DEDICATED_PREFIXES.some((prefix) => item.key.startsWith(prefix)),
    )
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载配置失败')
  }
}

async function saveConfig(): Promise<void> {
  saving.value = true
  try {
    await settingsApi.updateConfig(
      configs.value.map((item) => ({
        key: item.key,
        value: item.value,
        valueType: item.valueType,
      })),
    )
    ElMessage.success('配置已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

function auditFetcher(query: Record<string, unknown>) {
  return settingsApi.auditLogs(query)
}

const backups = ref<DbBackup[]>([])
const backupDir = ref('')
const dbPath = ref('')
const backupCreating = ref(false)

function formatSize(size: number): string {
  if (!size) {
    return '0 B'
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

async function loadBackups(): Promise<void> {
  try {
    const result = await settingsApi.backups()
    backups.value = result.list ?? []
    backupDir.value = result.dir
    dbPath.value = result.dbPath
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载备份列表失败')
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
  void loadConfig()
  void loadBackups()
})
</script>

<style scoped>
.settings-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.settings-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
  padding: 16px;
  cursor: pointer;
  font: inherit;
  color: inherit;
  border: 1px solid var(--aitp-border);
}

.settings-card:hover {
  border-color: var(--aitp-primary);
}

.settings-card__title {
  font-size: 15px;
  font-weight: 600;
}

.settings-card__desc {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

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
  gap: 12px;
}

.settings-block__hint {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.5;
}

.settings-block__desc {
  font-size: 12px;
  line-height: 1.4;
}
</style>
