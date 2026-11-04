<template>
  <el-container class="aitp-layout">
    <el-aside :width="appStore.sidebarCollapsed ? '64px' : '216px'" class="aitp-aside">
      <div class="aitp-brand">
        <el-icon :size="20" color="#2f6fed"><Promotion /></el-icon>
        <span v-show="!appStore.sidebarCollapsed" class="aitp-brand-text">AITestPilot</span>
      </div>
      <el-menu
        :default-active="activePath"
        :collapse="appStore.sidebarCollapsed"
        :collapse-transition="false"
        router
        class="aitp-menu"
      >
        <el-menu-item index="/">
          <el-icon><HomeFilled /></el-icon>
          <template #title>工作台</template>
        </el-menu-item>
        <el-menu-item index="/lab">
          <el-icon><MagicStick /></el-icon>
          <template #title>AI 实验室</template>
        </el-menu-item>
        <el-sub-menu index="asset">
          <template #title>
            <el-icon><Files /></el-icon>
            <span>测试资产</span>
          </template>
          <el-menu-item index="/projects">项目</el-menu-item>
          <el-menu-item index="/requirements">需求</el-menu-item>
          <el-menu-item index="/test-cases">测试用例</el-menu-item>
          <el-menu-item index="/bugs">缺陷</el-menu-item>
          <el-menu-item index="/knowledge">知识库</el-menu-item>
          <el-menu-item index="/reports">测试报告</el-menu-item>
        </el-sub-menu>
        <el-menu-item index="/lab/sessions">
          <el-icon><ChatDotRound /></el-icon>
          <template #title>会话记录</template>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <template #title>设置</template>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="aitp-header">
        <div class="aitp-header-left">
          <el-button text :icon="appStore.sidebarCollapsed ? Expand : Fold" @click="appStore.toggleSidebar()" />
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/' }">工作台</el-breadcrumb-item>
            <el-breadcrumb-item v-if="pageTitle">{{ pageTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="aitp-header-right">
          <el-select
            v-if="appStore.projects.length"
            :model-value="appStore.currentProjectId"
            class="aitp-project-select"
            placeholder="选择项目"
            size="small"
            @update:model-value="onProjectChange"
          >
            <el-option
              v-for="item in appStore.projects"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
          <el-tooltip placement="bottom-end" :disabled="!aiStatus">
            <template #content>
              <div v-if="aiStatus" class="ai-chip__tip">
                <div>生效模型：{{ aiStatus.provider }} / {{ aiStatus.model || '未指定' }}</div>
                <div>接口地址：{{ aiStatus.baseUrl || '-' }}</div>
                <div>来源层：{{ aiSourceText }}</div>
                <div>数据外发：{{ aiStatus.isLocal ? '不出本机' : '会上传第三方' }}</div>
                <div v-if="aiStatus.candidates.length > 1">降级顺序：{{ aiCandidateText }}</div>
              </div>
            </template>
            <el-tag :type="aiTagType" effect="plain" size="small" class="ai-chip">
              {{ aiChipText }}
            </el-tag>
          </el-tooltip>
          <el-tag :type="online ? 'success' : 'danger'" effect="plain" size="small">
            {{ online ? '后端在线' : '后端未连接' }}
          </el-tag>
        </div>
      </el-header>

      <el-main class="aitp-main">
        <router-view v-slot="{ Component }">
          <keep-alive :max="6">
            <component :is="Component" :key="routeKey" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Expand, Fold } from '@element-plus/icons-vue'
import { get } from '@/api/request'
import { settingsApi } from '@/api'
import type { AiStatus } from '@/api/types'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const appStore = useAppStore()
const online = ref(true)
const aiStatus = ref<AiStatus | null>(null)
let timer: number | undefined

const aiChipText = computed(() => {
  const status = aiStatus.value
  if (!status || !status.configured) {
    return 'AI 未配置'
  }
  return `${status.provider}/${status.model || '未指定模型'}`
})

const aiTagType = computed(() => {
  const status = aiStatus.value
  if (!status || !status.configured) {
    return 'info' as const
  }
  return status.isLocal ? ('warning' as const) : ('primary' as const)
})

const aiSourceText = computed(() => {
  const labels = aiStatus.value?.sourceLabels ?? {}
  const parts = Object.entries(labels)
    .filter(([, label]) => label && label !== '未配置')
    .map(([field, label]) => `${field}：${label}`)
  return parts.length ? parts.join(' / ') : '未配置'
})

const aiCandidateText = computed(() =>
  (aiStatus.value?.candidates ?? [])
    .map((item) => `${item.profileName || item.provider}/${item.model || '未指定'}`)
    .join(' → '),
)

const activePath = computed(() => {
  const path = route.path
  if (path.startsWith('/lab/') && path !== '/lab/sessions') {
    return '/lab'
  }
  if (path.startsWith('/settings')) {
    return '/settings'
  }
  return path
})

const pageTitle = computed(() => (route.meta.title as string | undefined) ?? '')
const routeKey = computed(() => route.path)

function onProjectChange(id: number): void {
  appStore.selectProject(id)
}

async function ping(): Promise<void> {
  try {
    await get('/health')
    online.value = true
  } catch {
    online.value = false
  }
}

async function loadAiStatus(): Promise<void> {
  try {
    aiStatus.value = await settingsApi.status()
  } catch {
    aiStatus.value = null
  }
}

watch(
  () => route.path,
  () => {
    appStore.loadProjects()
  },
)

onMounted(() => {
  void ping()
  void loadAiStatus()
  timer = window.setInterval(() => {
    void ping()
    void loadAiStatus()
  }, 15000)
})

onUnmounted(() => {
  if (timer) {
    window.clearInterval(timer)
  }
})
</script>

<style scoped>
.aitp-layout {
  height: 100%;
}

.aitp-aside {
  background: #ffffff;
  border-right: 1px solid var(--aitp-border);
  transition: width 0.2s;
  overflow-x: hidden;
}

.aitp-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 56px;
  padding: 0 16px;
  font-weight: 700;
  font-size: 16px;
  border-bottom: 1px solid var(--aitp-border);
}

.aitp-brand-text {
  white-space: nowrap;
}

.aitp-menu {
  border-right: none;
}

.aitp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 16px;
  background: #ffffff;
  border-bottom: 1px solid var(--aitp-border);
}

.aitp-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  overflow: hidden;
}

.aitp-header-left :deep(.el-breadcrumb) {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  min-width: 0;
  overflow: hidden;
}

.aitp-header-left :deep(.el-breadcrumb__item),
.aitp-header-left :deep(.el-breadcrumb__separator) {
  flex-shrink: 0;
  white-space: nowrap;
}

.aitp-header-left :deep(.el-breadcrumb__item:last-child) {
  flex-shrink: 1;
  min-width: 0;
  overflow: hidden;
}

.aitp-header-left :deep(.el-breadcrumb__item:last-child .el-breadcrumb__inner) {
  display: block;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.aitp-header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.aitp-project-select {
  width: 180px;
}

.ai-chip {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ai-chip__tip {
  line-height: 1.8;
  font-size: 12px;
  max-width: 320px;
  word-break: break-word;
}

.aitp-main {
  padding: 0;
  background: var(--aitp-bg);
  overflow-y: auto;
}
</style>
