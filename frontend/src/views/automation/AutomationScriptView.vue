<template>
  <div class="page">
    <div class="page-header">
      <div class="auto-head">
        <h2 class="page-title">{{ categoryInfo?.label || '自动化脚本' }}</h2>
        <span class="muted">{{ categoryHint }}</span>
      </div>
      <div class="auto-head__actions">
        <el-button :icon="Refresh" @click="reloadAll">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建脚本</el-button>
      </div>
    </div>

    <el-alert
      v-if="runtimeHint.length"
      :type="runtimeHintType"
      :closable="false"
      show-icon
      class="auto-alert"
    >
      <template #title>{{ runtimeHint[0] }}</template>
      <div v-for="line in runtimeHint.slice(1)" :key="line">{{ line }}</div>
    </el-alert>

    <div class="auto-stats">
      <MetricCard label="脚本总数" :value="stats.scriptCount" />
      <MetricCard label="执行次数" :value="stats.runTotal" />
      <MetricCard label="执行成功" :value="stats.runSuccess" tone="success" />
      <MetricCard label="执行失败" :value="stats.runFailed" tone="danger" />
    </div>

    <div class="panel panel-pad auto-block">
      <div class="auto-block__head">
        <h3>脚本列表</h3>
        <span class="muted">{{ runnerText }}</span>
      </div>
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetcher"
        :search-fields="searchFields"
        :max-height="440"
        selectable
        sync-query
        query-prefix="auto_"
      >
        <template #batch="{ rows }">
          <el-button type="primary" size="small" @click="batchExecute(rows)">批量后台执行</el-button>
        </template>
        <template #name="{ row }">
          <div class="auto-name">
            <span class="auto-name__title">{{ row.name }}</span>
            <span class="muted text-mono">{{ row.scriptNo }}</span>
            <span v-if="row.envId" class="muted">{{ envNameOf(row.envId) }}</span>
          </div>
        </template>
        <template #framework="{ row }">
          <span v-if="row.framework">{{ frameworkLabel(row.framework) }}</span>
          <span v-else class="muted">-</span>
        </template>
        <template #lastRunStatus="{ row }">
          <el-tag v-if="row.lastRunStatus" :type="runTagType(row.lastRunStatus)" size="small" effect="plain">
            {{ runStatusText(row.lastRunStatus) }}
          </el-tag>
          <span v-else class="muted">未执行</span>
        </template>
        <template #lastRunAt="{ row }">
          <span :class="{ muted: !row.lastRunAt }">{{ dateTimeText(row.lastRunAt) }}</span>
        </template>
        <template #status="{ row }">
          <el-tag :type="row.status === 'ready' ? 'success' : 'info'" size="small" effect="plain">
            {{ row.status === 'ready' ? '启用' : '停用' }}
          </el-tag>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button>
          <el-button link type="success" @click.stop="execute(row)">执行</el-button>
          <el-button link type="primary" @click.stop="executeAsync(row)">后台</el-button>
          <el-button link type="success" @click.stop="openKnowledge(row)">加入知识库</el-button>
          <el-button link type="info" @click.stop="openHistory(row)">历史</el-button>
          <el-button link type="danger" @click.stop="removeScript(row)">删除</el-button>
        </template>
      </BaseProTable>
    </div>

    <!-- 脚本编辑 -->
    <el-dialog
      v-model="editorVisible"
      :title="editingId ? '编辑脚本' : '新建脚本'"
      width="900px"
      top="6vh"
    >
      <el-form label-width="110px">
        <el-form-item label="脚本名称" required>
          <el-input v-model="form.name" placeholder="例如：登录主流程冒烟" maxlength="200" />
        </el-form-item>
        <el-form-item label="框架">
          <el-select v-model="form.framework" clearable placeholder="选择自动化框架" style="width: 100%">
            <el-option v-for="item in frameworkOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <div v-if="selectedFramework && !selectedFramework.ready" class="framework-hint">
            <p>
              当前解释器
              <span class="text-mono">{{ runtime?.pythonInterpreter || '未指定' }}</span>
              未就绪，缺：{{ (selectedFramework.missing || []).join('、') || '依赖' }}。
            </p>
            <p>{{ selectedFramework.setupHint }}</p>
            <p v-if="selectedFramework.installCommand" class="framework-hint__cmd text-mono">
              {{ selectedFramework.installCommand }}
            </p>
            <div v-if="alternativeInterpreters.length" class="framework-hint__alt">
              <span>本机已有解释器装好该框架：</span>
              <el-button
                v-for="item in alternativeInterpreters"
                :key="item.path"
                link
                type="primary"
                @click="useInterpreter(item.path)"
              >
                {{ item.path }}（Python {{ item.version }}）
              </el-button>
            </div>
            <el-button link type="primary" @click="router.push('/settings/automation')">
              打开解释器体检
            </el-button>
          </div>
        </el-form-item>
        <el-form-item v-if="category !== 'performance'" label="运行方式">
          <el-radio-group v-model="form.runMode">
            <el-radio value="script">直接执行</el-radio>
            <el-radio value="pytest">pytest</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="附加参数">
          <el-input
            v-model="form.entryCommand"
            placeholder="例如 -k smoke（作为参数直接传给解释器，不走 shell）"
          />
        </el-form-item>
        <el-form-item label="超时秒数">
          <el-input-number v-model="form.timeoutSeconds" :min="1" :max="7200" style="width: 200px" />
        </el-form-item>
        <el-form-item label="默认环境">
          <el-select v-model="form.envId" placeholder="不绑定环境" clearable style="width: 100%">
            <el-option
              v-for="item in envs"
              :key="item.id"
              :label="`${item.name}${item.kindLabel ? `（${item.kindLabel}）` : ''}`"
              :value="item.id"
            />
          </el-select>
          <p class="env-hint">执行留痕会记录这套环境，生产环境执行前会再次确认。</p>
        </el-form-item>
        <el-form-item label="覆盖用例方式">
          <el-radio-group v-model="form.caseMatchMode">
            <el-radio value="case_no">按用例编号</el-radio>
            <el-radio value="tag">按用例标签</el-radio>
          </el-radio-group>
          <p class="env-hint">
            {{
              form.caseMatchMode === 'case_no'
                ? '脚本执行结果按用例编号匹配到测试轮次里的用例。'
                : '脚本执行结果按标签匹配到测试轮次里的用例。'
            }}
          </p>
        </el-form-item>
        <el-form-item label="覆盖用例值">
          <el-input
            v-model="form.caseMatchValue"
            :placeholder="form.caseMatchMode === 'case_no' ? '例如 FT-001,FT-002' : '例如 smoke'"
            maxlength="500"
          />
          <p class="env-hint">
            多个值用英文逗号分隔；留空表示脚本不绑定用例，执行结果只留在自动化历史里。
          </p>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>
        <el-form-item :label="category === 'performance' ? 'JMeter 脚本' : '脚本正文'">
          <el-input
            v-model="form.content"
            type="textarea"
            :rows="16"
            class="text-mono"
            :placeholder="codePlaceholder"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveScript">保存</el-button>
      </template>
    </el-dialog>

    <!-- 执行结果 -->
    <el-dialog v-model="resultVisible" title="执行结果" width="860px" top="6vh">
      <div v-if="lastRun" class="auto-result">
        <div class="auto-result__head">
          <el-tag :type="runTagType(lastRun.status)" effect="plain">
            {{ runStatusText(lastRun.status) }}
          </el-tag>
          <span class="muted">退出码 {{ lastRun.exitCode ?? '-' }}</span>
          <span class="muted">耗时 {{ lastRun.durationMs ?? 0 }} ms</span>
          <span class="muted text-mono">{{ lastRun.runNo }}</span>
        </div>
        <div class="auto-result__cmd text-mono">{{ lastRun.commandLine || '（未启动进程）' }}</div>
        <el-alert
          v-if="lastRun.errorMessage"
          type="error"
          :closable="false"
          show-icon
          :title="lastRun.errorMessage"
          class="auto-alert"
        />
        <div v-if="lastRun.writebackStatus" class="auto-writeback">
          <el-tag :type="writebackTagType(lastRun.writebackStatus)" size="small" effect="plain">
            {{ lastRun.writebackLabel || lastRun.writebackStatus }}
          </el-tag>
          <span class="auto-writeback__summary">{{ lastRun.writebackSummary }}</span>
        </div>
        <el-table
          v-if="lastRun.caseResults?.length"
          :data="lastRun.caseResults"
          size="small"
          border
          max-height="240"
          class="auto-writeback__table"
        >
          <el-table-column prop="caseNo" label="用例编号" width="120" />
          <el-table-column prop="testName" label="测试名称" min-width="200" show-overflow-tooltip />
          <el-table-column label="回写结果" width="100">
            <template #default="{ row }">
              <el-tag :type="caseResultTagType(row.result)" size="small" effect="plain">
                {{ caseResultText(row.result) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="匹配方式" width="100">
            <template #default="{ row }">{{ mappedByText(row.mappedBy) }}</template>
          </el-table-column>
          <el-table-column prop="message" label="说明" min-width="180" show-overflow-tooltip />
        </el-table>
        <el-tabs v-model="resultTab">
          <el-tab-pane label="标准输出" name="stdout">
            <pre class="auto-result__out text-mono">{{ lastRun.stdout || '（无输出）' }}</pre>
          </el-tab-pane>
          <el-tab-pane label="错误输出" name="stderr">
            <pre class="auto-result__out text-mono">{{ lastRun.stderr || '（无输出）' }}</pre>
          </el-tab-pane>
          <el-tab-pane :label="`产物（${lastRun.artifacts?.length ?? 0}）`" name="artifacts">
            <div v-if="lastRun.artifacts?.length" class="auto-artifacts">
              <div v-for="item in lastRun.artifacts" :key="item.path" class="auto-artifacts__row">
                <span class="text-mono auto-artifacts__path">{{ item.path }}</span>
                <span class="muted">{{ formatSize(item.size) }}</span>
                <a :href="artifactUrl(lastRun.id, item.path)" class="auto-artifacts__link">
                  <el-button link type="primary" :icon="Download">下载</el-button>
                </a>
              </div>
            </div>
            <p v-else class="muted auto-artifacts__empty">
              本次执行没有产物。脚本把截图、日志等文件写到环境变量 AITP_RUN_DIR
              指向的目录，即可在这里下载。
            </p>
          </el-tab-pane>
        </el-tabs>
      </div>
      <template #footer>
        <el-button @click="resultVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 执行历史 -->
    <el-dialog v-model="historyVisible" title="执行历史" width="1020px" top="6vh">
      <el-table :data="historyRows" size="small" border max-height="420">
        <el-table-column prop="runNo" label="执行编号" width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="runTagType(row.status)" size="small" effect="plain">
              {{ runStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="退出码" width="80" align="right">
          <template #default="{ row }">{{ row.exitCode ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="90" align="right">
          <template #default="{ row }">{{ row.durationMs ?? 0 }} ms</template>
        </el-table-column>
        <el-table-column label="环境" width="120">
          <template #default="{ row }">{{ envNameOf(row.envId) || '-' }}</template>
        </el-table-column>
        <el-table-column label="回写" width="130">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.writebackStatus"
              :content="row.writebackSummary || '-'"
              placement="top"
              popper-class="auto-writeback__tip"
            >
              <el-tag :type="writebackTagType(row.writebackStatus)" size="small" effect="plain">
                {{ row.writebackLabel || row.writebackStatus }}
              </el-tag>
            </el-tooltip>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="开始时间" width="170">
          <template #default="{ row }">{{ dateTimeText(row.startedAt) }}</template>
        </el-table-column>
        <el-table-column prop="errorMessage" label="说明" min-width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="primary" @click="openRunDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="historyVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>

    <AddToKnowledgeDialog
      v-model="knowledgeVisible"
      :title="knowledgePayload.title"
      :content="knowledgePayload.content"
      ref-type="automation_script"
      :ref-id="knowledgePayload.refId"
      :ref-label="knowledgePayload.refLabel"
    />
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Plus, Refresh } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import MetricCard from '@/components/MetricCard.vue'
import AddToKnowledgeDialog from '@/components/AddToKnowledgeDialog.vue'
import { automationApi, testEnvApi } from '@/api'
import { dateTimeText } from '@/utils/format'
import type {
  AutomationCategory,
  AutomationCategoryInfo,
  AutomationInterpreters,
  AutomationRun,
  AutomationRuntime,
  AutomationScript,
  TestEnv,
} from '@/api/types'

const route = useRoute()
const router = useRouter()

const knowledgeVisible = ref(false)
const knowledgePayload = reactive({
  title: '',
  content: '',
  refId: null as number | null,
  refLabel: '',
})

function openKnowledge(row: AutomationScript): void {
  knowledgePayload.title = row.name
  knowledgePayload.content = [
    `# ${row.name}`,
    row.description || '',
    `**类别**：${row.categoryLabel || row.category}`,
    `**框架**：${row.framework || '-'}　**语言**：${row.language || '-'}`,
    row.content
      ? '```' + (row.language || '') + '\n' + row.content + '\n' + '```'
      : '',
  ].filter(Boolean).join('\n\n')
  knowledgePayload.refId = Number(row.id)
  knowledgePayload.refLabel = row.name
  knowledgeVisible.value = true
}

/** 四个二级菜单共用这一个页面，类别从路由里取。 */
const category = computed<AutomationCategory>(() => {
  const raw = String(route.params.category || 'web')
  if (raw === 'app' || raw === 'api' || raw === 'performance') {
    return raw
  }
  return 'web'
})

const CATEGORY_HINTS: Record<AutomationCategory, string> = {
  web: 'Web 端自动化用例，用 Python 解释器直接执行',
  app: '移动端自动化用例，用 Python 解释器直接执行',
  api: '接口自动化用例，用 Python 解释器直接执行',
  performance: 'JMeter 性能脚本，用本地 JMeter 命令行执行',
}

const categoryHint = computed(() => CATEGORY_HINTS[category.value])

/** 框架清单由后端 automation_frameworks.py 给出，页面只按当前类别筛，不再自己维护一份。 */
const frameworkChoices = computed(() =>
  (runtime.value?.frameworks ?? []).filter((item) => item.targets.includes(category.value)),
)

const frameworkOptions = computed(() =>
  frameworkChoices.value.map((item) => ({ label: item.label, value: item.code })),
)

const selectedFramework = computed(() =>
  frameworkChoices.value.find((item) => item.code === form.framework) ?? null,
)

function frameworkLabel(value: string): string {
  if (!value) {
    return '-'
  }
  return (runtime.value?.frameworks ?? []).find((item) => item.code === value)?.label ?? value
}

/** 本机别的解释器里已经装好当前框架依赖的，可以直接一键切过去。 */
const alternativeInterpreters = computed(() => {
  const packageName = selectedFramework.value?.package
  if (!packageName) {
    return []
  }
  return (interpreters.value?.candidates ?? []).filter(
    (item) => !item.isCurrent && item.packages[packageName],
  )
})

async function useInterpreter(path: string): Promise<void> {
  try {
    runtime.value = await automationApi.selectInterpreter(path)
    ElMessage.success(`执行解释器已切换为 ${path}`)
    await reloadAll()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '切换解释器失败')
  }
}

const codePlaceholder = computed(() =>
  category.value === 'performance'
    ? '<?xml version="1.0" encoding="UTF-8"?>\n<jmeterTestPlan version="1.2">…</jmeterTestPlan>'
    : 'print("hello")',
)

const categories = ref<AutomationCategoryInfo[]>([])
const runtime = ref<AutomationRuntime | null>(null)
const interpreters = ref<AutomationInterpreters | null>(null)
const envs = ref<TestEnv[]>([])

function envNameOf(envId?: number | null): string {
  if (!envId) {
    return ''
  }
  return envs.value.find((item) => item.id === envId)?.name ?? ''
}
const categoryInfo = computed(() => categories.value.find((item) => item.category === category.value))

const stats = computed(() => {
  const info = categoryInfo.value
  const runStats = info?.runStats ?? { total: 0, success: 0, failed: 0 }
  return {
    scriptCount: info?.scriptCount ?? 0,
    runTotal: runStats.total,
    runSuccess: runStats.success,
    runFailed: runStats.failed,
  }
})

const runnerText = computed(() =>
  category.value === 'performance'
    ? '执行方式：本地 JMeter 命令行（.jmx）'
    : '执行方式：本机 Python 解释器',
)

const runtimeHint = computed<string[]>(() => {
  const info = runtime.value
  if (!info) {
    return []
  }
  if (category.value === 'performance') {
    if (info.jmeterAvailable) {
      return [`JMeter 已就绪：${info.jmeterCommand}`]
    }
    return ['未找到本地 JMeter，请到「设置 → 性能测试管理设置」填写 JMeter 安装根目录后再执行。']
  }
  const lines: string[] = []
  if (info.pythonInterpreterAvailable) {
    lines.push(
      `Python 解释器已就绪：${info.pythonInterpreter}${info.pythonVersion ? `（${info.pythonVersion}）` : ''}`,
    )
  } else {
    lines.push('Python 解释器不可用，请到「设置 → 自动化管理设置」指定解释器路径后再执行。')
  }
  if (category.value === 'app') {
    if (info.appiumServerAvailable) {
      lines.push(`Appium Server 已连通：${info.appiumServerUrl}`)
      const devices = info.androidDevices ?? []
      lines.push(
        devices.length
          ? `已连接设备：${devices.map((item) => item.serial).join('、')}`
          : '未检测到已连接的 Android 设备（adb devices 为空），启动模拟器或连接真机后再执行。',
      )
    } else {
      lines.push(
        `Appium Server 未连通：${info.appiumServerUrl || '未配置'}，请先启动 Appium 服务，到「设置 → 自动化管理设置」可改地址。`,
      )
    }
    if (info.adbAvailable === false) {
      lines.push('未找到 adb 命令，请到「设置 → 自动化管理设置」填写 adb 的完整路径。')
    }
  }
  if (category.value === 'web' && info.playwrightBrowsersAvailable === false) {
    lines.push('Playwright 浏览器内核未下载：在执行解释器里运行 python -m playwright install chromium。')
  }
  return lines
})

const runtimeHintType = computed<'success' | 'warning'>(() => {
  const info = runtime.value
  if (!info) {
    return 'warning'
  }
  if (category.value === 'performance') {
    return info.jmeterAvailable ? 'success' : 'warning'
  }
  if (!info.pythonInterpreterAvailable) {
    return 'warning'
  }
  if (category.value === 'app' && (!info.appiumServerAvailable || info.adbAvailable === false)) {
    return 'warning'
  }
  if (category.value === 'web' && info.playwrightBrowsersAvailable === false) {
    return 'warning'
  }
  return 'success'
})

const tableRef = ref<TableExpose | null>(null)

const columns: ColumnDef[] = [
  { prop: 'name', label: '脚本', minWidth: 220, slot: 'name' },
  { prop: 'framework', label: '框架', width: 110, slot: 'framework' },
  { prop: 'fileName', label: '文件名', width: 180 },
  { prop: 'status', label: '状态', width: 90, slot: 'status' },
  { prop: 'lastRunStatus', label: '最近执行', width: 110, slot: 'lastRunStatus' },
  { prop: 'lastRunAt', label: '最近执行时间', width: 170, slot: 'lastRunAt' },
  { prop: 'actions', label: '操作', width: 380, slot: 'actions' },
]

const searchFields = computed<SearchField[]>(() => [
  { prop: 'keyword', label: '脚本名称 / 编号' },
  {
    prop: 'framework',
    label: '框架',
    type: 'select',
    options: frameworkOptions.value,
  },
  {
    prop: 'status',
    label: '状态',
    type: 'select',
    options: [
      { label: '启用', value: 'ready' },
      { label: '停用', value: 'disabled' },
    ],
  },
])

function fetcher(query: Record<string, unknown>) {
  return automationApi.list({ ...query, category: category.value })
}

// ---- 执行 ----
const resultVisible = ref(false)
const resultTab = ref('stdout')
const lastRun = ref<AutomationRun | null>(null)

async function confirmProdEnv(row: AutomationScript): Promise<boolean> {
  const env = envs.value.find((item) => item.id === row.envId)
  if (!env || env.kind !== 'prod') {
    return true
  }
  try {
    await ElMessageBox.confirm(
      `脚本绑定的是生产环境「${env.name}」，确认执行？`,
      '生产环境确认',
      { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' },
    )
    return true
  } catch {
    return false
  }
}

async function execute(row: AutomationScript): Promise<void> {
  if (!(await confirmProdEnv(row))) {
    return
  }
  try {
    const result = await automationApi.run(row.id)
    lastRun.value = await automationApi.runDetail(result.id).catch(() => result)
    resultTab.value = 'stdout'
    resultVisible.value = true
    if (result.status === 'success') {
      ElMessage.success('执行成功')
    } else {
      ElMessage.warning(`执行未通过：${runStatusText(result.status)}`)
    }
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '执行失败')
  }
}

async function batchExecute(rows: Record<string, unknown>[]): Promise<void> {
  const scripts = rows as unknown as AutomationScript[]
  if (!scripts.length) {
    return
  }
  const prodCount = scripts.filter(
    (row) => envs.value.find((env) => env.id === row.envId)?.kind === 'prod',
  ).length
  if (prodCount > 0) {
    try {
      await ElMessageBox.confirm(
        `选中的脚本里有 ${prodCount} 个绑定了生产环境，确认批量执行？`,
        '生产环境确认',
        { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  try {
    const result = await automationApi.batchRun(scripts.map((row) => Number(row.id)))
    ElMessage.success(
      `已投递 ${result.total} 个脚本到任务队列，可到「任务队列」查看进度`,
    )
    tableRef.value?.clearSelection()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量执行失败')
  }
}

async function executeAsync(row: AutomationScript): Promise<void> {
  if (!(await confirmProdEnv(row))) {
    return
  }
  try {
    const result = await automationApi.runAsync(row.id)
    ElMessage.success(`已加入任务队列（#${result.task.id}），可到「任务队列」查看进度`)
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加入队列失败')
  }
}

// ---- 编辑 ----
const editorVisible = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)

const form = reactive({
  name: '',
  framework: '',
  runMode: 'script',
  entryCommand: '',
  timeoutSeconds: 300,
  envId: null as number | null,
  caseMatchMode: 'case_no' as 'case_no' | 'tag',
  caseMatchValue: '',
  description: '',
  content: '',
})

function openCreate(): void {
  editingId.value = null
  form.name = ''
  form.framework = ''
  form.runMode = 'script'
  form.entryCommand = ''
  form.timeoutSeconds = runtime.value?.pythonTimeout ?? 300
  form.envId = null
  form.caseMatchMode = 'case_no'
  form.caseMatchValue = ''
  form.description = ''
  form.content = ''
  editorVisible.value = true
}

async function openEdit(row: AutomationScript): Promise<void> {
  try {
    const detail = await automationApi.detail(row.id)
    editingId.value = detail.id
    form.name = detail.name
    form.framework = detail.framework
    form.runMode = detail.runMode === 'pytest' ? 'pytest' : 'script'
    form.entryCommand = detail.entryCommand
    form.timeoutSeconds = detail.timeoutSeconds ?? runtime.value?.pythonTimeout ?? 300
    form.envId = detail.envId ?? null
    form.caseMatchMode = detail.caseMatchMode === 'tag' ? 'tag' : 'case_no'
    form.caseMatchValue = detail.caseMatchValue ?? ''
    form.description = detail.description
    form.content = detail.content
    editorVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载脚本失败')
  }
}

async function saveScript(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请填写脚本名称')
    return
  }
  saving.value = true
  try {
    const body: Record<string, unknown> = {
      name: form.name.trim(),
      category: category.value,
      framework: form.framework,
      runMode: category.value === 'performance' ? 'jmeter' : form.runMode,
      entryCommand: form.entryCommand.trim(),
      timeoutSeconds: form.timeoutSeconds,
      envId: form.envId,
      caseMatchMode: form.caseMatchMode,
      caseMatchValue: form.caseMatchValue.trim(),
      description: form.description,
      content: form.content,
    }
    if (editingId.value) {
      await automationApi.update(editingId.value, body)
      ElMessage.success('已保存')
    } else {
      await automationApi.create(body)
      ElMessage.success('已创建')
    }
    editorVisible.value = false
    await reloadAll()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeScript(row: AutomationScript): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除脚本「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await automationApi.remove(row.id)
    ElMessage.success('已删除')
    await reloadAll()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

// ---- 历史 ----
const historyVisible = ref(false)
const historyRows = ref<AutomationRun[]>([])

async function openHistory(row: AutomationScript): Promise<void> {
  try {
    const payload = await automationApi.runs({ scriptId: row.id, pageSize: 100 })
    historyRows.value = payload.list ?? []
    historyVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载执行历史失败')
  }
}

async function openRunDetail(row: AutomationRun): Promise<void> {
  try {
    lastRun.value = await automationApi.runDetail(row.id)
    resultTab.value = 'stdout'
    resultVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载执行详情失败')
  }
}

function artifactUrl(runId: number | string, path: string): string {
  return `/api/v1/automation/runs/${runId}/artifacts/download?path=${encodeURIComponent(path)}`
}

function formatSize(size: number): string {
  if (!size) {
    return '0 B'
  }
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

// ---- 展示辅助 ----
function runStatusText(status: string): string {
  const labels: Record<string, string> = {
    running: '执行中',
    success: '成功',
    failed: '失败',
    timeout: '超时',
    cancelled: '已取消',
    error: '启动失败',
  }
  return labels[status] || status
}

function runTagType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'success') {
    return 'success'
  }
  if (status === 'failed' || status === 'error') {
    return 'danger'
  }
  if (status === 'timeout') {
    return 'warning'
  }
  return 'info'
}

function writebackTagType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'parsed') {
    return 'success'
  }
  if (status === 'partial') {
    return 'warning'
  }
  if (status === 'fallback' || status === 'failed') {
    return 'danger'
  }
  return 'info'
}

function caseResultText(result: string): string {
  const labels: Record<string, string> = {
    passed: '通过',
    failed: '失败',
    blocked: '阻塞',
    skipped: '跳过',
  }
  return labels[result] || result
}

function caseResultTagType(result: string): 'success' | 'danger' | 'warning' | 'info' {
  if (result === 'passed') {
    return 'success'
  }
  if (result === 'failed') {
    return 'danger'
  }
  if (result === 'blocked') {
    return 'warning'
  }
  return 'info'
}

function mappedByText(mappedBy: string): string {
  const labels: Record<string, string> = {
    case_no: '用例编号',
    script: '脚本整体',
  }
  return labels[mappedBy] || mappedBy || '-'
}

async function reloadAll(): Promise<void> {
  try {
    categories.value = await automationApi.categories()
    runtime.value = await automationApi.runtime()
  } catch {
    // 体检信息拿不到不阻塞主流程，表格自己会提示加载失败
  }
  try {
    interpreters.value = await automationApi.interpreters(category.value)
  } catch {
    interpreters.value = null
  }
  try {
    envs.value = (await testEnvApi.list()).list
  } catch {
    envs.value = []
  }
  tableRef.value?.reload()
}

let activatedOnce = false
onMounted(() => {
  void reloadAll()
})
// 四个二级菜单共用组件、被 keep-alive 按路径缓存：切回来不会重新挂载，不重拉就还是上次的数据。
onActivated(() => {
  // 首次挂载时 onActivated 与 onMounted 同时触发，跳过以免重复请求
  if (activatedOnce) {
    void reloadAll()
  }
  activatedOnce = true
})
// 同一个组件内切类别只变路由参数、不重挂载，onActivated 也不会触发。
watch(category, () => {
  tableRef.value?.clearSelection()
  void reloadAll()
})
</script>

<style scoped>
.auto-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}

.auto-head__actions {
  display: flex;
  gap: 8px;
}

.auto-alert {
  margin-bottom: 12px;
}

.framework-hint {
  width: 100%;
  margin: 8px 0 0;
  padding: 9px 12px 9px 13px;
  border: 1px solid color-mix(in srgb, var(--aitp-note-warning) 16%, transparent);
  border-left: 3px solid var(--aitp-note-warning);
  border-radius: 10px;
  background: color-mix(in srgb, var(--aitp-note-warning) 5%, var(--aitp-panel));
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--aitp-text-weak);
}

.framework-hint p {
  margin: 2px 0;
  line-height: 1.6;
}

.framework-hint p:first-child {
  font-weight: 600;
  color: var(--aitp-text);
}

.framework-hint__cmd {
  padding: 4px 8px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  word-break: break-all;
}

.framework-hint__alt {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: 4px 0;
}

.auto-artifacts {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.auto-artifacts__row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 10px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.auto-artifacts__path {
  flex: 1;
  word-break: break-all;
}

.auto-artifacts__link {
  text-decoration: none;
}

.auto-artifacts__empty {
  margin: 4px 0;
  font-size: 13px;
  line-height: 1.7;
}

.env-hint {
  width: 100%;
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.auto-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.auto-block__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.auto-block__head h3 {
  margin: 0;
  font-size: 15px;
}

.auto-name {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.auto-name__title {
  font-weight: 600;
}

.auto-result__head {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.auto-result__cmd {
  padding: 8px 10px;
  margin-bottom: 12px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  word-break: break-all;
  white-space: pre-wrap;
}

.auto-result__out {
  margin: 0;
  padding: 10px 12px;
  max-height: 380px;
  overflow: auto;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
}

.auto-writeback {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 10px;
  margin-bottom: 12px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.auto-writeback__summary {
  flex: 1;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
}

.auto-writeback__table {
  margin-bottom: 14px;
}
</style>
