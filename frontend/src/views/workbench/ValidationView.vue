<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">案例验证沙箱</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建案例</el-button>
        <el-button type="success" :icon="VideoPlay" :loading="runningAll" @click="runAll">
          全量验证
        </el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="sandbox-alert"
      title="验证案例是行为回归夹具：写下输入与期望，跑一遍看实际与期望差在哪；不通过会立刻指出差哪一条。"
    />

    <div v-if="lastRun" class="panel panel-pad run-summary">
      <div class="run-summary__item">
        <span class="muted">总数</span>
        <strong>{{ lastRun.total }}</strong>
      </div>
      <div class="run-summary__item">
        <span class="muted">通过</span>
        <strong class="ok">{{ lastRun.passed }}</strong>
      </div>
      <div class="run-summary__item">
        <span class="muted">失败</span>
        <strong class="bad">{{ lastRun.failed }}</strong>
      </div>
      <div class="run-summary__item">
        <span class="muted">通过率</span>
        <strong>{{ percentText(lastRun.passRate * 100) }}</strong>
      </div>
    </div>

    <div class="panel panel-pad">
      <el-table v-loading="loading" :data="cases" border size="small">
        <el-table-column prop="name" label="案例" min-width="160" />
        <el-table-column prop="inputText" label="输入" min-width="240" show-overflow-tooltip />
        <el-table-column prop="capability" label="主能力" width="140">
          <template #default="scope">
            <el-tag size="small" effect="plain">{{ capabilityText(scope.row.capability) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="期望" width="110" align="center">
          <template #default="scope">
            <el-tooltip placement="top">
              <template #content>
                <div>路由：{{ (scope.row.expectedRoutes ?? []).map(capabilityText).join('、') || '-' }}</div>
                <div>缺项：{{ (scope.row.expectedMissing ?? []).join('、') || '-' }}</div>
                <div>规则：{{ (scope.row.expectedRules ?? []).join('、') || '-' }}</div>
              </template>
              <el-tag size="small" type="info">
                {{ (scope.row.expectedRoutes ?? []).length }}/{{
                  (scope.row.expectedMissing ?? []).length }}/{{
                  (scope.row.expectedRules ?? []).length
                }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="上次结果" width="110">
          <template #default="scope">
            <el-tag v-if="scope.row.lastResult?.passed !== undefined" size="small" :type="scope.row.lastResult.passed ? 'success' : 'danger'">
              {{ scope.row.lastResult.passed ? '通过' : '不通过' }}
            </el-tag>
            <span v-else class="muted">未跑过</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click="runCase(scope.row)">运行</el-button>
            <el-button link @click="openEdit(scope.row)">编辑</el-button>
            <el-button link type="danger" @click="remove(scope.row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">还没有验证案例</span>
        </template>
      </el-table>
    </div>

    <el-dialog v-model="editVisible" :title="form.id ? '编辑验证案例' : '新建验证案例'" width="720px">
      <el-form label-width="100px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="例如：重复回调路由到缺陷分析" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="输入" required>
          <el-input
            v-model="form.inputText"
            type="textarea"
            :rows="4"
            placeholder="要验证的输入，路由会拿它做匹配"
          />
        </el-form-item>
        <el-form-item label="主能力">
          <el-select v-model="form.capability" clearable placeholder="跑完比对时同时执行该能力" style="width: 100%">
            <el-option
              v-for="item in labStore.capabilities"
              :key="item.code"
              :label="item.label"
              :value="item.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="变量">
          <el-input
            v-model="variablesText"
            type="textarea"
            :rows="4"
            class="text-mono"
            placeholder='{"requirement": "..."}'
          />
        </el-form-item>
        <el-form-item label="期望路由">
          <el-input v-model="expectedRoutesText" placeholder="逗号分隔的能力编码" />
        </el-form-item>
        <el-form-item label="期望缺项">
          <el-input v-model="expectedMissingText" placeholder="逗号分隔，命中即算缺项" />
        </el-form-item>
        <el-form-item label="期望规则">
          <el-input v-model="expectedRulesText" placeholder="逗号分隔的行为安全规则" />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio value="published">启用</el-radio>
            <el-radio value="disabled">停用</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resultVisible" title="比对结果" width="820px">
      <template v-if="result">
        <el-alert
          :type="result.passed ? 'success' : 'error'"
          show-icon
          :closable="false"
          :title="result.passed ? '与期望一致' : `与期望有 ${result.errors.length} 处差异`"
        />
        <el-descriptions :column="2" border class="result-desc">
          <el-descriptions-item label="模型">{{ result.model || '-' }}</el-descriptions-item>
          <el-descriptions-item label="耗时">{{ result.latencyMs }} ms</el-descriptions-item>
          <el-descriptions-item label="路由来源">{{ result.route.source || '-' }}</el-descriptions-item>
          <el-descriptions-item label="实际路由">
            {{ result.route.actual.map((item) => capabilityText(item.capability)).join('、') || '-' }}
          </el-descriptions-item>
        </el-descriptions>

        <h4 class="result-title">路由命中</h4>
        <el-table :data="result.route.actual" border size="small">
          <el-table-column label="能力" width="160">
            <template #default="scope">{{ capabilityText(scope.row.capability) }}</template>
          </el-table-column>
          <el-table-column prop="score" label="分" width="70" align="right" />
          <el-table-column prop="reason" label="理由" min-width="200" />
          <el-table-column label="期望" width="80" align="center">
            <template #default="scope">
              <el-tag size="small" :type="result.route.expected.includes(scope.row.capability) ? 'success' : 'info'">
                {{ result.route.expected.includes(scope.row.capability) ? '命中' : '未列' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <p v-if="result.route.missed.length" class="missed">
          期望命中但实际没有：{{ result.route.missed.map(capabilityText).join('、') }}
        </p>

        <h4 class="result-title">信息缺项</h4>
        <el-table :data="result.missing.actual" border size="small">
          <el-table-column prop="label" label="缺项" width="160" />
          <el-table-column prop="detail" label="说明" min-width="220" />
          <el-table-column label="期望" width="80" align="center">
            <template #default="scope">
              <el-tag size="small" :type="result.missing.expected.includes(scope.row.label) ? 'success' : 'info'">
                {{ result.missing.expected.includes(scope.row.label) ? '命中' : '未列' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <p v-if="result.missing.missed.length" class="missed">
          期望命中但实际没有：{{ result.missing.missed.join('、') }}
        </p>

        <h4 class="result-title">行为安全规则</h4>
        <div>
          <el-tag v-for="rule in result.rules.actual" :key="rule" size="small" class="rule-tag">
            {{ rule }}
          </el-tag>
          <span v-if="!result.rules.actual.length" class="muted">未命中规则</span>
        </div>
        <p v-if="result.rules.missed.length" class="missed">
          期望命中但实际没有：{{ result.rules.missed.join('、') }}
        </p>

        <h4 class="result-title">差异清单</h4>
        <ul class="plain-list">
          <li v-for="(item, index) in result.errors" :key="index">{{ item }}</li>
          <li v-if="!result.errors.length" class="muted">没有差异</li>
        </ul>
      </template>
      <template #footer>
        <el-button @click="resultVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, VideoPlay } from '@element-plus/icons-vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { validationApi } from '@/api'
import { percentText } from '@/utils/format'
import type { ValidationCase, ValidationResult } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useLabStore } from '@/stores/lab'

interface CaseForm {
  id: number | null
  name: string
  description: string
  inputText: string
  capability: string
  variables: Record<string, unknown>
  expectedRoutes: string[]
  expectedMissing: string[]
  expectedRules: string[]
  status: string
}

const labStore = useLabStore()
const appStore = useAppStore()

/** 能力编码 → 中文名称；页面只展示中文，编码仍保留在数据里。 */
const capabilityLabels = computed(() => {
  const map = new Map<string, string>()
  for (const item of labStore.capabilities) {
    map.set(item.code, item.label)
  }
  for (const item of labStore.allCapabilities) {
    if (!map.has(item.code)) {
      map.set(item.code, item.label)
    }
  }
  return map
})

function capabilityText(code: string | null | undefined): string {
  if (!code) {
    return '-'
  }
  return capabilityLabels.value.get(code) ?? code
}

const cases = ref<ValidationCase[]>([])
const loading = ref(false)
const saving = ref(false)
const runningAll = ref(false)
const editVisible = ref(false)
const resultVisible = ref(false)
const result = ref<ValidationResult | null>(null)
const lastRun = ref<{
  total: number
  passed: number
  failed: number
  passRate: number
  results: Array<{ id: number; name: string; passed: boolean; errors: string[] }>
} | null>(null)

const variablesText = ref('{}')
const expectedRoutesText = ref('')
const expectedMissingText = ref('')
const expectedRulesText = ref('')

const form = reactive<CaseForm>({
  id: null,
  name: '',
  description: '',
  inputText: '',
  capability: '',
  variables: {},
  expectedRoutes: [],
  expectedMissing: [],
  expectedRules: [],
  status: 'published',
})

function splitList(text: string): string[] {
  return text
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await validationApi.cases({ pageSize: 100 })
    cases.value = data.list ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function resetForm(): void {
  form.id = null
  form.name = ''
  form.description = ''
  form.inputText = ''
  form.capability = ''
  form.variables = {}
  form.expectedRoutes = []
  form.expectedMissing = []
  form.expectedRules = []
  form.status = 'published'
  variablesText.value = '{}'
  expectedRoutesText.value = ''
  expectedMissingText.value = ''
  expectedRulesText.value = ''
}

function openCreate(): void {
  resetForm()
  editVisible.value = true
}

function openEdit(row: ValidationCase): void {
  form.id = row.id
  form.name = row.name
  form.description = row.description
  form.inputText = row.inputText
  form.capability = row.capability
  form.variables = { ...(row.variables ?? {}) }
  form.expectedRoutes = [...(row.expectedRoutes ?? [])]
  form.expectedMissing = [...(row.expectedMissing ?? [])]
  form.expectedRules = [...(row.expectedRules ?? [])]
  form.status = row.status
  variablesText.value = JSON.stringify(form.variables, null, 2)
  expectedRoutesText.value = form.expectedRoutes.join(',')
  expectedMissingText.value = form.expectedMissing.join(',')
  expectedRulesText.value = form.expectedRules.join(',')
  editVisible.value = true
}

async function save(): Promise<void> {
  if (!form.name.trim() || !form.inputText.trim()) {
    ElMessage.warning('名称与输入为必填')
    return
  }
  let variables: Record<string, unknown>
  try {
    const parsed = JSON.parse(variablesText.value || '{}') as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      ElMessage.warning('变量必须是 JSON 对象')
      return
    }
    variables = parsed as Record<string, unknown>
  } catch {
    ElMessage.warning('变量不是合法 JSON')
    return
  }
  const payload = {
    name: form.name.trim(),
    description: form.description,
    inputText: form.inputText,
    capability: form.capability,
    variables,
    expectedRoutes: splitList(expectedRoutesText.value),
    expectedMissing: splitList(expectedMissingText.value),
    expectedRules: splitList(expectedRulesText.value),
    status: form.status,
  }
  saving.value = true
  try {
    if (form.id) {
      await validationApi.updateCase(form.id, payload)
    } else {
      await validationApi.createCase(payload)
    }
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: ValidationCase): Promise<void> {
  try {
    await validationApi.removeCase(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

async function runCase(row: ValidationCase): Promise<void> {
  try {
    result.value = await validationApi.runCase(row.id, {
      projectId: appStore.currentProjectId ?? undefined,
    })
    resultVisible.value = true
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '运行失败')
  }
}

async function runAll(): Promise<void> {
  runningAll.value = true
  try {
    lastRun.value = await validationApi.runAll({
      projectId: appStore.currentProjectId ?? undefined,
    })
    ElMessage.success(
      `全量验证完成：通过 ${lastRun.value.passed}/${lastRun.value.total}`,
    )
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '全量验证失败')
  } finally {
    runningAll.value = false
  }
}

onMounted(async () => {
  await labStore.loadCapabilities()
  void labStore.loadAllCapabilities()
  await load()
})
</script>

<style scoped>
.sandbox-alert {
  margin-bottom: 12px;
}

.run-summary {
  display: flex;
  gap: 28px;
  margin-bottom: 12px;
  padding: 12px 16px;
}

.run-summary__item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.run-summary__item strong {
  font-size: 18px;
}

.run-summary__item .ok {
  color: var(--el-color-success);
}

.run-summary__item .bad {
  color: var(--el-color-danger);
}

.result-desc {
  margin-top: 12px;
}

.result-title {
  margin: 16px 0 8px;
  font-size: 14px;
}

.missed {
  color: var(--el-color-danger);
  margin: 8px 0 0;
  font-size: 13px;
}

.rule-tag {
  margin-right: 6px;
}

.plain-list {
  margin: 0;
  padding-left: 18px;
  line-height: 1.9;
}
</style>
