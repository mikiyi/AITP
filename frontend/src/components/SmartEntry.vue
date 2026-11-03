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
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { labApi } from '@/api'
import type { ModuleRouteResult, RouteResponse, RouteResult } from '@/api/types'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const appStore = useAppStore()

const text = ref('')
const loading = ref(false)
const result = ref<RouteResponse | null>(null)

async function route(): Promise<void> {
  const value = text.value.trim()
  if (!value) {
    ElMessage.warning('请先描述你的需求')
    return
  }
  loading.value = true
  try {
    result.value = await labApi.route({
      text: value,
      projectId: appStore.currentProjectId ?? undefined,
      topK: 3,
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '匹配失败')
  } finally {
    loading.value = false
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
  background: #fbfcfe;
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
</style>
