<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">性能测试管理设置</h2>
      <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
    </div>

    <RouteSubNav :items="EXEC_TABS" />

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>JMeter 启动配置</h3>
        <el-button type="primary" size="small" :loading="saving" @click="save">保存</el-button>
      </div>
      <p class="muted settings-block__hint">
        性能测试管理里的脚本由本机 JMeter 命令行执行。填写 JMeter 安装根目录（其下有 bin/jmeter 或 bin/jmeter.bat），
        脚本会以无界面模式运行并把结果写到数据目录下。
      </p>
      <el-form label-width="180px">
        <el-form-item label="JMeter 根目录">
          <el-input
            v-model="jmeterHome"
            placeholder="例如 C:\tools\apache-jmeter-5.6.3 或 /opt/apache-jmeter-5.6.3"
            clearable
          />
        </el-form-item>
        <el-form-item label="附加启动参数">
          <el-input
            v-model="jmeterArgs"
            placeholder="例如 -Jthread.count=50 -Jduration=60"
            clearable
          />
        </el-form-item>
        <el-form-item label="场景负载模型">
          <el-input
            v-model="loadModel"
            type="textarea" :rows="2"
            placeholder='JSON，如 {"threads":10,"rampUp":5,"loops":100,"duration":60}；jmx 用 ${__P(threads,...)} 引用'
          />
        </el-form-item>
        <el-form-item label="执行并发上限">
          <el-input-number v-model="concurrency" :min="1" :max="8" />
          <span class="muted form-hint">任务队列同时执行上限（1-8），相互隔离</span>
        </el-form-item>
        <el-form-item label="失败自动转缺陷">
          <el-radio-group v-model="autoBugMode">
            <el-radio-button value="off">关闭</el-radio-button>
            <el-radio-button value="review">待审（默认）</el-radio-button>
            <el-radio-button value="auto">自动</el-radio-button>
          </el-radio-group>
          <span class="muted form-hint">{{ AUTO_BUG_HINTS[autoBugMode] }}</span>
        </el-form-item>
      </el-form>

      <el-descriptions v-if="runtime" :column="2" border size="small" class="settings-desc">
        <el-descriptions-item label="可执行文件">{{ runtime.jmeterCommand || '未找到' }}</el-descriptions-item>
        <el-descriptions-item label="可用性">
          <el-tag :type="runtime.jmeterAvailable ? 'success' : 'danger'" size="small" effect="plain">
            {{ runtime.jmeterAvailable ? '可用' : '不可用' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="当前生效参数">
          {{ runtime.jmeterArgs.length ? runtime.jmeterArgs.join(' ') : '（无）' }}
        </el-descriptions-item>
        <el-descriptions-item label="运行环境">{{ runtime.platform }}</el-descriptions-item>
      </el-descriptions>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>运行说明</h3>
      </div>
      <ul class="settings-list">
        <li>JMeter 以 <span class="text-mono">-n</span> 无界面模式运行，参数为脚本路径、结果文件与日志文件。</li>
        <li>结果文件（.jtl）与日志文件按次执行落在数据目录的 automation/runs 下，便于回看。</li>
        <li>压测脚本建议先用小并发验证，再逐步加压；每次执行都会记录耗时与退出码。</li>
        <li>未配置 JMeter 时，性能脚本的执行会被拦住并提示先完成本页配置。</li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onActivated, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back } from '@element-plus/icons-vue'
import RouteSubNav from '@/components/RouteSubNav.vue'
import { EXEC_TABS } from '@/router/subnav'
import { automationApi, settingsApi } from '@/api'
import type { AutomationRuntime } from '@/api/types'

const router = useRouter()

const CONFIG_JMETER_HOME = 'performance.jmeter.home'
const CONFIG_JMETER_ARGS = 'performance.jmeter.args'
const CONFIG_AUTO_BUG_MODE = 'automation.auto_bug_mode'
const CONFIG_AUTO_BUG = 'automation.auto_bug'

type AutoBugMode = 'off' | 'review' | 'auto'

const AUTO_BUG_MODES: readonly AutoBugMode[] = ['off', 'review', 'auto']
/** 与后端 auto_bug_service._FALSE_VALUES 同口径：旧布尔键落在这个集合里算「关闭」。 */
const LEGACY_FALSE_VALUES = ['', '0', 'false', 'off', 'no', 'none']

const AUTO_BUG_HINTS: Record<AutoBugMode, string> = {
  off: '失败不自动生成任何缺陷，只在执行记录里手动「转缺陷」。',
  review: '接口场景未通过、自动化脚本失败 / 超时 / 异常先落「缺陷待审区」，人工「确认转缺陷」才生成 P2 缺陷并关联失败用例。',
  auto: '失败直接生成 P2 缺陷（描述带执行编号与错误证据）并关联失败用例，不进待审。',
}

const jmeterHome = ref('')
const jmeterArgs = ref('')
const loadModel = ref('')
const concurrency = ref(2)
const autoBugMode = ref<AutoBugMode>('review')
const saving = ref(false)
const runtime = ref<AutomationRuntime | null>(null)

async function loadConfigs(): Promise<void> {
  try {
    const configs = await settingsApi.config()
    const find = (key: string): string =>
      configs.find((item) => item.key === key)?.value ?? ''
    jmeterHome.value = find(CONFIG_JMETER_HOME)
    jmeterArgs.value = find(CONFIG_JMETER_ARGS)
    loadModel.value = find('performance.load_model')
    concurrency.value = Number(find('task.queue.concurrency') || 2) || 2
    const mode = find(CONFIG_AUTO_BUG_MODE).toLowerCase()
    autoBugMode.value = AUTO_BUG_MODES.includes(mode as AutoBugMode)
      ? (mode as AutoBugMode)
      : (LEGACY_FALSE_VALUES.includes(find(CONFIG_AUTO_BUG).toLowerCase()) ? 'off' : 'auto')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载配置失败')
  }
}

async function loadRuntime(): Promise<void> {
  try {
    runtime.value = await automationApi.runtime()
  } catch {
    runtime.value = null
  }
}

async function load(): Promise<void> {
  await loadConfigs()
  await loadRuntime()
}

async function save(): Promise<void> {
  saving.value = true
  try {
    await settingsApi.updateConfig([
      { key: CONFIG_JMETER_HOME, value: jmeterHome.value.trim(), valueType: 'string' },
      { key: CONFIG_JMETER_ARGS, value: jmeterArgs.value.trim(), valueType: 'string' },
      { key: 'performance.load_model', value: loadModel.value.trim(), valueType: 'json' },
      { key: 'task.queue.concurrency', value: String(concurrency.value), valueType: 'number' },
      { key: CONFIG_AUTO_BUG_MODE, value: autoBugMode.value, valueType: 'string' },
      // 旧布尔键跟着同步：还有历史数据与外部脚本只读它，不能让两键互相矛盾
      { key: CONFIG_AUTO_BUG, value: autoBugMode.value === 'auto' ? 'true' : 'false', valueType: 'boolean' },
    ])
    ElMessage.success('已保存')
    await loadConfigs()
    // 运行时体检要探本机 JMeter/adb，配好 JMeter 后要 4 秒以上；后台刷新即可，别把「保存」按钮锁住
    void loadRuntime()
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

.settings-block__hint {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.6;
}

.settings-desc {
  margin-top: 4px;
}

.settings-list {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.9;
  color: var(--aitp-text-weak);
}
</style>
