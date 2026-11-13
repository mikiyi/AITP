<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">执行矩阵</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建矩阵</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <div class="toolbar">
        <el-input
          v-model="filters.keyword"
          placeholder="搜索编号 / 名称"
          clearable
          style="width: 220px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 140px" @change="reload">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button :icon="RefreshLeft" @click="resetFilters">重置</el-button>
      </div>

      <el-table v-loading="loading" :data="rows" border stripe>
        <el-table-column prop="matrixNo" label="编号" width="110" />
        <el-table-column prop="name" label="名称" min-width="170" show-overflow-tooltip />
        <el-table-column prop="modeLabel" label="类型" width="110" />
        <el-table-column label="执行对象" min-width="150" show-overflow-tooltip>
          <template #default="scope">
            <span class="muted">{{ scope.row.mode === 'interface' ? scope.row.runName || '-' : `${scope.row.scriptIds.length} 个脚本` }}</span>
          </template>
        </el-table-column>
        <el-table-column label="格子进度" width="130">
          <template #default="scope">
            <span>{{ scope.row.cellDone }} / {{ scope.row.cellTotal }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="scope">
            <el-tag size="small" :type="statusTagType(scope.row.status)" effect="plain">
              {{ scope.row.statusLabel }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="165">
          <template #default="scope">{{ dateTimeText(scope.row.updatedAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click="openDetail(scope.row)">明细与对比</el-button>
            <el-button
              link
              type="success"
              :disabled="scope.row.status === 'queued' || scope.row.status === 'running'"
              @click="dispatch(scope.row)"
            >一次调度</el-button>
            <el-button link type="danger" @click="remove(scope.row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">暂无执行矩阵，点「新建矩阵」把一批用例铺到多环境 × 多端上跑</span>
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page.pageNum"
          v-model:page-size="page.pageSize"
          :total="page.total"
          layout="total, prev, pager, next"
          @current-change="load"
        />
      </div>
    </div>

    <el-dialog v-model="createVisible" title="新建执行矩阵" width="640px">
      <el-form label-width="110px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="100" placeholder="例如：结算链路多环境回归" />
        </el-form-item>
        <el-form-item label="类型" required>
          <el-radio-group v-model="form.mode">
            <el-radio-button value="interface">接口用例（环境 × 端）</el-radio-button>
            <el-radio-button value="automation">自动化脚本（环境 × 浏览器）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.mode === 'interface'" label="测试轮次" required>
          <el-select v-model="form.runId" filterable placeholder="选择一条含接口用例的轮次" style="width: 100%">
            <el-option v-for="run in runOptions" :key="run.id" :label="`${run.name}（${run.total} 条）`" :value="run.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.mode === 'interface'" label="端展开方式">
          <el-radio-group v-model="form.targetMode">
            <el-radio-button value="default">只跑首台应用</el-radio-button>
            <el-radio-button value="all">展开全部应用服务器</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-else label="自动化脚本" required>
          <el-select v-model="form.scriptIds" multiple filterable placeholder="选择脚本" style="width: 100%">
            <el-option v-for="script in scriptOptions" :key="script.id" :label="script.name" :value="script.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.mode === 'automation'" label="浏览器">
          <el-select v-model="form.browsers" multiple filterable allow-create default-first-option
                     placeholder="默认 chromium，可输入自定义标识" style="width: 100%">
            <el-option label="chromium" value="chromium" />
            <el-option label="firefox" value="firefox" />
            <el-option label="webkit" value="webkit" />
          </el-select>
        </el-form-item>
        <el-form-item label="测试环境" required>
          <el-select v-model="form.envIds" multiple filterable placeholder="选择 1~10 个环境" style="width: 100%">
            <el-option v-for="env in envOptions" :key="env.id" :label="`${env.name}（${env.kindLabel}）`" :value="env.id" />
          </el-select>
        </el-form-item>
        <el-alert type="info" :closable="false" show-icon
                  title="矩阵旁路执行、不回写轮次结论；包含生产环境时，调度时会要求显式确认（默认不勾选）。" />
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <ProdConfirmDialog ref="prodDialog" />
  </div>
</template>

<script setup lang="ts">
import { onActivated, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, RefreshLeft } from '@element-plus/icons-vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import ProdConfirmDialog from '@/components/ProdConfirmDialog.vue'
import { automationApi, executionMatrixApi, testEnvApi, testRunApi } from '@/api'
import { dateTimeText } from '@/utils/format'
import { useAppStore } from '@/stores/app'
import type { AutomationScript, ExecutionMatrix, TestEnv, TestRun } from '@/api/types'

const router = useRouter()
const appStore = useAppStore()

const loading = ref(false)
const rows = ref<ExecutionMatrix[]>([])
const page = reactive({ pageNum: 1, pageSize: 20, total: 0 })
const filters = reactive({ keyword: '', status: '' })

const statusOptions = [
  { value: 'draft', label: '草稿' },
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '执行中' },
  { value: 'done', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]

function statusTagType(status: string): 'info' | 'warning' | 'success' | 'danger' | 'primary' {
  if (status === 'done') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running' || status === 'queued') return 'warning'
  if (status === 'cancelled') return 'info'
  return 'primary'
}

async function load(): Promise<void> {
  if (!appStore.currentProjectId) return
  loading.value = true
  try {
    const payload = await executionMatrixApi.list({
      projectId: appStore.currentProjectId,
      pageNum: page.pageNum,
      pageSize: page.pageSize,
      keyword: filters.keyword || undefined,
      status: filters.status || undefined,
    })
    rows.value = payload.list
    page.total = payload.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载执行矩阵失败')
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
  reload()
}

const createVisible = ref(false)
const creating = ref(false)
const runOptions = ref<TestRun[]>([])
const envOptions = ref<TestEnv[]>([])
const scriptOptions = ref<AutomationScript[]>([])
const form = reactive({
  name: '',
  mode: 'interface' as 'interface' | 'automation',
  runId: undefined as number | undefined,
  targetMode: 'default',
  scriptIds: [] as number[],
  browsers: [] as string[],
  envIds: [] as number[],
})

async function openCreate(): Promise<void> {
  if (!appStore.currentProjectId) {
    ElMessage.warning('先选择一个项目')
    return
  }
  form.name = ''
  form.runId = undefined
  form.scriptIds = []
  form.browsers = []
  form.envIds = []
  form.targetMode = 'default'
  createVisible.value = true
  try {
    const [runs, envs, scripts] = await Promise.all([
      testRunApi.list({ projectId: appStore.currentProjectId, pageSize: 100 }),
      testEnvApi.list(),
      automationApi.list({ projectId: appStore.currentProjectId, pageSize: 100 }),
    ])
    runOptions.value = runs.list
    envOptions.value = envs.list
    scriptOptions.value = scripts.list
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载选项失败')
  }
}

async function submitCreate(): Promise<void> {
  if (!appStore.currentProjectId) return
  if (!form.name.trim()) {
    ElMessage.warning('请填写矩阵名称')
    return
  }
  if (form.mode === 'interface' && !form.runId) {
    ElMessage.warning('请选择测试轮次')
    return
  }
  if (form.mode === 'automation' && !form.scriptIds.length) {
    ElMessage.warning('请选择自动化脚本')
    return
  }
  if (!form.envIds.length) {
    ElMessage.warning('请至少选择一个测试环境')
    return
  }
  creating.value = true
  try {
    const detail = await executionMatrixApi.create({
      projectId: appStore.currentProjectId,
      name: form.name.trim(),
      mode: form.mode,
      runId: form.mode === 'interface' ? form.runId : undefined,
      targetMode: form.targetMode,
      scriptIds: form.mode === 'automation' ? form.scriptIds : undefined,
      browsers: form.mode === 'automation' ? form.browsers : undefined,
      envIds: form.envIds,
    })
    ElMessage.success(`矩阵 ${detail.matrix.matrixNo} 已创建，共 ${detail.matrix.cellTotal} 格`)
    createVisible.value = false
    router.push({ name: 'execution-matrix-detail', params: { id: detail.matrix.id } })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建矩阵失败')
  } finally {
    creating.value = false
  }
}

const prodDialog = ref<InstanceType<typeof ProdConfirmDialog> | null>(null)

async function dispatch(row: ExecutionMatrix): Promise<void> {
  try {
    const payload = await executionMatrixApi.run(row.id, { confirmProd: false })
    ElMessage.success(`已投递任务队列（任务 #${payload.task.id}），到明细页看逐格进度`)
    void load()
  } catch (err) {
    const message = err instanceof Error ? err.message : '调度失败'
    if (message.includes('生产环境')) {
      const ok = (await prodDialog.value?.open(message)) ?? false
      if (ok) {
        await dispatchConfirmed(row)
      }
      return
    }
    ElMessage.error(message)
  }
}

async function dispatchConfirmed(row: ExecutionMatrix): Promise<void> {
  try {
    const payload = await executionMatrixApi.run(row.id, { confirmProd: true })
    ElMessage.success(`已确认生产环境执行并投递任务队列（任务 #${payload.task.id}）`)
    void load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '调度失败')
  }
}

function openDetail(row: ExecutionMatrix): void {
  router.push({ name: 'execution-matrix-detail', params: { id: row.id } })
}

async function remove(row: ExecutionMatrix): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除矩阵「${row.name}」？格子结论与对比记录一并删除，执行流水与证据保留。`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await executionMatrixApi.remove(row.id)
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

onMounted(load)

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
</style>
