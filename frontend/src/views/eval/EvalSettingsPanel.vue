<template>
  <div class="eval-settings-panel">
  <div class="eval-settings-grid">
      <div class="panel panel-pad">
        <div class="toolbar">
          <h4 class="quality-title" style="margin: 0">评测配置</h4>
          <div class="spacer" />
          <el-button type="primary" :icon="Plus" @click="openConfigCreate">新建配置</el-button>
        </div>
        <el-table v-loading="configLoading" :data="configRows" border size="small">
          <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip />
          <el-table-column label="模型" min-width="150" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.provider }} / {{ scope.row.model || '-' }}</template>
          </el-table-column>
          <el-table-column label="参数" width="170">
            <template #default="scope">
              T={{ scope.row.temperature }} · p={{ scope.row.topP }} · {{ scope.row.maxTokens }}
            </template>
          </el-table-column>
          <el-table-column label="基线" width="70" align="center">
            <template #default="scope">
              <el-tag v-if="scope.row.isBaseline" size="small" type="success" effect="plain">基线</el-tag>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="引用" width="70" align="center">
            <template #default="scope">
              <el-tag v-if="scope.row.referenced" size="small" type="warning" effect="plain">已引用</el-tag>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="scope">
              <el-button link type="primary" :disabled="scope.row.referenced" @click="openConfigEdit(scope.row)">编辑</el-button>
            </template>
          </el-table-column>
          <template #empty><span class="muted">暂无配置；不建配置也可直接跑批</span></template>
        </el-table>
      </div>

      <div class="panel panel-pad">
        <div class="toolbar">
          <h4 class="quality-title" style="margin: 0">判定器</h4>
          <div class="spacer" />
          <el-button @click="loadJudges">刷新</el-button>
        </div>
        <el-table v-loading="judgeLoading" :data="judgeRows" border size="small">
          <el-table-column prop="name" label="名称" min-width="130" show-overflow-tooltip />
          <el-table-column label="策略" width="100">
            <template #default="scope">{{ labelOf(EVAL_JUDGE_STRATEGY_LABELS, scope.row.strategy) }} · {{ scope.row.panelSize }}</template>
          </el-table-column>
          <el-table-column label="一致率" width="90">
            <template #default="scope">{{ pct(scope.row.agreementRate) }}</template>
          </el-table-column>
          <el-table-column label="κ" width="80">
            <template #default="scope">{{ scope.row.kappa ?? '-' }}</template>
          </el-table-column>
          <el-table-column label="放行错误率" width="110">
            <template #default="scope">{{ pct(scope.row.falsePassRate) }}</template>
          </el-table-column>
          <el-table-column label="校准" width="90">
            <template #default="scope">
              <el-tag size="small" :type="judgeCalibrated(scope.row) ? 'success' : 'info'" effect="plain">
                {{ judgeCalibrated(scope.row) ? '可用' : '未达标' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="130">
            <template #default="scope">
              <el-button link type="primary" @click="openCalibrate(scope.row)">校准</el-button>
              <el-button link @click="openCalibrations(scope.row)">历史</el-button>
            </template>
          </el-table-column>
          <template #empty><span class="muted">暂无判定器；跑批默认走规则断言</span></template>
        </el-table>
      </div>
    </div>
  

<el-dialog v-model="configDialog" :title="configForm.id ? '编辑评测配置' : '新建评测配置'" width="720px">
  <el-alert
    v-if="configForm.id"
    type="info"
    :closable="false"
    show-icon
    class="config-alert"
    title="配置已被跑批引用后不可修改；需要调整请复制为新配置"
  />
  <el-form label-width="92px">
    <div class="config-section-title">评测对象</div>
    <el-form-item label="名称" required>
      <el-input
        v-model="configForm.name"
        maxlength="100"
        placeholder="按服务商与模型自动生成，可修改"
        @input="configNameTouched = true"
      />
    </el-form-item>
    <div class="config-row">
      <el-form-item label="AI 档案">
        <div class="field-block">
          <el-select
            v-model="configForm.profileId"
            clearable
            filterable
            placeholder="可选：从档案带出"
            style="width: 100%"
            @change="onConfigProfileChange"
          >
            <el-option
              v-for="item in configProfiles"
              :key="item.id"
              :label="`${item.name} · ${item.provider}/${item.model}`"
              :value="item.id"
            />
          </el-select>
          <div class="field-hint">选定后自动带出服务商与模型</div>
        </div>
      </el-form-item>
      <el-form-item label="服务商">
        <div class="field-block">
          <el-select
            v-model="configForm.provider"
            filterable
            allow-create
            default-first-option
            placeholder="选择服务商"
            style="width: 100%"
            @change="onConfigProviderChange"
          >
            <el-option v-for="item in configProviders" :key="item.name" :label="item.label" :value="item.name" />
          </el-select>
          <div class="field-hint">自定义网关可直接输入</div>
        </div>
      </el-form-item>
    </div>
    <div class="config-row">
      <el-form-item label="模型">
        <div class="field-block">
          <el-select
            v-model="configForm.model"
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入模型名"
            style="width: 100%"
            @change="onConfigModelChange"
          >
            <el-option v-for="item in configModelOptions" :key="item" :label="item" :value="item" />
          </el-select>
          <div class="field-hint">候选来自所选服务商与已有档案</div>
        </div>
      </el-form-item>
      <el-form-item label="基线">
        <div class="field-block">
          <el-switch v-model="configForm.isBaseline" />
          <div class="field-hint">同项目仅一个基线，对比跑批以它为参照</div>
        </div>
      </el-form-item>
    </div>

    <div class="config-section-title">采样参数</div>
    <el-form-item label="温度">
      <div class="field-block">
        <div class="sampling-row">
          <el-slider v-model="configForm.temperature" :min="0" :max="2" :step="0.1" class="sampling-slider" />
          <el-input-number
            v-model="configForm.temperature"
            :min="0"
            :max="2"
            :step="0.1"
            :precision="1"
            controls-position="right"
            style="width: 104px"
          />
        </div>
        <div class="field-hint">越低越稳定，评测建议 0–0.3</div>
      </div>
    </el-form-item>
    <el-form-item label="topP">
      <div class="field-block">
        <div class="sampling-row">
          <el-slider v-model="configForm.topP" :min="0.05" :max="1" :step="0.05" class="sampling-slider" />
          <el-input-number
            v-model="configForm.topP"
            :min="0.05"
            :max="1"
            :step="0.05"
            :precision="2"
            controls-position="right"
            style="width: 104px"
          />
        </div>
        <div class="field-hint">默认 1 不截断，通常只调温度</div>
      </div>
    </el-form-item>
    <div class="config-row">
      <el-form-item label="maxTokens">
        <div class="field-block">
          <el-input-number
            v-model="configForm.maxTokens"
            :min="1"
            :step="256"
            controls-position="right"
            style="width: 100%"
          />
          <div class="field-hint">单次回答长度上限</div>
        </div>
      </el-form-item>
      <el-form-item label="随机种子">
        <div class="field-block">
          <el-input-number
            v-model="configForm.seed"
            :min="0"
            :step="1"
            controls-position="right"
            placeholder="留空随机"
            style="width: 100%"
          />
          <div class="field-hint">固定后同配置可复现</div>
        </div>
      </el-form-item>
    </div>

    <div class="config-section-title">追溯与备注（可选）</div>
    <div class="config-row">
      <el-form-item label="提示词编码">
        <el-input v-model="configForm.promptCode" placeholder="如 QA_ANSWER_V2" />
      </el-form-item>
      <el-form-item label="提示词版本">
        <el-input v-model="configForm.promptVersion" placeholder="如 v2.3" />
      </el-form-item>
    </div>
    <el-form-item label="备注">
      <el-input v-model="configForm.note" type="textarea" :rows="2" maxlength="300" show-word-limit />
    </el-form-item>
  </el-form>
  <template #footer>
    <el-button @click="configDialog = false">取消</el-button>
    <el-button type="primary" :loading="configSaving" @click="saveConfig">保存</el-button>
  </template>
</el-dialog>

<el-dialog v-model="calibrateDialog" title="判定器校准（人评对拍）" width="760px">
  <el-form label-width="90px">
    <el-form-item label="判定器">
      <span>{{ calibrateTarget?.name }}</span>
    </el-form-item>
    <el-form-item label="跑批">
      <el-select v-model="calibrateRunId" placeholder="选择已完成跑批" style="width: 100%" @change="loadCalibrateItems">
        <el-option v-for="item in calibrateRuns" :key="item.id" :label="`#${item.id} · ${pct(item.successRate)}`" :value="item.id" />
      </el-select>
    </el-form-item>
  </el-form>
  <div class="muted" style="margin-bottom: 8px">
    只统计已回填人评的条目；先在跑批详情里给难例做人评，再回来对拍。
  </div>
  <el-table v-if="calibrateItems.length" :data="calibrateItems" size="small" border max-height="260">
    <el-table-column prop="caseNo" label="用例" width="110" />
    <el-table-column label="人评" width="100">
      <template #default="scope">
        <el-tag size="small" :type="scope.row.human === 'pass' ? 'success' : 'danger'" effect="plain">
          {{ scope.row.human === 'pass' ? '通过' : '未通过' }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column prop="judgeLabel" label="judge 判定" width="120" />
  </el-table>
  <el-empty v-else-if="calibrateRunId" description="该跑批还没有人评回填" :image-size="70" />
  <template v-if="calibrateResult">
    <el-alert
      :type="calibrateResult.calibrated ? 'success' : 'warning'"
      :closable="false"
      show-icon
      :title="`校准${calibrateResult.calibrated ? '可用' : '未达标'}：一致率 ${pct(calibrateResult.agreement)}，κ=${calibrateResult.kappa ?? '-'}，false-pass=${pct(calibrateResult.falsePassRate)}，样本 ${calibrateResult.sampleSize}`"
    />
  </template>
  <template #footer>
    <el-button @click="calibrateDialog = false">关闭</el-button>
    <el-button type="primary" :loading="calibrating" :disabled="!calibrateItems.length" @click="submitCalibrate">
      开始校准
    </el-button>
  </template>
</el-dialog>

<el-dialog v-model="calibrationHistoryDialog" title="校准历史" width="760px">
  <el-table :data="calibrationHistory" size="small" border>
    <el-table-column label="时间" width="170">
      <template #default="scope">{{ dateTimeText(scope.row.created_at) }}</template>
    </el-table-column>
    <el-table-column label="跑批" width="80">
      <template #default="scope">#{{ scope.row.run_id }}</template>
    </el-table-column>
    <el-table-column prop="sample_size" label="样本" width="80" />
    <el-table-column label="一致率" width="90">
      <template #default="scope">{{ pct(scope.row.agreement_after) }}</template>
    </el-table-column>
    <el-table-column label="κ" width="80">
      <template #default="scope">{{ scope.row.kappa_after ?? '-' }}</template>
    </el-table-column>
    <el-table-column label="false-pass" width="110">
      <template #default="scope">{{ pct(scope.row.false_pass_rate) }}</template>
    </el-table-column>
    <template #empty><span class="muted">还没有校准记录</span></template>
  </el-table>
</el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { settingsApi } from '@/api'
import { evalApi } from '@/api/eval'
import type { EvalCalibration, EvalCalibrationResult, EvalConfig, EvalJudge, EvalRun } from '@/api/eval'
import type { AiProfile } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { dateTimeText } from '@/utils/format'
import { EVAL_JUDGE_STRATEGY_LABELS } from '@/constants/eval'

const appStore = useAppStore()

function pct(value: number | null | undefined): string {
  return typeof value === 'number' ? `${(value * 100).toFixed(2)}%` : '-'
}

function labelOf(map: Record<string, string>, key: string): string {
  return map[key] ?? key
}

// ---------------------------------------------------------------- 配置 / 判定器

const configRows = ref<EvalConfig[]>([])
const configLoading = ref(false)
const configDialog = ref(false)
const configSaving = ref(false)
const configForm = reactive({
  id: null as number | null,
  profileId: null as number | null,
  name: '',
  provider: '',
  model: '',
  temperature: 0,
  topP: 1,
  maxTokens: 4096,
  seed: null as number | null,
  promptCode: '',
  promptVersion: '',
  isBaseline: false,
  note: '',
})

interface ProviderOption {
  name: string
  label: string
  defaultModel: string
}

const configProfiles = ref<AiProfile[]>([])
const configProviders = ref<ProviderOption[]>([])
const configNameTouched = ref(false)
let configCatalogLoaded = false

async function ensureConfigCatalog(): Promise<void> {
  if (configCatalogLoaded) return
  configCatalogLoaded = true
  try {
    const [profiles, providers] = await Promise.all([settingsApi.profiles(), settingsApi.providers()])
    configProfiles.value = (profiles as AiProfile[]) ?? []
    configProviders.value = (providers as ProviderOption[]) ?? []
  } catch {
    configCatalogLoaded = false
  }
}

const configModelOptions = computed<string[]>(() => {
  const options = new Set<string>()
  const spec = configProviders.value.find((item) => item.name === configForm.provider)
  if (spec?.defaultModel) options.add(spec.defaultModel)
  for (const profile of configProfiles.value) {
    if (!configForm.provider || profile.provider === configForm.provider) options.add(profile.model)
  }
  if (configForm.model) options.add(configForm.model)
  return [...options].filter(Boolean)
})

function suggestConfigName(): void {
  if (configNameTouched.value) return
  const label = configProviders.value.find((item) => item.name === configForm.provider)?.label || configForm.provider
  configForm.name = [label, configForm.model].filter(Boolean).join(' / ')
}

function onConfigProfileChange(value: number | string | null): void {
  if (value === null || value === '' || value === undefined) {
    configForm.profileId = null
    return
  }
  const profile = configProfiles.value.find((item) => Number(item.id) === Number(value))
  if (!profile) return
  configForm.profileId = Number(value)
  configForm.provider = profile.provider
  configForm.model = profile.model
  suggestConfigName()
}

function onConfigProviderChange(value: string): void {
  const spec = configProviders.value.find((item) => item.name === value)
  if (spec?.defaultModel) configForm.model = spec.defaultModel
  if (configForm.profileId !== null) {
    const profile = configProfiles.value.find((item) => Number(item.id) === Number(configForm.profileId))
    if (!profile || profile.provider !== value) configForm.profileId = null
  }
  suggestConfigName()
}

function onConfigModelChange(value: string): void {
  if (configForm.profileId !== null) {
    const profile = configProfiles.value.find((item) => Number(item.id) === Number(configForm.profileId))
    if (!profile || profile.model !== value) configForm.profileId = null
  }
  suggestConfigName()
}

function prefillConfigFromDefaultProfile(): void {
  if (configForm.provider || configForm.model) return
  const preferred =
    configProfiles.value.find((item) => item.isDefault && item.isActive) ||
    configProfiles.value.find((item) => item.isActive)
  if (!preferred) return
  configForm.profileId = Number(preferred.id)
  configForm.provider = preferred.provider
  configForm.model = preferred.model
  suggestConfigName()
}

async function loadConfigs(): Promise<void> {
  configLoading.value = true
  try {
    const page = await evalApi.configs({
      projectId: appStore.currentProjectId ?? undefined,
      pageNum: 1,
      pageSize: 100,
    })
    configRows.value = page.list ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载配置失败')
  } finally {
    configLoading.value = false
  }
}

async function openConfigCreate(): Promise<void> {
  configForm.id = null
  configForm.profileId = null
  configForm.name = ''
  configForm.provider = ''
  configForm.model = ''
  configForm.temperature = 0
  configForm.topP = 1
  configForm.maxTokens = 4096
  configForm.seed = null
  configForm.promptCode = ''
  configForm.promptVersion = ''
  configForm.isBaseline = false
  configForm.note = ''
  configNameTouched.value = false
  configDialog.value = true
  await ensureConfigCatalog()
  prefillConfigFromDefaultProfile()
}

function openConfigEdit(row: EvalConfig): void {
  configForm.id = row.id
  configForm.profileId = row.profileId
  configForm.name = row.name
  configForm.provider = row.provider
  configForm.model = row.model
  configForm.temperature = row.temperature
  configForm.topP = row.topP
  configForm.maxTokens = row.maxTokens
  configForm.seed = row.seed
  configForm.promptCode = row.promptCode
  configForm.promptVersion = row.promptVersion
  configForm.isBaseline = row.isBaseline
  configForm.note = row.note
  configNameTouched.value = true
  void ensureConfigCatalog()
  configDialog.value = true
}

async function saveConfig(): Promise<void> {
  if (!configForm.name.trim()) {
    ElMessage.warning('配置名称不能为空')
    return
  }
  if (!configForm.provider.trim() || !configForm.model.trim()) {
    ElMessage.warning('请先选择或输入服务商与模型')
    return
  }
  configSaving.value = true
  try {
    if (configForm.id) {
      await evalApi.updateConfig(configForm.id, {
        name: configForm.name,
        profileId: configForm.profileId,
        provider: configForm.provider,
        model: configForm.model,
        temperature: configForm.temperature,
        topP: configForm.topP,
        maxTokens: configForm.maxTokens,
        seed: configForm.seed,
        promptCode: configForm.promptCode,
        promptVersion: configForm.promptVersion,
        isBaseline: configForm.isBaseline,
        note: configForm.note,
      })
    } else {
      if (!appStore.currentProjectId) {
        ElMessage.warning('请先选择项目')
        return
      }
      await evalApi.createConfig({
        projectId: appStore.currentProjectId,
        name: configForm.name,
        profileId: configForm.profileId,
        provider: configForm.provider,
        model: configForm.model,
        temperature: configForm.temperature,
        topP: configForm.topP,
        maxTokens: configForm.maxTokens,
        seed: configForm.seed,
        promptCode: configForm.promptCode,
        promptVersion: configForm.promptVersion,
        isBaseline: configForm.isBaseline,
        note: configForm.note,
      })
    }
    ElMessage.success('已保存')
    configDialog.value = false
    await loadConfigs()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    configSaving.value = false
  }
}

const judgeRows = ref<EvalJudge[]>([])
const judgeLoading = ref(false)
const calibrateDialog = ref(false)
const calibrating = ref(false)
const calibrateTarget = ref<EvalJudge | null>(null)
const calibrateRunId = ref<number | null>(null)
const calibrateRuns = ref<EvalRun[]>([])
const calibrateItems = ref<Array<{ resultId: number; caseNo: string; human: string; judgeLabel: string }>>([])
const calibrateResult = ref<EvalCalibrationResult | null>(null)
const calibrationHistoryDialog = ref(false)
const calibrationHistory = ref<EvalCalibration[]>([])

async function loadJudges(): Promise<void> {
  judgeLoading.value = true
  try {
    const page = await evalApi.judges({
      projectId: appStore.currentProjectId ?? undefined,
      pageNum: 1,
      pageSize: 100,
    })
    judgeRows.value = page.list ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载判定器失败')
  } finally {
    judgeLoading.value = false
  }
}

function judgeCalibrated(row: EvalJudge): boolean {
  return Boolean(
    row.calibratedAt &&
      row.agreementRate !== null &&
      row.agreementRate >= 0.8 &&
      row.kappa !== null &&
      row.kappa >= 0.6 &&
      (row.falsePassRate === null || row.falsePassRate <= 0.05),
  )
}

async function openCalibrate(row: EvalJudge): Promise<void> {
  calibrateTarget.value = row
  calibrateRunId.value = null
  calibrateItems.value = []
  calibrateResult.value = null
  calibrateDialog.value = true
  try {
    const page = await evalApi.runs({
      projectId: appStore.currentProjectId ?? undefined,
      status: 'finished',
      pageNum: 1,
      pageSize: 100,
    })
    calibrateRuns.value = page.list ?? []
  } catch {
    calibrateRuns.value = []
  }
}

async function loadCalibrateItems(): Promise<void> {
  calibrateResult.value = null
  if (!calibrateRunId.value) {
    calibrateItems.value = []
    return
  }
  try {
    const detail = await evalApi.run(calibrateRunId.value)
    calibrateItems.value = detail.results
      .filter((row) => row.human_verdict === 'pass' || row.human_verdict === 'fail')
      .map((row) => {
        let judgeLabel = row.verdict
        try {
          const parsed = JSON.parse(row.judge_json || '{}') as Record<string, unknown>
          if (parsed.judge_label === 'pass' || parsed.judge_label === 'fail') {
            judgeLabel = String(parsed.judge_label)
          }
        } catch {
          // judge_json 异常时退回 verdict 展示
        }
        return { resultId: row.id, caseNo: row.case_no, human: row.human_verdict as string, judgeLabel }
      })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载人评样本失败')
  }
}

async function submitCalibrate(): Promise<void> {
  if (!calibrateTarget.value || !calibrateRunId.value) return
  calibrating.value = true
  try {
    calibrateResult.value = await evalApi.calibrate(calibrateTarget.value.id, {
      runId: calibrateRunId.value,
      items: calibrateItems.value.map((item) => ({ resultId: item.resultId, human: item.human })),
    })
    ElMessage.success(`校准完成：样本 ${calibrateResult.value.sampleSize}`)
    await loadJudges()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '校准失败')
  } finally {
    calibrating.value = false
  }
}

async function openCalibrations(row: EvalJudge): Promise<void> {
  calibrationHistoryDialog.value = true
  calibrationHistory.value = []
  try {
    const result = await evalApi.calibrations(row.id)
    calibrationHistory.value = result.list ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载校准历史失败')
  }
}

onMounted(() => {
  void loadConfigs()
  void loadJudges()
})
</script>

<style scoped>
.eval-settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
@media (max-width: 1280px) {
  .eval-settings-grid {
    grid-template-columns: 1fr;
  }
}
.config-alert {
  margin-bottom: 14px;
}
.config-section-title {
  margin: 2px 0 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.config-section-title:not(:first-of-type) {
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.config-row {
  display: flex;
  gap: 16px;
}
.config-row > .el-form-item {
  flex: 1 1 0;
  min-width: 0;
}
.field-block {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.sampling-row {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
}
.sampling-slider {
  flex: 1;
  min-width: 0;
}
.field-hint {
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}
.quality-title {
  margin: 18px 0 10px;
}
</style>
