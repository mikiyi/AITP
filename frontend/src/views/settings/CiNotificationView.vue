<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">CI 与通知</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
        <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>CI 触发令牌</h3>
        <el-tag :type="ciStatusType(ci?.status ?? 'inactive')" effect="plain" size="small">
          {{ ciStatusText[ci?.status ?? 'inactive'] }}
        </el-tag>
        <span v-if="ci?.lastUsedAt" class="muted">最近使用：{{ dateTimeText(ci.lastUsedAt) }}</span>
      </div>
      <p class="muted settings-block__hint">
        流水线用令牌调用 <code>POST /api/v1/ci/runs</code> 创建并执行测试轮次；令牌只在生成时显示一次，
        本地只保存哈希，遗失后只能重新生成。该接口可绕过本地访问口令，但必须先过「路由白名单 +
        有效期 + 吊销」三重校验，令牌泄露不再等于全权访问。
      </p>

      <template v-if="ci?.enabled">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="令牌名称">{{ ci.name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="令牌前缀">
            <code>{{ ci.tokenPrefix }}…</code>
          </el-descriptions-item>
          <el-descriptions-item label="绑定项目">
            {{ projectName(ci.projectId) || '全部项目（未绑定）' }}
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="ciStatusType(ci.status)" size="small" effect="plain">
              {{ ciStatusText[ci.status] }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="到期时间">
            {{ ci.expiresAt ? dateTimeText(ci.expiresAt) : '不过期' }}
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ dateTimeText(ci.createdAt) }}</el-descriptions-item>
          <el-descriptions-item label="触发地址" :span="2">
            <code class="ci-endpoint">{{ ci.endpoint }}</code>
          </el-descriptions-item>
        </el-descriptions>

        <div class="token-actions">
          <el-button :icon="CopyDocument" @click="copyText(ci.endpoint, '触发地址')">
            复制触发地址
          </el-button>
          <el-button :icon="Plus" @click="openTokenDialog">重新生成</el-button>
          <el-button type="danger" plain @click="revokeToken">吊销令牌</el-button>
        </div>

        <div class="ci-snippet">
          <pre>{{ curlSample }}</pre>
          <el-button link type="primary" class="ci-snippet__copy" @click="copyText(curlSample, '调用示例')">
            复制示例
          </el-button>
        </div>
        <p class="muted settings-block__hint">
          请求体可选 <code>caseIds</code> 直接指定用例，或传 <code>scope</code> 按功能点/优先级/类型筛选；
          <code>envId</code> 绑定测试环境后会同时入队接口批量执行，<code>automationScriptIds</code>
          可追加自动化脚本。返回里的 <code>runId</code> 可用于轮询
          <code>GET /api/v1/ci/runs/&lt;runId&gt;</code>。
        </p>
      </template>

      <template v-else>
        <el-empty description="还没有 CI 令牌，生成后即可从流水线触发测试轮次" :image-size="72">
          <el-button type="primary" :icon="Key" @click="openTokenDialog">生成 CI 令牌</el-button>
        </el-empty>
      </template>

      <el-alert
        v-if="issuedToken"
        type="success"
        :closable="false"
        show-icon
        class="token-alert"
        title="令牌已生成，请立即复制保存"
      >
        <div class="token-value">
          <code>{{ issuedToken }}</code>
          <el-button link type="primary" @click="copyText(issuedToken, '令牌')">复制</el-button>
        </div>
        <div class="muted">刷新或离开页面后将无法再次查看；重新生成会让旧令牌立即失效。</div>
      </el-alert>

      <div v-if="tokenList.length" class="token-history">
        <div class="settings-block__head token-history__head">
          <h4>令牌历史</h4>
          <span class="muted">展示有效期与吊销状态；被吊销或过期的令牌会立即失效</span>
        </div>
        <el-table :data="tokenList" size="small" border>
          <el-table-column prop="tokenPrefix" label="前缀" min-width="130">
            <template #default="{ row }"><code>{{ row.tokenPrefix }}…</code></template>
          </el-table-column>
          <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip />
          <el-table-column label="生效范围" width="150">
            <template #default="{ row }">
              {{ row.projectId ? projectName(row.projectId) : '全部项目' }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="ciStatusType(row.status)" size="small" effect="plain">
                {{ row.statusLabel }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="到期时间" width="170">
            <template #default="{ row }">
              {{ row.expiresAt ? dateTimeText(row.expiresAt) : '不过期' }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right" align="center">
            <template #default="{ row }">
              <el-button
                link
                type="danger"
                :disabled="row.status !== 'active'"
                @click="revokeOneToken(row)"
              >
                吊销
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>Webhook 通知渠道</h3>
        <span class="muted">轮次完成、门禁未通过、自动化失败、备份失败时自动推送</span>
        <div class="settings-block__actions">
          <el-button type="primary" size="small" :icon="Plus" @click="openChannelDialog()">
            新建渠道
          </el-button>
        </div>
      </div>

      <el-table v-loading="channelsLoading" :data="channels" size="small" border>
        <el-table-column prop="name" label="名称" min-width="150" show-overflow-tooltip />
        <el-table-column label="生效范围" width="140">
          <template #default="{ row }">{{ channelScopeText(row) }}</template>
        </el-table-column>
        <el-table-column prop="webhookUrl" label="Webhook 地址" min-width="240" show-overflow-tooltip />
        <el-table-column label="订阅事件" min-width="220">
          <template #default="{ row }">
            <span v-if="!row.events.length" class="muted">未订阅</span>
            <span v-else class="channel-events">
              <el-tag
                v-for="event in row.events"
                :key="event"
                size="small"
                effect="plain"
                class="channel-events__tag"
              >
                {{ eventLabel(event) }}
              </el-tag>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              :loading="togglingId === row.id"
              @change="(value: boolean | string | number) => toggleChannel(row, Boolean(value))"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :loading="testingId === row.id" @click="testChannel(row)">
              发送测试
            </el-button>
            <el-button link @click="openChannelDialog(row)">编辑</el-button>
            <el-button link type="danger" @click="removeChannel(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">还没有通知渠道，新建后事件才会推送</span>
        </template>
      </el-table>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>发送记录</h3>
        <span class="muted">每次投递的请求结果与重试次数都会留痕</span>
      </div>
      <BaseProTable
        ref="logTable"
        :columns="logColumns"
        :fetcher="logFetcher"
        :search-fields="logSearchFields"
        :max-height="480"
      >
        <template #event="{ row }">{{ eventLabel(row.event) }}</template>
        <template #status="{ row }">
          <el-tag :type="logStatusType(row.status)" size="small" effect="plain">
            {{ row.statusLabel }}
          </el-tag>
        </template>
        <template #httpStatus="{ row }">{{ row.httpStatus ?? '-' }}</template>
        <template #details="{ row }">
          <span>{{ row.errorMessage || row.responseText || '-' }}</span>
        </template>
        <template #toolbar>
          <el-button :icon="Refresh" @click="logTable?.reload()">刷新记录</el-button>
        </template>
      </BaseProTable>
    </div>

    <el-dialog v-model="tokenDialogVisible" title="生成 CI 令牌" width="460px">
      <el-form :model="tokenForm" label-width="90px" @submit.prevent>
        <el-form-item label="令牌名称">
          <el-input v-model="tokenForm.name" maxlength="100" placeholder="CI 触发令牌" />
        </el-form-item>
        <el-form-item label="绑定项目">
          <el-select
            v-model="tokenForm.projectId"
            clearable
            placeholder="不绑定（可触发任意项目）"
            style="width: 100%"
          >
            <el-option
              v-for="project in appStore.projects"
              :key="project.id"
              :label="project.name"
              :value="project.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="有效期">
          <el-select v-model="tokenForm.expiresInDays" style="width: 100%">
            <el-option
              v-for="option in expiryOptions"
              :key="String(option.value)"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <p class="muted dialog-hint">
        绑定项目后，令牌只能触发该项目；留空则不限项目。设定有效期后，过期令牌会立即失效。
        生成新令牌会让旧令牌立即失效。
      </p>
      <template #footer>
        <el-button @click="tokenDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="issuing" @click="issueToken">生成</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="channelDialogVisible"
      :title="editingChannelId === null ? '新建通知渠道' : '编辑通知渠道'"
      width="560px"
    >
      <el-form :model="channelForm" label-width="96px" @submit.prevent>
        <el-form-item label="名称">
          <el-input v-model="channelForm.name" maxlength="100" placeholder="例如：测试组飞书群" />
        </el-form-item>
        <el-form-item label="生效范围">
          <el-radio-group v-model="channelForm.scope">
            <el-radio value="global">全局（所有项目）</el-radio>
            <el-radio value="project">指定项目</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="channelForm.scope === 'project'" label="项目">
          <el-select v-model="channelForm.projectId" placeholder="选择项目" style="width: 100%">
            <el-option
              v-for="project in appStore.projects"
              :key="project.id"
              :label="project.name"
              :value="project.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="Webhook 地址">
          <el-input
            v-model="channelForm.webhookUrl"
            maxlength="1000"
            placeholder="https://example.com/webhook"
          />
        </el-form-item>
        <el-form-item label="订阅事件">
          <el-checkbox-group v-model="channelForm.events">
            <el-checkbox
              v-for="option in eventOptions"
              :key="option.value"
              :value="option.value"
              :label="option.label"
            />
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="channelForm.enabled" />
        </el-form-item>
      </el-form>
      <p class="muted dialog-hint">
        通知以 JSON POST 发送，内容包含项目、版本、轮次、通过率、失败数、发生时间与站内链接；
        投递失败按最多 3 次重试，不影响轮次与门禁结果。
      </p>
      <template #footer>
        <el-button @click="channelDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="channelSaving" @click="saveChannel">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, CopyDocument, Key, Plus, Refresh } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField } from '@/components/BaseProTable.vue'
import { settingsApi } from '@/api'
import {
  getCiSettings,
  issueCiToken,
  listCiTokens,
  revokeAllCiTokens,
  revokeCiToken,
  type CiSettings,
  type CiTokenItem,
} from '@/api/ci'
import type { CiEventOption, NotificationChannel, NotificationLog } from '@/api/types'
import { dateTimeText } from '@/utils/format'
import { useAppStore } from '@/stores/app'

type ChannelScope = 'global' | 'project'

const router = useRouter()
const appStore = useAppStore()

const ci = ref<CiSettings | null>(null)
const tokenList = ref<CiTokenItem[]>([])
const channels = ref<NotificationChannel[]>([])
const eventOptions = ref<CiEventOption[]>([])
const loading = ref(false)
const channelsLoading = ref(false)
const issuing = ref(false)
const issuedToken = ref('')
const tokenDialogVisible = ref(false)
const tokenForm = reactive<{ name: string; projectId: number | null; expiresInDays: number | null }>({
  name: 'CI 触发令牌',
  projectId: null,
  expiresInDays: null,
})

// 有效期选项：null 表示不过期（旧令牌迁移后默认如此）。
const expiryOptions = [
  { label: '不过期', value: null as number | null },
  { label: '30 天', value: 30 },
  { label: '90 天', value: 90 },
  { label: '180 天', value: 180 },
  { label: '365 天', value: 365 },
]

const channelDialogVisible = ref(false)
const channelSaving = ref(false)
const editingChannelId = ref<number | null>(null)
const channelForm = reactive<{
  name: string
  scope: ChannelScope
  projectId: number | null
  webhookUrl: string
  events: string[]
  enabled: boolean
}>({
  name: '',
  scope: 'global',
  projectId: null,
  webhookUrl: '',
  events: [],
  enabled: true,
})

const testingId = ref<number | null>(null)
const togglingId = ref<number | null>(null)

const logTable = ref<{ reload: () => void } | null>(null)
const logEventOptions = ref<CiEventOption[]>([])

const logColumns: ColumnDef[] = [
  { prop: 'createdAt', label: '时间', width: 170 },
  { prop: 'channelName', label: '渠道', width: 140 },
  { prop: 'event', label: '事件', width: 130, slot: 'event' },
  { prop: 'title', label: '标题', minWidth: 220 },
  { prop: 'status', label: '状态', width: 100, slot: 'status' },
  { prop: 'attempts', label: '尝试', width: 70, align: 'center' },
  { prop: 'httpStatus', label: 'HTTP', width: 80, align: 'center', slot: 'httpStatus' },
  { prop: 'errorMessage', label: '结果详情', minWidth: 220, slot: 'details' },
]

const logSearchFields = computed<SearchField[]>(() => [
  { prop: 'event', label: '事件', type: 'select', options: logEventOptions.value },
  {
    prop: 'status',
    label: '状态',
    type: 'select',
    options: [
      { label: '待发送', value: 'pending' },
      { label: '已送达', value: 'success' },
      { label: '发送失败', value: 'failed' },
    ],
  },
  { prop: 'keyword', label: '渠道 / 标题 / 错误' },
])

const eventLabelMap = computed(() => {
  const map = new Map<string, string>()
  for (const option of [...eventOptions.value, ...logEventOptions.value]) {
    map.set(option.value, option.label)
  }
  return map
})

const curlSample = computed(() => {
  const url = ci.value?.endpoint || '/api/v1/ci/runs'
  const projectId = ci.value?.projectId ?? appStore.currentProjectId ?? 1
  return [
    `curl -X POST "${url}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -H "X-AITP-CI-Token: <你的令牌>" \\`,
    `  -d '{"projectId": ${projectId}, "scope": {"priority": "P0"}}'`,
  ].join('\n')
})

function projectName(projectId: number | null | undefined): string {
  if (projectId === null || projectId === undefined) {
    return ''
  }
  return appStore.projects.find((project) => project.id === projectId)?.name ?? `#${projectId}`
}

function channelScopeText(channel: NotificationChannel): string {
  return channel.projectId === null
    ? '全局'
    : `项目：${projectName(channel.projectId)}`
}

function eventLabel(value: string): string {
  return eventLabelMap.value.get(value) ?? value
}

function logStatusType(status: NotificationLog['status']): 'success' | 'danger' | 'info' {
  if (status === 'success') {
    return 'success'
  }
  return status === 'failed' ? 'danger' : 'info'
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

async function loadCi(): Promise<void> {
  ci.value = await getCiSettings()
  if (ci.value.events.length) {
    eventOptions.value = ci.value.events
  }
  tokenList.value = (await listCiTokens()).list
}

async function loadChannels(): Promise<void> {
  channelsLoading.value = true
  try {
    const payload = await settingsApi.notificationChannels()
    channels.value = payload.list
    eventOptions.value = payload.eventOptions
  } finally {
    channelsLoading.value = false
  }
}

async function loadAll(): Promise<void> {
  loading.value = true
  try {
    await Promise.all([loadCi(), loadChannels()])
    logTable.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载 CI 与通知配置失败')
  } finally {
    loading.value = false
  }
}

function openTokenDialog(): void {
  tokenForm.name = ci.value?.name || 'CI 触发令牌'
  tokenForm.projectId = ci.value?.projectId ?? appStore.currentProjectId ?? null
  tokenForm.expiresInDays = null
  tokenDialogVisible.value = true
}

async function issueToken(): Promise<void> {
  issuing.value = true
  try {
    const payload = await issueCiToken({
      name: tokenForm.name || undefined,
      projectId: tokenForm.projectId ?? undefined,
      expiresInDays: tokenForm.expiresInDays ?? undefined,
    })
    ci.value = payload
    issuedToken.value = payload.token ?? ''
    tokenList.value = (await listCiTokens()).list
    tokenDialogVisible.value = false
    ElMessage.success('CI 令牌已生成，旧令牌已失效')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '生成 CI 令牌失败')
  } finally {
    issuing.value = false
  }
}

async function revokeToken(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '吊销后所有令牌再调用 CI 接口都会被拒绝。',
      '吊销全部 CI 令牌',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await revokeAllCiTokens()
    issuedToken.value = ''
    await loadCi()
    ElMessage.success('CI 令牌已吊销')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '吊销 CI 令牌失败')
  }
}

async function revokeOneToken(row: CiTokenItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `吊销「${row.name || row.tokenPrefix || row.id}」后，该令牌再调用 CI 接口会被拒绝。`,
      '吊销单个令牌',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await revokeCiToken(row.id)
    await loadCi()
    ElMessage.success('该令牌已吊销')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '吊销令牌失败')
  }
}

type TagType = 'success' | 'warning' | 'danger' | 'info'

function ciStatusType(status: CiSettings['status'] | CiTokenItem['status']): TagType {
  if (status === 'active') return 'success'
  if (status === 'expired') return 'warning'
  if (status === 'revoked') return 'danger'
  return 'info'
}

const ciStatusText: Record<string, string> = {
  active: '有效',
  expired: '已过期',
  revoked: '已吊销',
  inactive: '未启用',
}

function openChannelDialog(row?: NotificationChannel): void {
  editingChannelId.value = row?.id ?? null
  channelForm.name = row?.name ?? ''
  channelForm.scope = row?.projectId === null || row?.projectId === undefined ? 'global' : 'project'
  channelForm.projectId = row?.projectId ?? appStore.currentProjectId ?? null
  channelForm.webhookUrl = row?.webhookUrl ?? ''
  channelForm.events = [...(row?.events ?? [])]
  channelForm.enabled = row?.enabled ?? true
  channelDialogVisible.value = true
}

async function saveChannel(): Promise<void> {
  if (!channelForm.name.trim()) {
    ElMessage.warning('请输入渠道名称')
    return
  }
  if (!channelForm.webhookUrl.trim()) {
    ElMessage.warning('请输入 Webhook 地址')
    return
  }
  if (channelForm.scope === 'project' && channelForm.projectId === null) {
    ElMessage.warning('请选择生效项目')
    return
  }
  if (!channelForm.events.length) {
    ElMessage.warning('至少订阅一个事件')
    return
  }
  const payload = {
    name: channelForm.name.trim(),
    projectId: channelForm.scope === 'project' ? channelForm.projectId : null,
    webhookUrl: channelForm.webhookUrl.trim(),
    events: channelForm.events,
    enabled: channelForm.enabled,
  }
  channelSaving.value = true
  try {
    if (editingChannelId.value === null) {
      await settingsApi.createNotification(payload)
      ElMessage.success('通知渠道已创建')
    } else {
      await settingsApi.updateNotification(editingChannelId.value, payload)
      ElMessage.success('通知渠道已更新')
    }
    channelDialogVisible.value = false
    await loadChannels()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存通知渠道失败')
  } finally {
    channelSaving.value = false
  }
}

async function testChannel(row: NotificationChannel): Promise<void> {
  testingId.value = row.id
  try {
    const result = await settingsApi.testNotification(row.id)
    if (result.success) {
      ElMessage.success(`测试通知已送达（HTTP ${result.httpStatus}）`)
    } else {
      ElMessage.error(result.message || '测试通知发送失败')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '测试通知发送失败')
  } finally {
    testingId.value = null
    logTable.value?.reload()
  }
}

async function toggleChannel(row: NotificationChannel, enabled: boolean): Promise<void> {
  togglingId.value = row.id
  try {
    const updated = await settingsApi.updateNotification(row.id, { enabled })
    row.enabled = updated.enabled
    ElMessage.success(enabled ? '渠道已启用' : '渠道已停用')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '切换渠道状态失败')
    await loadChannels()
  } finally {
    togglingId.value = null
  }
}

async function removeChannel(row: NotificationChannel): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除后「${row.name}」不再接收任何事件通知。`, '删除通知渠道', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await settingsApi.removeNotification(row.id)
    ElMessage.success('通知渠道已删除')
    await loadChannels()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除通知渠道失败')
  }
}

async function logFetcher(query: Record<string, unknown>) {
  const page = await settingsApi.notificationLogs(query)
  if (page.eventOptions?.length) {
    logEventOptions.value = page.eventOptions
  }
  return page
}

onMounted(loadAll)
onActivated(loadAll)
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
}

.settings-block__head h3 {
  margin: 0;
  font-size: 15px;
}

.settings-block__actions {
  margin-left: auto;
}

.settings-block__hint {
  margin: 10px 0 0;
  line-height: 1.7;
  font-size: 12px;
}

.settings-block__hint code {
  padding: 0 3px;
  background: var(--aitp-subtle);
  border-radius: 3px;
  font-family: Consolas, monospace;
}

.ci-endpoint {
  padding: 0 4px;
  background: var(--aitp-subtle);
  border-radius: 3px;
  font-family: Consolas, monospace;
  word-break: break-all;
}

.token-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.token-alert {
  margin-top: 12px;
}

.token-history {
  margin-top: 16px;
}

.token-history__head {
  margin-bottom: 8px;
}

.token-history__head h4 {
  margin: 0;
  font-size: 13px;
}

.token-value {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0;
}

.token-value code {
  padding: 4px 8px;
  background: var(--aitp-panel);
  border-radius: 4px;
  font-family: Consolas, monospace;
  word-break: break-all;
}

.ci-snippet {
  position: relative;
  margin-top: 12px;
  padding: 12px 64px 12px 12px;
  background: var(--aitp-subtle);
  border-radius: 6px;
}

.ci-snippet pre {
  margin: 0;
  overflow-x: auto;
  font-family: Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
}

.ci-snippet__copy {
  position: absolute;
  top: 8px;
  right: 8px;
}

.channel-events {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
}

.channel-events__tag {
  margin: 0;
}

.dialog-hint {
  margin: 0;
  line-height: 1.6;
  font-size: 12px;
}
</style>
