<template>
  <el-container class="aitp-layout" :style="viewportStyle">
    <el-aside :width="appStore.sidebarCollapsed ? '64px' : '216px'" class="aitp-aside">
      <div class="aitp-brand">
        <el-icon :size="20" color="#2f6fed"><Promotion /></el-icon>
        <span v-show="!appStore.sidebarCollapsed" class="aitp-brand-text">
          <BrandName />
        </span>
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
          <template #title>首页</template>
        </el-menu-item>
        <el-menu-item index="/todos">
          <el-icon><List /></el-icon>
          <template #title>任务清单</template>
        </el-menu-item>
        <el-sub-menu index="lab">
          <template #title>
            <el-icon><MagicStick /></el-icon>
            <span>AI 实验室</span>
          </template>
          <el-menu-item index="/lab">能力总览</el-menu-item>
          <el-menu-item index="/lab/pipeline">顺序执行</el-menu-item>
          <el-menu-item index="/lab/prompts">提示词库</el-menu-item>
          <el-menu-item index="/lab/sessions">会话记录</el-menu-item>
          <el-menu-item index="/lab/records">执行记录</el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="asset">
          <template #title>
            <el-icon><Files /></el-icon>
            <span>测试资产</span>
          </template>
          <el-menu-item index="/projects">项目</el-menu-item>
          <el-menu-item index="/requirements">需求</el-menu-item>
          <el-menu-item index="/test-cases">测试用例</el-menu-item>
          <el-menu-item index="/test-cases/tree">用例树</el-menu-item>
          <el-menu-item index="/bugs">缺陷</el-menu-item>
          <el-menu-item index="/knowledge">知识库</el-menu-item>
          <el-menu-item index="/regressions">回归清单</el-menu-item>
          <el-menu-item index="/reports">测试报告</el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="flow">
          <template #title>
            <el-icon><Operation /></el-icon>
            <span>编排与验证</span>
          </template>
          <el-menu-item index="/workbench/entries">编排入口</el-menu-item>
          <el-menu-item index="/workbench/validation">验证沙箱</el-menu-item>
          <el-menu-item index="/workbench/relations">实体关联</el-menu-item>
        </el-sub-menu>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <template #title>设置</template>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container class="aitp-body">
      <el-header class="aitp-header">
        <div class="aitp-header-left">
          <el-button text :icon="appStore.sidebarCollapsed ? Expand : Fold" @click="appStore.toggleSidebar()" />
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/' }"><BrandName size="small" /></el-breadcrumb-item>
            <el-breadcrumb-item v-if="pageTitle">{{ pageTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="aitp-header-right">
          <GlobalSearch />
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
import { ElMessage } from 'element-plus'
import { Expand, Fold, Files, HomeFilled, List, MagicStick, Operation, Setting } from '@element-plus/icons-vue'
import { get } from '@/api/request'
import { settingsApi } from '@/api'
import type { AiStatus } from '@/api/types'
import GlobalSearch from '@/components/GlobalSearch.vue'
import BrandName from '@/components/BrandName.vue'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const appStore = useAppStore()
const online = ref(true)
const aiStatus = ref<AiStatus | null>(null)
let timer: number | undefined

/** 浏览器布局视口比物理屏幕还宽时，右侧内容会被屏幕边缘切掉，这里把可用宽度钉到屏幕宽度。 */
const visibleWidth = ref<number | null>(null)
let clipHintShown = false

const viewportStyle = computed(() =>
  visibleWidth.value ? { maxWidth: `${visibleWidth.value}px` } : undefined,
)

function updateVisibleWidth(): void {
  const screenWidth = window.screen?.width ?? 0
  const innerWidth = window.innerWidth
  if (screenWidth > 0 && innerWidth > screenWidth + 4) {
    visibleWidth.value = screenWidth
    if (!clipHintShown) {
      clipHintShown = true
      ElMessage({
        type: 'warning',
        duration: 3000,
        message: '浏览器窗口比屏幕还宽，右侧内容原本会被挡在屏幕外，已收窄到可见区域；按 Ctrl+0 可恢复浏览器缩放。',
      })
    }
  } else {
    visibleWidth.value = null
  }
}

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
  if (path.startsWith('/lab/records')) {
    return '/lab/records'
  }
  if (path.startsWith('/lab/sessions')) {
    return '/lab/sessions'
  }
  if (path.startsWith('/lab/pipeline')) {
    return '/lab/pipeline'
  }
  if (path.startsWith('/lab/prompts')) {
    return '/lab/prompts'
  }
  if (path.startsWith('/lab/')) {
    return '/lab'
  }
  if (path.startsWith('/workbench/')) {
    return path
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
  updateVisibleWidth()
  window.addEventListener('resize', updateVisibleWidth)
  timer = window.setInterval(() => {
    void ping()
    void loadAiStatus()
  }, 15000)
})

onUnmounted(() => {
  if (timer) {
    window.clearInterval(timer)
  }
  window.removeEventListener('resize', updateVisibleWidth)
})
</script>

<style scoped>
.aitp-layout {
  height: 100%;
  /* 根容器必须铺满视口：一旦宽度塌成内容宽，右侧就会空出一大块 */
  width: 100%;
}

.aitp-body {
  /* flex 子项默认 min-width:auto，窄内容时会把外层撑破，这里放开让它跟着收缩 */
  min-width: 0;
  width: 100%;
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
  min-width: 0;
}
</style>
