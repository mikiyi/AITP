<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">外部平台导入</h2>
      <div class="toolbar" style="margin: 0">
        <el-select v-model="platform" style="width: 160px" @change="switchPlatform">
          <el-option v-for="item in catalog?.platforms ?? []" :key="item.key" :label="item.label" :value="item.key" />
        </el-select>
        <el-select v-model="projectId" placeholder="选择项目" style="width: 220px">
          <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      class="ext-note"
      :title="`只读单向导入：从${platformInfo?.label ?? '外部平台'}拉取需求 / 用例 / 缺陷进 AITP，绝不回写源平台。口令仅存系统密钥库，库内只留引用与掩码；按 项目 + 平台 + 外部 ID 去重，重复导入自动跳过。`"
    />

    <div class="panel panel-pad ext-block">
      <div class="ext-head">
        <h3>连接配置</h3>
        <el-tag v-if="config?.hasPassword" size="small" effect="plain" type="success">
          已存口令 {{ config?.passwordMasked }}
        </el-tag>
      </div>
      <el-form label-width="120px" class="ext-form" @submit.prevent>
        <el-form-item :label="labels.url" required>
          <el-input v-model="form.baseUrl" :placeholder="labels.urlPlaceholder" />
        </el-form-item>
        <el-form-item :label="labels.product" required>
          <el-input v-model="form.productId" :placeholder="labels.productPlaceholder" style="width: 260px" />
        </el-form-item>
        <el-form-item v-if="platform === 'testlink'" label="测试计划 ID" required>
          <el-input v-model="form.testPlanId" placeholder="用例挂在测试计划下，如 88" style="width: 220px" />
        </el-form-item>
        <el-form-item :label="labels.account" :required="platform !== 'testlink'">
          <el-input v-model="form.username" :placeholder="labels.accountPlaceholder" style="width: 280px" />
        </el-form-item>
        <el-form-item :label="labels.secret">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            autocomplete="new-password"
            :placeholder="config?.hasPassword ? '留空保持已存口令不变' : labels.secretPlaceholder"
            style="width: 280px"
          />
        </el-form-item>

        <template v-if="platform === 'jira'">
          <el-form-item label="需求类型">
            <el-input v-model="form.requirementTypes" placeholder="Jira issue 类型，逗号分隔，如 Story,Requirement" style="width: 420px" />
          </el-form-item>
          <el-form-item label="用例类型">
            <el-input v-model="form.caseTypes" placeholder="如 Test（配合 Xray/Zephyr）；留空则不导入用例" style="width: 420px" />
          </el-form-item>
          <el-form-item label="缺陷类型">
            <el-input v-model="form.bugTypes" placeholder="如 Bug" style="width: 420px" />
          </el-form-item>
        </template>

        <el-form-item label="TLS 校验">
          <el-checkbox v-model="form.insecureTls">允许自签名 / 内网 CA 证书（跳过证书校验）</el-checkbox>
        </el-form-item>

        <el-collapse v-if="platform === 'zentao'" class="ext-server">
          <el-collapse-item name="server">
            <template #title>
              <span class="ext-server-title">服务器认证（HTTP Basic，可选）</span>
            </template>
            <el-alert
              type="warning"
              :closable="false"
              class="ext-note"
              title="若禅道站点前还有一层服务器级 HTTP Basic 防护（反向代理 / runner 防护层，与禅道登录口令不同源），请在此填写。特征：整站任意地址都返回 401 且带 WWW-Authenticate: Basic。实际项目禅道常与 AITP 不在同一台机器，地址与认证按目标实例填写。"
            />
            <el-form-item label="服务器账号">
              <el-input v-model="form.serverUsername" placeholder="HTTP Basic 账号" style="width: 280px" />
            </el-form-item>
            <el-form-item label="服务器密码">
              <el-input
                v-model="form.serverPassword"
                type="password"
                show-password
                autocomplete="new-password"
                :placeholder="config?.hasPassword ? '留空保持已存口令不变' : 'HTTP Basic 密码'"
                style="width: 280px"
              />
            </el-form-item>
          </el-collapse-item>
        </el-collapse>

        <el-form-item>
          <el-button type="primary" :loading="saving" :disabled="!projectId" @click="saveConfig">保存配置</el-button>
          <el-button :loading="previewing" :disabled="!projectId" @click="doPreview">测试预览</el-button>
          <el-button v-if="config" type="danger" plain :disabled="!projectId" @click="removeConfig">删除配置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="panel panel-pad ext-block">
      <div class="ext-head">
        <h3>导入</h3>
      </div>
      <el-form label-width="120px" class="ext-form" @submit.prevent>
        <el-form-item label="导入内容">
          <el-checkbox-group v-model="selectedKinds">
            <el-checkbox v-for="kind in catalog?.kinds ?? []" :key="kind.key" :value="kind.key">
              {{ kind.label }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="映射模板">
          <el-select v-model="mappingId" placeholder="默认映射" clearable style="width: 280px">
            <el-option v-for="m in mappings" :key="m.id" :label="m.name + (m.isDefault ? '（默认）' : '')" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="每类上限">
          <el-input-number v-model="maxResults" :min="1" :max="1000" />
        </el-form-item>
        <el-form-item>
          <el-button :loading="previewing" :disabled="!projectId" @click="doPreview">预览</el-button>
          <el-button type="primary" :loading="importing" :disabled="!projectId || !selectedKinds.length" @click="doImport">
            开始导入
          </el-button>
        </el-form-item>
      </el-form>

      <template v-if="preview">
        <el-alert
          v-if="preview.errors.length"
          type="warning"
          :closable="false"
          class="ext-note"
          :title="preview.errors.join('；')"
        />
        <el-table :data="previewRows" border size="small" class="ext-counts">
          <el-table-column prop="label" label="内容" width="120" />
          <el-table-column prop="count" label="拉取条数" width="100" align="right" />
          <el-table-column label="样本标题">
            <template #default="{ row }">
              <span class="ext-samples">{{ row.samples || '—' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <template v-if="importResult">
        <el-alert
          :type="importResult.status === 'success' ? 'success' : importResult.status === 'partial' ? 'warning' : 'error'"
          :closable="false"
          class="ext-note"
          :title="`导入${statusLabel(importResult.status)}：批次 #${importResult.batchId}`"
        />
        <el-table :data="importRows" border size="small" class="ext-counts">
          <el-table-column prop="label" label="内容" width="120" />
          <el-table-column prop="imported" label="新增" width="90" align="right" />
          <el-table-column prop="skipped" label="跳过（已存在）" width="140" align="right" />
          <el-table-column label="错误">
            <template #default="{ row }">
              <span class="ext-samples">{{ row.errors || '—' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </div>

    <div class="panel panel-pad ext-block">
      <div class="ext-head">
        <h3>映射模板</h3>
        <el-button size="small" :icon="Plus" :disabled="!projectId" @click="openMapping()">新建模板</el-button>
      </div>
      <p class="ext-desc">
        映射定义 AITP 字段 → 源平台字段路径，支持点号（a.b）、数组拼接（steps[].content）与兜底（a|b）。
        不同平台/版本字段名有差异时可自定义；留空使用默认映射。
      </p>
      <el-table :data="mappings" border size="small">
        <el-table-column prop="name" label="名称" />
        <el-table-column label="默认" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.isDefault" size="small" effect="plain" type="success">默认</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="updatedAt" label="更新时间" width="200" />
        <el-table-column label="操作" width="140" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openMapping(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="removeMapping(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>暂无自定义模板，导入使用默认映射</template>
      </el-table>
    </div>

    <div class="panel panel-pad ext-block">
      <div class="ext-head">
        <h3>导入批次</h3>
        <el-button size="small" :icon="Refresh" :disabled="!projectId" @click="loadBatches">刷新</el-button>
      </div>
      <el-table :data="batches" border size="small">
        <el-table-column prop="id" label="#" width="70" />
        <el-table-column label="平台" width="100">
          <template #default="{ row }">{{ platformLabel(row.platform) }}</template>
        </el-table-column>
        <el-table-column prop="kinds" label="内容" width="200" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTag(row.status)" effect="plain">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="新增 / 跳过" width="140" align="right">
          <template #default="{ row }">{{ sum(row.imported) }} / {{ sum(row.skipped) }}</template>
        </el-table-column>
        <el-table-column prop="createdAt" label="时间" width="200" />
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openBatch(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty>暂无导入记录</template>
      </el-table>
    </div>

    <el-dialog v-model="mappingDialog.visible" :title="mappingDialog.id ? '编辑映射模板' : '新建映射模板'" width="640px">
      <el-form label-width="90px" @submit.prevent>
        <el-form-item label="名称" required>
          <el-input v-model="mappingDialog.name" placeholder="如：禅道 18.x 字段映射" />
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="mappingDialog.isDefault" />
        </el-form-item>
        <el-form-item label="映射 JSON">
          <el-input v-model="mappingDialog.json" type="textarea" :rows="14" spellcheck="false" class="ext-json" />
        </el-form-item>
        <el-button size="small" @click="resetMappingJson">恢复默认映射</el-button>
      </el-form>
      <template #footer>
        <el-button @click="mappingDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="mappingSaving" @click="saveMapping">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="batchDialog.visible" title="批次详情" width="640px">
      <template v-if="batchDialog.batch">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="批次">#{{ batchDialog.batch.id }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ statusLabel(batchDialog.batch.status) }}</el-descriptions-item>
          <el-descriptions-item label="内容">{{ batchDialog.batch.kinds }}</el-descriptions-item>
          <el-descriptions-item label="时间">{{ batchDialog.batch.createdAt }}</el-descriptions-item>
        </el-descriptions>
        <h4 class="ext-sub">计数</h4>
        <pre class="ext-pre">{{ pretty(batchDialog.batch.imported) }}</pre>
        <h4 class="ext-sub">错误</h4>
        <pre class="ext-pre">{{ errorText(batchDialog.batch.errors) }}</pre>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { externalImportApi } from '@/api'
import type {
  ExternalImportBatch,
  ExternalImportCatalog,
  ExternalImportConfig,
  ExternalImportMapping,
  ExternalImportPreview,
  ExternalImportResult,
} from '@/api/types'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
const projectId = ref<number | null>(appStore.currentProjectId ?? null)
const platform = ref('zentao')

const catalog = ref<ExternalImportCatalog | null>(null)
const config = ref<ExternalImportConfig | null>(null)
const mappings = ref<ExternalImportMapping[]>([])
const batches = ref<ExternalImportBatch[]>([])
const preview = ref<ExternalImportPreview | null>(null)
const importResult = ref<ExternalImportResult | null>(null)

const saving = ref(false)
const previewing = ref(false)
const importing = ref(false)
const mappingSaving = ref(false)

const form = reactive({
  baseUrl: '',
  productId: '',
  username: '',
  password: '',
  serverUsername: '',
  serverPassword: '',
  insecureTls: false,
  testPlanId: '',
  requirementTypes: 'Story,Requirement',
  caseTypes: 'Test',
  bugTypes: 'Bug',
})
const selectedKinds = ref<string[]>(['requirements', 'cases', 'bugs'])
const mappingId = ref<number | null>(null)
const maxResults = ref(200)

const platformInfo = computed(() =>
  catalog.value?.platforms.find((item) => item.key === platform.value) ?? null,
)
const platformKinds = computed(() => platformInfo.value?.kinds ?? [])
const defaultMapping = computed(() =>
  platformInfo.value?.defaultMapping ?? catalog.value?.defaultMapping ?? {},
)
const labels = computed(() => {
  if (platform.value === 'jira') {
    return {
      url: 'Jira 地址', urlPlaceholder: '如 https://company.atlassian.net（可含子路径）',
      product: '项目 Key', productPlaceholder: 'Jira 项目 Key，如 DEMO',
      account: 'Jira 邮箱', accountPlaceholder: '登录 Jira 的邮箱',
      secret: 'API Token', secretPlaceholder: 'Jira API Token（或个人访问令牌）',
    }
  }
  if (platform.value === 'testlink') {
    return {
      url: 'TestLink 地址', urlPlaceholder: '如 https://testlink.example.com',
      product: '测试项目 ID', productPlaceholder: 'TestLink 测试项目 ID，如 77',
      account: 'TestLink 账号', accountPlaceholder: '可选，用于记录',
      secret: 'API devKey', secretPlaceholder: '在 TestLink 账号的 API 接口里生成',
    }
  }
  return {
    url: '禅道地址', urlPlaceholder: '如 https://zentao.example.com（可含子路径）',
    product: '产品 ID', productPlaceholder: '禅道产品 ID，如 1',
    account: '禅道账号', accountPlaceholder: '禅道应用登录账号',
    secret: '禅道密码', secretPlaceholder: '禅道应用登录密码',
  }
})

const mappingDialog = reactive({
  visible: false,
  id: null as number | null,
  name: '',
  isDefault: false,
  json: '',
})
const batchDialog = reactive({
  visible: false,
  batch: null as ExternalImportBatch | null,
})

const kindLabel = (key: string): string =>
  catalog.value?.kinds.find((k) => k.key === key)?.label ?? key

const platformLabel = (key: string): string =>
  catalog.value?.platforms.find((p) => p.key === key)?.label ?? key

const previewRows = computed(() =>
  Object.entries(preview.value?.counts ?? {}).map(([key, count]) => ({
    label: kindLabel(key),
    count,
    samples: (preview.value?.samples[key] ?? []).filter(Boolean).join('；'),
  })),
)

const importRows = computed(() => {
  const result = importResult.value
  if (!result) return []
  const keys = Object.keys(result.fetched)
  return keys.map((key) => ({
    label: kindLabel(key),
    imported: result.imported[key] ?? 0,
    skipped: result.skipped[key] ?? 0,
    errors: (result.errors[key] ?? []).join('；'),
  }))
})

function statusLabel(status: string): string {
  return { success: '成功', partial: '部分成功', error: '失败', running: '进行中' }[status] ?? status
}

function statusTag(status: string): 'success' | 'warning' | 'danger' | 'info' {
  return status === 'success' ? 'success' : status === 'partial' ? 'warning' : status === 'error' ? 'danger' : 'info'
}

function sum(counts: Record<string, number>): number {
  return Object.values(counts ?? {}).reduce((acc, n) => acc + n, 0)
}

function pretty(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2)
}

function errorText(errors: Record<string, string[]>): string {
  const entries = Object.entries(errors ?? {})
  if (!entries.length) return '无'
  return entries.map(([key, list]) => `${kindLabel(key)}：\n${list.join('\n')}`).join('\n\n')
}

function fillForm(cfg: ExternalImportConfig | null): void {
  form.baseUrl = cfg?.baseUrl ?? ''
  form.productId = cfg?.productId ?? ''
  form.username = cfg?.username ?? ''
  form.serverUsername = cfg?.serverUsername ?? ''
  form.insecureTls = Boolean(cfg?.extra?.insecureTls)
  form.testPlanId = String(cfg?.extra?.testPlanId ?? '')
  form.requirementTypes = joinTypes(cfg?.extra?.requirementTypes, 'Story,Requirement')
  form.caseTypes = joinTypes(cfg?.extra?.caseTypes, 'Test')
  form.bugTypes = joinTypes(cfg?.extra?.bugTypes, 'Bug')
  form.password = ''
  form.serverPassword = ''
}

function joinTypes(value: unknown, fallback: string): string {
  if (Array.isArray(value)) return value.map(String).join(',')
  return typeof value === 'string' && value ? value : fallback
}

function splitTypes(value: string): string[] {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

async function loadConfig(): Promise<void> {
  if (!projectId.value) {
    config.value = null
    fillForm(null)
    return
  }
  config.value = await externalImportApi.config(projectId.value, platform.value)
  fillForm(config.value)
}

async function loadMappings(): Promise<void> {
  mappings.value = projectId.value
    ? await externalImportApi.mappings(projectId.value, platform.value)
    : []
}

async function loadBatches(): Promise<void> {
  batches.value = projectId.value ? await externalImportApi.batches(projectId.value) : []
}

async function reloadProject(): Promise<void> {
  preview.value = null
  importResult.value = null
  mappingId.value = null
  await Promise.all([loadConfig(), loadMappings(), loadBatches()])
  syncKinds()
}

function syncKinds(): void {
  const allowed = platformKinds.value
  if (!allowed.length) return
  const kept = selectedKinds.value.filter((kind) => allowed.includes(kind))
  selectedKinds.value = kept.length ? kept : [...allowed]
}

async function switchPlatform(): Promise<void> {
  selectedKinds.value = []
  config.value = null
  fillForm(null)
  await reloadProject()
}

async function saveConfig(): Promise<void> {
  if (!projectId.value) return
  saving.value = true
  try {
    config.value = await externalImportApi.saveConfig({
      projectId: projectId.value,
      platform: platform.value,
      baseUrl: form.baseUrl,
      productId: form.productId,
      username: form.username,
      password: form.password,
      serverUsername: form.serverUsername,
      serverPassword: form.serverPassword,
      extra: {
        ...(config.value?.extra ?? {}),
        insecureTls: form.insecureTls,
        testPlanId: form.testPlanId,
        requirementTypes: splitTypes(form.requirementTypes),
        caseTypes: splitTypes(form.caseTypes),
        bugTypes: splitTypes(form.bugTypes),
      },
    })
    fillForm(config.value)
    ElMessage.success('连接配置已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeConfig(): Promise<void> {
  if (!projectId.value) return
  try {
    await ElMessageBox.confirm('删除后需重新填写连接与口令，确定删除？', '删除配置', { type: 'warning' })
  } catch {
    return
  }
  await externalImportApi.removeConfig(projectId.value, platform.value)
  config.value = null
  fillForm(null)
  ElMessage.success('配置已删除')
}

async function doPreview(): Promise<void> {
  if (!projectId.value) return
  previewing.value = true
  preview.value = null
  try {
    preview.value = await externalImportApi.preview({
      projectId: projectId.value,
      platform: platform.value,
      mappingId: mappingId.value ?? undefined,
      kinds: selectedKinds.value,
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '预览失败')
  } finally {
    previewing.value = false
  }
}

async function doImport(): Promise<void> {
  if (!projectId.value || !selectedKinds.value.length) return
  importing.value = true
  importResult.value = null
  try {
    importResult.value = await externalImportApi.runImport({
      projectId: projectId.value,
      platform: platform.value,
      kinds: selectedKinds.value,
      mappingId: mappingId.value ?? undefined,
      maxResults: maxResults.value,
    })
    ElMessage.success(`导入${statusLabel(importResult.value.status)}`)
    await loadBatches()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '导入失败')
  } finally {
    importing.value = false
  }
}

function openMapping(row?: ExternalImportMapping): void {
  mappingDialog.id = row?.id ?? null
  mappingDialog.name = row?.name ?? ''
  mappingDialog.isDefault = row?.isDefault ?? false
  mappingDialog.json = JSON.stringify(row?.mapping ?? defaultMapping.value, null, 2)
  mappingDialog.visible = true
}

function resetMappingJson(): void {
  mappingDialog.json = JSON.stringify(defaultMapping.value, null, 2)
}

async function saveMapping(): Promise<void> {
  if (!projectId.value) return
  let mapping: Record<string, unknown>
  try {
    mapping = JSON.parse(mappingDialog.json) as Record<string, unknown>
  } catch {
    ElMessage.error('映射 JSON 格式不合法')
    return
  }
  if (!mapping || typeof mapping !== 'object' || Array.isArray(mapping)) {
    ElMessage.error('映射必须是 JSON 对象')
    return
  }
  mappingSaving.value = true
  try {
    const body = {
      projectId: projectId.value,
      platform: platform.value,
      name: mappingDialog.name,
      isDefault: mappingDialog.isDefault,
      mapping,
    }
    if (mappingDialog.id) {
      await externalImportApi.updateMapping(mappingDialog.id, body)
    } else {
      await externalImportApi.createMapping(body)
    }
    mappingDialog.visible = false
    ElMessage.success('映射模板已保存')
    await loadMappings()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    mappingSaving.value = false
  }
}

async function removeMapping(row: ExternalImportMapping): Promise<void> {
  if (!projectId.value) return
  try {
    await ElMessageBox.confirm(`确定删除映射模板「${row.name}」？`, '删除模板', { type: 'warning' })
  } catch {
    return
  }
  await externalImportApi.removeMapping(row.id, projectId.value)
  ElMessage.success('模板已删除')
  await loadMappings()
}

async function openBatch(row: ExternalImportBatch): Promise<void> {
  if (!projectId.value) return
  batchDialog.batch = await externalImportApi.batch(row.id, projectId.value)
  batchDialog.visible = true
}

watch(() => appStore.currentProjectId, (value) => {
  projectId.value = value ?? null
})

watch(projectId, () => {
  void reloadProject()
})

onMounted(async () => {
  catalog.value = await externalImportApi.catalog()
  await reloadProject()
})
</script>

<style scoped>
.ext-block {
  margin-bottom: 14px;
}

.ext-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.ext-head h3 {
  margin: 0;
  font-size: 15px;
}

.ext-note {
  margin-bottom: 12px;
}

.ext-form {
  max-width: 760px;
}

.ext-desc {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--aitp-text-weak);
}

.ext-server {
  margin: 0 0 14px;
  border-top: none;
}

.ext-server-title {
  font-size: 13px;
  font-weight: 600;
}

.ext-counts {
  margin-top: 12px;
}

.ext-samples {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.ext-json :deep(textarea) {
  font-family: var(--aitp-font-mono, monospace);
  font-size: 12px;
}

.ext-sub {
  margin: 14px 0 6px;
  font-size: 13px;
}

.ext-pre {
  margin: 0;
  padding: 10px;
  font-size: 12px;
  line-height: 1.6;
  background: var(--aitp-fill-soft, rgba(0, 0, 0, 0.03));
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>