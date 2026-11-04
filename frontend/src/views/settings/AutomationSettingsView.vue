<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">自动化管理设置</h2>
      <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
    </div>

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
            placeholder="例如 D:\\app_install\\python311\\python.exe"
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
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back } from '@element-plus/icons-vue'
import { automationApi, settingsApi } from '@/api'
import type { AutomationRuntime } from '@/api/types'

const router = useRouter()

const CONFIG_INTERPRETER = 'automation.python.interpreter'
const CONFIG_TIMEOUT = 'automation.python.timeout'

const interpreter = ref('')
const timeout = ref(300)
const saving = ref(false)
const runtime = ref<AutomationRuntime | null>(null)

async function load(): Promise<void> {
  try {
    const configs = await settingsApi.config()
    const find = (key: string): string =>
      configs.find((item) => item.key === key)?.value ?? ''
    interpreter.value = find(CONFIG_INTERPRETER)
    const raw = Number(find(CONFIG_TIMEOUT))
    timeout.value = Number.isInteger(raw) && raw > 0 ? raw : 300
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
      { key: CONFIG_INTERPRETER, value: interpreter.value.trim(), valueType: 'string' },
      { key: CONFIG_TIMEOUT, value: String(timeout.value), valueType: 'int' },
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
