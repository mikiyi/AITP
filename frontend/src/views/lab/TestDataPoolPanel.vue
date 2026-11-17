<template>
  <div class="tdp">
    <div class="panel panel-pad">
      <div class="tdp-head">
        <h3 class="tdp-title">独立数据池</h3>
        <span class="tdp-tip">
          一份数据池可被多条用例 / 场景复用；引用写法 <code>pool:名称</code>，改池即改全部引用
        </span>
        <div class="spacer" />
        <el-button type="primary" :icon="Plus" :disabled="!projectId" @click="openCreate">新建数据池</el-button>
      </div>

      <el-empty v-if="!projectId" description="请先在上方选择项目" />
      <el-table v-else v-loading="loading" :data="rows" border stripe size="small" max-height="420">
        <el-table-column prop="name" label="名称（引用写法）" min-width="180">
          <template #default="{ row }"><code>pool:{{ row.name }}</code></template>
        </el-table-column>
        <el-table-column prop="description" label="说明" min-width="200" show-overflow-tooltip>
          <template #default="{ row }"><span class="muted">{{ row.description || '-' }}</span></template>
        </el-table-column>
        <el-table-column label="内容摘要" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ snippet(row.content) }}</template>
        </el-table-column>
        <el-table-column prop="updatedAt" label="更新时间" width="170">
          <template #default="{ row }">{{ dateTimeText(row.updatedAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openPreview(row)">预览</el-button>
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty><span class="muted">还没有数据池，新建一份后在用例「测试数据」里写 pool:名称 即可复用</span></template>
      </el-table>
      <p class="tdp-tip">
        支持与用例「测试数据」相同的格式：内联文本 / CSV / JSON，或本地 .txt / .csv / .xlsx / .xls / .json / .sql 路径。
      </p>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑数据池' : '新建数据池'" width="640px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="100" placeholder="例如：登录账号组、下单参数集" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" maxlength="500" placeholder="这份数据用来测什么，方便别人判断能不能复用" />
        </el-form-item>
        <el-form-item label="内容" required>
          <el-input
            v-model="form.content"
            type="textarea"
            :rows="10"
            placeholder="直接贴数据，或填本地文件路径：.txt / .csv / .xlsx / .xls / .json / .sql"
          />
        </el-form-item>
        <el-form-item>
          <el-button size="small" :loading="previewLoading" @click="previewDraft">解析预览</el-button>
          <el-tag v-if="draftPreview" size="small" effect="plain" :type="draftPreview.kind === 'empty' ? 'info' : 'success'">
            {{ previewLabel(draftPreview) }}
          </el-tag>
        </el-form-item>
      </el-form>
      <div v-if="draftPreview && draftPreview.sampleRows.length" class="tdp-sample">
        <div v-for="(line, i) in draftPreview.sampleRows" :key="i" class="tdp-sample__row text-mono">
          {{ line.join(' | ') }}
        </div>
      </div>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!form.name.trim() || !form.content.trim()" @click="save">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="previewVisible" :title="`数据池预览 · ${previewTitle}`" width="720px">
      <div v-if="poolPreview">
        <div class="tdp-preview-meta">
          <el-tag size="small" effect="plain">{{ previewLabel(poolPreview) }}</el-tag>
          <span class="muted">引用写法：<code>pool:{{ previewTitle }}</code></span>
        </div>
        <el-table v-if="poolPreview.sampleRows.length" :data="poolPreview.sampleRows" border size="small" max-height="360">
          <el-table-column
            v-for="(col, index) in poolPreview.columns.length ? poolPreview.columns : poolPreview.sampleRows[0].map((_, i) => `列 ${i + 1}`)"
            :key="col"
            :label="col"
            min-width="120"
            show-overflow-tooltip
          >
            <template #default="{ row }">{{ row[index] ?? '' }}</template>
          </el-table-column>
        </el-table>
        <pre v-else class="tdp-text">{{ poolPreview.textSample || '空内容' }}</pre>
        <p class="tdp-tip">只展示前 5 行样本；执行时按全量数据消费。</p>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { caseApi, dataPoolApi } from '@/api'
import type { DataPool, TestDataPreview } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { dateTimeText } from '@/utils/format'
import { fetchAllPages } from '@/utils/fetchAll'

const appStore = useAppStore()
const projectId = computed(() => appStore.currentProjectId ?? null)

const loading = ref(false)
const rows = ref<DataPool[]>([])
const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<DataPool | null>(null)
const form = reactive({ name: '', description: '', content: '' })

const previewLoading = ref(false)
const draftPreview = ref<TestDataPreview | null>(null)
const previewVisible = ref(false)
const previewTitle = ref('')
const poolPreview = ref<TestDataPreview | null>(null)

function snippet(text: string): string {
  const value = (text || '').replace(/\s+/g, ' ').trim()
  return value.length > 80 ? `${value.slice(0, 80)}…` : value || '-'
}

function previewLabel(preview: TestDataPreview): string {
  if (preview.kind === 'empty') return '空数据'
  const labels: Record<string, string> = {
    excel: 'Excel', json: 'JSON', 'inline-json': 'JSON', sql: 'SQL', txt: '文本', 'inline-text': '文本',
  }
  const base = labels[preview.kind] ?? preview.kind
  return preview.rowCount > 0 ? `${base} · ${preview.rowCount} 条记录` : `${base} · 文本`
}

async function load(): Promise<void> {
  if (!projectId.value) {
    rows.value = []
    return
  }
  loading.value = true
  try {
    rows.value = await fetchAllPages((pageNum, pageSize) =>
      dataPoolApi.list({ projectId: projectId.value, pageNum, pageSize }))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载数据池失败')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  form.name = ''
  form.description = ''
  form.content = ''
  draftPreview.value = null
  dialogVisible.value = true
}

function openEdit(row: DataPool): void {
  editing.value = row
  form.name = row.name
  form.description = row.description
  form.content = row.content
  draftPreview.value = null
  dialogVisible.value = true
}

async function previewDraft(): Promise<void> {
  previewLoading.value = true
  try {
    draftPreview.value = await caseApi.previewTestData(form.content, projectId.value)
  } catch (err) {
    draftPreview.value = null
    ElMessage.error(err instanceof Error ? err.message : '解析失败')
  } finally {
    previewLoading.value = false
  }
}

async function save(): Promise<void> {
  if (!projectId.value) return
  saving.value = true
  try {
    if (editing.value) {
      await dataPoolApi.update(editing.value.id, {
        name: form.name.trim(),
        description: form.description,
        content: form.content,
      })
      ElMessage.success('数据池已更新，引用它的用例 / 场景立即生效')
    } else {
      await dataPoolApi.create({
        projectId: projectId.value,
        name: form.name.trim(),
        description: form.description,
        content: form.content,
      })
      ElMessage.success(`已新建数据池，用例里写 pool:${form.name.trim()} 即可引用`)
    }
    dialogVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: DataPool): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除数据池「${row.name}」前会先检查引用：仍被用例 / 场景以 pool:${row.name} 引用时会被拦截。确定继续？`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await dataPoolApi.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

async function openPreview(row: DataPool): Promise<void> {
  try {
    poolPreview.value = await dataPoolApi.preview(row.id)
    previewTitle.value = row.name
    previewVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '预览失败')
  }
}

watch(projectId, () => void load())
onMounted(() => void load())
</script>

<style scoped>
.tdp-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.tdp-title {
  margin: 0;
  font-size: 14px;
}

.tdp-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 8px 0 0;
}

.tdp-sample {
  border: 1px dashed var(--el-border-color);
  border-radius: 4px;
  padding: 6px 10px;
  max-height: 160px;
  overflow: auto;
}

.tdp-sample__row {
  font-size: 12px;
  white-space: pre-wrap;
}

.tdp-preview-meta {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 10px;
}

.tdp-text {
  white-space: pre-wrap;
  max-height: 360px;
  overflow: auto;
  font-size: 12px;
}

.spacer {
  flex: 1;
}
</style>
