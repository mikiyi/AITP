<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">SQL 分析</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button type="primary" :icon="Plus" @click="openCreate">新建分析</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetcher"
        :search-fields="searchFields"
        :extra-query="extraQuery"
        sync-query
        @row-click="openDetail"
      >
        <template #riskLevel="{ row }">
          <el-tag size="small" :type="riskTag(row.riskLevel)" effect="plain">
            {{ row.riskLabel }}
          </el-tag>
        </template>
        <template #sqlText="{ row }">
          <span class="text-mono sql-preview">{{ row.sqlText || '-' }}</span>
        </template>
        <template #source="{ row }">{{ row.source === 'ai' ? 'AI 实验室' : '手工录入' }}</template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="openDetail(row)">查看</el-button>
          <el-button link @click.stop="openEdit(row)">编辑</el-button>
          <el-button link type="success" @click.stop="openKnowledge(row)">加入知识库</el-button>
          <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
        </template>
      </BaseProTable>
    </div>

    <el-drawer v-model="detailVisible" :title="detail?.title || 'SQL 分析详情'" size="52%">
      <div v-if="detail" class="sql-detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="编号">{{ detail.sqlNo }}</el-descriptions-item>
          <el-descriptions-item label="风险级别">
            <el-tag size="small" :type="riskTag(detail.riskLevel)" effect="plain">
              {{ detail.riskLabel }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="项目">{{ projectName(detail.projectId) }}</el-descriptions-item>
          <el-descriptions-item label="来源">
            {{ detail.source === 'ai' ? 'AI 实验室' : '手工录入' }}
          </el-descriptions-item>
        </el-descriptions>
        <div class="sql-detail__block">
          <div class="sql-detail__label">被分析的 SQL</div>
          <pre class="sql-detail__sql">{{ detail.sqlText || '（未单独记录 SQL）' }}</pre>
        </div>
        <div class="sql-detail__block">
          <div class="sql-detail__label">分析结论</div>
          <MarkdownView :source="detail.analysis || '（暂无分析内容）'" />
        </div>
      </div>
    </el-drawer>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑 SQL 分析' : '新建 SQL 分析'" width="760px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="例如：订单列表查询未走索引" />
        </el-form-item>
        <el-form-item label="项目" required>
          <el-select v-model="form.projectId" placeholder="选择项目" @change="onProjectChange">
            <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="版本">
          <el-select v-model="form.versionId" placeholder="选择版本" clearable style="width: 100%">
            <el-option v-for="item in formVersionOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="风险级别">
          <el-radio-group v-model="form.riskLevel">
            <el-radio value="none">无风险</el-radio>
            <el-radio value="low">低风险</el-radio>
            <el-radio value="high">高风险</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="被分析 SQL">
          <el-input
            v-model="form.sqlText"
            type="textarea"
            :rows="7"
            class="sql-input"
            placeholder="粘贴被分析的 SQL"
          />
        </el-form-item>
        <el-form-item label="分析结论">
          <el-input v-model="form.analysis" type="textarea" :rows="10" placeholder="支持 Markdown" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <AddToKnowledgeDialog
      v-model="knowledgeVisible"
      :title="knowledgePayload.title"
      :content="knowledgePayload.content"
      ref-type="sql_analysis"
      :ref-id="knowledgePayload.refId"
      :ref-label="knowledgePayload.refLabel"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import AddToKnowledgeDialog from '@/components/AddToKnowledgeDialog.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { projectVersionApi, sqlAnalysisApi } from '@/api'
import type { SqlAnalysis } from '@/api/types'
import { useProjectScope } from '@/composables/useProjectScope'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
const tableRef = ref<TableExpose | null>(null)

/** 列表只展示本页当前项目的分析。 */
const extraQuery = computed(() => ({
  projectId: appStore.currentProjectId ?? undefined,
}))

const columns: ColumnDef[] = [
  { prop: 'sqlNo', label: '编号', width: 110 },
  { prop: 'title', label: '标题', minWidth: 190 },
  { prop: 'riskLevel', label: '风险级别', width: 100, slot: 'riskLevel' },
  { prop: 'versionCode', label: '版本号', width: 100 },
  { prop: 'versionName', label: '版本名称', width: 120 },
  { prop: 'sqlText', label: 'SQL', minWidth: 200, slot: 'sqlText' },
  { prop: 'updatedAt', label: '更新时间', width: 170 },
  { prop: 'id', label: '操作', width: 260, slot: 'actions', fixed: 'right' },
]

const searchFields = computed<SearchField[]>(() => [
  { prop: 'keyword', label: '标题 / SQL' },
  {
    prop: 'riskLevel',
    label: '风险级别',
    type: 'select',
    options: [
      { label: '无风险', value: 'none' },
      { label: '低风险', value: 'low' },
      { label: '高风险', value: 'high' },
    ],
  },
  { prop: 'versionId', label: '版本', type: 'select', options: versionOptions.value },
])

const versionOptions = ref<Array<{ label: string; value: number }>>([])

async function loadVersionOptions(): Promise<void> {
  const pid = appStore.currentProjectId
  if (!pid) { versionOptions.value = []; return }
  try {
    const result = await projectVersionApi.list(pid)
    versionOptions.value = result.list.map((v) => ({
      label: v.versionCode,
      value: v.id as number,
    }))
  } catch { versionOptions.value = [] }
}

useProjectScope({
  load: () => void loadVersionOptions(),
  // 旧项目的版本筛选在新项目里不存在，留着会把列表筛成空。
  resetScopedFilters: () => tableRef.value?.clearFilter('versionId'),
})

function fetcher(query: Record<string, unknown>) {
  return sqlAnalysisApi.list(query)
}

function riskTag(level: string): 'danger' | 'warning' | 'info' {
  if (level === 'high') return 'danger'
  if (level === 'low') return 'warning'
  return 'info'
}

function projectName(id: number): string {
  return appStore.projects.find((item) => item.id === id)?.name ?? '-'
}

/* ---- 详情 ---- */

const detailVisible = ref(false)
const detail = ref<SqlAnalysis | null>(null)

async function openDetail(row: SqlAnalysis): Promise<void> {
  try {
    detail.value = await sqlAnalysisApi.get(Number(row.id))
    detailVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载详情失败')
  }
}

/* ---- 新建 / 编辑 ---- */

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<SqlAnalysis | null>(null)
const form = reactive({
  title: '',
  projectId: null as number | null,
  versionId: null as number | null,
  riskLevel: 'none',
  sqlText: '',
  analysis: '',
})

/** 表单里的版本下拉跟随表单中选的项目。 */
const formVersionOptions = ref<Array<{ label: string; value: number }>>([])
const formVersionProjectId = ref<number | null>(null)

watch(() => form.projectId, async (pid) => {
  if (!pid) {
    formVersionOptions.value = []
    formVersionProjectId.value = null
    return
  }
  formVersionProjectId.value = pid
  try {
    const result = await projectVersionApi.list(pid)
    if (formVersionProjectId.value === pid) {
      formVersionOptions.value = result.list.map((v) => ({
        label: v.versionCode,
        value: v.id as number,
      }))
    }
  } catch { formVersionOptions.value = [] }
}, { immediate: true })

/** 表单里换了项目，版本必须跟着换，否则会挂到别的项目的版本上。 */
function onProjectChange(): void {
  form.versionId = null
}

function openCreate(): void {
  editing.value = null
  const fq = tableRef.value?.filterQuery() ?? {}
  Object.assign(form, {
    title: '',
    projectId: appStore.currentProjectId ?? null,
    // 列表正按版本筛选时，新建的 SQL 分析直接挂到该版本下，保存后不会从列表里消失。
    versionId: fq.versionId ? Number(fq.versionId) : null,
    riskLevel: 'none',
    sqlText: '',
    analysis: '',
  })
  dialogVisible.value = true
}

function openEdit(row: SqlAnalysis): void {
  editing.value = row
  Object.assign(form, {
    title: row.title,
    projectId: row.projectId,
    versionId: row.versionId ?? null,
    riskLevel: row.riskLevel,
    sqlText: row.sqlText,
    analysis: row.analysis,
  })
  dialogVisible.value = true
}

async function save(): Promise<void> {
  if (!form.title.trim()) {
    ElMessage.warning('标题为必填项')
    return
  }
  if (!form.projectId) {
    ElMessage.warning('请选择项目')
    return
  }
  saving.value = true
  try {
    const payload = {
      title: form.title.trim(),
      projectId: form.projectId,
      versionId: form.versionId,
      riskLevel: form.riskLevel,
      sqlText: form.sqlText,
      analysis: form.analysis,
    }
    if (editing.value) {
      await sqlAnalysisApi.update(Number(editing.value.id), payload)
      ElMessage.success('已保存')
    } else {
      await sqlAnalysisApi.create(payload)
      ElMessage.success('已新建 SQL 分析')
    }
    dialogVisible.value = false
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: SqlAnalysis): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除 SQL 分析「${row.title}」？删除后无法找回。`, '删除确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await sqlAnalysisApi.remove(Number(row.id))
    ElMessage.success('已删除')
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

/* ---- 加入知识库 ---- */

const knowledgeVisible = ref(false)
const knowledgePayload = reactive({
  title: '',
  content: '',
  refId: null as number | null,
  refLabel: '',
})

function openKnowledge(row: SqlAnalysis): void {
  knowledgePayload.title = row.title
  knowledgePayload.content = [
    `# ${row.title}`,
    `**风险级别**：${row.riskLabel}`,
    row.sqlText ? `## 被分析 SQL\n\n\`\`\`sql\n${row.sqlText}\n\`\`\`` : '',
    row.analysis ? `## 分析结论\n\n${row.analysis}` : '',
  ].filter(Boolean).join('\n\n')
  knowledgePayload.refId = Number(row.id)
  knowledgePayload.refLabel = row.title
  knowledgeVisible.value = true
}
</script>

<style scoped>
.sql-preview {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.sql-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sql-detail__label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}

.sql-detail__sql {
  margin: 0;
  padding: 12px;
  background: var(--aitp-subtle);
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 260px;
  overflow-y: auto;
}

.sql-input :deep(textarea) {
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
}
</style>
