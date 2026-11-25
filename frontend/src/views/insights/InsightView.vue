<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">质量洞察</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="Refresh" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </div>
    <p class="muted page-hint">
      确定性计算，不调用模型：失败聚类按「标题 + 实际结果」归一化签名聚合最近 N 轮；
      影响面结合需求关联与用例依赖反向闭包，并带最近 20 次失败率作为风险权重。
    </p>

    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <el-tab-pane label="跨轮次失败聚类" name="clusters">
        <div class="panel panel-pad">
          <div class="insight-toolbar">
            <span class="muted">统计最近</span>
            <el-select v-model="rounds" style="width: 110px" @change="loadClusters">
              <el-option v-for="n in roundOptions" :key="n" :label="`${n} 轮`" :value="n" />
            </el-select>
            <el-tag size="small" effect="plain">轮次 {{ clusterData?.runCount ?? 0 }}</el-tag>
            <el-tag size="small" type="danger" effect="plain">
              失败 {{ clusterData?.totalFailures ?? 0 }} 条
            </el-tag>
            <el-tag size="small" type="warning" effect="plain">
              聚类 {{ clusterData?.clusters.length ?? 0 }} 组
            </el-tag>
          </div>

          <el-table :data="clusterData?.clusters ?? []" size="small" border>
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="cluster-detail">
                  <div class="cluster-detail__row">
                    <strong>涉及用例：</strong>
                    <span v-if="!row.cases.length" class="muted">-</span>
                    <el-tag
                      v-for="item in row.cases"
                      :key="item.caseId"
                      size="small"
                      effect="plain"
                      class="cluster-case"
                    >
                      {{ item.caseNo }} {{ item.title }}
                    </el-tag>
                  </div>
                  <div class="cluster-detail__row">
                    <strong>来源轮次：</strong>
                    <span class="muted">{{ row.runNames.join('、') || '-' }}</span>
                  </div>
                  <div class="cluster-detail__row">
                    <strong>每轮失败数：</strong>
                    <span class="cluster-series">
                      <i
                        v-for="(count, index) in row.series"
                        :key="index"
                        :style="{ height: `${Math.max(6, count * 10)}px` }"
                        :title="`第 ${index + 1} 轮：${count} 条`"
                      />
                    </span>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="label" label="失败签名" min-width="280" show-overflow-tooltip />
            <el-table-column prop="count" label="出现次数" width="100" align="center" />
            <el-table-column prop="caseCount" label="涉及用例" width="100" align="center" />
            <el-table-column label="趋势" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="trendType(row.trend)" size="small" effect="plain">
                  {{ row.trendLabel }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="lastSeen" label="最近出现" width="180" />
            <template #empty>
              <el-empty :image-size="58" description="最近轮次没有失败记录" />
            </template>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="变更影响面" name="impact">
        <div class="panel panel-pad impact-panel">
          <div class="settings-block__head">
            <h3>选择变更范围</h3>
            <span class="muted">直接变更用例 + 需求关联用例 + 依赖反向闭包</span>
          </div>
          <div class="impact-form">
            <el-select
              v-model="impactCaseIds"
              multiple
              filterable
              collapse-tags
              collapse-tags-tooltip
              placeholder="选择变更用例"
              style="flex: 1; min-width: 280px"
            >
              <el-option
                v-for="item in caseOptions"
                :key="item.id"
                :label="`${item.caseNo} ${item.title}`"
                :value="item.id"
              />
            </el-select>
            <el-select
              v-model="impactRequirementIds"
              multiple
              filterable
              collapse-tags
              collapse-tags-tooltip
              placeholder="选择需求（可选）"
              style="flex: 1; min-width: 240px"
            >
              <el-option
                v-for="item in requirementOptions"
                :key="item.id"
                :label="item.title"
                :value="item.id"
              />
            </el-select>
            <el-button type="primary" :loading="impactLoading" @click="calcImpact">计算影响面</el-button>
          </div>

          <div v-if="impact" class="impact-result">
            <div class="impact-summary">
              <el-tag size="small" effect="plain">直接变更 {{ impact.direct.length }}</el-tag>
              <el-tag size="small" type="warning" effect="plain">依赖波及 {{ impact.affected.length }}</el-tag>
              <el-tag size="small" type="danger" effect="plain">合计 {{ impact.total }}</el-tag>
            </div>
            <el-table :data="[...impact.direct, ...impact.affected]" size="small" border>
              <el-table-column prop="caseNo" label="用例编号" width="140" />
              <el-table-column prop="title" label="用例标题" min-width="240" show-overflow-tooltip />
              <el-table-column prop="reason" label="纳入原因" width="160" />
              <el-table-column label="依赖层级" width="100" align="center">
                <template #default="{ row }">{{ row.depth }}</template>
              </el-table-column>
              <el-table-column label="最近失败率" width="120" align="center">
                <template #default="{ row }">
                  <span :class="row.failureRate > 0 ? 'risk-high' : 'muted'">{{ row.failureRate }}%</span>
                </template>
              </el-table-column>
              <template #empty>
                <span class="muted">没有命中任何用例</span>
              </template>
            </el-table>
          </div>
          <p v-else class="muted impact-empty">选择用例或需求后点「计算影响面」。</p>
        </div>

        <div class="panel panel-pad">
          <div class="settings-block__head">
            <h3>用例依赖</h3>
            <span class="muted">同项目、禁自依赖、禁环；被依赖用例变更时会影响全部下游</span>
            <div class="settings-block__actions">
              <el-button type="primary" size="small" :icon="Plus" @click="openDependencyDialog">
                新增依赖
              </el-button>
            </div>
          </div>
          <el-table :data="dependencies" size="small" border v-loading="dependencyLoading">
            <el-table-column label="用例" min-width="240">
              <template #default="{ row }">{{ row.caseNo }} {{ row.caseTitle }}</template>
            </el-table-column>
            <el-table-column label="依赖" min-width="240">
              <template #default="{ row }">{{ row.dependsOnCaseNo }} {{ row.dependsOnCaseTitle }}</template>
            </el-table-column>
            <el-table-column prop="note" label="备注" min-width="160" show-overflow-tooltip />
            <el-table-column prop="createdAt" label="创建时间" width="180" />
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button link type="danger" @click="removeDependency(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <span class="muted">还没有配置依赖关系</span>
            </template>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dependencyVisible" title="新增用例依赖" width="520px">
      <el-form label-width="90px" @submit.prevent>
        <el-form-item label="用例">
          <el-select v-model="dependencyForm.caseId" filterable placeholder="选择用例" style="width: 100%">
            <el-option
              v-for="item in caseOptions"
              :key="item.id"
              :label="`${item.caseNo} ${item.title}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="依赖用例">
          <el-select v-model="dependencyForm.dependsOnCaseId" filterable placeholder="选择被依赖用例" style="width: 100%">
            <el-option
              v-for="item in caseOptions"
              :key="item.id"
              :label="`${item.caseNo} ${item.title}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="dependencyForm.note" maxlength="200" placeholder="可选，如：共用登录态" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dependencyVisible = false">取消</el-button>
        <el-button type="primary" :loading="dependencySaving" @click="saveDependency">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { caseApi, insightApi, requirementApi } from '@/api'
import type {
  CaseDependency,
  FailureCluster,
  FailureClusterResult,
  ImpactResult,
  Requirement,
  TestCase,
} from '@/api/types'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
const activeTab = ref<'clusters' | 'impact'>('clusters')
const loading = ref(false)
const roundOptions = [3, 5, 10, 20]
const rounds = ref(5)
const clusterData = ref<FailureClusterResult | null>(null)

const caseOptions = ref<Pick<TestCase, 'id' | 'caseNo' | 'title'>[]>([])
const requirementOptions = ref<Pick<Requirement, 'id' | 'title'>[]>([])
const dependencies = ref<CaseDependency[]>([])
const dependencyLoading = ref(false)
const dependencyVisible = ref(false)
const dependencySaving = ref(false)
const dependencyForm = reactive({ caseId: null as number | null, dependsOnCaseId: null as number | null, note: '' })

const impactCaseIds = ref<number[]>([])
const impactRequirementIds = ref<number[]>([])
const impactLoading = ref(false)
const impact = ref<ImpactResult | null>(null)

function trendType(trend: FailureCluster['trend']): 'danger' | 'success' | 'info' {
  if (trend === 'up') return 'danger'
  if (trend === 'down') return 'success'
  return 'info'
}

async function loadClusters(): Promise<void> {
  if (!appStore.currentProjectId) {
    clusterData.value = null
    return
  }
  try {
    clusterData.value = await insightApi.failureClusters({
      projectId: appStore.currentProjectId,
      rounds: rounds.value,
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败聚类失败')
  }
}

async function loadOptions(): Promise<void> {
  if (!appStore.currentProjectId) {
    caseOptions.value = []
    requirementOptions.value = []
    dependencies.value = []
    return
  }
  try {
    const cases = await caseApi.list({ projectId: appStore.currentProjectId, pageSize: 200 })
    caseOptions.value = (cases.list ?? []).map((item) => ({
      id: item.id,
      caseNo: item.caseNo,
      title: item.title,
    }))
    const requirements = await requirementApi.list({ projectId: appStore.currentProjectId, pageSize: 200 })
    requirementOptions.value = (requirements.list ?? []).map((item) => ({ id: item.id, title: item.title }))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载用例 / 需求选项失败')
  }
}

async function loadDependencies(): Promise<void> {
  if (!appStore.currentProjectId) {
    dependencies.value = []
    return
  }
  dependencyLoading.value = true
  try {
    dependencies.value = (await insightApi.dependencies({ projectId: appStore.currentProjectId })).items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载依赖关系失败')
  } finally {
    dependencyLoading.value = false
  }
}

async function reload(): Promise<void> {
  loading.value = true
  try {
    await Promise.all([loadClusters(), loadOptions(), loadDependencies()])
    impact.value = null
  } finally {
    loading.value = false
  }
}

function onTabChange(): void {
  if (activeTab.value === 'impact' && !dependencies.value.length) {
    void loadDependencies()
  }
}

async function calcImpact(): Promise<void> {
  if (!impactCaseIds.value.length && !impactRequirementIds.value.length) {
    ElMessage.warning('请至少选择一个用例或需求')
    return
  }
  impactLoading.value = true
  try {
    impact.value = await insightApi.impact({
      projectId: appStore.currentProjectId,
      caseIds: impactCaseIds.value,
      requirementIds: impactRequirementIds.value,
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '计算影响面失败')
  } finally {
    impactLoading.value = false
  }
}

function openDependencyDialog(): void {
  dependencyForm.caseId = null
  dependencyForm.dependsOnCaseId = null
  dependencyForm.note = ''
  dependencyVisible.value = true
}

async function saveDependency(): Promise<void> {
  if (!dependencyForm.caseId || !dependencyForm.dependsOnCaseId) {
    ElMessage.warning('请选择用例与依赖用例')
    return
  }
  dependencySaving.value = true
  try {
    await insightApi.addDependency({
      projectId: appStore.currentProjectId,
      caseId: dependencyForm.caseId,
      dependsOnCaseId: dependencyForm.dependsOnCaseId,
      note: dependencyForm.note,
    })
    ElMessage.success('依赖已保存')
    dependencyVisible.value = false
    await loadDependencies()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存依赖失败')
  } finally {
    dependencySaving.value = false
  }
}

async function removeDependency(row: CaseDependency): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除「${row.caseNo} 依赖 ${row.dependsOnCaseNo}」后，影响面不再沿这条链扩散。`,
      '删除依赖',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await insightApi.removeDependency(row.id)
    ElMessage.success('依赖已删除')
    await loadDependencies()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除依赖失败')
  }
}

onMounted(reload)
watch(() => appStore.currentProjectId, () => reload())
</script>

<style scoped>
.page-hint {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.7;
}

.insight-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
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

.settings-block__actions {
  margin-left: auto;
}

.cluster-detail {
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12px;
}

.cluster-detail__row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.cluster-case {
  margin: 0;
}

.cluster-series {
  display: inline-flex;
  align-items: flex-end;
  gap: 4px;
  height: 40px;
}

.cluster-series i {
  width: 10px;
  background: var(--el-color-danger);
  border-radius: 2px;
  display: inline-block;
}

.impact-panel {
  margin-bottom: 16px;
}

.impact-form {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.impact-result {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.impact-summary {
  display: flex;
  gap: 8px;
}

.impact-empty {
  margin: 14px 0 0;
  font-size: 12px;
}

.risk-high {
  color: var(--el-color-danger);
  font-weight: 600;
}
</style>
