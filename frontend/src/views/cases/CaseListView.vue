<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">测试用例</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="Upload" @click="importVisible = true">导入 xlsx</el-button>
        <BaseExportMenu
          :formats="['xlsx', 'csv', 'json']"
          :resolve-url="resolveExportUrl"
          :guard="exportGuard"
        />
        <el-button :icon="DataAnalysis" @click="openCoverage">覆盖矩阵</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建用例</el-button>
      </div>
    </div>

    <div class="case-layout">
      <div class="panel panel-pad case-nav">
        <h3 class="case-nav__title">
          功能点
          <el-tag size="small" effect="plain">{{ caseTotal }} 条</el-tag>
        </h3>
        <el-input
          v-model="moduleKeyword"
          size="small"
          placeholder="过滤功能点"
          clearable
          class="case-nav__filter"
        />
        <ModuleTree
          ref="moduleNavRef"
          v-loading="moduleNavLoading"
          :nodes="moduleNavNodes"
          :max-height="420"
          :keyword="moduleKeyword"
          :active-query="activeModuleQuery"
          :count-of="navCountOf"
          :query-of="navQueryOf"
          :load-cases="loadNodeCases"
          :empty-text="hasModuleNodes ? '没有匹配的功能点' : '该项目还没有功能点'"
          @select="onModuleNodeClick"
          @open-case="openCaseDetail"
        />
        <p class="case-nav__hint muted">节点数字含下级功能点的用例数，点击筛选，再点一次取消</p>
      </div>

      <div class="panel panel-pad">
        <BaseProTable
          ref="tableRef"
          :columns="columns"
          :fetcher="fetcher"
          :search-fields="searchFields"
          :extra-query="extraQuery"
          selectable
          sync-query
          @row-click="onRowClick"
          @filters-reset="clearModuleNav"
        >
          <template #batch="{ rows }">
            <el-button size="small" type="danger" plain :loading="batchRemoving" @click="batchRemove(rows)">
              批量删除
            </el-button>
          </template>
          <template #caseType="{ row }">
            <el-tag size="small" effect="plain">{{ caseTypeLabel(row.caseType) }}</el-tag>
          </template>
          <template #priority="{ row }">
            <el-tag :type="priorityTag(row.priority)" size="small" effect="plain">{{ row.priority }}</el-tag>
          </template>
          <template #status="{ row }">
            <el-tag :type="statusTag(row.status)" size="small" effect="plain">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
          <template #moduleId="{ row }">
            {{ moduleName(row.moduleId) }}
          </template>
          <template #actions="{ row }">
            <el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button>
            <el-button link type="success" @click.stop="openKnowledge(row)">加入知识库</el-button>
            <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
          </template>
        </BaseProTable>
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑用例' : '新建用例'" width="720px" top="4vh">
      <el-form :model="form" label-width="90px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="格式：场景-条件-预期" />
        </el-form-item>
        <el-form-item label="项目" required>
          <el-select v-model="form.projectId" placeholder="选择项目" @change="onProjectChange">
            <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="版本">
          <el-select v-model="form.versionId" placeholder="选择版本" clearable filterable style="width: 100%">
            <el-option v-for="item in formVersionOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="功能点">
          <el-select v-model="form.moduleId" placeholder="选择功能点" clearable filterable :loading="formModuleLoading">
            <el-option v-for="item in formModuleOptions" :key="item.id" :label="item.path" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="需求">
          <el-select v-model="form.requirementId" placeholder="选择需求" clearable filterable>
            <el-option v-for="item in formRequirementOptions" :key="item.id" :label="item.title" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="用例类型">
          <el-select v-model="form.caseType">
            <el-option v-for="item in caseTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority">
            <el-option v-for="item in ['P0', 'P1', 'P2', 'P3']" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="执行状态">
          <el-select v-model="form.status">
            <el-option v-for="item in caseStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="前置条件">
          <el-input v-model="form.preconditions" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="操作步骤">
          <div class="multiline-editor">
            <el-input
              v-model="form.stepsText"
              type="textarea"
              :rows="6"
              placeholder="每行一条操作步骤，支持换行；操作与预期分框填写，不要混写"
            />
            <div class="multiline-editor__meta">
              <span class="muted">{{ stepLineCount }} 行步骤</span>
              <span v-if="pairMismatch" class="multiline-editor__warn">
                步骤 {{ stepLineCount }} 行 / 预期 {{ expectedLineCount }} 行：按行号配对，缺的预期留空
              </span>
              <span v-else class="muted">与「预期结果」按行号一一对应</span>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="预期结果">
          <div class="multiline-editor">
            <el-input
              v-model="form.expectedText"
              type="textarea"
              :rows="6"
              placeholder="每行一条预期结果，与操作步骤按行号对应，支持换行"
            />
            <div class="multiline-editor__meta">
              <span class="muted">{{ expectedLineCount }} 行预期</span>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="form.tags" placeholder="逗号分隔" />
        </el-form-item>
        <el-form-item v-if="extraFields.length" label="扩展字段">
          <div class="extra-editor">
            <div
              v-for="field in extraFields"
              :key="field.key"
              class="extra-row"
              :class="{ 'extra-row--data': field.key === '测试数据' }"
            >
              <span class="extra-label">{{ field.label }}</span>
              <div v-if="field.key === '测试数据'" class="test-data-editor">
                <el-input
                  v-model="form.extra[field.key]"
                  type="textarea"
                  :rows="3"
                  placeholder="直接填写测试数据，或填本地文件路径：.txt / .csv / .xlsx / .xls / .json / .sql"
                />
                <div class="test-data-editor__bar">
                  <el-button size="small" :loading="previewLoading" @click="previewTestData">解析预览</el-button>
                  <el-tag v-if="testDataPreview" size="small" effect="plain" :type="testDataPreview.kind === 'empty' ? 'info' : 'success'">
                    {{ testDataPreviewLabel }}
                  </el-tag>
                  <span class="muted test-data-editor__hint">自动化 / 性能执行自动消费该数据</span>
                </div>
                <div v-if="testDataPreview && testDataPreview.sampleRows.length" class="test-data-editor__sample">
                  <div v-for="(row, i) in testDataPreview.sampleRows" :key="i" class="test-data-editor__row text-mono">
                    {{ row.join(' | ') }}
                  </div>
                </div>
                <div v-else-if="testDataPreview && testDataPreview.textSample" class="test-data-editor__sample text-mono">
                  {{ testDataPreview.textSample }}
                </div>
              </div>
              <el-input v-else v-model="form.extra[field.key]" :placeholder="field.placeholder" style="flex: 1" />
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="importVisible" title="导入 xlsx 用例" width="520px">
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept=".xlsx"
        :on-change="onFileChange"
        :file-list="fileList"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽 xlsx 文件到此处，或<em>点击选择</em></div>
      </el-upload>
      <p class="muted import-hint">
        导入前请确保已在「项目」中选择目标项目；编号冲突的用例会自动重排编号后导入，不会跳过。
      </p>
      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="doImport">开始导入</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="coverageVisible" title="功能点覆盖矩阵" width="900px">
      <el-alert
        v-if="coverageTableRows.length"
        type="info"
        :closable="false"
        show-icon
        :title="coverageSummary"
      />
      <el-table
        :data="coverageTableRows"
        size="small"
        border
        max-height="520"
        class="coverage-table"
        :row-class-name="coverageRowClass"
      >
        <el-table-column prop="path" label="功能点" min-width="260" show-overflow-tooltip />
        <el-table-column prop="caseCount" label="用例数" width="80" align="right" />
        <el-table-column prop="p0Count" label="P0" width="70" align="right" />
        <el-table-column prop="p1Count" label="P1" width="70" align="right" />
        <el-table-column prop="passCount" label="通过" width="70" align="right" />
        <el-table-column prop="failCount" label="失败" width="70" align="right" />
        <el-table-column prop="blockedCount" label="阻塞" width="70" align="right" />
        <el-table-column prop="notExecutedCount" label="未执行" width="80" align="right" />
      </el-table>
      <el-empty v-if="!coverageLoading && !coverageTableRows.length" description="当前项目还没有功能点数据" />
      <template #footer>
        <el-button @click="coverageVisible = false">关闭</el-button>
        <el-button type="primary" :loading="coverageLoading" @click="loadCoverage">刷新</el-button>
      </template>
    </el-dialog>
  </div>

    <AddToKnowledgeDialog
      v-model="knowledgeVisible"
      :title="knowledgePayload.title"
      :content="knowledgePayload.content"
      ref-type="test_case"
      :ref-id="knowledgePayload.refId"
      :ref-label="knowledgePayload.refLabel"
      :module-id="knowledgePayload.moduleId"
    />
</template>

<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { DataAnalysis, Plus, Upload, UploadFilled } from '@element-plus/icons-vue'
import type { UploadFile, UploadFiles } from 'element-plus'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import BaseExportMenu from '@/components/BaseExportMenu.vue'
import AddToKnowledgeDialog from '@/components/AddToKnowledgeDialog.vue'
import ModuleTree, { type ModuleTreeExpose } from '@/components/ModuleTree.vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { caseApi, moduleApi, projectVersionApi, requirementApi } from '@/api'
import type { PagePayload } from '@/api/request'
import { NODE_CASE_PAGE_SIZE } from '@/composables/useNodeCases'
import type {
  CaseStep,
  CaseTreeNode,
  ModuleCoverage,
  ModuleCoverageStat,
  ModuleNode,
  ModuleOption,
  Requirement,
  TestCase,
  TestDataPreview,
} from '@/api/types'
import { caseTypeText } from '@/utils/format'
import { useProjectScope } from '@/composables/useProjectScope'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const route = useRoute()

const knowledgeVisible = ref(false)
const knowledgePayload = reactive({
  title: '',
  content: '',
  refId: null as number | null,
  refLabel: '',
  moduleId: null as number | null,
})

/** 用例正文整理成 Markdown：前置条件 → 步骤 → 预期结果。 */
function caseMarkdown(row: TestCase): string {
  const lines = [`# ${row.title}`]
  if (row.preconditions) lines.push(`**前置条件**：${row.preconditions}`)
  if (row.steps?.length) {
    lines.push('**测试步骤**\n' + row.steps.map((s, i) => `${i + 1}. ${s.action}`).join('\n'))
  }
  if (row.expectedResult) lines.push('**预期结果**\n' + row.expectedResult)
  if (row.tags) lines.push(`**标签**：${row.tags}`)
  return lines.join('\n\n')
}

function openKnowledge(row: TestCase): void {
  knowledgePayload.title = row.title
  knowledgePayload.content = caseMarkdown(row)
  knowledgePayload.refId = Number(row.id)
  knowledgePayload.refLabel = row.title
  knowledgePayload.moduleId = row.moduleId ?? null
  knowledgeVisible.value = true
}
const appStore = useAppStore()
const tableRef = ref<TableExpose | null>(null)

const caseTypeOptions = [
  { label: '功能测试', value: 'functional' },
  { label: '接口测试', value: 'interface' },
  { label: '边界测试', value: 'boundary' },
  { label: '安全测试', value: 'security' },
  { label: 'UI 测试', value: 'ui' },
  { label: '异常测试', value: 'exception' },
]

const caseStatusOptions = [
  { label: '未执行', value: 'not_executed' },
  { label: '通过', value: 'pass' },
  { label: '失败', value: 'fail' },
  { label: '阻塞', value: 'blocked' },
]

interface ExtraField {
  key: string
  label: string
  placeholder: string
}

/** 各用例类型在 xlsx 里的专属列，对应导出器读取的 extra 字段名。 */
const EXTRA_FIELDS: Record<string, ExtraField[]> = {
  functional: [
    { key: '测试数据', label: '测试数据', placeholder: '' },
    { key: '执行时间', label: '执行时间', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
  interface: [
    { key: '模块/接口名', label: '模块/接口名', placeholder: '' },
    { key: '接口URL', label: '接口URL', placeholder: 'https://' },
    { key: '请求方式', label: '请求方式', placeholder: 'GET/POST' },
    { key: 'token/鉴权方式', label: '鉴权方式', placeholder: 'Bearer Token' },
    { key: '请求头', label: '请求头', placeholder: 'JSON' },
    { key: '请求参数', label: '请求参数', placeholder: 'Query 参数' },
    { key: '请求体', label: '请求体', placeholder: 'JSON 请求体' },
    { key: '预期状态码', label: '预期状态码', placeholder: '200' },
    { key: '预期响应体', label: '预期响应体', placeholder: '' },
    { key: '断言规则', label: '断言规则', placeholder: '' },
    { key: '幂等性校验', label: '幂等性校验', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
  boundary: [
    { key: '边界类型', label: '边界类型', placeholder: '如：等价类边界' },
    { key: '边界描述', label: '边界描述', placeholder: '' },
    { key: '测试数据', label: '测试数据', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
  security: [
    { key: '权限类型', label: '权限类型', placeholder: '如：越权访问' },
    { key: '测试数据', label: '测试数据', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
  ui: [
    { key: '测试数据', label: '测试数据', placeholder: '' },
    { key: '执行时间', label: '执行时间', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
  exception: [
    { key: '异常类型', label: '异常类型', placeholder: '如：网络超时' },
    { key: '测试数据', label: '测试数据', placeholder: '' },
    { key: '备注', label: '备注', placeholder: '' },
  ],
}

/** 列表列渲染用的功能点名称，跟随本页当前项目。 */
const moduleOptions = ref<ModuleOption[]>([])

const extraQuery = computed(() => ({
  projectId: appStore.currentProjectId ?? undefined,
}))

/** 当前项目下的版本清单，供版本下拉筛选。 */
const versionOptions = ref<Array<{ label: string; value: number }>>([])

async function loadVersionOptions(): Promise<void> {
  const pid = appStore.currentProjectId
  if (!pid) {
    versionOptions.value = []
    return
  }
  try {
    const result = await projectVersionApi.list(pid)
    versionOptions.value = result.list.map((v) => ({
      label: v.versionCode,
      value: v.id as number,
    }))
  } catch {
    versionOptions.value = []
  }
}

/**
 * 功能点筛选面板（原「用例树」独立页并入这里）。
 * 节点数字按「本级 + 下级」汇总，点一下写进 moduleId 筛选，与列表看到的结果一致。
 */
interface ModuleNavMeta {
  /** 写进 moduleId 筛选的值：功能点为子树 id 列表，未关联为 'none'，全部为空。 */
  query: string
  /** 本级 + 下级的用例数，与列表按该节点筛出来的条数同口径。 */
  total: number
}

/** 「全部用例」「未关联功能点」是前端合成的节点，接口里没有；负 id 占位避免和真实模块撞键。 */
const NAV_ALL_ID = -1
const NAV_NONE_ID = -2

const moduleNavRef = ref<ModuleTreeExpose | null>(null)
const moduleNavNodes = ref<CaseTreeNode[]>([])
const moduleNavMeta = ref<Map<string, ModuleNavMeta>>(new Map())
const moduleNavLoading = ref(false)
const moduleKeyword = ref('')
const caseTotal = ref(0)
/** 当前写进列表 moduleId 的筛选串：面板高亮与「再点一次取消」都以它为准。 */
const activeModuleQuery = ref('')
const hasModuleNodes = computed(() => moduleNavNodes.value.some((node) => Number(node.id) > 0))

function navCountOf(node: ModuleNode): number {
  return moduleNavMeta.value.get(String(node.id))?.total ?? 0
}

function navQueryOf(node: ModuleNode): string {
  return moduleNavMeta.value.get(String(node.id))?.query ?? String(node.id)
}

function syntheticNavNode(id: number, name: string, count: number): CaseTreeNode {
  return {
    id,
    projectId: appStore.currentProjectId ?? 0,
    parentId: null,
    nodeType: 'feature',
    name,
    code: '',
    description: '',
    sortOrder: 0,
    status: 'active',
    depth: 0,
    path: name,
    references: {},
    cases: [],
    caseCount: count,
    passCount: 0,
    failCount: 0,
    children: [],
  }
}

/**
 * 一次递归把「子树 id 串 + 子树用例数」登记进 meta。
 * 节点数字、moduleId 筛选、用例续取三个口径都来自这里，面板和列表才对得上。
 */
function collectNavMeta(
  list: CaseTreeNode[],
  meta: Map<string, ModuleNavMeta>,
): { ids: string[]; total: number } {
  const ids: string[] = []
  let total = 0
  list.forEach((node) => {
    const child = collectNavMeta((node.children ?? []) as CaseTreeNode[], meta)
    const own = [String(node.id), ...child.ids]
    const count = Number(node.caseCount ?? 0) + child.total
    meta.set(String(node.id), { query: own.join(','), total: count })
    ids.push(...own)
    total += count
  })
  return { ids, total }
}

/** 节点用例续取：筛选口径与「点这个节点筛列表」完全一致，一页 100 条（后端 MAX_PAGE_SIZE）。 */
function loadNodeCases(node: ModuleNode, pageNum: number): Promise<PagePayload<TestCase>> {
  const query = navQueryOf(node)
  return caseApi.list({
    projectId: appStore.currentProjectId ?? undefined,
    versionId: Number(route.query.versionId) || undefined,
    moduleId: query || undefined,
    pageNum,
    pageSize: NODE_CASE_PAGE_SIZE,
  })
}

async function loadModuleNav(): Promise<void> {
  const pid = appStore.currentProjectId
  if (!pid) {
    moduleNavNodes.value = []
    moduleNavMeta.value = new Map()
    caseTotal.value = 0
    moduleNavRef.value?.resetDrilldown()
    return
  }
  moduleNavLoading.value = true
  try {
    // 节点数字跟着版本筛选走，否则和列表里的条数对不上
    const versionId = Number(route.query.versionId) || null
    const data = await caseApi.tree(pid, versionId)
    const meta = new Map<string, ModuleNavMeta>()
    collectNavMeta((data.list ?? []) as CaseTreeNode[], meta)
    // orphans 只是单批回传（后端默认 200 条一批），条数要以 orphanCount 为准
    const orphanCount = Number(data.orphanCount ?? (data.orphans ?? []).length)
    const total = Number(data.total ?? 0)
    meta.set(String(NAV_ALL_ID), { query: '', total })
    meta.set(String(NAV_NONE_ID), { query: 'none', total: orphanCount })
    const nodes: CaseTreeNode[] = [syntheticNavNode(NAV_ALL_ID, '全部用例', total), ...((data.list ?? []) as CaseTreeNode[])]
    if (orphanCount > 0) {
      nodes.push(syntheticNavNode(NAV_NONE_ID, '未关联功能点', orphanCount))
    }
    moduleNavMeta.value = meta
    moduleNavNodes.value = nodes
    caseTotal.value = total
    await nextTick()
    syncModuleNav()
    // 新建 / 删除 / 导入后重拉树：旧缓存的页码不再可信，展开着的节点重取第一页
    moduleNavRef.value?.invalidateCases()
  } catch (err) {
    moduleNavNodes.value = []
    moduleNavMeta.value = new Map()
    caseTotal.value = 0
    ElMessage.error(err instanceof Error ? err.message : '功能点加载失败')
  } finally {
    moduleNavLoading.value = false
  }
}

watch(
  () => route.query.versionId,
  () => {
    void loadModuleNav()
  },
)

/** 地址栏或重置按钮改了筛选，面板高亮要跟着走，否则树和列表两边显示对不上。 */
function syncModuleNav(): void {
  activeModuleQuery.value = String(tableRef.value?.filterQuery().moduleId ?? '')
}

function clearModuleNav(): void {
  activeModuleQuery.value = ''
}

function onModuleNodeClick(node: ModuleNode): void {
  const query = navQueryOf(node)
  const deselect = query !== '' && query === activeModuleQuery.value
  const next = deselect ? '' : query
  activeModuleQuery.value = next
  tableRef.value?.setFilter('moduleId', next || undefined)
}

/** 面板里的用例行：点标题直接进详情，和列表行点击同一条路径。 */
function openCaseDetail(item: TestCase): void {
  void router.push(`/test-cases/${item.id}`)
}

const columns: ColumnDef[] = [
  { prop: 'caseNo', label: '编号', width: 100 },
  { prop: 'title', label: '标题', minWidth: 220 },
  { prop: 'caseType', label: '类型', width: 100, slot: 'caseType' },
  { prop: 'priority', label: '优先级', width: 90, slot: 'priority' },
  { prop: 'status', label: '执行状态', width: 100, slot: 'status' },
  { prop: 'moduleId', label: '功能点', minWidth: 160, slot: 'moduleId' },
  { prop: 'versionCode', label: '版本号', width: 110 },
  { prop: 'versionName', label: '版本名称', width: 120 },
  { prop: 'id', label: '操作', width: 210, slot: 'actions' },
]

const searchFields = computed<SearchField[]>(() => [
  { prop: 'keyword', label: '标题 / 编号' },
  { prop: 'caseType', label: '类型', type: 'select', options: caseTypeOptions },
  { prop: 'priority', label: '优先级', type: 'select', options: ['P0', 'P1', 'P2', 'P3'].map((p) => ({ label: p, value: p })) },
  { prop: 'status', label: '执行状态', type: 'select', options: caseStatusOptions },
  { prop: 'versionId', label: '版本', type: 'select', options: versionOptions.value },
  // 功能点由左侧面板驱动，不占工具栏；登记成筛选项才能进地址栏、被重置清掉、跟着导出走。
  { prop: 'moduleId', label: '功能点', hidden: true },
])

function fetcher(query: Record<string, unknown>) {
  return caseApi.list(query)
}

function caseTypeLabel(value: string): string {
  return caseTypeText(value)
}

function statusLabel(value: string): string {
  return caseStatusOptions.find((item) => item.value === value)?.label ?? value
}

function priorityTag(priority: string): 'danger' | 'warning' | 'info' | 'primary' {
  if (priority === 'P0') {
    return 'danger'
  }
  if (priority === 'P1') {
    return 'warning'
  }
  if (priority === 'P2') {
    return 'info'
  }
  return 'primary'
}

function statusTag(value: string): 'success' | 'danger' | 'warning' | 'info' {
  if (value === 'pass') {
    return 'success'
  }
  if (value === 'fail') {
    return 'danger'
  }
  if (value === 'blocked') {
    return 'warning'
  }
  return 'info'
}

function moduleName(id: number | null): string {
  if (!id) {
    return '-'
  }
  return moduleOptions.value.find((item) => item.id === id)?.name ?? String(id)
}

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<TestCase | null>(null)
const form = reactive({
  title: '',
  projectId: null as number | null,
  moduleId: null as number | null,
  requirementId: null as number | null,
  versionId: null as number | null,
  caseType: 'functional',
  priority: 'P2',
  status: 'not_executed',
  preconditions: '',
  stepsText: '',
  expectedText: '',
  tags: '',
  extra: {} as Record<string, string>,
})

const extraFields = computed<ExtraField[]>(() => EXTRA_FIELDS[form.caseType] ?? [])

const stepLineCount = computed(() => form.stepsText.split('\n').filter((l) => l.trim()).length)
const expectedLineCount = computed(() => form.expectedText.split('\n').filter((l) => l.trim()).length)
const pairMismatch = computed(() =>
  stepLineCount.value > 0 && expectedLineCount.value > 0 && stepLineCount.value !== expectedLineCount.value)

const previewLoading = ref(false)
const testDataPreview = ref<TestDataPreview | null>(null)
const testDataPreviewLabel = computed(() => {
  const p = testDataPreview.value
  if (!p) return ''
  if (p.kind === 'empty') return '空数据'
  const labels: Record<string, string> = {
    excel: 'Excel', json: 'JSON', 'inline-json': 'JSON', sql: 'SQL', txt: '文本', 'inline-text': '文本',
  }
  const base = labels[p.kind] ?? p.kind
  return p.rowCount > 0 ? `${base} · ${p.rowCount} 条记录` : `${base} · 文本`
})

async function previewTestData(): Promise<void> {
  previewLoading.value = true
  try {
    testDataPreview.value = await caseApi.previewTestData(form.extra['测试数据'] ?? '')
  } catch (err) {
    testDataPreview.value = null
    ElMessage.error(err instanceof Error ? err.message : '测试数据解析失败')
  } finally {
    previewLoading.value = false
  }
}

/** 表单里的版本、功能点、需求下拉跟随表单中选的项目，切换项目时重新拉取该项目的清单。 */
const formVersionOptions = ref<Array<{ label: string; value: number }>>([])
const formModuleOptions = ref<ModuleOption[]>([])
const formRequirementOptions = ref<Requirement[]>([])
const formModuleLoading = ref(false)
/** 请求回来时项目可能已经换过一次，只认最后一次请求的项目。 */
const formScopeProjectId = ref<number | null>(null)

watch(() => form.projectId, async (pid) => {
  formScopeProjectId.value = pid ?? null
  if (!pid) {
    formVersionOptions.value = []
    formModuleOptions.value = []
    formRequirementOptions.value = []
    return
  }
  const isStale = () => formScopeProjectId.value !== pid
  formModuleLoading.value = true
  try {
    try {
      const result = await projectVersionApi.list(pid)
      if (!isStale()) {
        formVersionOptions.value = result.list.map((v) => ({
          label: v.versionCode,
          value: v.id as number,
        }))
      }
    } catch {
      if (!isStale()) {
        formVersionOptions.value = []
      }
    }
    try {
      const modules = await moduleApi.options(pid, true)
      if (!isStale()) {
        formModuleOptions.value = modules
      }
    } catch {
      if (!isStale()) {
        formModuleOptions.value = []
      }
    }
    try {
      const page = await requirementApi.list({ projectId: pid, pageSize: 100 })
      if (!isStale()) {
        formRequirementOptions.value = page.list
      }
    } catch {
      if (!isStale()) {
        formRequirementOptions.value = []
      }
    }
  } finally {
    formModuleLoading.value = false
  }
}, { immediate: true })

/** 表单里换了项目，版本、功能点、需求都会指向别的项目，必须一起清掉。 */
function onProjectChange(): void {
  form.versionId = null
  form.moduleId = null
  form.requirementId = null
}

function openCreate(): void {
  editing.value = null
  const fq = tableRef.value?.filterQuery() ?? {}
  const moduleFilter = Number(fq.moduleId)
  Object.assign(form, {
    title: '',
    projectId: appStore.currentProjectId,
    // 列表正按单个功能点筛选时，新建的用例直接挂到该功能点下，保存后不会从列表里消失。
    moduleId: Number.isInteger(moduleFilter) && moduleFilter > 0 ? moduleFilter : null,
    requirementId: null,
    // 列表正按版本筛选时，新建的用例直接挂到该版本下，保存后不会从列表里消失。
    versionId: fq.versionId ? Number(fq.versionId) : null,
    caseType: 'functional',
    priority: 'P2',
    status: 'not_executed',
    preconditions: '',
    stepsText: '',
    expectedText: '',
    tags: '',
    extra: {},
  })
  testDataPreview.value = null
  dialogVisible.value = true
}

async function openEdit(row: TestCase): Promise<void> {
  try {
    const detail = await caseApi.get(row.id)
    editing.value = detail
    Object.assign(form, {
      title: detail.title ?? '',
      projectId: detail.projectId ?? null,
      moduleId: detail.moduleId ?? null,
      requirementId: detail.requirementId ?? null,
      versionId: detail.versionId ?? null,
      caseType: detail.caseType ?? 'functional',
      priority: detail.priority ?? 'P2',
      status: detail.status ?? 'not_executed',
      preconditions: detail.preconditions ?? '',
      stepsText: (detail.steps ?? []).map((s) => s.action ?? '').join('\n'),
      expectedText: (detail.steps ?? []).some((s) => (s.expected ?? '').trim())
        ? (detail.steps ?? []).map((s) => s.expected ?? '').join('\n')
        : (detail.expectedResult ?? ''),
      tags: detail.tags ?? '',
      extra: { ...(detail.extra ?? {}) },
    })
    testDataPreview.value = null
    dialogVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载用例失败')
  }
}

async function save(): Promise<void> {
  if (!form.title.trim()) {
    ElMessage.warning('标题为必填项')
    return
  }
  if (!form.projectId) {
    ElMessage.warning('请先选择项目')
    return
  }
  saving.value = true
  try {
    const trimTail = (lines: string[]): string[] => {
      const out = lines.map((line) => line.trim())
      while (out.length && !out[out.length - 1]) out.pop()
      return out
    }
    const stepLines = trimTail(form.stepsText.split('\n'))
    const expectedLines = trimTail(form.expectedText.split('\n'))
    const steps: CaseStep[] = []
    for (let i = 0; i < Math.max(stepLines.length, expectedLines.length); i++) {
      const action = stepLines[i] ?? ''
      const expected = expectedLines[i] ?? ''
      if (action || expected) steps.push({ action, expected })
    }
    const payload = {
      ...form,
      steps,
      expectedResult: form.expectedText,
    }
    if (editing.value) {
      await caseApi.update(editing.value.id, payload)
    } else {
      await caseApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value?.reload()
    void loadModuleNav()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: TestCase): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除用例「${row.title}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await caseApi.remove(row.id)
    ElMessage.success('已删除')
    tableRef.value?.reload()
    void loadModuleNav()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

function onRowClick(row: TestCase): void {
  void router.push(`/test-cases/${row.id}`)
}

const batchRemoving = ref(false)

async function batchRemove(rows: Record<string, any>[]): Promise<void> {
  const ids = rows.map((row) => Number(row.id)).filter((id) => Number.isInteger(id) && id > 0)
  if (!ids.length) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${ids.length} 条用例？删除为软删，仍会占用编号。`,
      '批量删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  batchRemoving.value = true
  try {
    const result = await caseApi.batchRemove(ids)
    const skipped = result.skipped?.length ? `，跳过 ${result.skipped.length} 条` : ''
    ElMessage.success(`已删除 ${result.count} 条${skipped}`)
    tableRef.value?.clearSelection()
    tableRef.value?.reload()
    void loadModuleNav()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量删除失败')
  } finally {
    batchRemoving.value = false
  }
}

const importVisible = ref(false)
const importing = ref(false)
const fileList = ref<UploadFiles>([])
let pickedFile: UploadFile | null = null

function onFileChange(file: UploadFile, files: UploadFiles): void {
  pickedFile = file
  fileList.value = files.slice(-1)
}

async function doImport(): Promise<void> {
  if (!pickedFile?.raw) {
    ElMessage.warning('请先选择 xlsx 文件')
    return
  }
  if (!appStore.currentProjectId) {
    ElMessage.warning('请先在本页「项目」下拉选择项目')
    return
  }
  importing.value = true
  try {
    const data = new FormData()
    data.append('file', pickedFile.raw)
    data.append('projectId', String(appStore.currentProjectId))
    const result = await caseApi.importExcel(data)
    const renumbered = result.renumbered ?? 0
    ElMessage.success(
      `导入完成：新增 ${result.created ?? 0} 条，跳过 ${result.skipped ?? 0} 条` +
        (renumbered ? `，${renumbered} 条编号冲突已自动重排` : ''),
    )
    importVisible.value = false
    tableRef.value?.reload()
    void loadModuleNav()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '导入失败')
  } finally {
    importing.value = false
  }
}

function exportGuard(): string {
  return appStore.currentProjectId ? '' : '请先在本页「项目」下拉选择项目'
}

function resolveExportUrl(format: string): string {
  return caseApi.exportUrl({
    projectId: appStore.currentProjectId,
    format,
    ...tableRef.value?.filterQuery(),
  })
}

const coverageVisible = ref(false)
const coverageLoading = ref(false)
const coverageRows = ref<ModuleCoverage[]>([])
const coverageUnassigned = ref<ModuleCoverageStat | null>(null)
const coverageTotal = ref(0)

/** 未关联功能点的用例单独占一行，保证和当前范围的用例总数对得上。 */
const coverageTableRows = computed<ModuleCoverage[]>(() => {
  const left = coverageUnassigned.value
  if (!left || left.caseCount === 0) {
    return coverageRows.value
  }
  return [
    ...coverageRows.value,
    {
      moduleId: 0,
      name: '未关联功能点',
      nodeType: 'module',
      parentId: null,
      path: '未关联功能点',
      ...left,
    },
  ]
})

const coverageSummary = computed(() => {
  const left = coverageUnassigned.value?.caseCount ?? 0
  return `当前范围共 ${coverageTotal.value} 条用例，其中未关联功能点 ${left} 条。`
    + '数字按「本级 + 下级」统计，与左侧功能点面板一致。'
})

function coverageRowClass({ row }: { row: ModuleCoverage }): string {
  return row.moduleId === 0 ? 'coverage-table__row--unassigned' : ''
}

async function loadCoverage(): Promise<void> {
  const projectId = appStore.currentProjectId
  if (!projectId) {
    coverageRows.value = []
    coverageUnassigned.value = null
    coverageTotal.value = 0
    return
  }
  coverageLoading.value = true
  try {
    // 矩阵跟着当前列表的版本筛选走，否则和列表里的条数对不上
    const versionId = Number(tableRef.value?.filterQuery().versionId) || null
    const result = await caseApi.coverageModules(projectId, versionId)
    const rows = result.list ?? []
    const unassigned = result.unassigned ?? null
    coverageRows.value = rows
    coverageUnassigned.value = unassigned
    // 根节点之间不重叠；根之和 + 未关联 = 当前范围用例总数
    coverageTotal.value =
      rows.filter((row) => row.parentId === null).reduce((sum, row) => sum + row.caseCount, 0)
      + (unassigned?.caseCount ?? 0)
  } catch (err) {
    coverageRows.value = []
    coverageUnassigned.value = null
    coverageTotal.value = 0
    ElMessage.error(err instanceof Error ? err.message : '加载覆盖矩阵失败')
  } finally {
    coverageLoading.value = false
  }
}

function openCoverage(): void {
  coverageVisible.value = true
  void loadCoverage()
}

async function loadOptions(): Promise<void> {
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

useProjectScope({
  load: () => {
    void loadOptions()
    void loadVersionOptions()
    void loadModuleNav()
  },
  resetScopedFilters: () => {
    clearModuleNav()
    // 旧项目的版本、功能点筛选在新项目里不存在，留着会把列表筛成空。
    tableRef.value?.clearFilter(['versionId', 'moduleId'])
  },
})
</script>

<style scoped>
.case-layout {
  display: grid;
  grid-template-columns: minmax(200px, 236px) minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

/* 表格列宽之和超过剩余空间时靠自身横向滚动，不能把网格列撑破 */
.case-layout > .panel {
  min-width: 0;
}

.case-nav__title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  font-size: 15px;
}

.case-nav__filter {
  margin-bottom: 8px;
}

.case-nav__hint {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.5;
}

@media (max-width: 1280px) {
  .case-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

.multiline-editor {
  width: 100%;
}

.multiline-editor__meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 4px;
  font-size: 12px;
}

.multiline-editor__warn {
  color: var(--el-color-warning);
}

.extra-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 8px;
}

.extra-row--data {
  align-items: stretch;
}

.test-data-editor {
  flex: 1;
  min-width: 0;
}

.test-data-editor__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 6px;
}

.test-data-editor__hint {
  font-size: 12px;
}

.test-data-editor__sample {
  margin-top: 6px;
  padding: 6px 8px;
  background: var(--aitp-subtle);
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  font-size: 12px;
  max-height: 132px;
  overflow-y: auto;
}

.test-data-editor__row {
  white-space: pre-wrap;
  word-break: break-all;
}

.import-hint {
  font-size: 12px;
  margin: 10px 0 0;
}

.extra-editor {
  width: 100%;
}

.extra-label {
  width: 110px;
  flex: none;
  font-size: 12px;
  color: var(--aitp-text-secondary, #606266);
  padding-top: 6px;
}

.coverage-table {
  margin-top: 12px;
}

.coverage-table :deep(.coverage-table__row--unassigned) {
  color: var(--el-text-color-secondary);
  font-style: italic;
}
</style>

<style>
/* 用例编辑对话框：内容超高时可滚动到底，滚动条沿用主题色而非浏览器默认。
   用 :has 锁定含多行步骤编辑器的对话框，不依赖 Element Plus 的 custom-class（新版已忽略）。 */
.el-dialog:has(.multiline-editor) .el-dialog__body {
  max-height: calc(100vh - 220px);
  overflow-y: auto;
  padding-right: 14px;
  scrollbar-width: thin;
  scrollbar-color: var(--el-color-primary-light-5, #79bbff) var(--aitp-subtle, #f2f4f3);
}
.el-dialog:has(.multiline-editor) .el-dialog__body::-webkit-scrollbar {
  width: 8px;
}
.el-dialog:has(.multiline-editor) .el-dialog__body::-webkit-scrollbar-track {
  background: var(--aitp-subtle, #f2f4f3);
  border-radius: 4px;
}
.el-dialog:has(.multiline-editor) .el-dialog__body::-webkit-scrollbar-thumb {
  background: var(--el-color-primary-light-5, #79bbff);
  border-radius: 4px;
}
.el-dialog:has(.multiline-editor) .el-dialog__body::-webkit-scrollbar-thumb:hover {
  background: var(--el-color-primary-light-3, #a0cfff);
}
</style>
