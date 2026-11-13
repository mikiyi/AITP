<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">探索性测试设置</h2>
      <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
    </div>

    <RouteSubNav :items="EXEC_TABS" />

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>探索参数与 MCP 服务</h3>
        <div class="settings-block__actions">
          <el-button size="small" @click="reset">重置为默认</el-button>
          <el-button type="primary" size="small" :loading="saving" @click="save">保存配置</el-button>
        </div>
      </div>
      <el-alert type="info" :closable="false" show-icon
                title="探索产物先进知识待审池人工审核，审核通过才转缺陷；口令/令牌输入按配置脱敏，绝不留明文。"
                style="margin-bottom: 12px" />

      <el-form label-width="140px" style="max-width: 760px">
        <el-form-item label="最大步数">
          <el-input-number v-model="form.maxSteps" :min="1" :max="200" />
          <span class="muted" style="margin-left: 12px">收敛上限，默认 20</span>
        </el-form-item>
        <el-form-item label="最大时长（秒）">
          <el-input-number v-model="form.maxSeconds" :min="1" :max="3600" />
        </el-form-item>
        <el-form-item label="单步超时（秒）">
          <el-input-number v-model="form.perStepTimeout" :min="1" :max="300" />
        </el-form-item>
        <el-form-item label="单步截图数">
          <el-input-number v-model="form.perStepScreenshots" :min="0" :max="5" />
          <span class="muted" style="margin-left: 12px">异常步强制补 3 张</span>
        </el-form-item>
        <el-form-item label="记录选择器">
          <el-switch v-model="form.recordSelectors" />
        </el-form-item>
        <el-form-item label="输入脱敏">
          <el-switch v-model="form.inputMasking" />
          <span class="muted" style="margin-left: 12px">建议保持开启</span>
        </el-form-item>
        <el-form-item label="日志切片窗口（秒）">
          <el-input-number v-model="form.logWindowSeconds" :min="1" :max="10" />
          <span class="muted" style="margin-left: 12px">双锚点 ±N 秒，默认 5</span>
        </el-form-item>
        <el-form-item label="MCP 服务命令">
          <el-input v-model="form.mcpCommand" placeholder='例如：python path/to/mock_mcp_server.py'
                    style="width: 100%" />
          <div class="muted" style="margin-top: 4px">
            钉住工具（只允许调用这些）：
            <el-tag v-for="tool in form.pinnedTools" :key="tool" size="small" style="margin-right: 6px">
              {{ tool }}
            </el-tag>
          </div>
        </el-form-item>
      </el-form>

      <div class="settings-block__head" style="margin-top: 8px">
        <h3>日志来源（最多 5 个）</h3>
        <div class="settings-block__actions">
          <el-button size="small" :disabled="form.logSources.length >= 5" @click="addSource">添加来源</el-button>
        </div>
      </div>
      <el-table v-if="form.logSources.length" :data="form.logSources" border stripe size="small">
        <el-table-column label="名称" min-width="140">
          <template #default="scope">
            <el-input v-model="scope.row.name" placeholder="来源名" size="small" />
          </template>
        </el-table-column>
        <el-table-column label="日志文件绝对路径" min-width="320">
          <template #default="scope">
            <el-input v-model="scope.row.path" placeholder="例如：D:/logs/app.log" size="small" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="scope">
            <el-button link type="danger" @click="form.logSources.splice(scope.$index, 1)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <p v-else class="muted">未配置日志来源：探索异常时不会生成日志切片（证据仍含截图/网络/控制台）。</p>
      <div v-if="savedMsg" style="margin-top: 12px">
        <el-alert type="success" :closable="false" show-icon :title="savedMsg" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back } from '@element-plus/icons-vue'
import RouteSubNav from '@/components/RouteSubNav.vue'
import { EXEC_TABS } from '@/router/subnav'
import { exploratoryApi } from '@/api'
import type { ExploratorySettings } from '@/api/types'

const router = useRouter()
const saving = ref(false)
const savedMsg = ref('')

const form = reactive<ExploratorySettings>({
  maxSteps: 20,
  maxSeconds: 180,
  perStepTimeout: 30,
  perStepScreenshots: 1,
  recordSelectors: true,
  inputMasking: true,
  screenshotDir: 'exploratory/screenshots',
  logSliceDir: 'exploratory/log-slices',
  logSources: [],
  logWindowSeconds: 5,
  mcpCommand: '',
  pinnedTools: [],
})

async function load(): Promise<void> {
  try {
    const settings = await exploratoryApi.settings()
    Object.assign(form, settings)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载配置失败')
  }
}

async function save(): Promise<void> {
  saving.value = true
  try {
    const saved = await exploratoryApi.saveSettings({
      maxSteps: form.maxSteps,
      maxSeconds: form.maxSeconds,
      perStepTimeout: form.perStepTimeout,
      perStepScreenshots: form.perStepScreenshots,
      recordSelectors: form.recordSelectors,
      inputMasking: form.inputMasking,
      logWindowSeconds: form.logWindowSeconds,
      logSources: form.logSources,
      mcpCommand: form.mcpCommand,
    })
    Object.assign(form, saved)
    savedMsg.value = '配置已保存'
    ElMessage.success('配置已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存配置失败')
  } finally {
    saving.value = false
  }
}

async function reset(): Promise<void> {
  const meta = await exploratoryApi.meta()
  Object.assign(form, meta.defaults)
}

function addSource(): void {
  form.logSources.push({ name: '', path: '', source: 'file' })
}

onMounted(load)
</script>

<style scoped>
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>