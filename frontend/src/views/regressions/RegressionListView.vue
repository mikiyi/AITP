<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">回归清单</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建清单</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <div class="toolbar">
        <el-input
          v-model="filters.keyword"
          placeholder="搜索标题"
          clearable
          style="width: 200px"
          @input="scheduleQuery"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 150px" @change="reload">
          <el-option label="草稿" value="draft" />
          <el-option label="就绪" value="ready" />
          <el-option label="执行中" value="executing" />
          <el-option label="已完成" value="done" />
        </el-select>
        <el-select v-model="filters.riskLevel" placeholder="风险" clearable style="width: 140px" @change="reload">
          <el-option label="低" value="low" />
          <el-option label="中" value="medium" />
          <el-option label="高" value="high" />
        </el-select>
        <el-select v-model="filters.versionId" placeholder="版本" clearable style="width: 140px" @change="reload">
          <el-option v-for="item in versionOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button :icon="RefreshLeft" @click="resetFilters">重置</el-button>
      </div>

      <el-table v-loading="loading" :data="rows" border stripe>
        <el-table-column prop="title" label="标题" min-width="170" show-overflow-tooltip />
        <el-table-column label="版本号" width="110">
          <template #default="scope">
            <span class="muted">{{ scope.row.versionCode || scope.row.version || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="版本名称" width="130">
          <template #default="scope">
            <span class="muted">{{ scope.row.versionName || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="affectedModules" label="影响范围" min-width="130" show-overflow-tooltip />
        <el-table-column label="风险" width="90">
          <template #default="scope">
            <el-tag size="small" :type="riskTagType(scope.row.riskLevel)">
              {{ riskText(scope.row.riskLevel) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="scope">
            <el-tag size="small" :type="statusTagType(scope.row.status)" effect="plain">
              {{ statusText(scope.row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="165">
          <template #default="scope">{{ dateTimeText(scope.row.updatedAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click="openDetail(scope.row)">明细</el-button>
            <el-button link @click="openEdit(scope.row)">编辑</el-button>
            <el-button link type="success" @click="openKnowledge(scope.row)">加入知识库</el-button>
            <el-button link type="danger" @click="remove(scope.row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">暂无回归清单</span>
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="pageNum"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @current-change="load"
          @size-change="reload"
        />
      </div>
    </div>

    <el-dialog v-model="editVisible" :title="form.id ? '编辑回归清单' : '新建回归清单'" width="680px">
      <el-form label-width="100px">
        <el-form-item label="项目" required>
          <el-select v-model="form.projectId" placeholder="选择项目" style="width: 100%" @change="onFormProjectChange">
            <el-option
              v-for="item in appStore.projects"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="例如：支付链路 2.3 版本回归" />
        </el-form-item>
        <el-form-item label="变更范围">
          <el-input v-model="form.changeScope" type="textarea" :rows="3" placeholder="本次改了什么" />
        </el-form-item>
        <el-form-item label="影响模块">
          <el-input v-model="form.affectedModules" placeholder="例如：支付/退款、订单中心" />
        </el-form-item>
        <el-form-item label="项目版本">
          <el-select v-model="form.versionId" placeholder="不指定版本" clearable style="width: 100%">
            <el-option v-for="item in formVersionOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="版本备注">
          <el-input v-model="form.version" placeholder="例如：v2.3.0" />
        </el-form-item>
        <el-form-item label="风险等级">
          <el-radio-group v-model="form.riskLevel">
            <el-radio value="low">低</el-radio>
            <el-radio value="medium">中</el-radio>
            <el-radio value="high">高</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="来源">
          <el-radio-group v-model="form.source">
            <el-radio value="manual">手工</el-radio>
            <el-radio value="ai">AI 生成</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.content" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="回归明细" size="62%">
      <template v-if="detail">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="标题">{{ detail.title }}</el-descriptions-item>
          <el-descriptions-item label="版本号">{{ detail.versionCode || detail.version || '-' }}</el-descriptions-item>
          <el-descriptions-item label="版本名称">{{ detail.versionName || '-' }}</el-descriptions-item>
          <el-descriptions-item label="风险">
            <el-tag size="small" :type="riskTagType(detail.riskLevel)">{{ riskText(detail.riskLevel) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="影响模块" :span="3">
            {{ detail.affectedModules || '-' }}
          </el-descriptions-item>
        </el-descriptions>

        <el-descriptions v-if="detail.coverage" :column="2" border class="detail-coverage">
          <el-descriptions-item v-for="row in coverageRows" :key="row.label" :label="row.label">
            <span class="coverage-rate">{{ row.rateText }}</span>
            <span class="muted coverage-parts">{{ row.part }}/{{ row.whole }} 条</span>
            <div class="muted coverage-hint">{{ row.hint }}</div>
          </el-descriptions-item>
        </el-descriptions>

        <div class="toolbar detail-toolbar">
          <el-button type="primary" :icon="MagicStick" :loading="generating" @click="generate">
            AI 生成回归点
          </el-button>
          <el-button :icon="Plus" @click="openAddItem">添加回归点</el-button>
          <el-button type="success" :icon="Check" :disabled="!items.length" @click="submitResults">
            批量提交执行结果
          </el-button>
          <div class="spacer" />
          <el-tag size="small" effect="plain">
            共 {{ items.length }} 条 / 通过 {{ countResult('passed') }} / 失败 {{ countResult('failed') }}
          </el-tag>
        </div>

        <el-table :data="items" border size="small">
          <el-table-column prop="seq" label="#" width="56" align="center" />
          <el-table-column prop="title" label="回归点" min-width="180" />
          <el-table-column prop="module" label="模块" width="130" show-overflow-tooltip />
          <el-table-column prop="priority" label="优先级" width="86" align="center" />
          <el-table-column prop="verifyTarget" label="验证目标" min-width="180" show-overflow-tooltip />
          <el-table-column label="执行方式" width="92" align="center">
            <template #default="scope">
              <el-tooltip
                :content="
                  scope.row.execMode === 'auto'
                    ? '自动：提交时由服务端真跑，结论只认这次真实执行的结果与证据'
                    : '手工：由执行人回写结论，标记「通过」必须附证据'
                "
                placement="top"
              >
                <el-tag :type="scope.row.execMode === 'auto' ? 'primary' : 'info'" size="small" effect="plain">
                  {{ scope.row.execMode === 'auto' ? '自动' : '手工' }}
                </el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="结果" width="150">
            <template #default="scope">
              <el-select
                v-model="scope.row.result"
                size="small"
                style="width: 110px"
                :disabled="scope.row.execMode === 'auto'"
                @change="markDirty(scope.row)"
              >
                <el-option label="通过" value="passed" />
                <el-option label="失败" value="failed" />
                <el-option label="阻塞" value="blocked" />
                <el-option label="跳过" value="skipped" />
                <el-option label="未执行" value="not_executed" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="备注" min-width="150">
            <template #default="scope">
              <el-input
                v-model="scope.row.remark"
                size="small"
                placeholder="备注"
                @change="markDirty(scope.row)"
              />
            </template>
          </el-table-column>
          <el-table-column label="证据" width="128">
            <template #default="scope">
              <el-button
                v-if="scope.row.evidenceId"
                link
                type="success"
                size="small"
                @click="openItemEvidence(scope.row)"
              >
                EV-{{ scope.row.evidenceId }}
              </el-button>
              <el-button
                v-else-if="scope.row.execMode !== 'auto'"
                link
                type="primary"
                size="small"
                @click="openEvidenceForm(scope.row)"
              >
                {{ hasEvidence(scope.row) ? '已填证据' : '填证据' }}
              </el-button>
              <span v-else class="muted">真跑后生成</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="scope">
              <el-button link type="danger" @click="removeItem(scope.row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <span class="muted">还没有回归点，可以 AI 生成或手工添加</span>
          </template>
        </el-table>
      </template>
    </el-drawer>

    <el-dialog v-model="itemVisible" title="添加回归点" width="560px">
      <el-form label-width="96px">
        <el-form-item label="标题" required>
          <el-input v-model="itemForm.title" />
        </el-form-item>
        <el-form-item label="模块">
          <el-input v-model="itemForm.module" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="itemForm.priority" style="width: 100%">
            <el-option label="P0" value="P0" />
            <el-option label="P1" value="P1" />
            <el-option label="P2" value="P2" />
          </el-select>
        </el-form-item>
        <el-form-item label="原因">
          <el-input v-model="itemForm.reason" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="验证目标">
          <el-input v-model="itemForm.verifyTarget" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="itemVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingItem" @click="saveItem">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="evidenceVisible" title="手工回写证据" width="600px" append-to-body>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="标记「通过」必须至少填一项证据；备注不算证据。还没验证的点请标「未执行」或「阻塞」。"
      />
      <el-form label-width="96px" style="margin-top: 12px">
        <el-form-item label="回归点">
          <span class="muted">{{ evidenceForm.title || '-' }}</span>
        </el-form-item>
        <el-form-item label="日志摘要">
          <el-input v-model="evidenceForm.logSummary" type="textarea" :rows="3" placeholder="关键日志、命令输出片段" />
        </el-form-item>
        <el-form-item label="请求摘要">
          <el-input v-model="evidenceForm.requestSummary" type="textarea" :rows="2" placeholder="方法 + 地址 + 关键参数" />
        </el-form-item>
        <el-form-item label="响应摘要">
          <el-input v-model="evidenceForm.responseSummary" type="textarea" :rows="2" placeholder="状态码 + 关键返回字段" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="evidenceVisible = false">取消</el-button>
        <el-button type="primary" @click="applyEvidence">保存证据</el-button>
      </template>
    </el-dialog>

    <EvidenceDrawer v-model="itemEvidenceVisible" :locator="evidenceLocator" title="回归点执行证据" />
  </div>

    <AddToKnowledgeDialog
      v-model="knowledgeVisible"
      :title="knowledgePayload.title"
      :content="knowledgePayload.content"
      ref-type="regression"
      :ref-id="knowledgePayload.refId"
      :ref-label="knowledgePayload.refLabel"
    />
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, MagicStick, Plus, Refresh, RefreshLeft } from '@element-plus/icons-vue'
import { projectVersionApi, regressionApi } from '@/api'
import { dateTimeText } from '@/utils/format'
import { useAutoQuery } from '@/composables/useAutoQuery'
import { useProjectScope } from '@/composables/useProjectScope'
import type { Regression, RegressionItem } from '@/api/types'
import { useAppStore } from '@/stores/app'
import AddToKnowledgeDialog from '@/components/AddToKnowledgeDialog.vue'
import EvidenceDrawer from '@/components/EvidenceDrawer.vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'

interface RegressionForm {
  id: number | null
  projectId: number | null
  title: string
  changeScope: string
  affectedModules: string
  version: string
  versionId: number | null
  riskLevel: string
  source: string
  content: string
}

interface ItemForm {
  title: string
  module: string
  priority: string
  reason: string
  verifyTarget: string
}

const appStore = useAppStore()

const knowledgeVisible = ref(false)
const knowledgePayload = reactive({
  title: '',
  content: '',
  refId: null as number | null,
  refLabel: '',
})

function openKnowledge(row: Regression): void {
  knowledgePayload.title = row.title
  knowledgePayload.content = [
    `# ${row.title}`,
    row.changeScope ? `**变更范围**：${row.changeScope}` : '',
    row.affectedModules ? `**影响模块**：${row.affectedModules}` : '',
    row.content || '',
  ].filter(Boolean).join('\n\n')
  knowledgePayload.refId = Number(row.id)
  knowledgePayload.refLabel = row.title
  knowledgeVisible.value = true
}

const rows = ref<Regression[]>([])
const items = ref<RegressionItem[]>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const saving = ref(false)
const generating = ref(false)
const savingItem = ref(false)
const editVisible = ref(false)
const detailVisible = ref(false)
const itemVisible = ref(false)
const detail = ref<Regression | null>(null)

const filters = reactive({ keyword: '', status: '', riskLevel: '', versionId: null as number | null })

const form = reactive<RegressionForm>({
  id: null,
  projectId: null,
  title: '',
  changeScope: '',
  affectedModules: '',
  version: '',
  versionId: null,
  riskLevel: 'low',
  source: 'manual',
  content: '',
})

/** 筛选下拉跟随本页项目，表单下拉跟随表单里选的项目。 */
const versionOptions = ref<Array<{ label: string; value: number }>>([])
const formVersionOptions = ref<Array<{ label: string; value: number }>>([])

async function versionsOf(projectId: number | null | undefined): Promise<Array<{ label: string; value: number }>> {
  if (!projectId) return []
  try {
    const result = await projectVersionApi.list(projectId)
    return (result.list ?? []).map((v) => ({ label: v.versionCode, value: v.id as number }))
  } catch {
    return []
  }
}

async function loadVersionOptions(): Promise<void> {
  versionOptions.value = await versionsOf(appStore.currentProjectId)
}

async function loadFormVersionOptions(): Promise<void> {
  formVersionOptions.value = await versionsOf(form.projectId)
}

function onFormProjectChange(): void {
  form.versionId = null
  void loadFormVersionOptions()
}

const itemForm = reactive<ItemForm>({
  title: '',
  module: '',
  priority: 'P1',
  reason: '',
  verifyTarget: '',
})

function riskText(level: string): string {
  if (level === 'high') return '高'
  if (level === 'medium') return '中'
  return '低'
}

function riskTagType(level: string): 'danger' | 'warning' | 'info' {
  if (level === 'high') return 'danger'
  if (level === 'medium') return 'warning'
  return 'info'
}

function statusText(status: string): string {
  if (status === 'ready') return '就绪'
  if (status === 'executing') return '执行中'
  if (status === 'done') return '已完成'
  return '草稿'
}

function statusTagType(status: string): 'success' | 'warning' | 'info' {
  if (status === 'done') return 'success'
  if (status === 'executing') return 'warning'
  return 'info'
}

function countResult(result: string): number {
  return items.value.filter((item) => item.result === result).length
}

/** 明细抽屉的执行覆盖：比率一律取服务端 coverage 口径，前端不再自己算分母。 */
const coverageRows = computed(() => {
  const coverage = detail.value?.coverage
  const num = (value?: number) => (typeof value === 'number' ? value : 0)
  return [
    {
      label: '执行覆盖率',
      rateText: `${num(coverage?.executedRate)}%`,
      part: num(coverage?.executed),
      whole: num(coverage?.total),
      hint: '已回写结果的回归点 / 全部回归点（手工与自动都算）',
    },
    {
      label: '自动项占比',
      rateText: `${num(coverage?.autoShare)}%`,
      part: num(coverage?.autoTotal),
      whole: num(coverage?.total),
      hint: '标为自动执行的回归点占比，决定这份清单能真跑到什么程度',
    },
    {
      label: '自动项真跑率',
      rateText: `${num(coverage?.autoRate)}%`,
      part: num(coverage?.autoExecuted),
      whole: num(coverage?.autoTotal),
      hint: '自动项里由服务端真实执行并落证据的占比',
    },
    {
      label: '证据覆盖率',
      rateText: `${num(coverage?.evidenceRate)}%`,
      part: num(coverage?.evidenced),
      whole: num(coverage?.executed),
      hint: '已执行条目中带请求 / 响应摘要或附件证据的占比',
    },
  ]
})

async function load(): Promise<void> {
  loading.value = true
  try {
    const page = await regressionApi.list({
      keyword: filters.keyword,
      status: filters.status,
      riskLevel: filters.riskLevel,
      versionId: filters.versionId ?? undefined,
      projectId: appStore.currentProjectId ?? undefined,
      pageNum: pageNum.value,
      pageSize: pageSize.value,
    })
    rows.value = page.list ?? []
    total.value = page.total ?? 0
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function reload(): void {
  pageNum.value = 1
  void load()
}

const scheduleQuery = useAutoQuery(reload)

function resetFilters(): void {
  filters.keyword = ''
  filters.status = ''
  filters.riskLevel = ''
  filters.versionId = null
  reload()
}

function openCreate(): void {
  form.id = null
  form.projectId = appStore.currentProjectId
  form.title = ''
  form.changeScope = ''
  form.affectedModules = ''
  form.version = ''
  form.versionId = null
  form.riskLevel = 'low'
  form.source = 'manual'
  form.content = ''
  void loadFormVersionOptions()
  editVisible.value = true
}

function openEdit(row: Regression): void {
  form.id = row.id
  form.projectId = row.projectId
  form.title = row.title
  form.changeScope = row.changeScope
  form.affectedModules = row.affectedModules
  form.version = row.version
  form.versionId = Number(row.versionId ?? 0) || null
  form.riskLevel = row.riskLevel
  form.source = row.source
  form.content = row.content
  void loadFormVersionOptions()
  editVisible.value = true
}

async function save(): Promise<void> {
  if (!form.projectId) {
    ElMessage.warning('请选择项目')
    return
  }
  if (!form.title.trim()) {
    ElMessage.warning('标题为必填')
    return
  }
  saving.value = true
  try {
    if (form.id) {
      await regressionApi.update(form.id, { ...form })
    } else {
      await regressionApi.create({ ...form })
    }
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: Regression): Promise<void> {
  try {
    await regressionApi.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

async function openDetail(row: Regression): Promise<void> {
  detailVisible.value = true
  detail.value = row
  try {
    const full = await regressionApi.get(row.id)
    detail.value = full
    items.value = full.items ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载明细失败')
  }
}

async function generate(): Promise<void> {
  if (!detail.value) {
    return
  }
  generating.value = true
  try {
    const result = await regressionApi.generate(detail.value.id)
    ElMessage.success(`已生成 ${result.created} 条回归点`)
    items.value = result.items ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '生成失败')
  } finally {
    generating.value = false
  }
}

function openAddItem(): void {
  itemForm.title = ''
  itemForm.module = ''
  itemForm.priority = 'P1'
  itemForm.reason = ''
  itemForm.verifyTarget = ''
  itemVisible.value = true
}

async function saveItem(): Promise<void> {
  if (!detail.value) {
    return
  }
  if (!itemForm.title.trim()) {
    ElMessage.warning('标题为必填')
    return
  }
  savingItem.value = true
  try {
    await regressionApi.addItem(detail.value.id, { ...itemForm })
    ElMessage.success('已添加')
    itemVisible.value = false
    await openDetail(detail.value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '添加失败')
  } finally {
    savingItem.value = false
  }
}

async function removeItem(item: RegressionItem): Promise<void> {
  if (!detail.value) {
    return
  }
  try {
    await regressionApi.removeItem(detail.value.id, item.id)
    ElMessage.success('已删除')
    await openDetail(detail.value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

function markDirty(item: RegressionItem): void {
  item.executedBy = item.executedBy ?? '本地用户'
  item.executedAt = item.executedAt ?? new Date().toISOString()
}

const evidenceVisible = ref(false)
const itemEvidenceVisible = ref(false)
const evidenceLocator = ref<Record<string, string | number>>({})
const evidenceForm = reactive({ itemId: 0, title: '', logSummary: '', requestSummary: '', responseSummary: '' })

function hasEvidence(item: RegressionItem): boolean {
  return !!(item.logSummary || item.requestSummary || item.responseSummary)
}

function openEvidenceForm(item: RegressionItem): void {
  Object.assign(evidenceForm, {
    itemId: Number(item.id),
    title: item.title ?? '',
    logSummary: item.logSummary ?? '',
    requestSummary: item.requestSummary ?? '',
    responseSummary: item.responseSummary ?? '',
  })
  evidenceVisible.value = true
}

function applyEvidence(): void {
  const target = items.value.find((item) => Number(item.id) === evidenceForm.itemId)
  if (!target) {
    ElMessage.error('回归点已不在清单里，请刷新后重试')
    return
  }
  target.logSummary = evidenceForm.logSummary
  target.requestSummary = evidenceForm.requestSummary
  target.responseSummary = evidenceForm.responseSummary
  markDirty(target)
  evidenceVisible.value = false
}

function openItemEvidence(item: RegressionItem): void {
  // 有证据号按证据取；没有则按回归点定位，查不到时抽屉给中文降级
  evidenceLocator.value = item.evidenceId
    ? { evidenceId: Number(item.evidenceId) }
    : { regressionItemId: Number(item.id) }
  itemEvidenceVisible.value = true
}

async function submitResults(): Promise<void> {
  if (!detail.value) {
    return
  }
  const missing = items.value.filter(
    (item) => item.execMode !== 'auto' && item.result === 'passed' && !hasEvidence(item),
  )
  if (missing.length) {
    ElMessage.warning(
      `${missing.length} 条手工回归点标了「通过」但没有证据：${missing
        .slice(0, 3)
        .map((item) => String(item.title ?? item.id))
        .join('、')}${missing.length > 3 ? ' 等' : ''}。请先「填证据」，或改标「未执行 / 阻塞」。`,
    )
    return
  }
  const results = items.value.map((item) => ({
    itemId: item.id,
    result: item.result || 'not_executed',
    remark: item.remark || '',
    logSummary: item.logSummary || '',
    requestSummary: item.requestSummary || '',
    responseSummary: item.responseSummary || '',
  }))
  try {
    const payload = await regressionApi.execute(detail.value.id, results)
    const autoCount = (payload.executed ?? []).length
    ElMessage.success(
      `已回写 ${payload.updated.length} 条，跳过 ${payload.skipped.length} 条` +
        (autoCount ? `；其中 ${autoCount} 条自动项由服务端真跑` : ''),
    )
    await openDetail(detail.value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  }
}

/** 列表按本页当前项目过滤，切换项目要重拉版本清单并清掉跨项目的版本筛选。 */
useProjectScope({
  load: () => void loadVersionOptions(),
  resetScopedFilters: () => {
    filters.versionId = null
    reload()
  },
})

onMounted(async () => {
  await appStore.loadProjects()
  await load()
})
</script>

<style scoped>
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.detail-toolbar {
  margin: 12px 0;
}

.detail-coverage {
  margin: 12px 0;
}

.detail-coverage .coverage-rate {
  font-weight: 600;
}

.detail-coverage .coverage-parts {
  margin-left: 6px;
}

.detail-coverage .coverage-hint {
  font-size: 12px;
  line-height: 1.5;
}
</style>
