<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">MCP 服务</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
        <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>AITP MCP 服务端</h3>
        <el-tag :type="settings?.enabled ? 'success' : 'info'" effect="plain" size="small">
          {{ settings?.enabled ? '已开启' : '未开启' }}
        </el-tag>
        <span class="muted">{{ settings?.protocol }}</span>
      </div>
      <el-alert type="info" :closable="false" show-icon title="只读优先，写入走待审">
        <div v-for="(note, index) in settings?.notes ?? []" :key="index" class="mcp-note">{{ note }}</div>
      </el-alert>

      <el-form label-width="110px" class="mcp-form" @submit.prevent>
        <el-form-item label="开放服务">
          <el-switch v-model="form.enabled" @change="saveSwitch" />
          <span class="muted mcp-hint">关闭时所有 JSON-RPC 请求返回 403。</span>
        </el-form-item>
        <el-form-item label="允许写入">
          <el-switch v-model="form.allowWrite" :disabled="!form.enabled" @change="saveSwitch" />
          <span class="muted mcp-hint">开启后仅开放 bugs.draft.create，且只落缺陷待审区。</span>
        </el-form-item>
        <el-form-item label="服务端点">
          <code class="mcp-endpoint">{{ settings?.endpoint || '-' }}</code>
          <el-button link type="primary" @click="copyText(settings?.endpoint || '', '端点')">复制</el-button>
        </el-form-item>
        <el-form-item label="访问令牌">
          <template v-if="settings?.hasToken">
            <el-tag type="success" effect="plain" size="small">已配置</el-tag>
            <code class="mcp-token">{{ settings?.tokenPreview }}</code>
            <span class="muted mcp-token-meta">
              最后使用：{{ settings?.lastUsedAt || '尚未使用' }}
              <template v-if="settings?.lastClient">· {{ settings.lastClient }}</template>
            </span>
          </template>
          <el-tag
            v-else-if="settings?.tokenRequired"
            type="danger"
            effect="plain"
            size="small"
          >
            无生效令牌（客户端将被拒绝）
          </el-tag>
          <el-tag v-else type="warning" effect="plain" size="small">未配置（仅回环访问）</el-tag>
          <el-button type="primary" plain size="small" :loading="rotating" @click="requestToken">
            {{ settings?.hasToken ? '轮换令牌' : '生成令牌' }}
          </el-button>
        </el-form-item>
      </el-form>

      <div class="mcp-curl">
        <pre>{{ curlSample }}</pre>
        <el-button link type="primary" class="mcp-curl__copy" @click="copyText(curlSample, '调用示例')">
          复制示例
        </el-button>
      </div>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>令牌记录</h3>
        <span class="muted">明文只在生成时展示一次；停用可恢复，删除不可恢复</span>
      </div>
      <el-table :data="settings?.tokens ?? []" size="small" border>
        <el-table-column label="令牌" width="140">
          <template #default="{ row }">
            <code class="mcp-token">{{ row.preview }}</code>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-tag :type="tokenStatusType(row)" size="small" effect="plain">
              {{ tokenStatusLabel(row) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="createdAt" label="创建时间" width="180" />
        <el-table-column label="最后使用" width="180">
          <template #default="{ row }">{{ row.lastUsedAt || '尚未使用' }}</template>
        </el-table-column>
        <el-table-column label="最后调用客户端" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.lastClient || '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'active'"
              link
              type="warning"
              @click="disableToken(row)"
            >
              停用
            </el-button>
            <el-button
              v-else-if="row.status === 'disabled'"
              link
              type="primary"
              @click="enableToken(row)"
            >
              启用
            </el-button>
            <el-button
              v-if="row.status !== 'deleted'"
              link
              type="danger"
              @click="deleteToken(row)"
            >
              删除
            </el-button>
            <span v-else class="muted">不可恢复</span>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">还没有生成过令牌</span>
        </template>
      </el-table>
    </div>

    <el-dialog
      v-model="tokenDialogVisible"
      title="新的访问令牌"
      width="560px"
      :close-on-click-modal="false"
      :before-close="handleTokenDialogClose"
      @closed="resetIssuedToken"
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="令牌只显示这一次，关闭后无法再次查看；请立即复制并保存到 AI 客户端配置"
      />
      <div class="mcp-token-reveal">
        <code>{{ issuedToken }}</code>
        <el-button type="primary" plain size="small" @click="copyIssuedToken">复制令牌</el-button>
      </div>
      <p class="muted mcp-token-reveal__hint">
        忘记令牌只能重新轮换；轮换后旧令牌立即失效，已配置的客户端需同步更新。
      </p>
      <template #footer>
        <el-button @click="handleTokenDialogClose()">关闭</el-button>
        <el-button type="primary" @click="confirmTokenSaved">我已保存</el-button>
      </template>
    </el-dialog>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>工具清单</h3>
        <span class="muted">tools/list 返回的全部工具；标注「写」的需开启允许写入</span>
      </div>
      <el-table :data="settings?.tools ?? []" size="small" border>
        <el-table-column prop="name" label="工具" width="200">
          <template #default="{ row }">
            <code>{{ row.name }}</code>
            <el-tag v-if="isWriteTool(row.name)" type="warning" size="small" effect="plain" class="mcp-write-tag">
              写
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="说明" min-width="420" show-overflow-tooltip />
        <template #empty>
          <span class="muted">加载中或没有可用工具</span>
        </template>
      </el-table>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>最近调用</h3>
        <span class="muted">initialize / tools/list / tools/call 的最近 30 条留痕</span>
      </div>
      <el-table :data="logs" size="small" border>
        <el-table-column prop="createdAt" label="时间" width="180" />
        <el-table-column prop="method" label="方法" width="140" />
        <el-table-column prop="tool" label="工具" width="180" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ok' ? 'success' : 'danger'" size="small" effect="plain">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="durationMs" label="耗时(ms)" width="100" align="center" />
        <el-table-column prop="detail" label="详情" min-width="240" show-overflow-tooltip />
        <template #empty>
          <span class="muted">还没有调用记录</span>
        </template>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, Refresh } from '@element-plus/icons-vue'
import { settingsApi } from '@/api'
import type { McpCallLog, McpSettings, McpTokenRecord } from '@/api/types'

/** 写工具固定只有缺陷草稿一个入口，前端按名字标注并与后端保持一致。 */
const WRITE_TOOLS = new Set(['bugs.draft.create'])

const router = useRouter()
const loading = ref(false)
const rotating = ref(false)
const settings = ref<McpSettings | null>(null)
const logs = ref<McpCallLog[]>([])
/** 明文令牌：只存在弹窗生命周期内，关闭即清空，不写 localStorage。 */
const issuedToken = ref('')
const tokenCopied = ref(false)
const tokenDialogVisible = ref(false)
const form = reactive({ enabled: false, allowWrite: false })

const curlSample = computed(() => {
  const endpoint = settings.value?.endpoint || '/api/v1/mcp'
  const header = settings.value?.tokenRequired ? '\n  -H "Authorization: Bearer <你的令牌>" \\' : ' \\'
  return [
    `curl -X POST "${endpoint}" \\`,
    `  -H "Content-Type: application/json"${header}`,
    `  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`,
  ].join('\n')
})

function isWriteTool(name: string): boolean {
  return WRITE_TOOLS.has(name)
}

function copyText(text: string, label: string): void {
  if (!text) {
    ElMessage.warning('没有可复制的内容')
    return
  }
  void navigator.clipboard.writeText(text).then(
    () => ElMessage.success(`已复制${label}`),
    () => ElMessage.error('复制失败，请手动选择文本'),
  )
}

async function loadAll(): Promise<void> {
  loading.value = true
  try {
    const payload = await settingsApi.mcpSettings()
    settings.value = payload
    form.enabled = payload.enabled
    form.allowWrite = payload.allowWrite
    logs.value = (await settingsApi.mcpLogs()).list
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载 MCP 设置失败')
  } finally {
    loading.value = false
  }
}

async function saveSwitch(): Promise<void> {
  try {
    const payload = await settingsApi.saveMcpSettings({
      enabled: form.enabled,
      allowWrite: form.allowWrite,
    })
    settings.value = payload
    ElMessage.success('MCP 设置已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存 MCP 设置失败')
    await loadAll()
  }
}

async function requestToken(): Promise<void> {
  if (settings.value?.hasToken) {
    try {
      await ElMessageBox.confirm(
        '轮换后旧令牌立即失效，已配置的 AI 客户端需要更新令牌，是否继续？',
        '轮换 MCP 令牌',
        { type: 'warning', confirmButtonText: '继续轮换', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  rotating.value = true
  try {
    const result = await settingsApi.rotateMcpToken()
    issuedToken.value = result.token
    tokenCopied.value = false
    tokenDialogVisible.value = true
    await loadAll()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '生成令牌失败')
  } finally {
    rotating.value = false
  }
}

function copyIssuedToken(): void {
  const text = issuedToken.value
  if (!text) {
    ElMessage.warning('没有可复制的内容')
    return
  }
  void navigator.clipboard.writeText(text).then(
    () => {
      tokenCopied.value = true
      ElMessage.success('已复制令牌')
    },
    () => ElMessage.error('复制失败，请手动选择文本'),
  )
}

function confirmTokenSaved(): void {
  tokenCopied.value = true
  tokenDialogVisible.value = false
}

/** 关闭弹窗前确认：没复制就关闭时提示令牌将无法再次查看。 */
function handleTokenDialogClose(done?: () => void): void {
  const close = (): void => {
    tokenDialogVisible.value = false
    if (done) {
      done()
    }
  }
  if (tokenCopied.value || !issuedToken.value) {
    close()
    return
  }
  void ElMessageBox.confirm(
    '还没有复制令牌，关闭后将无法再次查看，只能重新轮换。确定关闭吗？',
    '令牌尚未保存',
    { type: 'warning', confirmButtonText: '仍然关闭', cancelButtonText: '返回复制' },
  ).then(close, () => undefined)
}

function resetIssuedToken(): void {
  issuedToken.value = ''
  tokenCopied.value = false
}

function tokenStatusLabel(row: McpTokenRecord): string {
  if (row.status === 'active') {
    return '生效中'
  }
  if (row.status === 'deleted') {
    return '已删除'
  }
  if (row.disableReason === 'rotated') {
    return '已停用（已轮换）'
  }
  if (row.disableReason === 'replaced') {
    return '已停用（被替换）'
  }
  return '已停用'
}

function tokenStatusType(row: McpTokenRecord): 'success' | 'info' | 'danger' {
  if (row.status === 'active') {
    return 'success'
  }
  return row.status === 'deleted' ? 'danger' : 'info'
}

async function disableToken(row: McpTokenRecord): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `停用 ${row.preview} 后，使用该令牌的客户端将立即失效；记录会保留，可以再次启用。`,
      '停用令牌',
      { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await runTokenAction(() => settingsApi.disableMcpToken(Number(row.id)), '令牌已停用')
}

async function enableToken(row: McpTokenRecord): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `启用 ${row.preview} 后，当前生效令牌会被自动停用（同时只能有一个生效令牌）。`,
      '启用令牌',
      { type: 'warning', confirmButtonText: '启用', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await runTokenAction(() => settingsApi.enableMcpToken(Number(row.id)), '令牌已启用')
}

async function deleteToken(row: McpTokenRecord): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除 ${row.preview} 后不可恢复，使用该令牌的客户端将立即失效。确定删除吗？`,
      '删除令牌',
      { type: 'error', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await runTokenAction(() => settingsApi.deleteMcpToken(Number(row.id)), '令牌已删除')
}

async function runTokenAction(
  action: () => Promise<McpSettings>,
  successText: string,
): Promise<void> {
  try {
    settings.value = await action()
    ElMessage.success(successText)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '操作失败')
  }
}

onMounted(loadAll)
</script>

<style scoped>
.settings-block {
  margin-bottom: 16px;
}

.settings-block__head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.settings-block__head h3 {
  margin: 0;
  font-size: 15px;
}

.mcp-note {
  line-height: 1.7;
  font-size: 12px;
}

.mcp-form {
  margin-top: 12px;
}

.mcp-hint {
  margin-left: 10px;
  font-size: 12px;
}

.mcp-endpoint,
.mcp-token {
  padding: 0 4px;
  background: var(--aitp-subtle);
  border-radius: 3px;
  font-family: Consolas, monospace;
  word-break: break-all;
}

.mcp-token {
  margin: 0 8px;
}

.mcp-token-meta {
  margin-right: 10px;
  font-size: 12px;
}

.mcp-token-reveal {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 14px;
  padding: 12px;
  background: var(--aitp-subtle);
  border-radius: 6px;
}

.mcp-token-reveal code {
  flex: 1;
  font-family: Consolas, monospace;
  font-size: 13px;
  word-break: break-all;
  user-select: all;
}

.mcp-token-reveal__hint {
  margin: 10px 0 0;
  font-size: 12px;
}

.mcp-write-tag {
  margin-left: 6px;
}

.mcp-curl {
  position: relative;
  margin-top: 8px;
  padding: 12px 64px 12px 12px;
  background: var(--aitp-subtle);
  border-radius: 6px;
}

.mcp-curl pre {
  margin: 0;
  overflow-x: auto;
  font-family: Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
}

.mcp-curl__copy {
  position: absolute;
  top: 8px;
  right: 8px;
}
</style>
