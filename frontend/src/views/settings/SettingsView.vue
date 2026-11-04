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
      <button class="settings-card panel" type="button" @click="router.push('/settings/templates')">
        <span class="settings-card__title">测试文档模板设置</span>
        <span class="settings-card__desc">用例 / 报告 / 脑图模板，支持上传模板文件</span>
      </button>
      <button class="settings-card panel" type="button" @click="router.push('/settings/backup')">
        <span class="settings-card__title">数据备份与还原设置</span>
        <span class="settings-card__desc">数据库快照备份、上传备份文件与还原</span>
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
          <div class="settings-config__hint">
            <el-icon class="settings-config__icon"><InfoFilled /></el-icon>
            <span>{{ item.hint || item.description || '按当前配置项的类型填写对应值' }}</span>
          </div>
        </el-form-item>
      </el-form>
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
import { ElMessage } from 'element-plus'
import { InfoFilled } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField } from '@/components/BaseProTable.vue'
import { settingsApi } from '@/api'
import type { SystemConfigItem } from '@/api/types'

const router = useRouter()

const configs = ref<SystemConfigItem[]>([])
/**
 * 自动化与性能相关的配置项有专门的设置卡片（解释器路径、JMeter 目录），
 * 这里不再重复列出，避免两处入口改同一个键互相覆盖。
 */
const DEDICATED_PREFIXES = ['automation.', 'performance.']
const saving = ref(false)

const auditColumns: ColumnDef[] = [
  { prop: 'createdAt', label: '时间', width: 170 },
  { prop: 'actor', label: '操作者', width: 110 },
  { prop: 'action', label: '动作', width: 120 },
  { prop: 'resourceType', label: '资源类型', width: 140 },
  { prop: 'resourceLabel', label: '资源', minWidth: 200 },
  { prop: 'result', label: '结果', width: 90 },
  { prop: 'traceId', label: 'TraceId', width: 200 },
]

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

onMounted(() => {
  void loadConfig()
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

/* 配置项说明：小图标 + 取值解释，新手也能看懂能填什么 */
.settings-config__hint {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--aitp-text-weak);
}

.settings-config__icon {
  margin-top: 2px;
  flex-shrink: 0;
  color: var(--el-color-primary);
  opacity: 0.85;
}
</style>
