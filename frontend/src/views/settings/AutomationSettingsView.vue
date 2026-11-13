<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">自动化管理设置</h2>
      <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
    </div>

    <RouteSubNav :items="EXEC_TABS" />

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>Python 解释器</h3>
        <el-button type="primary" size="small" :loading="saving" @click="save">保存</el-button>
      </div>
      <p class="muted settings-block__hint">
        Web 自动化、App 自动化、接口自动化三类用例都由这个解释器执行。
        留空则使用 AITP 服务自身的解释器。解释器路径由本机管理员指定，仅用于执行你自己编写的用例脚本。
      </p>
      <el-form label-width="180px">
        <el-form-item label="解释器路径">
          <el-input
            v-model="interpreter"
            placeholder="例如 C:\Python312\python.exe 或 /usr/bin/python3"
            clearable
          />
        </el-form-item>
        <el-form-item label="默认超时（秒）">
          <el-input-number v-model="timeout" :min="1" :max="7200" style="width: 200px" />
        </el-form-item>
      </el-form>

      <el-descriptions v-if="runtime" :column="2" border size="small" class="settings-desc">
        <el-descriptions-item label="当前生效解释器">{{ runtime.pythonInterpreter }}</el-descriptions-item>
        <el-descriptions-item label="解释器版本">{{ runtime.pythonVersion || '-' }}</el-descriptions-item>
        <el-descriptions-item label="可用性">
          <el-tag :type="runtime.pythonInterpreterAvailable ? 'success' : 'danger'" size="small" effect="plain">
            {{ runtime.pythonInterpreterAvailable ? '可用' : '不可用' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="配置来源">
          {{ runtime.pythonInterpreterConfigured ? '已手动指定' : '默认使用服务自身解释器' }}
        </el-descriptions-item>
      </el-descriptions>

      <div class="settings-block__head settings-block__head--sub">
        <h4>本机解释器体检</h4>
        <el-button size="small" :loading="probing" @click="probe">重新体检</el-button>
      </div>
      <p class="muted settings-block__hint">
        下面列出本机找得到的 Python 解释器，以及它们各自装好了哪些自动化框架依赖。
        「已装了几个」是当前类别框架包的实际命中数，不是 pip 总包数。
      </p>
      <el-table :data="interpreterCandidates" size="small" border class="settings-desc">
        <el-table-column label="解释器" min-width="320">
          <template #default="scope">
            <div class="text-mono">{{ scope.row.path }}</div>
            <div class="muted">Python {{ scope.row.version }} · 来源：{{ scope.row.source }}</div>
          </template>
        </el-table-column>
        <el-table-column label="框架依赖" width="150">
          <template #default="scope">
            <el-tag
              :type="scope.row.installedCount === scope.row.totalPackages ? 'success' : scope.row.installedCount ? 'warning' : 'info'"
              size="small"
              effect="plain"
            >
              {{ scope.row.installedCount }} / {{ scope.row.totalPackages }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="缺少" min-width="220">
          <template #default="scope">
            <span v-if="!scope.row.missing.length" class="muted">无</span>
            <span v-else>{{ scope.row.missing.join('、') }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130">
          <template #default="scope">
            <el-tag v-if="scope.row.isCurrent" size="small" type="success" effect="plain">当前使用</el-tag>
            <el-button v-else link type="primary" :loading="switching === scope.row.path" @click="useInterpreter(scope.row.path)">
              使用此解释器
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>App 自动化框架</h3>
        <el-button type="primary" size="small" :loading="saving" @click="save">保存</el-button>
      </div>
      <p class="muted settings-block__hint">
        App 自动化按平台分两套框架：iOS 与 Android 用 Appium，鸿蒙（HOS）用 Hypium。
        框架的 Python 包要装在上面那个解释器里，命令行工具装在 PATH 上会自动识别，装在别处就填绝对路径。
      </p>
      <el-form label-width="180px">
        <el-form-item label="Appium Server 地址">
          <el-input v-model="appiumServer" placeholder="默认 http://127.0.0.1:4723" clearable />
        </el-form-item>
        <el-form-item label="Appium 命令路径">
          <el-input v-model="appiumCommand" placeholder="留空按 PATH 查找，例如 appium.cmd 或 /usr/local/bin/appium" clearable />
        </el-form-item>
        <el-form-item label="adb 命令路径">
          <el-input v-model="adbCommand" placeholder="留空按 PATH 查找，例如 adb.exe 或 ~/android-sdk/platform-tools/adb" clearable />
        </el-form-item>
        <el-form-item label="鸿蒙 hdc 命令路径">
          <el-input v-model="hdcCommand" placeholder="留空按 PATH 查找，例如 hdc.exe 或 /usr/local/bin/hdc" clearable />
        </el-form-item>
      </el-form>

      <div class="settings-block__head settings-block__head--sub">
        <h4>环境自检</h4>
        <el-button size="small" :loading="probing" @click="probe">重新体检</el-button>
      </div>
      <p class="muted settings-block__hint">
        进入页面、保存配置或点「重新体检」时会自动检查框架依赖、设备连接、adb 与 Playwright 浏览器内核；
        未就绪的项会给出可复制的命令，装完再体检一次即可。
      </p>
      <div class="selfcheck">
        <div
          v-for="item in appSelfChecks"
          :key="item.key"
          class="selfcheck__item"
          :class="{ 'selfcheck__item--bad': !item.ready }"
        >
          <div class="selfcheck__head">
            <span class="selfcheck__title">{{ item.title }}</span>
            <el-tag :type="item.ready ? 'success' : 'warning'" size="small" effect="plain">
              {{ item.ready ? '已就绪' : '未就绪' }}
            </el-tag>
          </div>
          <div class="selfcheck__detail">{{ item.detail }}</div>
          <div v-for="command in item.commands" :key="command" class="selfcheck__cmd">
            <code>{{ command }}</code>
            <el-button link type="primary" :icon="CopyDocument" @click="copyCommand(command)">
              复制
            </el-button>
          </div>
          <div v-if="item.hint" class="selfcheck__hint">{{ item.hint }}</div>
        </div>
      </div>

      <el-table v-if="appFrameworks.length" :data="appFrameworks" size="small" border class="settings-desc">
        <el-table-column prop="label" label="框架" min-width="160" />
        <el-table-column label="适用平台" min-width="130">
          <template #default="scope">{{ scope.row.platformLabels.join(' / ') || '不限' }}</template>
        </el-table-column>
        <el-table-column label="依赖包" min-width="180">
          <template #default="scope">
            {{ scope.row.package || '无需安装包' }}
            <span v-if="scope.row.packageVersion" class="muted">（{{ scope.row.packageVersion }}）</span>
          </template>
        </el-table-column>
        <el-table-column label="命令行工具" min-width="140">
          <template #default="scope">{{ scope.row.command || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="scope">
            <el-tag :type="scope.row.ready ? 'success' : 'warning'" size="small" effect="plain">
              {{ scope.row.ready ? '就绪' : '缺依赖' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="缺少" min-width="220">
          <template #default="scope">
            <span v-if="!scope.row.missing?.length" class="muted">无</span>
            <span v-else>{{ scope.row.missing.join('、') }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>执行策略说明</h3>
      </div>
      <ul class="settings-list">
        <li>只有登记在「自动化脚本」里的脚本才会被执行，脚本正文保存在本机数据库中。</li>
        <li>执行时子进程不使用 shell，附加参数按普通参数传递，不存在命令拼接注入面。</li>
        <li>每次执行都有超时上限，超时按「超时」收口并留下执行记录。</li>
        <li>执行时子进程环境会剥离 AI 密钥变量，脚本拿不到模型凭据。</li>
        <li>执行记录写入审计日志，可在「设置 → 设置总览」的审计日志里按资源类型追溯。</li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back, CopyDocument } from '@element-plus/icons-vue'
import RouteSubNav from '@/components/RouteSubNav.vue'
import { EXEC_TABS } from '@/router/subnav'
import { automationApi, settingsApi } from '@/api'
import type {
  AutomationFrameworkRuntime,
  AutomationInterpreters,
  AutomationRuntime,
} from '@/api/types'

const router = useRouter()

const CONFIG_INTERPRETER = 'automation.python.interpreter'
const CONFIG_TIMEOUT = 'automation.python.timeout'
const CONFIG_APPIUM = 'automation.appium.command'
const CONFIG_APPIUM_SERVER = 'automation.appium.server'
const CONFIG_ADB = 'automation.adb.command'
const CONFIG_HDC = 'automation.hdc.command'

const interpreter = ref('')
const timeout = ref(300)
const appiumCommand = ref('')
const appiumServer = ref('')
const adbCommand = ref('')
const hdcCommand = ref('')
const saving = ref(false)
const probing = ref(false)
const switching = ref('')
const runtime = ref<AutomationRuntime | null>(null)
const interpreters = ref<AutomationInterpreters | null>(null)

/** App 自动化的两套框架：iOS / Android 走 Appium，鸿蒙走 Hypium。 */
const appFrameworks = computed(() =>
  (runtime.value?.frameworks ?? []).filter((item) => item.targets.includes('app')),
)

interface SelfCheckItem {
  key: string
  title: string
  ready: boolean
  detail: string
  commands: string[]
  hint?: string
}

/** 按缺失项挑命令：已经装好的包不再提示安装，只提示真正缺的那一步。 */
function frameworkCommands(item: AutomationFrameworkRuntime): string[] {
  const missing = item.missing ?? []
  const commands: string[] = []
  if (!item.packageInstalled && item.installCommand) {
    commands.push(item.installCommand)
  }
  if (item.code === 'appium') {
    if (missing.some((text) => text.includes('命令行工具'))) {
      commands.push('npm install -g appium')
    }
    if (missing.some((text) => text.includes('Appium Server'))) {
      commands.push('appium')
    }
  } else if (item.code === 'uiautomator2' && !item.packageInstalled) {
    commands.push(...(item.setupCommands ?? []))
  }
  return commands
}

/** App 自动化四项自检：框架依赖、连接设备、adb、浏览器内核。 */
const appSelfChecks = computed<SelfCheckItem[]>(() => {
  const rt = runtime.value
  const frameworks = appFrameworks.value
  const blocked = frameworks.filter((item) => !item.ready)
  const frameworkDetail = blocked.length
    ? blocked
        .map((item) => `${item.label} 缺 ${(item.missing ?? []).join('、')}`)
        .join('；')
    : frameworks.length
      ? `${frameworks.map((item) => item.label).join('、')} 依赖均已就绪`
      : '未加载到 App 自动化框架清单'
  const frameworkCommandList = Array.from(
    new Set(blocked.flatMap(frameworkCommands)),
  )

  const devices = [...(rt?.androidDevices ?? []), ...(rt?.hosDevices ?? [])]
  const deviceReady = devices.length > 0
  const deviceDetail = !rt
    ? '运行时体检结果未加载'
    : deviceReady
      ? devices.map((item) => `${item.serial}（${item.state}）`).join('、')
      : rt.adbAvailable || rt.hdcAvailable
        ? '未检测到设备：用 USB 连接设备并开启「开发者选项 → USB 调试」，或先启动模拟器'
        : '未检测到 adb / hdc，先按下一项把命令行工具装好'
  const deviceCommands = deviceReady || !rt
    ? []
    : [
        ...(rt.adbAvailable ? ['adb devices'] : []),
        ...(rt.hdcAvailable ? ['hdc list targets'] : []),
      ]

  const adbReady = Boolean(rt?.adbAvailable)
  const adbDetail = !rt
    ? '运行时体检结果未加载'
    : adbReady
      ? rt.adbCommand || '已在 PATH 中找到'
      : '未找到 adb：下载 Android SDK Platform-Tools，把 platform-tools 目录加入 PATH，或在上方填写 adb.exe 绝对路径'

  const browserReady = Boolean(rt?.playwrightBrowsersAvailable)
  const browserCommands = !browserReady && rt?.pythonInterpreter
    ? [`"${rt.pythonInterpreter}" -m playwright install chromium`]
    : []

  return [
    {
      key: 'framework',
      title: '框架',
      ready: frameworks.length > 0 && blocked.length === 0,
      detail: frameworkDetail,
      commands: frameworkCommandList,
      hint: blocked.map((item) => item.setupHint).filter(Boolean).join('；') || undefined,
    },
    {
      key: 'devices',
      title: '连接设备',
      ready: deviceReady,
      detail: deviceDetail,
      commands: deviceCommands,
    },
    {
      key: 'adb',
      title: 'adb',
      ready: adbReady,
      detail: adbDetail,
      commands: adbReady ? [] : ['adb version', 'adb devices'],
    },
    {
      key: 'browser',
      title: '浏览器内核',
      ready: browserReady,
      detail: !rt
        ? '运行时体检结果未加载'
        : browserReady
          ? 'Playwright 浏览器内核已下载'
          : '未下载 Chromium 内核，Web 自动化用 Playwright 时会启动失败',
      commands: browserCommands,
    },
  ]
})

function copyCommand(text: string): void {
  void navigator.clipboard.writeText(text).then(
    () => ElMessage.success('命令已复制'),
    () => ElMessage.warning('复制失败，请手动选择命令'),
  )
}

/** 体检结果补上「缺哪些包」的易读字段，避免模板里反复拼。 */
const interpreterCandidates = computed(() =>
  (interpreters.value?.candidates ?? []).map((item) => ({
    ...item,
    missing: Object.entries(item.packages)
      .filter(([, version]) => !version)
      .map(([name]) => name),
  })),
)

async function loadRuntime(): Promise<void> {
  try {
    runtime.value = await automationApi.runtime()
    interpreters.value = await automationApi.interpreters()
  } catch {
    runtime.value = null
    interpreters.value = null
  }
}

async function load(): Promise<void> {
  try {
    const configs = await settingsApi.config()
    const find = (key: string): string =>
      configs.find((item) => item.key === key)?.value ?? ''
    interpreter.value = find(CONFIG_INTERPRETER)
    appiumCommand.value = find(CONFIG_APPIUM)
    appiumServer.value = find(CONFIG_APPIUM_SERVER)
    adbCommand.value = find(CONFIG_ADB)
    hdcCommand.value = find(CONFIG_HDC)
    const raw = Number(find(CONFIG_TIMEOUT))
    timeout.value = Number.isInteger(raw) && raw > 0 ? raw : 300
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载配置失败')
  }
  await loadRuntime()
}

async function probe(): Promise<void> {
  probing.value = true
  try {
    await loadRuntime()
  } finally {
    probing.value = false
  }
}

async function useInterpreter(path: string): Promise<void> {
  switching.value = path
  try {
    await automationApi.selectInterpreter(path)
    interpreter.value = path
    ElMessage.success(`执行解释器已切换为 ${path}`)
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '切换解释器失败')
  } finally {
    switching.value = ''
  }
}

async function save(): Promise<void> {
  saving.value = true
  try {
    await settingsApi.updateConfig([
      { key: CONFIG_INTERPRETER, value: interpreter.value.trim(), valueType: 'string' },
      { key: CONFIG_TIMEOUT, value: String(timeout.value), valueType: 'int' },
      { key: CONFIG_APPIUM, value: appiumCommand.value.trim(), valueType: 'string' },
      { key: CONFIG_APPIUM_SERVER, value: appiumServer.value.trim(), valueType: 'string' },
      { key: CONFIG_ADB, value: adbCommand.value.trim(), valueType: 'string' },
      { key: CONFIG_HDC, value: hdcCommand.value.trim(), valueType: 'string' },
    ])
    ElMessage.success('已保存')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void load()
})
onActivated(() => {
  void load()
})
</script>

<style scoped>
.settings-block {
  margin-bottom: 16px;
}

.settings-block__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.settings-block__head h3 {
  margin: 0;
  font-size: 15px;
}

.settings-block__head--sub {
  margin-top: 20px;
}

.settings-block__head h4 {
  margin: 0;
  font-size: 14px;
}

.settings-block__hint {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.6;
}

.settings-desc {
  margin-top: 4px;
}

.selfcheck {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.selfcheck__item {
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.selfcheck__item--bad {
  border-color: color-mix(in srgb, var(--aitp-note-warning) 16%, transparent);
  border-left: 3px solid var(--aitp-note-warning);
  background: color-mix(in srgb, var(--aitp-note-warning) 5%, var(--aitp-panel));
}

.selfcheck__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.selfcheck__title {
  font-size: 13px;
  font-weight: 600;
}

.selfcheck__detail {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--aitp-text-weak);
}

.selfcheck__cmd {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}

.selfcheck__cmd code {
  flex: 1;
  padding: 4px 8px;
  font-size: 12px;
  word-break: break-all;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}

.selfcheck__hint {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-color-warning-dark-2);
}

@media (max-width: 900px) {
  .selfcheck {
    grid-template-columns: 1fr;
  }
}

.settings-list {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.9;
  color: var(--aitp-text-weak);
}
</style>
