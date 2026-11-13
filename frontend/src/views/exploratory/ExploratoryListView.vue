<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">Web 探索性测试</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="Setting" @click="router.push('/settings/exploratory')">探索设置</el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建探索</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <div class="toolbar">
        <el-input v-model="filters.keyword" placeholder="搜索编号 / 名称 / 目标地址" clearable
                  style="width: 240px" @keyup.enter="reload" @clear="reload" />
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="filters.driver" placeholder="驱动" clearable style="width: 130px" @change="reload">
          <el-option v-for="item in driverOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button :icon="RefreshLeft" @click="resetFilters">重置</el-button>
      </div>

      <el-table v-loading="loading" :data="rows" border stripe>
        <el-table-column prop="sessionNo" label="编号" width="100" />
        <el-table-column prop="name" label="名称" min-width="150" show-overflow-tooltip />
        <el-table-column prop="targetUrl" label="目标地址" min-width="170" show-overflow-tooltip />
        <el-table-column label="驱动" width="100">
          <template #default="scope">
            <span>{{ driverLabel(scope.row.driver) }}</span>
            <el-tag v-if="scope.row.degraded" size="small" type="warning" effect="plain">已降级</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="步数" width="80">
          <template #default="scope">{{ scope.row.stepDone }} / {{ scope.row.stepTotal }}</template>
        </el-table-column>
        <el-table-column label="异常" width="70">
          <template #default="scope">
            <el-tag v-if="scope.row.anomalyCount" size="small" type="danger" effect="plain">
              {{ scope.row.anomalyCount }}
            </el-tag>
            <span v-else class="muted">0</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="scope">
            <el-tag size="small" :type="statusTagType(scope.row.status)" effect="plain">
              {{ scope.row.statusLabel || statusLabel(scope.row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="160">
          <template #default="scope">{{ dateTimeText(scope.row.updatedAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click="openDetail(scope.row)">明细</el-button>
            <el-button link type="success" :disabled="scope.row.status === 'queued' || scope.row.status === 'running'"
                       @click="run(scope.row)">运行</el-button>
            <el-button link type="danger" @click="remove(scope.row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">暂无探索会话，点「新建探索」给目标地址排一个探索计划</span>
        </template>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page.pageNum" v-model:page-size="page.pageSize"
                       :total="page.total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="createVisible" title="新建探索会话" width="680px">
      <el-form label-width="120px">
        <el-form-item label="名称">
          <el-input v-model="form.name" maxlength="100" placeholder="留空则用目标地址" />
        </el-form-item>
        <el-form-item label="目标地址" required>
          <el-input v-model="form.targetUrl" placeholder="http://127.0.0.1:8760/" />
        </el-form-item>
        <el-form-item label="驱动">
          <el-radio-group v-model="form.driver">
            <el-radio-button value="scripted">确定性脚本</el-radio-button>
            <el-radio-button value="model">模型决策（无模型自动降级）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="最大步数">
          <el-input-number v-model="form.maxSteps" :min="1" :max="200" />
        </el-form-item>
        <el-form-item label="探索计划">
          <el-table :data="plan" border size="small">
            <el-table-column label="动作" width="150">
              <template #default="scope">
                <el-select v-model="scope.row.action" size="small">
                  <el-option label="导航 navigate" value="navigate" />
                  <el-option label="点击 click" value="click" />
                  <el-option label="输入 type" value="type" />
                  <el-option label="快照 snapshot" value="snapshot" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="选择器 / 地址" min-width="180">
              <template #default="scope">
                <el-input v-if="scope.row.action === 'navigate'" v-model="scope.row.url" size="small"
                          placeholder="留空用目标地址" />
                <el-input v-else v-model="scope.row.selector" size="small" placeholder="#id / text=..." />
              </template>
            </el-table-column>
            <el-table-column label="输入值（自动脱敏）" min-width="140">
              <template #default="scope">
                <el-input v-if="scope.row.action === 'type'" v-model="scope.row.value" size="small" />
                <span v-else class="muted">-</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="70">
              <template #default="scope">
                <el-button link type="danger" @click="plan.splice(scope.$index, 1)">删</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-button size="small" style="margin-top: 8px" @click="plan.push({ action: 'snapshot' })">
            添加步骤
          </el-button>
        </el-form-item>
        <el-alert type="info" :closable="false" show-icon
                  title="探索按计划逐步执行：每步读快照→观测，异常步强制补 3 张截图并生成双锚点日志切片；产物进待审池人工审核。" />
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onActivated, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, RefreshLeft, Setting } from '@element-plus/icons-vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { exploratoryApi } from '@/api'
import { dateTimeText } from '@/utils/format'
import { useAppStore } from '@/stores/app'
import type { ExploratoryOption, ExplorationSession, ExplorationPlanStep } from '@/api/types'

const router = useRouter()
const appStore = useAppStore()

const loading = ref(false)
const rows = ref<ExplorationSession[]>([])
const page = reactive({ pageNum: 1, pageSize: 20, total: 0 })
const filters = reactive({ keyword: '', status: '', driver: '' })

const statusOptions = ref<ExploratoryOption[]>([])
const driverOptions = ref<ExploratoryOption[]>([])

function statusLabel(status: string): string {
  return statusOptions.value.find((item) => item.value === status)?.label || status
}
function driverLabel(driver: string): string {
  return driverOptions.value.find((item) => item.value === driver)?.label || driver
}

function statusTagType(status: string): 'info' | 'warning' | 'success' | 'danger' | 'primary' {
  if (status === 'done') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running' || status === 'queued') return 'warning'
  if (status === 'cancelled') return 'info'
  return 'primary'
}

async function loadMeta(): Promise<void> {
  try {
    const meta = await exploratoryApi.meta()
    statusOptions.value = meta.statuses
    driverOptions.value = meta.drivers
  } catch { /* 枚举失败不阻塞列表 */ }
}

async function load(): Promise<void> {
  if (!appStore.currentProjectId) return
  loading.value = true
  try {
    const payload = await exploratoryApi.sessions({
      projectId: appStore.currentProjectId,
      pageNum: page.pageNum,
      pageSize: page.pageSize,
      keyword: filters.keyword || undefined,
      status: filters.status || undefined,
      driver: filters.driver || undefined,
    })
    rows.value = payload.list
    page.total = payload.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载探索会话失败')
  } finally {
    loading.value = false
  }
}

function reload(): void {
  page.pageNum = 1
  void load()
}
function resetFilters(): void {
  filters.keyword = ''
  filters.status = ''
  filters.driver = ''
  reload()
}

const createVisible = ref(false)
const creating = ref(false)
const plan = ref<ExplorationPlanStep[]>([])
const form = reactive({
  name: '',
  targetUrl: '',
  driver: 'scripted',
  maxSteps: 20,
})

async function openCreate(): Promise<void> {
  if (!appStore.currentProjectId) {
    ElMessage.warning('先选择一个项目')
    return
  }
  form.name = ''
  form.targetUrl = ''
  form.driver = 'scripted'
  form.maxSteps = 20
  plan.value = [
    { action: 'navigate', url: '' },
    { action: 'snapshot' },
  ]
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  if (!appStore.currentProjectId) return
  if (!form.targetUrl.trim()) {
    ElMessage.warning('请填写目标地址')
    return
  }
  creating.value = true
  try {
    const session = await exploratoryApi.createSession({
      projectId: appStore.currentProjectId,
      name: form.name.trim() || undefined,
      targetUrl: form.targetUrl.trim(),
      driver: form.driver,
      maxSteps: form.maxSteps,
      plan: plan.value,
    })
    ElMessage.success(`探索会话 ${session.sessionNo} 已创建`)
    createVisible.value = false
    router.push({ name: 'exploratory-detail', params: { id: session.id } })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建失败')
  } finally {
    creating.value = false
  }
}

async function run(row: ExplorationSession): Promise<void> {
  try {
    await exploratoryApi.runSession(row.id as number)
    ElMessage.success('已投递探索任务，到明细页看逐步进度')
    void load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '运行失败')
  }
}

function openDetail(row: ExplorationSession): void {
  router.push({ name: 'exploratory-detail', params: { id: row.id } })
}

async function remove(row: ExplorationSession): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除探索会话「${row.name || row.sessionNo}」？步骤留痕与产物一并移除。`,
      '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await exploratoryApi.removeSession(row.id as number)
    ElMessage.success('已删除')
    void load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

watch(
  () => appStore.currentProjectId,
  () => {
    page.pageNum = 1
    void load()
  },
)

let activated = false

onMounted(() => {
  void loadMeta()
  void load()
})

onActivated(() => {
  // keep-alive 缓存下从明细返回不重新挂载，首次激活已由 onMounted 加载
  if (!activated) {
    activated = true
    return
  }
  void load()
})
</script>

<style scoped>
.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>