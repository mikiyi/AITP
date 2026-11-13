<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">知识库管理</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
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

    <!-- 知识库概览：正式条目、待审核、分类、标签与落盘情况 -->
    <div class="knowledge-stats">
      <MetricCard
        label="正式条目"
        :value="storageInfo?.articleCount ?? 0"
        icon="Notebook"
        tone="primary"
      />
      <MetricCard
        label="待审核"
        :value="pendingCount"
        icon="Clock"
        tone="warning"
        hint="审核通过才计入正式库"
      />
      <MetricCard
        label="分类"
        :value="categoryStore.list.length"
        icon="Collection"
        tone="info"
      />
      <MetricCard
        label="标签"
        :value="tagCount"
        icon="PriceTag"
        tone="success"
      />
      <MetricCard
        label="落盘文件"
        :value="storageInfo?.fileCount ?? 0"
        icon="Document"
        tone="primary"
        :hint="storageInfo?.fileMode === 'mirror' ? '双写 Markdown' : '仅存数据库'"
      />
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
              <el-tag
                size="small"
                effect="plain"
                :type="row.sourceType === 'assistant' ? 'success' : 'info'"
              >
                {{ row.sourceTypeLabel || row.sourceType }}
              </el-tag>
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
        <template #label>正式库与复用</template>

        <div class="panel panel-pad">
          <el-alert
            type="info"
            show-icon
            :closable="false"
            title="本项目知识与全局知识在同一张表：归属列标明「本项目 / 全局·来源」，复用列列出引用了它的项目。切到「全局」范围可发现其他项目共享的知识并引用到本项目；正文只保留一份，源改动同步到所有引用方。"
            class="reuse-hint"
          />
          <BaseProTable
            ref="articleTableRef"
            :columns="articleColumns"
            :fetcher="articleFetcher"
            :search-fields="articleSearchFields"
            sync-query
            query-prefix="a_"
            @row-click="onArticleClick"
            @loaded="onArticlesLoaded"
          >
            <template #toolbar>
              <el-select v-model="articleScope" style="width: 170px" @change="reloadArticles">
                <el-option label="本项目 + 已引用" value="available" />
                <el-option label="仅本项目" value="project" />
                <el-option label="全局（可复用）" value="global" />
                <el-option label="全部" value="all" />
              </el-select>
              <BaseExportMenu :resolve-url="resolveArticleExportUrl" />
            </template>
            <template #ownership="{ row }">
              <div class="ownership">
                <el-tag v-if="isGlobal(row)" size="small" type="warning" effect="plain">
                  全局{{ row.sourceProjectName ? ` · 来自${row.sourceProjectName}` : '' }}
                </el-tag>
                <el-tag v-else size="small" type="success" effect="plain">本项目</el-tag>
                <span v-if="isReferencedByCurrent(row)" class="ownership__self">已引用到本项目</span>
              </div>
              <div v-if="(row.referencedProjects || []).length" class="ownership__refs">
                <span class="muted">复用：</span>
                <el-tag
                  v-for="ref in row.referencedProjects"
                  :key="ref.projectId"
                  size="small"
                  effect="plain"
                  class="ownership__ref"
                >
                  {{ ref.projectName || `项目#${ref.projectId}` }}
                </el-tag>
              </div>
            </template>
            <template #sourceType="{ row }">
              <el-tag
                size="small"
                effect="plain"
                :type="row.sourceType === 'assistant' ? 'success' : 'info'"
              >
                {{ row.sourceTypeLabel || row.sourceType }}
              </el-tag>
            </template>
            <template #reviewStatus="{ row }">
              <el-tag :type="articleStatusType(row)" size="small" effect="plain">
                {{ articleStatusLabel(row) }}
              </el-tag>
            </template>
            <template #currentVersion="{ row }">
              <span>v{{ row.currentVersion ?? 1 }}</span>
              <el-tag
                v-if="hasPendingProposal(row)"
                size="small"
                type="warning"
                effect="plain"
                class="version-pending"
              >
                待审 v{{ row.draftVersion }}
              </el-tag>
            </template>
            <template #organize="{ row }">
              <el-button
                v-if="row.sourceType === 'assistant' && row.reviewStatus === 'published'"
                link
                :type="row.organizeStatus === 'done' ? 'success' : 'primary'"
                :loading="row.organizeStatus === 'running'"
                :disabled="row.organizeStatus === 'running' || row.organizeStatus === 'done'"
                @click.stop="startOrganize(row)"
              >
                {{ organizeLabel(row) }}
              </el-button>
              <span v-else class="muted">-</span>
            </template>
            <template #actions="{ row }">
              <el-button link type="primary" @click.stop="openArticle(row)">查看</el-button>
              <el-button
                v-if="canPromote(row)"
                link
                type="success"
                @click.stop="promoteToGlobal(row)"
              >
                提升为全局
              </el-button>
              <el-button
                v-if="canReference(row)"
                link
                type="success"
                @click.stop="referenceToCurrentProject(row)"
              >
                引用到本项目
              </el-button>
              <el-button
                v-if="isGlobal(row) && isReferencedByCurrent(row)"
                link
                type="warning"
                @click.stop="unreferenceFromCurrentProject(row)"
              >
                取消引用
              </el-button>
              <el-button
                v-if="!isGlobal(row) && row.reviewStatus === 'published'"
                link
                type="warning"
                @click.stop="revoke(row)"
              >
                撤销
              </el-button>
              <el-button v-else-if="!isGlobal(row)" link type="success" @click.stop="restore(row)">
                恢复
              </el-button>
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
          <!-- 弹窗会盖住页头的项目切换器，这里把项目名写进占位文案，避免选错功能点 -->
          <el-select v-model="form.moduleId" clearable filterable :placeholder="modulePlaceholder">
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

    <!-- 外发前的站内检查：浏览器打不开任意本地文件，这里把内容与路径一起摆出来 -->
    <el-drawer
      v-model="sanitizeVisible"
      title="外发前数据检查：知识库问答助手来源条目"
      size="52%"
      @closed="onSanitizeClosed"
    >
      <el-skeleton v-if="sanitizeLoading" :rows="6" animated />
      <template v-else>
        <el-alert
          type="info"
          show-icon
          :closable="false"
          :title="sanitizeMeta?.fileMode === 'mirror'
            ? `共 ${sanitizeMeta?.articleCount ?? 0} 篇，文件目录：${sanitizeMeta?.storagePath ?? ''}`
            : '当前为仅存数据库模式，没有 .md 文件，下面展示数据库正文'"
          class="sanitize-hint"
        />
        <el-alert
          type="success"
          show-icon
          :closable="false"
          title="发送到云端模型前会自动脱敏手机号、身份证、邮箱、银行卡号、API Key 与账号口令；这里展示的是脱敏前原文，便于人工核对。"
          class="sanitize-hint"
        />
        <el-empty v-if="!sanitizeFiles.length" description="还没有问答助手来源的正式条目" />
        <div v-for="file in sanitizeFiles" :key="file.articleId" class="sanitize-item">
          <div class="sanitize-item__head">
            <span class="sanitize-item__title">{{ file.title }}</span>
            <el-tag size="small" effect="plain" :type="file.exists ? 'success' : 'warning'">
              {{ file.fromFile ? '已读取 .md' : '数据库正文' }}
            </el-tag>
            <el-tag v-if="file.truncated" size="small" type="warning" effect="plain">已截断</el-tag>
          </div>
          <div class="sanitize-item__path">
            <span class="muted">{{ file.absolutePath || '未落盘（仅存数据库）' }}</span>
            <el-button
              v-if="file.absolutePath"
              link
              type="primary"
              size="small"
              @click="copyPath(file.absolutePath)"
            >
              复制路径
            </el-button>
          </div>
          <pre class="sanitize-item__content">{{ file.content }}</pre>
        </div>
      </template>
    </el-drawer>

    <ReferenceBlockDialog
      v-model="blockedVisible"
      :message="blockedMessage"
      :refs="blockedRefs"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Files, Plus, Search } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import BaseExportMenu from '@/components/BaseExportMenu.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import MetricCard from '@/components/MetricCard.vue'
import { knowledgeApi, moduleApi, workbenchApi } from '@/api'
import type { PagePayload } from '@/api/request'
import type {
  AssistantPreview,
  AssistantPreviewFile,
  KnowledgeArticle,
  KnowledgeDraft,
  KnowledgeStorage,
  ModuleOption,
} from '@/api/types'
import { useAiGuardStore } from '@/stores/aiGuard'
import { useAppStore } from '@/stores/app'
import { useBadgeStore } from '@/stores/badges'
import { DEFAULT_KNOWLEDGE_CATEGORY, useKnowledgeCategoryStore } from '@/stores/knowledgeCategory'
import { useReferenceGuard } from '@/composables/useReferenceGuard'
import ReferenceBlockDialog from '@/components/ReferenceBlockDialog.vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'

const router = useRouter()
const appStore = useAppStore()
const badgeStore = useBadgeStore()
const categoryStore = useKnowledgeCategoryStore()
const aiGuard = useAiGuardStore()

const activeTab = ref('drafts')
const articleScope = ref<'available' | 'project' | 'global' | 'all'>('available')
const keyword = ref('')
const pendingCount = ref(0)
const storageInfo = ref<KnowledgeStorage | null>(null)
const tagCount = ref(0)
const moduleOptions = ref<ModuleOption[]>([])

const modulePlaceholder = computed(() =>
  appStore.currentProject
    ? `当前项目「${appStore.currentProject.name}」的功能点`
    : '请先在本页「项目」下拉选择项目',
)

const { blockedVisible, blockedRefs, blockedMessage, handleError } = useReferenceGuard()

const draftTableRef = ref<TableExpose | null>(null)
const articleTableRef = ref<TableExpose | null>(null)

const categoryOptions = computed(() => categoryStore.options)

function articleStatusLabel(row: KnowledgeArticle): string {
  if (row.reviewStatus === 'published') {
    return '已发布'
  }
  return row.reviewStatus === 'merged' ? '已合并' : '已撤销'
}

function articleStatusType(row: KnowledgeArticle): 'success' | 'warning' | 'info' {
  if (row.reviewStatus === 'published') {
    return 'success'
  }
  return row.reviewStatus === 'merged' ? 'warning' : 'info'
}

/** 草稿版本号高于当前版本，说明有未审的整理/编辑提案。 */
function hasPendingProposal(row: KnowledgeArticle): boolean {
  return Number(row.draftVersion ?? 0) > Number(row.currentVersion ?? 1)
}

const draftColumns: ColumnDef[] = [
  { prop: 'title', label: '标题', minWidth: 220 },
  { prop: 'sourceType', label: '来源', width: 130, slot: 'sourceType' },
  { prop: 'producer', label: '产出能力', width: 140, slot: 'producer' },
  { prop: 'reviewStatus', label: '状态', width: 100, slot: 'reviewStatus' },
  { prop: 'answeredAt', label: '回答时间', width: 170 },
  { prop: 'createdAt', label: '产生时间', width: 170 },
  { prop: 'id', label: '操作', width: 140, slot: 'actions' },
]

const SOURCE_TYPE_OPTIONS = [
  { label: '知识库问答助手', value: 'assistant' },
  { label: 'AI 产出', value: 'ai_output' },
  { label: '手工录入', value: 'manual' },
  { label: '会话', value: 'session' },
  { label: '测试用例', value: 'case' },
  { label: '缺陷', value: 'bug' },
  { label: '测试报告', value: 'report' },
]

const draftSearchFields: SearchField[] = [
  { prop: 'keyword', label: '标题' },
  { prop: 'sourceType', label: '来源', type: 'select', options: SOURCE_TYPE_OPTIONS },
]

const articleColumns: ColumnDef[] = [
  { prop: 'title', label: '标题', minWidth: 200 },
  { prop: 'ownership', label: '归属 / 复用项目', minWidth: 200, slot: 'ownership' },
  { prop: 'sourceType', label: '来源', width: 120, slot: 'sourceType' },
  { prop: 'category', label: '分类', width: 100 },
  { prop: 'currentVersion', label: '版本', width: 110, slot: 'currentVersion' },
  { prop: 'reviewStatus', label: '状态', width: 90, slot: 'reviewStatus' },
  { prop: 'updatedAt', label: '更新时间', width: 160 },
  { prop: 'organizeStatus', label: '自动整理', width: 120, slot: 'organize' },
  { prop: 'id', label: '操作', width: 250, slot: 'actions' },
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
    scope: articleScope.value,
    projectId: appStore.currentProjectId ?? undefined,
    keyword: keyword.value.trim() || query.keyword || undefined,
  })
}

function reloadArticles(): void {
  articleTableRef.value?.reload()
}

/** 全局知识 = 归属项目为空（提升后保留来源项目）。 */
function isGlobal(row: KnowledgeArticle): boolean {
  return row.projectId == null
}

function isReferencedByCurrent(row: KnowledgeArticle): boolean {
  const pid = Number(appStore.currentProjectId)
  return (row.referencedProjects || []).some((ref) => Number(ref.projectId) === pid)
}

function canPromote(row: KnowledgeArticle): boolean {
  return row.reviewStatus === 'published'
    && row.projectId != null
    && Number(row.projectId) === Number(appStore.currentProjectId)
}

function canReference(row: KnowledgeArticle): boolean {
  if (!appStore.currentProjectId) {
    return false
  }
  if (!isGlobal(row)) {
    return false
  }
  const sourceId = row.sourceProjectId ?? row.projectId
  if (sourceId != null && Number(sourceId) === Number(appStore.currentProjectId)) {
    return false
  }
  return !isReferencedByCurrent(row)
}

async function promoteToGlobal(row: KnowledgeArticle): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `把《${row.title}》提升为全局知识？提升后所有项目可见并可按需引用，正文仍只保留这一份。`,
      '提升为全局知识',
      { type: 'warning', confirmButtonText: '提升' },
    )
  } catch {
    return
  }
  try {
    await knowledgeApi.promoteArticle(row.id)
    ElMessage.success('已提升为全局知识，来源项目已保留')
    articleTableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提升失败')
  }
}

async function referenceToCurrentProject(row: KnowledgeArticle): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) {
    ElMessage.warning('请先在顶部选择要引用到的项目')
    return
  }
  try {
    await knowledgeApi.referenceProject(row.id, Number(projectId))
    ElMessage.success(`已引用到「${appStore.currentProject?.name || '当前项目'}」`)
    articleTableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '引用失败')
  }
}

async function unreferenceFromCurrentProject(row: KnowledgeArticle): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `取消「${appStore.currentProject?.name || '当前项目'}」对《${row.title}》的引用？正文仍保留在全局，其他引用方不受影响。`,
      '取消引用',
      { type: 'warning', confirmButtonText: '取消引用' },
    )
  } catch {
    return
  }
  try {
    await knowledgeApi.unreferenceProject(row.id, Number(projectId))
    ElMessage.success('已取消引用')
    articleTableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '取消引用失败')
  }
}

function resolveArticleExportUrl(format: string): string {
  const filters = articleTableRef.value?.filterQuery() ?? {}
  return knowledgeApi.exportArticlesUrl({
    ...filters,
    scope: articleScope.value,
    projectId: appStore.currentProjectId ?? undefined,
    keyword: keyword.value.trim() || filters.keyword || undefined,
    format,
  })
}

async function loadStorageStats(): Promise<void> {
  try {
    storageInfo.value = await knowledgeApi.storage()
  } catch {
    storageInfo.value = null
  }
  try {
    const tags = await knowledgeApi.tags()
    tagCount.value = Array.isArray(tags) ? tags.length : 0
  } catch {
    tagCount.value = 0
  }
}

async function refreshPending(): Promise<void> {
  try {
    const overview = await workbenchApi.overview()
    pendingCount.value = overview.pendingKnowledge ?? 0
  } catch {
    pendingCount.value = 0
  }
  // 待审数变了同步导航角标（knowledge 角标是实时状态，审核后立刻反映）
  await badgeStore.load()
  await loadStorageStats()
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

// ---------------- 问答助手来源条目的自动整理 ----------------

function organizeLabel(row: KnowledgeArticle): string {
  if (row.organizeStatus === 'running') {
    return '后台自动整理中'
  }
  if (row.organizeStatus === 'done') {
    return '已整理完'
  }
  return '待自动整理'
}

const sanitizeVisible = ref(false)
const sanitizeLoading = ref(false)
const sanitizeMeta = ref<AssistantPreview | null>(null)
const sanitizeFiles = ref<AssistantPreviewFile[]>([])
let sanitizeResolve: (() => void) | null = null

function onSanitizeClosed(): void {
  sanitizeResolve?.()
  sanitizeResolve = null
}

/** 打开站内 .md 检查，用户关闭抽屉后 resolve，确认框随之重新出现。 */
async function openSanitizePreview(): Promise<void> {
  // 先挂关闭回调再发请求：用户可能在请求返回前就关掉抽屉
  const closed = new Promise<void>((resolve) => {
    sanitizeResolve = resolve
  })
  sanitizeVisible.value = true
  sanitizeLoading.value = true
  try {
    const payload = await knowledgeApi.assistantPreview()
    sanitizeMeta.value = payload
    sanitizeFiles.value = payload.files ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '读取外发数据失败')
  } finally {
    sanitizeLoading.value = false
  }
  await closed
}

async function copyPath(path: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(path)
    ElMessage.success('路径已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择路径')
  }
}

let organizeTimer = 0
let watchingArticleId: number | null = null

function stopOrganizePolling(): void {
  if (organizeTimer) {
    window.clearInterval(organizeTimer)
    organizeTimer = 0
  }
}

function startOrganizePolling(articleId: number): void {
  if (organizeTimer && watchingArticleId === articleId) {
    return
  }
  stopOrganizePolling()
  watchingArticleId = articleId
  organizeTimer = window.setInterval(() => {
    void pollOrganize()
  }, 4000)
}

async function pollOrganize(): Promise<void> {
  const articleId = watchingArticleId
  if (!articleId) {
    stopOrganizePolling()
    return
  }
  try {
    const status = await knowledgeApi.organizeStatus(articleId)
    if (status.organizeStatus === 'running') {
      return
    }
    stopOrganizePolling()
    await articleTableRef.value?.load()
    if (status.task?.status === 'success') {
      ElMessage.success('自动整理完成，已生成待审核的整理提案')
    } else if (status.task?.status === 'failed') {
      ElMessage.error(`自动整理失败：${status.task.errorMessage || '未知原因'}`)
    } else if (status.task?.status === 'cancelled') {
      ElMessage.warning('自动整理已取消')
    }
  } catch {
    // 轮询失败不打扰用户，下一次表格加载会重新判断是否需要继续
    stopOrganizePolling()
  }
}

/** 列表里只要有「整理中」就继续轮询，刷新页面或切回本页也能接上。 */
function onArticlesLoaded(payload: PagePayload<KnowledgeArticle>): void {
  const running = (payload.list ?? []).find((row) => row.organizeStatus === 'running')
  if (running) {
    startOrganizePolling(Number(running.id))
  } else {
    stopOrganizePolling()
  }
}

async function startOrganize(row: KnowledgeArticle): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '是否要调用LLM将来源于知识库问答助手的知识库正式数据做自动去重、归类、整理？',
      '自动整理确认',
      { type: 'warning', confirmButtonText: '开始整理', cancelButtonText: '不整理' },
    )
  } catch {
    return
  }
  // 本地首选失败会降级到云端，是否外发以后端候选链为准
  let externalPossible = true
  try {
    externalPossible = (await knowledgeApi.assistantEgress('hybrid')).externalPossible
  } catch {
    externalPossible = true
  }
  const confirmed = await aiGuard.ensureExternalConfirmed({
    externalPossible,
    onInspect: externalPossible ? openSanitizePreview : undefined,
  })
  if (!confirmed) {
    return
  }
  try {
    await knowledgeApi.organizeArticle(row.id, { desensitized: externalPossible })
    ElMessage.success('已提交后台自动整理，结果会生成待审核的整理提案')
    await articleTableRef.value?.load()
    startOrganizePolling(Number(row.id))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '发起自动整理失败')
  }
}

onBeforeUnmount(stopOrganizePolling)

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
  // 归属 / 复用列与「本项目+已引用」范围都依赖当前项目，切项目后重拉列表
  articleTableRef.value?.reload()
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

.version-pending {
  margin-left: 6px;
}

.knowledge-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.knowledge-tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}

.reuse-hint {
  margin-bottom: 10px;
}

.ownership {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.ownership__self {
  font-size: 12px;
  color: var(--el-color-success);
}

.ownership__refs {
  margin-top: 4px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  font-size: 12px;
}

.ownership__ref {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
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

.sanitize-hint {
  margin-bottom: 10px;
}

.sanitize-item {
  padding: 10px 0;
  border-bottom: 1px solid var(--aitp-border);
}

.sanitize-item:last-child {
  border-bottom: none;
}

.sanitize-item__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.sanitize-item__title {
  font-weight: 600;
  font-size: 13px;
}

.sanitize-item__path {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0 6px;
  font-size: 12px;
  word-break: break-all;
}

.sanitize-item__content {
  margin: 0;
  max-height: 240px;
  overflow: auto;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--aitp-subtle);
  border: 1px solid var(--aitp-border);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
