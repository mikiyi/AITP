<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">知识库设置</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button :icon="FolderOpened" @click="migrateVisible = true">迁移目录</el-button>
        <el-button :icon="RefreshRight" :loading="reindexing" @click="doReindex">重建索引</el-button>
        <el-button type="primary" :icon="Check" :loading="saving" @click="save">保存</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <el-form label-width="180px" v-loading="loading">
        <el-form-item label="Markdown 存放目录">
          <el-input v-model="storagePath" placeholder="留空则使用默认目录（AITP_ROOT/knowledge）" />
          <div class="muted form-hint">
            正式知识条目以 .md 文件落盘，SQLite 只存索引与元数据；路径会展开 ~ 并转成绝对路径后写入配置。
          </div>
        </el-form-item>

        <el-form-item label="文件模式">
          <el-radio-group v-model="fileMode">
            <el-radio value="mirror">双写文件（mirror）</el-radio>
            <el-radio value="db_only">仅数据库（db_only）</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="启动时重建索引">
          <el-switch v-model="autoReindex" />
          <div class="muted form-hint">开启后启动会扫描知识库目录，按文件内容重建索引</div>
        </el-form-item>
      </el-form>

      <el-descriptions :column="4" border size="small" class="storage-info">
        <el-descriptions-item label="当前目录">{{ storage.storagePath || '-' }}</el-descriptions-item>
        <el-descriptions-item label="目录状态">
          <el-tag size="small" effect="plain" :type="storage.exists ? 'success' : 'info'">
            {{ storage.exists ? '已存在' : '不存在' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="落盘文件">{{ storage.fileCount }}</el-descriptions-item>
        <el-descriptions-item label="正式条目">{{ storage.articleCount }}</el-descriptions-item>
      </el-descriptions>
    </div>

    <div class="panel panel-pad notes">
      <h3>知识治理规则</h3>
      <ul>
        <li>AI 产出物先进「待审核池」，审核通过才成为正式知识库条目并计入数字资产。</li>
        <li>编辑已发布条目会产生新版本，旧版本仍可检索，直到新版本审核通过。</li>
        <li>被下游引用的条目禁止撤销与删除，会返回引用清单。</li>
        <li>撤销后的条目退出全文检索，但记录保留可追溯。</li>
        <li>改存放目录只改配置；要真正搬文件请用「迁移目录」，搬完自动按新目录重建索引。</li>
        <li>文件被外部改过或丢失时，用「重建索引」以文件为准回填数据库并重建全文索引。</li>
      </ul>
    </div>

    <el-dialog v-model="migrateVisible" title="迁移知识库存放目录" width="560px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="迁移会把当前目录下的 .md 复制到新目录，再以新目录重建索引，并清理旧目录里的同名文件"
      />
      <el-input v-model="migrateTarget" class="migrate-input" placeholder="新目录的绝对路径" />
      <div class="muted form-hint">
        当前目录：{{ storage.storagePath || '-' }}
      </div>
      <template #footer>
        <el-button @click="migrateVisible = false">取消</el-button>
        <el-button type="primary" :loading="migrating" @click="doMigrate">开始迁移</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, FolderOpened, Refresh, RefreshRight } from '@element-plus/icons-vue'
import { knowledgeApi } from '@/api'
import type { KnowledgeStorage } from '@/api/types'

const storage = ref<KnowledgeStorage>({
  storagePath: '',
  fileMode: 'mirror',
  autoReindex: false,
  articleCount: 0,
  fileCount: 0,
  exists: false,
})

const loading = ref(false)
const saving = ref(false)
const reindexing = ref(false)
const migrating = ref(false)
const migrateVisible = ref(false)
const migrateTarget = ref('')

const storagePath = ref('')
const fileMode = ref('mirror')
const autoReindex = ref(false)

async function load(): Promise<void> {
  loading.value = true
  try {
    const info = await knowledgeApi.storage()
    storage.value = info
    storagePath.value = info.storagePath
    fileMode.value = info.fileMode || 'mirror'
    autoReindex.value = info.autoReindex
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  try {
    await knowledgeApi.updateStorage({
      storagePath: storagePath.value.trim(),
      fileMode: fileMode.value,
      autoReindex: autoReindex.value,
    })
    ElMessage.success('已保存')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function doReindex(): Promise<void> {
  reindexing.value = true
  try {
    const result = await knowledgeApi.reindex()
    ElMessage.success(
      `索引已重建：扫描 ${result.scanned} 个文件，新增 ${result.imported}，更新 ${result.updated}，跳过 ${result.skipped}`,
    )
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '重建索引失败')
  } finally {
    reindexing.value = false
  }
}

async function doMigrate(): Promise<void> {
  if (!migrateTarget.value.trim()) {
    ElMessage.warning('请填写新目录')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认把知识库文件迁移到「${migrateTarget.value.trim()}」？旧目录中的同名文件会被清理。`,
      '迁移确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  migrating.value = true
  try {
    const result = await knowledgeApi.migrateStorage({ targetPath: migrateTarget.value.trim() })
    ElMessage.success(
      `迁移完成：复制 ${result.copied} 个文件，新增 ${result.imported}，更新 ${result.updated}，跳过 ${result.skipped}`,
    )
    migrateVisible.value = false
    migrateTarget.value = ''
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '迁移失败')
  } finally {
    migrating.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<style scoped>
.form-hint {
  font-size: 12px;
  line-height: 1.5;
  margin-top: 4px;
}

.storage-info {
  margin-top: 8px;
}

.migrate-input {
  margin: 12px 0 6px;
}

.notes {
  margin-top: 16px;
}

.notes h3 {
  margin: 0 0 8px;
  font-size: 15px;
}

.notes ul {
  margin: 0;
  padding-left: 18px;
  color: var(--aitp-text-weak);
  line-height: 1.9;
}
</style>
