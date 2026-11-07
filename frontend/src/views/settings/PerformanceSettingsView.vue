<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">性能测试管理设置</h2>
      <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
    </div>

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
import { automationApi, settingsApi } from '@/api'
import type { AutomationRuntime } from '@/api/types'

const router = useRouter()

const CONFIG_JMETER_HOME = 'performance.jmeter.home'
const CONFIG_JMETER_ARGS = 'performance.jmeter.args'

const jmeterHome = ref('')
const jmeterArgs = ref('')
const saving = ref(false)
const runtime = ref<AutomationRuntime | null>(null)

async function load(): Promise<void> {
  try {
    const configs = await settingsApi.config()
    const find = (key: string): string =>
      configs.find((item) => item.key === key)?.value ?? ''
    jmeterHome.value = find(CONFIG_JMETER_HOME)
    jmeterArgs.value = find(CONFIG_JMETER_ARGS)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载配置失败')
  }
  try {
    runtime.value = await automationApi.runtime()
  } catch {
    runtime.value = null
  }
}

async function save(): Promise<void> {
  saving.value = true
  try {
    await settingsApi.updateConfig([
      { key: CONFIG_JMETER_HOME, value: jmeterHome.value.trim(), valueType: 'string' },
      { key: CONFIG_JMETER_ARGS, value: jmeterArgs.value.trim(), valueType: 'string' },
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
