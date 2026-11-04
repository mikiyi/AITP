<template>
  <div class="smart-entry panel panel-pad">
    <div class="smart-entry__row">
      <el-input
        v-model="text"
        type="textarea"
        :rows="2"
        resize="none"
        placeholder="描述你要做的事（例：支付回调重复推送导致金额翻倍），自动匹配 AI 能力与功能点"
        @keyup.ctrl.enter="route"
      />
      <div class="smart-entry__actions">
        <el-button type="primary" :icon="Search" :loading="loading" @click="route">智能匹配</el-button>
        <span class="muted smart-entry__hint">Ctrl + Enter 快捷匹配</span>
      </div>
    </div>

    <div v-if="result" class="smart-entry__result">
      <div class="smart-entry__group">
        <div class="smart-entry__group-title">
          匹配的 AI 能力
          <el-tag v-if="result.source === 'fallback'" size="small" type="info" effect="plain">
            离线关键词匹配
          </el-tag>
        </div>
        <div class="smart-entry__cards">
          <button
            v-for="item in result.capabilityRoutes"
            :key="item.capability"
            class="smart-entry__card"
            type="button"
            @click="openCapability(item)"
          >
            <span class="smart-entry__card-title">{{ item.label }}</span>
            <span class="smart-entry__card-reason">{{ item.reason }}</span>
            <span class="smart-entry__card-score">{{ Math.round(item.score * 100) }} 分</span>
          </button>
          <span v-if="!result.capabilityRoutes.length" class="muted">未匹配到能力候选</span>
        </div>
      </div>

      <div class="smart-entry__group">
        <div class="smart-entry__group-title">归属功能点</div>
        <div class="smart-entry__cards">
          <button
            v-for="item in result.moduleRoutes"
            :key="item.moduleId"
            class="smart-entry__card"
            type="button"
            @click="openModule(item)"
          >
            <span class="smart-entry__card-title">{{ item.path }}</span>
            <span class="smart-entry__card-reason">{{ item.reason }}</span>
            <span class="smart-entry__card-score">{{ Math.round(item.score * 100) }} 分</span>
          </button>
          <span v-if="!result.moduleRoutes.length" class="muted">未匹配到功能点候选</span>
        </div>
      </div>

      <div class="smart-entry__group">
        <div class="smart-entry__group-title">
          执行前预检
          <el-tag size="small" :type="preflight?.ready ? 'success' : 'warning'" effect="plain">
            {{ preflight?.ready ? '可执行' : '有阻断项' }}
          </el-tag>
        </div>
        <div v-if="preflight" class="preflight">
          <div class="preflight__bar">
            <span class="muted">整体完整度</span>
            <el-progress
              :percentage="preflight.overallCompleteness * 100"
              :format="percentText"
              :stroke-width="10"
              :status="preflight.ready ? 'success' : 'exception'"
              style="flex: 1"
            />
            <el-tag size="small" :type="preflight.modelReady ? 'success' : 'danger'" effect="plain">
              {{ preflight.modelReady ? '模型可用' : '无可用模型' }}
            </el-tag>
          </div>

          <div class="preflight__order">
            <span class="muted">执行顺序</span>
            <template v-for="(code, index) in preflight.order" :key="code">
              <el-tag size="small" effect="plain">{{ index + 1 }}. {{ labelOf(code) }}</el-tag>
              <span v-if="index < preflight.order.length - 1" class="arrow">→</span>
            </template>
            <span v-if="!preflight.order.length" class="muted">无</span>
          </div>

          <el-table :data="preflight.tasks" size="small" border>
            <el-table-column prop="label" label="任务" width="130" />
            <el-table-column label="角色" width="76" align="center">
              <template #default="scope">
                <el-tag size="small" :type="scope.row.role === 'primary' ? 'primary' : 'info'" effect="plain">
                  {{ scope.row.role === 'primary' ? '主任务' : '辅助' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="完整度" width="130">
              <template #default="scope">
                <el-progress
                  :percentage="scope.row.completeness * 100"
                  :format="percentText"
                  :stroke-width="8"
                  :status="scope.row.ready ? 'success' : 'exception'"
                />
              </template>
            </el-table-column>
            <el-table-column label="缺项" min-width="180">
              <template #default="scope">
                <span v-if="scope.row.missing.length" class="missing">
                  {{ scope.row.missing.join('、') }}
                </span>
                <span v-else class="muted">必填齐全</span>
                <div v-if="scope.row.missingOptional.length" class="muted optional-line">
                  可选未填：{{ scope.row.missingOptional.join('、') }}
                </div>
              </template>
            </el-table-column>
          </el-table>

          <ul v-if="preflight.blockers.length" class="preflight__notes">
            <li v-for="(item, index) in preflight.blockers" :key="`b-${index}`" class="blocker">
              {{ item }}
            </li>
          </ul>
          <ul v-if="preflight.warnings.length" class="preflight__notes">
            <li v-for="(item, index) in preflight.warnings" :key="`w-${index}`" class="warning">
              {{ item }}
            </li>
          </ul>
        </div>
        <span v-else class="muted">点击「智能匹配」后给出缺项与执行顺序</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { labApi } from '@/api'
import type { ModuleRouteResult, PreflightResult, RouteResponse, RouteResult } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { percentText } from '@/utils/format'
import { useAiGuardStore } from '@/stores/aiGuard'

const router = useRouter()
const aiGuard = useAiGuardStore()
const appStore = useAppStore()

const text = ref('')
const loading = ref(false)
const result = ref<RouteResponse | null>(null)
const preflight = ref<PreflightResult | null>(null)

const CAPABILITY_LABELS: Record<string, string> = {
  requirement_confirm: '需求确认',
  testpoint_generation: '测试点生成',
  case_generation: '用例生成',
  xmind_to_case: 'XMind 转用例',
  bug_analysis: '缺陷分析',
  log_analysis: '日志分析',
  sql_analysis: 'SQL 分析',
  regression_generation: '回归清单',
  report_generation: '报告生成',
  knowledge_qa: '知识问答',
  prompt_optimize: '提示词优化',
  api_test_design: '接口测试设计',
  test_plan: '测试计划',
  case_review: '用例评审',
  bug_classification: '缺陷归类',
  risk_assessment: '风险评估',
  automation_script: '自动化脚本生成',
  performance_script: '性能脚本生成',
}

function labelOf(code: string): string {
  return CAPABILITY_LABELS[code] ?? code
}

async function route(): Promise<void> {
  const value = text.value.trim()
  if (!value) {
    ElMessage.warning('请先描述你的需求')
    return
  }
  // 智能路由会调用模型，先确认数据已脱敏
  if (!(await aiGuard.ensureExternalConfirmed())) {
    return
  }
  loading.value = true
  preflight.value = null
  try {
    result.value = await labApi.route({
      text: value,
      projectId: appStore.currentProjectId ?? undefined,
      topK: 3,
    })
    await loadPreflight(value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '匹配失败')
  } finally {
    loading.value = false
  }
}

/** 预检与匹配并行做，纯本地计算，不调模型。 */
async function loadPreflight(value: string): Promise<void> {
  try {
    preflight.value = await labApi.preflight({
      text: value,
      projectId: appStore.currentProjectId ?? undefined,
      topK: 3,
    })
  } catch {
    preflight.value = null
  }
}

function openCapability(item: RouteResult): void {
  const query: Record<string, string> = { text: text.value.trim() }
  if (item.targetModuleId) {
    query.moduleId = String(item.targetModuleId)
  }
  void router.push({ path: `/lab/${item.capability}`, query })
}

function openModule(item: ModuleRouteResult): void {
  const capability = result.value?.capabilityRoutes[0]
  const query: Record<string, string> = { text: text.value.trim(), moduleId: String(item.moduleId) }
  void router.push({
    path: capability ? `/lab/${capability.capability}` : '/lab',
    query,
  })
}

defineExpose({ route, text })
</script>

<style scoped>
.smart-entry__row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.smart-entry__actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
}

.smart-entry__hint {
  font-size: 12px;
  white-space: nowrap;
}

.smart-entry__result {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.smart-entry__group-title {
  font-size: 13px;
  color: var(--aitp-text-weak);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.smart-entry__cards {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.smart-entry__card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 8px 12px;
  border: 1px solid var(--aitp-border);
  border-radius: 8px;
  background: var(--aitp-hover);
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
}

.smart-entry__card:hover {
  border-color: var(--aitp-primary);
}

.smart-entry__card-title {
  font-weight: 600;
}

.smart-entry__card-reason {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.smart-entry__card-score {
  font-size: 12px;
  color: var(--aitp-primary);
}

.preflight {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--aitp-border);
  border-radius: 8px;
  background: var(--aitp-hover);
}

.preflight__bar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.preflight__order {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 13px;
}

.arrow {
  color: var(--aitp-text-weak);
}

.missing {
  color: var(--el-color-danger);
  font-size: 12px;
}

.optional-line {
  font-size: 12px;
  margin-top: 2px;
}

.preflight__notes {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.8;
}

.blocker {
  color: var(--el-color-danger);
}

.warning {
  color: var(--el-color-warning);
}
</style>
