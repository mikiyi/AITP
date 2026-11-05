<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">AI 设置</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新增档案</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="优先级数值越大越优先；首选模型不可用时按优先级自动降级，页面只弹 3 秒提示，不阻塞主流程"
      class="page-alert"
    />

    <div class="panel panel-pad ai-config-panel">
      <div class="section-head">
        <h3>AI 调用参数</h3>
        <el-button type="primary" size="small" :loading="savingConfig" @click="saveAiConfig">保存</el-button>
      </div>
      <el-form label-width="160px">
        <el-form-item label="自动降级">
          <el-radio-group v-model="aiDegradeEnabled">
            <el-radio :value="true">true</el-radio>
            <el-radio :value="false">false</el-radio>
          </el-radio-group>
          <div class="muted ai-config-hint">
            true：首选模型不可用时，按优先级自动换用下一个可用模型，页面只弹 3 秒提示；false：首选模型失败就直接报错，不自动切换。
          </div>
        </el-form-item>
        <el-form-item label="默认超时（秒）">
          <el-input v-model="aiTimeoutDefault" placeholder="整数，单位秒，默认 60" style="width: 200px" />
          <div class="muted ai-config-hint">
            整数，单位秒，默认 60。单次 AI 调用超过这个时长即判定超时。云端接口一般 30~60，本地小模型可放宽到 120~300。
          </div>
        </el-form-item>
      </el-form>
    </div>

    <div class="panel panel-pad">
      <div class="section-head">
        <h3>当前生效配置</h3>
        <el-tag v-if="status.configured" type="success" effect="plain" size="small">已配置</el-tag>
        <el-tag v-else type="info" effect="plain" size="small">未配置</el-tag>
      </div>
      <el-descriptions :column="4" border size="small">
        <el-descriptions-item label="供应商">{{ status.provider || '-' }}</el-descriptions-item>
        <el-descriptions-item label="模型">{{ status.model || '-' }}</el-descriptions-item>
        <el-descriptions-item label="接口地址">{{ status.baseUrl || '-' }}</el-descriptions-item>
        <el-descriptions-item label="档案">{{ status.profileName || '环境变量 / 内置默认' }}</el-descriptions-item>
        <el-descriptions-item label="来源层">{{ sourceText }}</el-descriptions-item>
        <el-descriptions-item label="数据外发">{{ status.isLocal ? '不出本机' : '会上传第三方' }}</el-descriptions-item>
        <el-descriptions-item label="凭证">
          {{ status.isLocal ? '本地大模型无需配置 KEY' : status.hasApiKey ? '已配置' : '未配置' }}
        </el-descriptions-item>
        <el-descriptions-item label="优先级">{{ status.priority }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="status.candidates.length" class="candidates">
        <span class="muted">降级顺序：</span>
        <div class="candidates__list">
          <div
            v-for="(item, index) in status.candidates"
            :key="`${item.profileId}-${item.model}`"
            class="candidates__row"
          >
            <span class="candidates__order">{{ index + 1 }}</span>
            <el-tag
              size="small"
              effect="plain"
              :type="item.isLocal ? 'warning' : 'primary'"
            >
              {{ item.profileName || item.provider }}/{{ item.model || '未指定' }}
            </el-tag>
            <span class="muted candidates__priority">优先级 {{ item.priority }}</span>
          </div>
        </div>
      </div>
      <div v-if="status.error" class="muted status-error">{{ status.error }}</div>
    </div>

    <div class="panel panel-pad">
      <el-table v-loading="loading" :data="profiles" border stripe>
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column prop="provider" label="供应商" width="100" />
        <el-table-column prop="model" label="模型" min-width="140" show-overflow-tooltip />
        <el-table-column prop="baseUrl" label="Base URL" min-width="150" show-overflow-tooltip />
        <el-table-column prop="priority" label="优先级" width="90" sortable />
        <el-table-column label="API Key" width="160">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.useEnvKey"
              placement="top"
              :content="envKeyTip(row)"
            >
              <el-tag size="small" :type="row.envKeyPresent ? 'success' : 'warning'" effect="plain">
                本地变量 {{ row.envKeyName || '未设置' }}
              </el-tag>
            </el-tooltip>
            <el-tooltip v-else-if="row.isLocal" content="本地大模型无需配置 KEY" placement="top">
              <el-tag size="small" type="info" effect="plain">本地无需 Key</el-tag>
            </el-tooltip>
            <el-tag v-else-if="row.hasApiKey" size="small" type="success" effect="plain">
              {{ row.apiKeyMasked || '已配置' }}
            </el-tag>
            <el-tag v-else size="small" type="info" effect="plain">未配置</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch
              :model-value="row.isActive"
              :loading="activeBusyId === row.id"
              @change="(value: string | number | boolean) => toggleActive(row, Boolean(value))"
            />
          </template>
        </el-table-column>
        <el-table-column label="标记" width="110">
          <template #default="{ row }">
            <div class="mark-tags">
              <el-tag v-if="row.isDefault" size="small" type="primary" effect="plain">默认</el-tag>
              <el-tag v-if="row.isLocal" size="small" type="warning" effect="plain">本地</el-tag>
              <span v-if="!row.isDefault && !row.isLocal" class="muted">-</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="warning" @click="openKey(row)">设置 Key</el-button>
            <el-button link type="success" :loading="checkingId === row.id" @click="checkProfile(row)">
              自检
            </el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="panel panel-pad providers-block">
      <h3>支持的供应商</h3>
      <el-table :data="providers" border size="small">
        <el-table-column prop="label" label="名称" width="140" />
        <el-table-column prop="name" label="标识" width="140" />
        <el-table-column prop="protocol" label="协议" width="140" />
        <el-table-column prop="defaultBaseUrl" label="默认 Base URL" min-width="240" show-overflow-tooltip />
        <el-table-column prop="defaultModel" label="默认模型" min-width="160" />
        <el-table-column label="需要 Key" width="100">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.requiresApiKey ? '是' : '否' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="环境变量" min-width="220">
          <template #default="{ row }">
            <span class="text-mono">{{ (row.envKeys || []).join(' / ') || '-' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑 AI 档案' : '新增 AI 档案'" width="720px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="例如：通义千问" />
        </el-form-item>
        <el-form-item label="供应商" required>
          <el-select v-model="form.provider" @change="onProviderChange">
            <el-option v-for="item in providers" :key="item.name" :label="item.label" :value="item.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="协议">
          <el-select v-model="form.protocol">
            <el-option label="openai_compat" value="openai_compat" />
            <el-option label="ollama_native" value="ollama_native" />
          </el-select>
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.baseUrl" />
        </el-form-item>
        <el-form-item label="模型">
          <div class="model-field">
            <el-select
              v-model="form.model"
              filterable
              allow-create
              default-first-option
              placeholder="从服务端拉取，或直接输入模型名"
              style="flex: 1"
            >
              <el-option v-for="item in modelOptions" :key="item.id" :label="modelLabel(item)" :value="item.id" />
            </el-select>
            <el-button :loading="loadingModels" @click="loadModels">拉取模型列表</el-button>
          </div>
          <div class="muted form-hint">本地模型走 /api/tags，OpenAI 兼容接口走 /models。</div>
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number v-model="form.priority" :min="0" :max="1000" />
          <span class="muted form-hint">数值越大越优先</span>
        </el-form-item>
        <el-form-item label="温度">
          <el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" />
          <div class="field-hint">
            <el-icon><InfoFilled /></el-icon>
            <span>取值范围 0~2，越大回答越发散。生成用例、评审建议 0.2~0.7；需要稳定复现同一结果时用 0~0.3。</span>
          </div>
        </el-form-item>
        <el-form-item label="最大 Token">
          <el-input-number v-model="form.maxTokens" :min="256" :max="200000" :step="256" />
          <div class="field-hint">
            <el-icon><InfoFilled /></el-icon>
            <span>单次回复的最大长度（含推理内容），默认 4096。长用例、长报告可调到 8192；调大更慢、消耗更多 token。</span>
          </div>
        </el-form-item>
        <el-form-item label="超时(秒)">
          <el-input-number v-model="form.timeout" :min="5" :max="600" />
          <div class="field-hint">
            <el-icon><InfoFilled /></el-icon>
            <span>单次调用最长等待秒数，默认 60。本地大模型首次加载较慢，建议 120~300；云端接口 30~60 即可。</span>
          </div>
        </el-form-item>
        <el-form-item label="默认档案">
          <el-switch v-model="form.isDefault" />
          <div class="field-hint">
            <el-icon><InfoFilled /></el-icon>
            <span>开启后新请求优先使用这个档案；同一时间只会保留一个默认档案，开启会自动取消其他档案的默认标记。</span>
          </div>
        </el-form-item>

        <el-form-item label="启用本地 Key">
          <el-switch v-model="form.useEnvKey" :disabled="isLocalProvider" />
          <div class="field-hint">
            <el-icon><InfoFilled /></el-icon>
            <span v-if="isLocalProvider">配置本地大模型时无需配置本地 Key，本地模型不校验 API Key。</span>
            <span v-else>
              开启后不再使用「设置 Key」保存的密钥，改为从本机环境变量读取：填写的变量名必须与本机环境变量完全一致（区分大小写）。
              用户变量只对当前登录用户生效，系统变量对所有用户生效；本工具直接读取注册表，设置完变量无需重启服务。
              变量不存在时，调用模型会提示「未读取到环境变量」。
            </span>
          </div>
        </el-form-item>
        <template v-if="form.useEnvKey && !isLocalProvider">
          <el-form-item label="变量名" required>
            <el-input v-model="form.envKeyName" placeholder="例如 OPENAI_API_KEY" />
            <div class="field-hint">
              <el-icon><InfoFilled /></el-icon>
              <span>只能包含字母、数字与下划线，且不能以数字开头，不能用连字符（-）。例如 mysensenva_key。</span>
            </div>
          </el-form-item>
          <el-form-item label="变量范围">
            <el-radio-group v-model="form.envKeyScope">
              <el-radio value="user">用户变量</el-radio>
              <el-radio value="system">系统变量</el-radio>
            </el-radio-group>
            <div class="field-hint">
              <el-icon><InfoFilled /></el-icon>
              <span>用户变量位于 HKEY_CURRENT_USER\Environment，系统变量位于 HKEY_LOCAL_MACHINE；不确定时先用「用户变量」。</span>
            </div>
          </el-form-item>
        </template>

        <el-form-item v-if="!form.useEnvKey && !isLocalProvider" label="临时 Key">
          <el-input
            v-model="draftApiKey"
            type="password"
            show-password
            placeholder="仅用于本次连通性测试，不保存、不落库"
          />
          <div class="field-hint">
            <el-icon><InfoFilled /></el-icon>
            <span>连通性测试可以使用未保存的配置与临时 Key；正式 Key 请用列表里的「设置 Key」写入系统密钥库。</span>
          </div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" plain :icon="Connection" :loading="checkingDraft" @click="checkDraft">
            测试大模型配置是否连通
          </el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="keyVisible" title="设置 API Key" width="520px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="Key 存入系统密钥库（Windows DPAPI / macOS 钥匙串），数据库只保留掩码，接口不会回传明文"
      />
      <el-input
        v-model="apiKey"
        type="password"
        show-password
        placeholder="粘贴 API Key"
        class="key-input"
      />
      <template #footer>
        <el-button @click="keyVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingKey" @click="saveKey">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="checkVisible" title="AI 自检结果" width="600px">
      <div v-if="checkResult" class="check-result">
        <div class="check-result__head">
          <el-tag :type="checkResult.ok ? 'success' : 'danger'" effect="dark" size="small">
            {{ checkResult.ok ? '通过' : '失败' }}
          </el-tag>
          <span>{{ checkResult.provider }} / {{ checkResult.model || '未指定模型' }}</span>
          <span class="muted">耗时 {{ checkResult.latencyMs }} ms</span>
        </div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="接口地址">{{ checkResult.baseUrl || '-' }}</el-descriptions-item>
          <el-descriptions-item label="配置来源">
            {{ checkResult.draft ? '未保存配置' : '已保存档案' }}
          </el-descriptions-item>
          <el-descriptions-item label="Token" :span="2">{{ usageText }}</el-descriptions-item>
        </el-descriptions>
        <div v-if="checkResult.ok" class="check-result__reply">
          <div class="muted">回复片段</div>
          <pre class="check-result__pre">{{ checkResult.reply || '(空回复)' }}</pre>
        </div>
        <el-alert
          v-if="checkResult.ok && checkResult.hint"
          type="warning"
          :closable="false"
          show-icon
          :title="checkResult.hint"
          class="check-result__alert"
        />
        <el-alert
          v-else
          type="error"
          :closable="false"
          show-icon
          :title="checkResult.hint || checkResult.message"
          class="check-result__alert"
        />
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, InfoFilled, Plus, Refresh } from '@element-plus/icons-vue'
import { settingsApi } from '@/api'
import type { AiModelInfo, AiProfile, AiStatus, SelfCheckResult } from '@/api/types'

interface ProviderSpec {
  name: string
  label: string
  protocol: string
  defaultBaseUrl: string
  defaultModel: string
  requiresApiKey: boolean
  /** 本地自托管模型（Ollama 等）无需 API Key */
  isLocal: boolean
  envKeys: string[]
}

function emptyStatus(): AiStatus {
  return {
    configured: false,
    provider: '',
    model: '',
    baseUrl: '',
    profileId: null,
    profileName: null,
    priority: 0,
    isLocal: false,
    locality: '',
    egress: 'none',
    hasApiKey: false,
    sources: {},
    sourceLabels: {},
    candidates: [],
    error: '',
  }
}

const profiles = ref<AiProfile[]>([])
const providers = ref<ProviderSpec[]>([])
const loading = ref(false)

const AI_DEGRADE_KEY = 'ai.degrade.enabled'
const AI_TIMEOUT_KEY = 'ai.timeout.default'
const aiDegradeEnabled = ref(true)
const aiTimeoutDefault = ref('60')
const savingConfig = ref(false)

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<AiProfile | null>(null)

const form = reactive({
  name: '',
  provider: '',
  protocol: 'openai_compat',
  baseUrl: '',
  model: '',
  priority: 0,
  temperature: 0.7,
  maxTokens: 4096,
  timeout: 60,
  isDefault: false,
  useEnvKey: false,
  envKeyName: '',
  envKeyScope: 'user' as 'user' | 'system',
})

/** 行内启用开关的加载态 */
const activeBusyId = ref<number | null>(null)

const keyVisible = ref(false)
const savingKey = ref(false)
const apiKey = ref('')
const keyTarget = ref<AiProfile | null>(null)

const status = ref<AiStatus>(emptyStatus())
const checkVisible = ref(false)
const checkResult = ref<SelfCheckResult | null>(null)
const checkingId = ref<number | null>(null)
const checkingDraft = ref(false)
const draftApiKey = ref('')
const modelOptions = ref<AiModelInfo[]>([])
const loadingModels = ref(false)

/** 当前选择的供应商是不是本地自托管模型（Ollama 等） */
const isLocalProvider = computed(() => {
  const spec = providers.value.find((item) => item.name === form.provider)
  return Boolean(spec?.isLocal)
})

const sourceText = computed(() => {
  const labels = status.value?.sourceLabels ?? {}
  const parts = Object.entries(labels)
    .filter(([, label]) => label && label !== '未配置')
    .map(([field, label]) => `${field}:${label}`)
  return parts.length ? parts.join(' / ') : '未配置'
})

const usageText = computed(() => {
  const usage = checkResult.value?.usage ?? {}
  const total = usage.totalTokens ?? usage.total_tokens
  if (total === undefined || total === null) {
    return '-'
  }
  const prompt = usage.promptTokens ?? usage.prompt_tokens ?? 0
  const completion = usage.completionTokens ?? usage.completion_tokens ?? 0
  return `总计 ${total}（输入 ${prompt} / 输出 ${completion}）`
})

function modelLabel(item: AiModelInfo): string {
  const detail = item.parameterSize || item.quantization
  return detail ? `${item.label} · ${detail}` : item.label
}

async function load(): Promise<void> {
  loading.value = true
  try {
    profiles.value = await settingsApi.profiles()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadProviders(): Promise<void> {
  try {
    providers.value = (await settingsApi.providers()) as ProviderSpec[]
  } catch {
    providers.value = []
  }
}

async function loadStatus(): Promise<void> {
  try {
    status.value = await settingsApi.status()
  } catch {
    status.value = emptyStatus()
  }
}

/** 加载 AI 调用参数配置。 */
async function loadAiConfig(): Promise<void> {
  try {
    const configs = await settingsApi.config()
    const find = (key: string): string =>
      configs.find((item) => item.key === key)?.value ?? ''
    aiDegradeEnabled.value = find(AI_DEGRADE_KEY).toLowerCase() !== 'false'
    const timeout = find(AI_TIMEOUT_KEY)
    aiTimeoutDefault.value = timeout || '60'
  } catch {
    // 加载失败保留默认值
  }
}

async function saveAiConfig(): Promise<void> {
  savingConfig.value = true
  try {
    await settingsApi.updateConfig([
      { key: AI_DEGRADE_KEY, value: aiDegradeEnabled.value ? 'true' : 'false', valueType: 'bool' },
      { key: AI_TIMEOUT_KEY, value: aiTimeoutDefault.value.trim(), valueType: 'int' },
    ])
    ElMessage.success('已保存')
    await loadAiConfig()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    savingConfig.value = false
  }
}

function loadAll(): void {
  void loadProviders()
  void load()
  void loadStatus()
  void loadAiConfig()
}

function onProviderChange(name: string): void {
  const spec = providers.value.find((item) => item.name === name)
  if (!spec) {
    return
  }
  form.protocol = spec.protocol
  form.baseUrl = spec.defaultBaseUrl
  form.model = spec.defaultModel
  if (spec.isLocal) {
    form.useEnvKey = false
  }
  modelOptions.value = []
}

async function loadModels(): Promise<void> {
  loadingModels.value = true
  try {
    const payload: Record<string, unknown> = {
      provider: form.provider,
      protocol: form.protocol,
      baseUrl: form.baseUrl,
    }
    if (editing.value) {
      payload.profileId = editing.value.id
    }
    modelOptions.value = await settingsApi.models(payload)
    if (!modelOptions.value.length) {
      ElMessage.info('服务端没有返回可用模型，可直接手动输入模型名')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '拉取模型列表失败')
    modelOptions.value = []
  } finally {
    loadingModels.value = false
  }
}

function openCreate(): void {
  editing.value = null
  Object.assign(form, {
    name: '',
    provider: providers.value[0]?.name ?? '',
    protocol: providers.value[0]?.protocol ?? 'openai_compat',
    baseUrl: providers.value[0]?.defaultBaseUrl ?? '',
    model: providers.value[0]?.defaultModel ?? '',
    priority: 0,
    temperature: 0.7,
    maxTokens: 4096,
    timeout: 60,
    isDefault: false,
    useEnvKey: false,
    envKeyName: '',
    envKeyScope: 'user',
  })
  draftApiKey.value = ''
  modelOptions.value = []
  dialogVisible.value = true
}

function openEdit(row: AiProfile): void {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    provider: row.provider,
    protocol: row.protocol,
    baseUrl: row.baseUrl,
    model: row.model,
    priority: row.priority,
    temperature: row.temperature,
    maxTokens: row.maxTokens,
    timeout: row.timeout,
    isDefault: row.isDefault,
    useEnvKey: row.useEnvKey,
    envKeyName: row.envKeyName,
    envKeyScope: row.envKeyScope,
  })
  draftApiKey.value = ''
  modelOptions.value = []
  dialogVisible.value = true
}

async function save(): Promise<void> {
  if (!form.name.trim() || !form.provider) {
    ElMessage.warning('名称与供应商为必填项')
    return
  }
  if (form.useEnvKey && !form.envKeyName.trim()) {
    ElMessage.warning('启用本地 Key 时必须填写环境变量名')
    return
  }
  // 「启用」在列表里单独开关，这里不提交，避免用旧值覆盖
  const payload = {
    name: form.name,
    provider: form.provider,
    protocol: form.protocol,
    baseUrl: form.baseUrl,
    model: form.model,
    priority: form.priority,
    temperature: form.temperature,
    maxTokens: form.maxTokens,
    timeout: form.timeout,
    isDefault: form.isDefault,
    useEnvKey: isLocalProvider.value ? false : form.useEnvKey,
    envKeyName: form.envKeyName.trim(),
    envKeyScope: form.envKeyScope,
  }
  saving.value = true
  try {
    if (editing.value) {
      await settingsApi.updateProfile(editing.value.id, payload)
    } else {
      await settingsApi.createProfile(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    await load()
    await loadStatus()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

function openKey(row: AiProfile): void {
  keyTarget.value = row
  apiKey.value = ''
  keyVisible.value = true
}

async function saveKey(): Promise<void> {
  if (!keyTarget.value) {
    return
  }
  if (!apiKey.value.trim()) {
    ElMessage.warning('请输入 API Key')
    return
  }
  savingKey.value = true
  try {
    await settingsApi.setApiKey(keyTarget.value.id, apiKey.value.trim())
    ElMessage.success('Key 已存入系统密钥库')
    keyVisible.value = false
    await load()
    await loadStatus()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    savingKey.value = false
  }
}

async function remove(row: AiProfile): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除档案「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await settingsApi.removeProfile(row.id)
    ElMessage.success('已删除')
    await load()
    await loadStatus()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

async function checkProfile(row: AiProfile): Promise<void> {
  checkingId.value = row.id
  try {
    checkResult.value = await settingsApi.selfCheck({ profileId: row.id })
    checkVisible.value = true
    if (checkResult.value.ok) {
      ElMessage.success(`连接成功 · ${checkResult.value.latencyMs} ms`)
    } else {
      ElMessage.error(checkResult.value.hint || checkResult.value.message || '连接失败')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '自检请求失败')
  } finally {
    checkingId.value = null
  }
}

async function checkDraft(): Promise<void> {
  checkingDraft.value = true
  try {
    const payload: Record<string, unknown> = {
      provider: form.provider,
      protocol: form.protocol,
      baseUrl: form.baseUrl,
      model: form.model,
      timeout: form.timeout,
    }
    if (editing.value) {
      payload.profileId = editing.value.id
    }
    payload.useEnvKey = form.useEnvKey
    payload.envKeyName = form.envKeyName.trim()
    payload.envKeyScope = form.envKeyScope
    if (!form.useEnvKey && draftApiKey.value.trim()) {
      payload.apiKey = draftApiKey.value.trim()
    }
    checkResult.value = await settingsApi.selfCheck(payload)
    checkVisible.value = true
    if (checkResult.value.ok) {
      ElMessage.success(`连接成功 · ${checkResult.value.latencyMs} ms`)
    } else {
      ElMessage.error(checkResult.value.hint || checkResult.value.message || '连接失败')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '连通性测试失败')
  } finally {
    checkingDraft.value = false
  }
}

/** 环境变量 Key 的悬停说明 */
function envKeyTip(row: AiProfile): string {
  const scope = row.envKeyScope === 'system' ? '系统变量' : '用户变量'
  const status = row.envKeyPresent ? '当前可读取' : '当前未读取到，调用会报「未读取到环境变量」'
  return `${scope} ${row.envKeyName || '（未设置变量名）'} · ${status}`
}

/** 列表里直接启停档案，不用进编辑弹框 */
async function toggleActive(row: AiProfile, value: boolean): Promise<void> {
  activeBusyId.value = row.id
  try {
    await settingsApi.updateProfile(row.id, { isActive: value })
    row.isActive = value
    ElMessage.success(value ? '已启用' : '已停用')
    await loadStatus()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '切换失败')
    await load()
  } finally {
    activeBusyId.value = null
  }
}

onMounted(loadAll)
</script>

<style scoped>
.page-alert {
  margin-bottom: 12px;
}

.ai-config-panel {
  margin-bottom: 14px;
}

.ai-config-hint {
  font-size: 12px;
  line-height: 1.6;
  margin-top: 4px;
}

.section-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.section-head h3 {
  margin: 0;
  font-size: 15px;
}

.candidates {
  margin-top: 10px;
}

.candidates__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}

.candidates__row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.candidates__order {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border-radius: 50%;
  flex-shrink: 0;
}

.candidates__priority {
  font-size: 12px;
}

.status-error {
  margin-top: 8px;
  color: var(--el-color-danger);
}

.providers-block {
  margin-top: 16px;
}

.providers-block h3 {
  margin: 0 0 10px;
  font-size: 15px;
}

.form-hint {
  margin-left: 10px;
  font-size: 12px;
}

.mark-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.mark-tags :deep(.el-tag + .el-tag) {
  margin-left: 0;
}

/* 字段说明：图标 + 取值解释 */
.field-hint {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--aitp-text-weak);
}

.field-hint .el-icon {
  margin-top: 2px;
  flex-shrink: 0;
  color: var(--el-color-primary);
  opacity: 0.85;
}

.model-field {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.key-input {
  margin-top: 12px;
}

.check-result__head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.check-result__reply {
  margin-top: 12px;
}

.check-result__pre {
  margin: 6px 0 0;
  padding: 10px;
  background: var(--aitp-bg);
  border-radius: 4px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 180px;
  overflow-y: auto;
}

.check-result__alert {
  margin-top: 12px;
}
</style>
