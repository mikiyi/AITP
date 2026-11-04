<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">回归清单</h2>
      <div class="toolbar" style="margin: 0">
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
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
        <el-button :icon="RefreshLeft" @click="resetFilters">重置</el-button>
      </div>

      <el-table v-loading="loading" :data="rows" border stripe>
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="version" label="版本" width="100">
          <template #default="scope">
            <span class="muted">{{ scope.row.version || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="affectedModules" label="影响范围" min-width="160" show-overflow-tooltip />
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
        <el-table-column prop="updatedAt" label="更新时间" width="170" />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click="openDetail(scope.row)">明细</el-button>
            <el-button link @click="openEdit(scope.row)">编辑</el-button>
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
          <el-select v-model="form.projectId" placeholder="选择项目" style="width: 100%">
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
        <el-form-item label="版本">
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
          <el-descriptions-item label="版本">{{ detail.version || '-' }}</el-descriptions-item>
          <el-descriptions-item label="风险">
            <el-tag size="small" :type="riskTagType(detail.riskLevel)">{{ riskText(detail.riskLevel) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="影响模块" :span="3">
            {{ detail.affectedModules || '-' }}
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
          <el-table-column label="结果" width="150">
            <template #default="scope">
              <el-select
                v-model="scope.row.result"
                size="small"
                style="width: 110px"
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
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, MagicStick, Plus, Refresh, RefreshLeft, Search } from '@element-plus/icons-vue'
import { regressionApi } from '@/api'
import type { Regression, RegressionItem } from '@/api/types'
import { useAppStore } from '@/stores/app'

interface RegressionForm {
  id: number | null
  projectId: number | null
  title: string
  changeScope: string
  affectedModules: string
  version: string
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

const filters = reactive({ keyword: '', status: '', riskLevel: '' })

const form = reactive<RegressionForm>({
  id: null,
  projectId: null,
  title: '',
  changeScope: '',
  affectedModules: '',
  version: '',
  riskLevel: 'low',
  source: 'manual',
  content: '',
})

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

async function load(): Promise<void> {
  loading.value = true
  try {
    const page = await regressionApi.list({
      keyword: filters.keyword,
      status: filters.status,
      riskLevel: filters.riskLevel,
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

function resetFilters(): void {
  filters.keyword = ''
  filters.status = ''
  filters.riskLevel = ''
  reload()
}

function openCreate(): void {
  form.id = null
  form.projectId = appStore.currentProjectId
  form.title = ''
  form.changeScope = ''
  form.affectedModules = ''
  form.version = ''
  form.riskLevel = 'low'
  form.source = 'manual'
  form.content = ''
  editVisible.value = true
}

function openEdit(row: Regression): void {
  form.id = row.id
  form.projectId = row.projectId
  form.title = row.title
  form.changeScope = row.changeScope
  form.affectedModules = row.affectedModules
  form.version = row.version
  form.riskLevel = row.riskLevel
  form.source = row.source
  form.content = row.content
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

async function submitResults(): Promise<void> {
  if (!detail.value) {
    return
  }
  const results = items.value.map((item) => ({
    itemId: item.id,
    result: item.result || 'not_executed',
    remark: item.remark || '',
  }))
  try {
    const payload = await regressionApi.execute(detail.value.id, results)
    ElMessage.success(`已回写 ${payload.updated.length} 条，跳过 ${payload.skipped.length} 条`)
    await openDetail(detail.value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  }
}

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
</style>
