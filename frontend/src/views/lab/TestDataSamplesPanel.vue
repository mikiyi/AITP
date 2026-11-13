<template>
  <div class="tds">
    <div class="panel panel-pad">
      <div class="tds-head">
        <h3 class="tds-title">脱敏样本导入</h3>
        <span class="tds-local">原始样本只在内存中解析，落盘的是脱敏副本</span>
      </div>
      <el-alert
        v-if="!projectId"
        type="warning"
        :closable="false"
        title="请先在页面顶部选择项目，样本与方案都归到该项目下"
        class="tds-alert"
      />
      <el-form label-width="110px" label-position="right">
        <el-form-item label="样本文件" required>
          <el-upload
            ref="uploadRef"
            :auto-upload="false"
            :limit="1"
            accept=".csv,.json,.xlsx"
            :on-change="onFileChange"
            :on-exceed="onExceed"
          >
            <el-button :icon="Plus">选择文件</el-button>
            <template #tip>
              <div class="tds-tip">支持 CSV / JSON / XLSX，≤20MB、≤5000 行；列名即脱敏规则的键</div>
            </template>
          </el-upload>
        </el-form-item>
        <el-form-item label="样本名称">
          <el-input v-model="sampleName" placeholder="留空用文件名" maxlength="100" />
        </el-form-item>
        <el-form-item label="脱敏方案">
          <el-select
            v-model="selectedScheme"
            clearable
            placeholder="不选则按识别结果自动生成规则"
            style="width: 320px"
          >
            <el-option
              v-for="item in schemes"
              :key="item.id"
              :label="`${item.name}（v${item.version}）`"
              :value="item.id"
            />
          </el-select>
          <el-button link type="primary" @click="openCreateScheme">管理方案</el-button>
        </el-form-item>
        <el-form-item label="保留天数">
          <el-input-number v-model="retentionDays" :min="1" :max="3650" />
          <span class="tds-tip">到期自动删除脱敏副本并软删记录</span>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="uploading" :disabled="!projectId" @click="importSample">
            导入并脱敏
          </el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="panel panel-pad">
      <div class="tds-head">
        <h3 class="tds-title">脱敏方案</h3>
        <el-button size="small" :icon="Plus" :disabled="!projectId" @click="openCreateScheme">
          新建方案
        </el-button>
      </div>
      <el-table :data="schemes" border stripe size="small" empty-text="还没有脱敏方案">
        <el-table-column prop="name" label="方案" min-width="160" show-overflow-tooltip />
        <el-table-column label="版本" width="80" align="center">
          <template #default="{ row }">v{{ row.version }}</template>
        </el-table-column>
        <el-table-column label="规则" width="80" align="center">
          <template #default="{ row }">{{ ruleCount(row) }} 条</template>
        </el-table-column>
        <el-table-column label="默认" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.isDefault" size="small" type="success" effect="plain">默认</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="说明" min-width="180" show-overflow-tooltip>
          <template #default="{ row }"><span class="muted">{{ row.description || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEditScheme(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="removeScheme(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="panel panel-pad">
      <div class="tds-head">
        <h3 class="tds-title">样本池</h3>
        <div class="tds-head__actions">
          <el-button size="small" @click="cleanup">清理过期</el-button>
        </div>
      </div>
      <el-table :data="samples" border stripe size="small" empty-text="还没有导入样本">
        <el-table-column prop="name" label="样本" min-width="150" show-overflow-tooltip />
        <el-table-column prop="sourceFormat" label="格式" width="80" align="center" />
        <el-table-column label="规模" width="110" align="center">
          <template #default="{ row }">{{ row.rowCount }} 行 / {{ row.columnCount }} 列</template>
        </el-table-column>
        <el-table-column label="敏感列" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="muted">{{ sensitiveText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="方案" width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ schemeLabel(row) }}</template>
        </el-table-column>
        <el-table-column label="到期" width="120" align="center">
          <template #default="{ row }">{{ dateText(row.expiresAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="260" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="download(row, 'json')">JSON</el-button>
            <el-button link type="primary" size="small" @click="download(row, 'csv')">CSV</el-button>
            <el-button link type="primary" size="small" @click="openSchemeFromSample(row)">存为方案</el-button>
            <el-button link type="danger" size="small" @click="removeSample(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="schemeDialog" :title="editingSchemeId ? '编辑脱敏方案' : '新建脱敏方案'" width="820px">
      <el-form label-width="90px">
        <el-form-item label="方案名称" required>
          <el-input v-model="schemeForm.name" maxlength="100" placeholder="例如：订单标准脱敏" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="schemeForm.description" maxlength="500" />
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="schemeForm.isDefault" />
        </el-form-item>
        <el-form-item label="字段规则">
          <div class="tds-rules">
            <div v-for="(rule, index) in schemeForm.rules" :key="index" class="tds-rule">
              <el-input v-model="rule.column" class="tds-rule__col" placeholder="列名" />
              <el-select v-model="rule.type" class="tds-rule__type">
                <el-option v-for="item in RULE_TYPES" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
              <template v-if="rule.type === 'mask'">
                <el-input-number v-model="rule.keepPrefix" :min="0" :max="64" controls-position="right" class="tds-rule__num" />
                <span class="muted">首</span>
                <el-input-number v-model="rule.keepSuffix" :min="0" :max="64" controls-position="right" class="tds-rule__num" />
                <span class="muted">尾</span>
              </template>
              <template v-else-if="rule.type === 'hash' || rule.type === 'random'">
                <el-input-number v-model="rule.length" :min="1" :max="64" controls-position="right" class="tds-rule__num" />
                <span class="muted">位</span>
              </template>
              <template v-else-if="rule.type === 'enum'">
                <el-input v-model="rule.values" class="tds-rule__values" placeholder="枚举值，逗号分隔" />
              </template>
              <template v-else>
                <el-input v-model="rule.replacement" class="tds-rule__values" placeholder="替换为" />
              </template>
              <el-button link type="danger" @click="removeRule(index)">删除</el-button>
            </div>
            <el-button link type="primary" :icon="Plus" @click="addRule()">新增字段</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="schemeDialog = false">取消</el-button>
        <el-button type="primary" :loading="schemeSaving" @click="saveScheme">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadInstance } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { testDataApi } from '@/api'
import type { TestDataMaskRule, TestDataMaskScheme, TestDataSample } from '@/api/types'
import { useAppStore } from '@/stores/app'

const RULE_TYPES = [
  { value: 'mask', label: '掩码（保留首尾）' },
  { value: 'hash', label: '哈希' },
  { value: 'random', label: '随机替换' },
  { value: 'enum', label: '枚举替换' },
  { value: 'redact', label: '固定占位' },
] as const

interface RuleRow {
  column: string
  type: TestDataMaskRule['type']
  keepPrefix: number
  keepSuffix: number
  length: number
  values: string
  replacement: string
}

const appStore = useAppStore()
const projectId = computed(() => appStore.currentProjectId ?? null)
const schemes = ref<TestDataMaskScheme[]>([])
const samples = ref<TestDataSample[]>([])
const uploading = ref(false)
const loading = ref(false)
const selectedScheme = ref<number | null>(null)
const retentionDays = ref(30)
const sampleName = ref('')
const file = ref<File | null>(null)
const uploadRef = ref<UploadInstance>()

const schemeDialog = ref(false)
const schemeSaving = ref(false)
const editingSchemeId = ref<number | null>(null)
const schemeForm = reactive({
  name: '',
  description: '',
  isDefault: false,
  rules: [] as RuleRow[],
})

function emptyRule(column = '', rule?: TestDataMaskRule): RuleRow {
  return {
    column,
    type: rule?.type ?? 'mask',
    keepPrefix: rule?.keepPrefix ?? 0,
    keepSuffix: rule?.keepSuffix ?? 0,
    length: rule?.length ?? 12,
    values: (rule?.values ?? []).join('，'),
    replacement: rule?.replacement ?? '[已脱敏]',
  }
}

async function reload(): Promise<void> {
  if (!projectId.value) {
    schemes.value = []
    samples.value = []
    return
  }
  loading.value = true
  try {
    const [schemeResult, sampleResult] = await Promise.all([
      testDataApi.maskSchemes(projectId.value),
      testDataApi.samples(projectId.value),
    ])
    schemes.value = schemeResult.list
    samples.value = sampleResult.list
  } finally {
    loading.value = false
  }
}

watch(projectId, () => {
  selectedScheme.value = null
  void reload()
}, { immediate: true })

function onFileChange(uploadFile: UploadFile): void {
  file.value = (uploadFile.raw as File | undefined) ?? null
  if (!sampleName.value) sampleName.value = (uploadFile.name || '').replace(/\.[^.]+$/, '')
}

function onExceed(): void {
  ElMessage.warning('一次只能导入一个样本，请先移除已选文件')
}

async function importSample(): Promise<void> {
  if (!projectId.value) {
    ElMessage.warning('请先选择项目')
    return
  }
  if (!file.value) {
    ElMessage.warning('请先选择样本文件')
    return
  }
  const form = new FormData()
  form.append('projectId', String(projectId.value))
  form.append('name', sampleName.value.trim() || file.value.name)
  form.append('file', file.value)
  form.append('retentionDays', String(retentionDays.value))
  if (selectedScheme.value) form.append('schemeId', String(selectedScheme.value))
  uploading.value = true
  try {
    const result = await testDataApi.importSample(form)
    const detected = Object.keys(result.detection)
    ElMessage.success(
      `已导入 ${result.rowCount} 行；${detected.length ? `识别并脱敏：${detected.join('、')}` : '未识别到敏感列'}`,
    )
    file.value = null
    sampleName.value = ''
    uploadRef.value?.clearFiles()
    await reload()
  } finally {
    uploading.value = false
  }
}

function ruleCount(scheme: TestDataMaskScheme): number {
  return Object.keys(scheme.rules || {}).length
}

function schemeLabel(sample: TestDataSample): string {
  if (!sample.schemeId) return '自动规则'
  const scheme = schemes.value.find((item) => item.id === sample.schemeId)
  return `${scheme?.name ?? `方案 #${sample.schemeId}`} v${sample.schemeVersion}`
}

function sensitiveText(sample: TestDataSample): string {
  const columns = Object.keys(sample.detection || {})
  return columns.length ? columns.join('、') : '未识别'
}

function dateText(value: string): string {
  return value ? value.slice(0, 10) : '—'
}

function download(sample: TestDataSample, format: 'json' | 'csv'): void {
  if (!projectId.value) return
  window.open(testDataApi.sampleDownloadUrl(sample.id, projectId.value, format), '_blank')
}

function resetSchemeForm(): void {
  editingSchemeId.value = null
  schemeForm.name = ''
  schemeForm.description = ''
  schemeForm.isDefault = false
  schemeForm.rules = [emptyRule()]
}

function openCreateScheme(): void {
  resetSchemeForm()
  schemeDialog.value = true
}

function openEditScheme(scheme: TestDataMaskScheme): void {
  editingSchemeId.value = scheme.id
  schemeForm.name = scheme.name
  schemeForm.description = scheme.description
  schemeForm.isDefault = scheme.isDefault
  schemeForm.rules = Object.entries(scheme.rules || {}).map(([column, rule]) => emptyRule(column, rule))
  schemeDialog.value = true
}

function openSchemeFromSample(sample: TestDataSample): void {
  resetSchemeForm()
  const columns = Object.keys(sample.detection || {})
  schemeForm.name = `${sample.name} 方案`
  schemeForm.rules = (columns.length ? columns : sample.columns.slice(0, 20)).map(
    (column) => emptyRule(column, sample.rules?.[column]),
  )
  schemeDialog.value = true
}

function addRule(): void {
  schemeForm.rules.push(emptyRule())
}

function removeRule(index: number): void {
  schemeForm.rules.splice(index, 1)
}

function rulesPayload(): Record<string, TestDataMaskRule> {
  const rules: Record<string, TestDataMaskRule> = {}
  for (const row of schemeForm.rules) {
    const column = row.column.trim()
    if (!column) continue
    const rule: TestDataMaskRule = { type: row.type }
    if (row.type === 'mask') {
      rule.keepPrefix = row.keepPrefix
      rule.keepSuffix = row.keepSuffix
      rule.char = '*'
    } else if (row.type === 'hash' || row.type === 'random') {
      rule.length = row.length
    } else if (row.type === 'enum') {
      rule.values = row.values.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean)
    } else {
      rule.replacement = row.replacement || '[已脱敏]'
    }
    rules[column] = rule
  }
  return rules
}

async function saveScheme(): Promise<void> {
  if (!projectId.value) return
  if (!schemeForm.name.trim()) {
    ElMessage.warning('请填写方案名称')
    return
  }
  schemeSaving.value = true
  try {
    const payload = {
      projectId: projectId.value,
      name: schemeForm.name.trim(),
      description: schemeForm.description.trim(),
      isDefault: schemeForm.isDefault,
      rules: rulesPayload(),
    }
    const saved = editingSchemeId.value
      ? await testDataApi.updateMaskScheme(editingSchemeId.value, payload)
      : await testDataApi.createMaskScheme(payload)
    selectedScheme.value = saved.id
    schemeDialog.value = false
    ElMessage.success('脱敏方案已保存')
    await reload()
  } finally {
    schemeSaving.value = false
  }
}

async function removeScheme(scheme: TestDataMaskScheme): Promise<void> {
  if (!projectId.value) return
  try {
    await ElMessageBox.confirm(`确认删除脱敏方案「${scheme.name}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  await testDataApi.removeMaskScheme(scheme.id, projectId.value)
  ElMessage.success('方案已删除')
  await reload()
}

async function removeSample(sample: TestDataSample): Promise<void> {
  if (!projectId.value) return
  try {
    await ElMessageBox.confirm(
      `确认删除样本「${sample.name}」？脱敏副本会一并删除。`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  await testDataApi.removeSample(sample.id, projectId.value)
  ElMessage.success('样本已删除')
  await reload()
}

async function cleanup(): Promise<void> {
  if (!projectId.value) return
  const result = await testDataApi.cleanupSamples(projectId.value)
  ElMessage.success(result.count ? `已清理 ${result.count} 个过期样本` : '没有过期样本')
  await reload()
}
</script>

<style scoped>
.tds {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tds-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.tds-head__actions {
  display: flex;
  gap: 8px;
}

.tds-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.tds-local {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.tds-alert {
  margin-bottom: 12px;
}

.tds-tip {
  margin-left: 8px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.tds-rules {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.tds-rule {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--aitp-border);
  border-radius: 8px;
  background: var(--aitp-subtle);
}

.tds-rule__col {
  width: 180px;
}

.tds-rule__type {
  width: 180px;
}

.tds-rule__num {
  width: 90px;
}

.tds-rule__values {
  width: 220px;
}
</style>
