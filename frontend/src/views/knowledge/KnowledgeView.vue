<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">知识库</h2>
      <div class="toolbar" style="margin: 0">
        <el-input
          v-model="keyword"
          placeholder="全文检索（已发布条目）"
          clearable
          style="width: 260px"
          @keyup.enter="runSearch"
        />
        <el-button type="primary" :icon="Search" @click="runSearch">检索</el-button>
        <el-button :icon="Files" @click="openCategoryDialog">分类管理</el-button>
        <el-button :icon="Plus" @click="openCreate">手工录入</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="knowledge-tabs">
      <el-tab-pane name="drafts">
        <template #label>
          <span>
            待审核池
            <el-badge v-if="pendingCount" :value="pendingCount" class="knowledge-badge" />
          </span>
        </template>

        <div class="panel panel-pad">
          <BaseProTable
            ref="draftTableRef"
            :columns="draftColumns"
            :fetcher="draftFetcher"
            :search-fields="draftSearchFields"
            selectable
            sync-query
            query-prefix="d_"
            @row-click="onDraftClick"
          >
            <template #batch="{ rows }">
              <el-button size="small" type="success" plain :loading="batchBusy" @click="batchReview(rows, 'approve')">
                批量通过
              </el-button>
              <el-button size="small" type="danger" plain :loading="batchBusy" @click="batchReview(rows, 'reject')">
                批量驳回
              </el-button>
            </template>
            <template #sourceType="{ row }">
              <el-tag size="small" effect="plain">{{ row.sourceType }}</el-tag>
            </template>
            <template #producer="{ row }">
              {{ row.producer || '-' }}
            </template>
            <template #reviewStatus="{ row }">
              <el-tag :type="row.reviewStatus === 'pending' ? 'warning' : 'info'" size="small" effect="plain">
                {{ row.reviewStatus === 'pending' ? '待审核' : row.reviewStatus }}
              </el-tag>
            </template>
            <template #actions="{ row }">
              <el-button link type="success" @click.stop="review(row, 'approve')">通过</el-button>
              <el-button link type="danger" @click.stop="review(row, 'reject')">驳回</el-button>
            </template>
          </BaseProTable>
        </div>
      </el-tab-pane>

      <el-tab-pane name="articles">
        <template #label>正式库</template>

        <div class="panel panel-pad">
          <BaseProTable
            ref="articleTableRef"
            :columns="articleColumns"
            :fetcher="articleFetcher"
            :search-fields="articleSearchFields"
            sync-query
            query-prefix="a_"
            @row-click="onArticleClick"
          >
            <template #toolbar>
              <BaseExportMenu :resolve-url="resolveArticleExportUrl" />
            </template>
            <template #reviewStatus="{ row }">
              <el-tag :type="row.reviewStatus === 'published' ? 'success' : 'info'" size="small" effect="plain">
                {{ row.reviewStatus === 'published' ? '已发布' : '已撤销' }}
              </el-tag>
            </template>
            <template #currentVersion="{ row }">
              v{{ row.currentVersion ?? 1 }}
            </template>
            <template #actions="{ row }">
              <el-button link type="primary" @click.stop="openArticle(row)">查看</el-button>
              <el-button
                v-if="row.reviewStatus === 'published'"
                link
                type="warning"
                @click.stop="revoke(row)"
              >
                撤销
              </el-button>
              <el-button v-else link type="success" @click.stop="restore(row)">恢复</el-button>
            </template>
          </BaseProTable>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dialogVisible" title="录入知识条目" width="720px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item label="分类">
          <el-select
            v-model="form.category"
            filterable
            allow-create
            default-first-option
            placeholder="选择分类，或直接输入新分类"
          >
            <el-option
              v-for="item in categoryOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="功能点">
          <el-select v-model="form.moduleId" clearable filterable placeholder="选择功能点">
            <el-option v-for="item in moduleOptions" :key="item.id" :label="item.path" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="form.tags" placeholder="逗号分隔" />
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="form.summary" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="正文" required>
          <el-input v-model="form.content" type="textarea" :rows="12" placeholder="支持 Markdown" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">提交到待审池</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="categoryVisible" title="知识分类管理" width="520px">
      <p class="muted category-hint">
        分类名称就是知识条目上显示的分类；新增后所有录入入口都能直接选到。
      </p>
      <div class="category-add">
        <el-input
          v-model="newCategory"
          maxlength="20"
          placeholder="输入新分类名称"
          @keyup.enter="addCategory"
        />
        <el-button type="primary" :loading="categoryBusy" @click="addCategory">新增</el-button>
      </div>
      <el-table :data="categoryStore.list" size="small" border max-height="320">
        <el-table-column prop="name" label="分类名称" />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.isSystem ? 'info' : 'success'">
              {{ row.isSystem ? '内置' : '自定义' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button v-if="!row.isSystem" link type="danger" @click="removeCategory(row)">
              删除
            </el-button>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-drawer v-model="previewVisible" :title="previewTitle" size="46%">
      <MarkdownView v-if="previewContent" :source="previewContent" />
    </el-drawer>

    <ReferenceBlockDialog
      v-model="blockedVisible"
      :message="blockedMessage"
      :refs="blockedRefs"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Files, Plus, Search } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import BaseExportMenu from '@/components/BaseExportMenu.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import { knowledgeApi, moduleApi, workbenchApi } from '@/api'
import type { KnowledgeArticle, KnowledgeDraft, ModuleOption } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { DEFAULT_KNOWLEDGE_CATEGORY, useKnowledgeCategoryStore } from '@/stores/knowledgeCategory'
import { useReferenceGuard } from '@/composables/useReferenceGuard'
import ReferenceBlockDialog from '@/components/ReferenceBlockDialog.vue'

const router = useRouter()
const appStore = useAppStore()
const categoryStore = useKnowledgeCategoryStore()

const activeTab = ref('drafts')
const keyword = ref('')
const pendingCount = ref(0)
const moduleOptions = ref<ModuleOption[]>([])

const { blockedVisible, blockedRefs, blockedMessage, handleError } = useReferenceGuard()

const draftTableRef = ref<TableExpose | null>(null)
const articleTableRef = ref<TableExpose | null>(null)

const categoryOptions = computed(() => categoryStore.options)

const draftColumns: ColumnDef[] = [
  { prop: 'title', label: '标题', minWidth: 220 },
  { prop: 'sourceType', label: '来源', width: 110, slot: 'sourceType' },
  { prop: 'producer', label: '产出能力', width: 140, slot: 'producer' },
  { prop: 'reviewStatus', label: '状态', width: 100, slot: 'reviewStatus' },
  { prop: 'createdAt', label: '产生时间', width: 170 },
  { prop: 'id', label: '操作', width: 140, slot: 'actions' },
]

const draftSearchFields: SearchField[] = [
  { prop: 'keyword', label: '标题' },
  { prop: 'sourceType', label: '来源', type: 'select', options: [
    { label: 'AI 产出', value: 'ai_output' },
    { label: '手工录入', value: 'manual' },
    { label: '会话', value: 'session' },
  ] },
]

const articleColumns: ColumnDef[] = [
  { prop: 'title', label: '标题', minWidth: 220 },
  { prop: 'category', label: '分类', width: 110 },
  { prop: 'currentVersion', label: '版本', width: 80, slot: 'currentVersion' },
  { prop: 'reviewStatus', label: '状态', width: 100, slot: 'reviewStatus' },
  { prop: 'updatedAt', label: '更新时间', width: 170 },
  { prop: 'id', label: '操作', width: 160, slot: 'actions' },
]

const articleSearchFields = computed<SearchField[]>(() => [
  { prop: 'keyword', label: '标题' },
  { prop: 'category', label: '分类', type: 'select', options: categoryOptions.value },
])

function draftFetcher(query: Record<string, unknown>) {
  return knowledgeApi.listDrafts(query)
}

function articleFetcher(query: Record<string, unknown>) {
  // 顶部全文检索优先，没填时再用表格自身的标题筛选，否则两个输入框会互相吞掉条件
  return knowledgeApi.listArticles({
    ...query,
    keyword: keyword.value.trim() || query.keyword || undefined,
  })
}

function resolveArticleExportUrl(format: string): string {
  const filters = articleTableRef.value?.filterQuery() ?? {}
  return knowledgeApi.exportArticlesUrl({
    ...filters,
    keyword: keyword.value.trim() || filters.keyword || undefined,
    format,
  })
}

async function refreshPending(): Promise<void> {
  try {
    const overview = await workbenchApi.overview()
    pendingCount.value = overview.pendingKnowledge ?? 0
  } catch {
    pendingCount.value = 0
  }
}

async function review(row: KnowledgeDraft, action: 'approve' | 'reject'): Promise<void> {
  let comment = ''
  if (action === 'reject') {
    try {
      const { value } = await ElMessageBox.prompt('请填写驳回原因', '驳回', {
        inputPlaceholder: '例如：结论缺少数据支撑',
        inputValidator: (v) => (v && v.trim() ? true : '请填写驳回原因'),
      })
      comment = value
    } catch {
      return
    }
  }
  try {
    await knowledgeApi.reviewDraft(row.id, action, comment)
    ElMessage.success(action === 'approve' ? '已通过，正式知识库 +1' : '已驳回')
    draftTableRef.value?.reload()
    articleTableRef.value?.reload()
    await refreshPending()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '审核失败')
  }
}

const batchBusy = ref(false)

async function batchReview(rows: Record<string, any>[], action: 'approve' | 'reject'): Promise<void> {
  const ids = rows.map((row) => Number(row.id)).filter((id) => Number.isInteger(id) && id > 0)
  if (!ids.length) {
    return
  }
  let comment = ''
  if (action === 'reject') {
    try {
      const { value } = await ElMessageBox.prompt(
        `将驳回选中的 ${ids.length} 条待审条目，请填写统一驳回原因`,
        '批量驳回',
        { inputPlaceholder: '例如：结论缺少数据支撑', inputValidator: (v) => (v && v.trim() ? true : '请填写驳回原因') },
      )
      comment = value
    } catch {
      return
    }
  } else {
    try {
      await ElMessageBox.confirm(
        `将通过选中的 ${ids.length} 条待审条目，通过后计入数字资产并落盘。`,
        '批量通过',
        { type: 'warning' },
      )
    } catch {
      return
    }
  }
  batchBusy.value = true
  try {
    const result = await knowledgeApi.batchReview(ids, action, comment)
    const failed = result.failCount ? `，${result.failCount} 条未处理` : ''
    ElMessage.success(`${action === 'approve' ? '已通过' : '已驳回'} ${result.successCount} 条${failed}`)
    draftTableRef.value?.clearSelection()
    draftTableRef.value?.reload()
    articleTableRef.value?.reload()
    await refreshPending()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量审核失败')
  } finally {
    batchBusy.value = false
  }
}

async function revoke(row: KnowledgeArticle): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '撤销后该条目将移出数字资产与全文检索。若被下游引用，撤销会被拒绝。',
      '撤销确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await knowledgeApi.revokeArticle(row.id)
    ElMessage.success('已撤销')
    articleTableRef.value?.reload()
    await refreshPending()
  } catch (err) {
    handleError(err, '撤销失败')
  }
}

async function restore(row: KnowledgeArticle): Promise<void> {
  try {
    await knowledgeApi.restoreArticle(row.id)
    ElMessage.success('已恢复')
    articleTableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '恢复失败')
  }
}

const previewVisible = ref(false)
const previewTitle = ref('')
const previewContent = ref('')

function onDraftClick(row: KnowledgeDraft): void {
  previewTitle.value = row.title
  previewContent.value = row.content
  previewVisible.value = true
}

function openArticle(row: KnowledgeArticle): void {
  void router.push(`/knowledge/${row.id}`)
}

function onArticleClick(row: KnowledgeArticle): void {
  openArticle(row)
}

const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive({
  title: '',
  content: '',
  summary: '',
  category: DEFAULT_KNOWLEDGE_CATEGORY,
  tags: '',
  moduleId: null as number | null,
})

function openCreate(): void {
  Object.assign(form, {
    title: '',
    content: '',
    summary: '',
    category: DEFAULT_KNOWLEDGE_CATEGORY,
    tags: '',
    moduleId: null,
  })
  dialogVisible.value = true
}

async function save(): Promise<void> {
  if (!form.title.trim() || !form.content.trim()) {
    ElMessage.warning('标题与正文为必填项')
    return
  }
  saving.value = true
  try {
    // 下拉里直接输入的新分类先建分类，再提交条目，避免校验失败
    const category = form.category.trim()
    if (category && !categoryStore.names.includes(category)) {
      await categoryStore.create(category)
    }
    await knowledgeApi.createDraft({ ...form, category, sourceType: 'manual' })
    ElMessage.success('已进入待审核池，审核通过后才计入数字资产')
    dialogVisible.value = false
    draftTableRef.value?.reload()
    await refreshPending()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  } finally {
    saving.value = false
  }
}

function runSearch(): void {
  if (!keyword.value.trim()) {
    articleTableRef.value?.reload()
    return
  }
  activeTab.value = 'articles'
  articleTableRef.value?.reload()
}

async function loadModuleOptions(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) {
    moduleOptions.value = []
    return
  }
  try {
    moduleOptions.value = await moduleApi.options(projectId, true)
  } catch {
    moduleOptions.value = []
  }
}

watch(() => appStore.currentProjectId, () => {
  void loadModuleOptions()
})

const categoryVisible = ref(false)
const categoryBusy = ref(false)
const newCategory = ref('')

function openCategoryDialog(): void {
  newCategory.value = ''
  categoryVisible.value = true
  void categoryStore.load(true)
}

async function addCategory(): Promise<void> {
  const name = newCategory.value.trim()
  if (!name) {
    ElMessage.warning('请输入分类名称')
    return
  }
  if (categoryStore.names.includes(name)) {
    ElMessage.warning('该分类已存在')
    return
  }
  categoryBusy.value = true
  try {
    await categoryStore.create(name)
    newCategory.value = ''
    ElMessage.success('分类已新增')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '新增分类失败')
  } finally {
    categoryBusy.value = false
  }
}

async function removeCategory(row: { id: number; name: string }): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除分类「${row.name}」？已归入该分类的条目不受影响。`, '删除分类', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await categoryStore.remove(row.id)
    ElMessage.success('已删除')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

onMounted(() => {
  void loadModuleOptions()
  void refreshPending()
  void categoryStore.load()
})
</script>

<style scoped>
.knowledge-badge {
  margin-left: 4px;
}

.knowledge-tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}

.category-hint {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.6;
}

.category-add {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
</style>
