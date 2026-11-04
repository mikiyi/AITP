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
        <template v-for="node in menuNodes" :key="node.type === 'group' ? node.key : node.path">
          <el-menu-item v-if="node.type === 'item'" :index="node.path">
            <el-icon><component :is="node.icon" /></el-icon>
            <template #title>{{ node.title }}</template>
          </el-menu-item>
          <el-sub-menu v-else :index="node.key">
            <template #title>
              <el-icon><component :is="node.icon" /></el-icon>
              <span :class="{ 'is-active-group': isGroupActive(node, activePath) }">{{ node.title }}</span>
            </template>
            <el-menu-item v-for="child in node.children" :key="child.path" :index="child.path">
              {{ child.title }}
            </el-menu-item>
          </el-sub-menu>
        </template>
      </el-menu>
    </el-aside>

    <el-container class="aitp-body">
      <el-header class="aitp-header">
        <div class="aitp-header-left">
          <el-button text :icon="appStore.sidebarCollapsed ? Expand : Fold" @click="appStore.toggleSidebar()" />
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/' }"><BrandName size="small" /></el-breadcrumb-item>
            <el-breadcrumb-item v-if="groupTitle" :to="{ path: groupPath }">{{ groupTitle }}</el-breadcrumb-item>
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
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Expand, Fold } from '@element-plus/icons-vue'
import { get } from '@/api/request'
import { settingsApi } from '@/api'
import type { AiStatus } from '@/api/types'
import GlobalSearch from '@/components/GlobalSearch.vue'
import BrandName from '@/components/BrandName.vue'
import { useAppStore } from '@/stores/app'
import { MENU_NODES, isGroupActive, matchMenu } from '@/router/menu'

const route = useRoute()
const appStore = useAppStore()
const online = ref(true)
const aiStatus = ref<AiStatus | null>(null)
let timer: number | undefined

/**
 * 浏览器窗口可能比屏幕还大，或有一部分拖到了屏幕外（分辨率变化、缩放变化都会触发）。
 * 这时超出屏幕的那部分内容永远落在屏幕之外：页面自己滚不到，用户也看不见，
 * 所以按"内容区在屏幕上的实际可见范围"把应用整体收进来。
 */
const visibleWidth = ref<number | null>(null)
const visibleHeight = ref<number | null>(null)
let clipHintShown = false

const viewportStyle = computed(() => {
  const style: Record<string, string> = {}
  if (visibleWidth.value) {
    style.maxWidth = `${visibleWidth.value}px`
  }
  if (visibleHeight.value) {
    style.maxHeight = `${visibleHeight.value}px`
  }
  return Object.keys(style).length ? style : undefined
})

function updateVisibleSize(): void {
  const screen = (window.screen ?? undefined) as (Screen & { availLeft?: number; availTop?: number }) | undefined
  const availWidth = screen?.availWidth ?? 0
  const availHeight = screen?.availHeight ?? 0
  if (!screen || !availWidth || !availHeight) {
    visibleWidth.value = null
    visibleHeight.value = null
    return
  }
  const innerWidth = window.innerWidth
  const innerHeight = window.innerHeight
  const outerHeight = window.outerHeight || innerHeight
  // 浏览器工具栏（标签页 + 地址栏）占的高度，用来换算内容区顶部相对屏幕的位置
  const chromeHeight = Math.max(outerHeight - innerHeight, 0)
  // 多显示器时把窗口坐标换算到当前显示器坐标系
  const displayLeft = screen.availLeft ?? 0
  const displayTop = screen.availTop ?? 0
  const offsetX = window.screenX - displayLeft
  const offsetY = window.screenY - displayTop + chromeHeight
  const visibleW = availWidth - Math.max(offsetX, 0)
  const visibleH = availHeight - Math.max(offsetY, 0)
  visibleWidth.value = visibleW > 320 && visibleW < innerWidth - 4 ? visibleW : null
  visibleHeight.value = visibleH > 240 && visibleH < innerHeight - 4 ? visibleH : null
  if ((visibleWidth.value || visibleHeight.value) && !clipHintShown) {
    clipHintShown = true
    ElMessage({
      type: 'warning',
      duration: 3000,
      message:
        '浏览器窗口有部分超出屏幕，超出的内容（右侧或底部）看不到也滚不到；已收进可见区域，按 Ctrl+0 可恢复浏览器缩放。',
    })
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

const menuNodes = MENU_NODES

/** 当前路由命中的导航项，用来驱动侧栏高亮。 */
const menuMatch = computed(() => matchMenu(route.path))
const activePath = computed(() => menuMatch.value.path)

const pageTitle = computed(() => (route.meta.title as string | undefined) ?? '')

/**
 * 面包屑里的分组标题：像「顺序执行」这类分组内入口要显示成「AI 实验室 / 顺序执行」，
 * 分组标题与页面标题相同时（能力总览本身就是 AI 实验室首页）只保留一层。
 */
const groupTitle = computed(() => {
  const group = menuMatch.value.group
  if (!group || group === pageTitle.value) {
    return ''
  }
  return group
})

/** 分组面包屑指向该分组的第一个入口。 */
const groupPath = computed(() => {
  const node = MENU_NODES.find(
    (item) => item.type === 'group' && item.title === menuMatch.value.group,
  )
  return node && node.type === 'group' && node.children.length ? node.children[0].path : '/'
})

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
  async () => {
    appStore.loadProjects()
    await nextTick()
    scrollActiveMenuIntoView()
  },
)

/** 菜单较长时把当前所在入口滚进可视区，避免"看不到自己在哪一页"。 */
function scrollActiveMenuIntoView(): void {
  const menu = document.querySelector<HTMLElement>('.aitp-menu')
  if (!menu) {
    return
  }
  const active = menu.querySelector<HTMLElement>('.el-menu-item.is-active')
  if (!active) {
    return
  }
  const menuRect = menu.getBoundingClientRect()
  const itemRect = active.getBoundingClientRect()
  if (itemRect.top < menuRect.top) {
    menu.scrollTop -= menuRect.top - itemRect.top
  } else if (itemRect.bottom > menuRect.bottom) {
    menu.scrollTop += itemRect.bottom - menuRect.bottom
  }
}

/**
 * 展开子菜单后把新出现的那几项滚进可视区，否则下半截入口在屏幕外看不到。
 * 子菜单高度是分几步撑开的，所以每次菜单内容高度变化都重新定位一次，
 * 最后一次（高度稳定后）就是正确位置。
 */
function scrollOpenedSubMenuIntoView(): void {
  const menu = document.querySelector<HTMLElement>('.aitp-menu')
  if (!menu) {
    return
  }
  if (menu.scrollHeight === lastContentHeight) {
    // 内容高度没变，说明只是用户自己滚动了，不要打断
    return
  }
  lastContentHeight = menu.scrollHeight
  const opened = [...document.querySelectorAll<HTMLElement>('.el-sub-menu.is-opened')]
  const sub = opened[opened.length - 1]
  if (!sub) {
    return
  }
  const items = sub.querySelectorAll<HTMLElement>('.el-menu-item')
  const last = items[items.length - 1]
  if (!last) {
    return
  }
  const menuRect = menu.getBoundingClientRect()
  const itemRect = last.getBoundingClientRect()
  if (itemRect.bottom <= menuRect.bottom && itemRect.top >= menuRect.top) {
    return
  }
  // 菜单还没产生滚动条时写 scrollTop 会被浏览器丢弃，等下一帧再试
  if (menu.scrollHeight <= menu.clientHeight) {
    if (menuRetryHandle) {
      window.cancelAnimationFrame(menuRetryHandle)
    }
    menuRetryHandle = window.requestAnimationFrame(() => {
      menuRetryHandle = undefined
      scrollOpenedSubMenuIntoView()
    })
    return
  }
  if (itemRect.bottom > menuRect.bottom) {
    menu.scrollTop += itemRect.bottom - menuRect.bottom
  } else {
    menu.scrollTop -= menuRect.top - itemRect.top
  }
}

/**
 * 用 MutationObserver 直接盯 DOM：子菜单展开会改 class 与高度样式，
 * 等布局稳定后把新出现的入口滚进可视区（Element Plus 的 open 事件在打包后不可靠）。
 */
let menuObserver: MutationObserver | null = null
let menuSyncHandle: number | undefined
let menuRetryHandle: number | undefined
let lastContentHeight = 0

function handleMenuMutation(): void {
  if (menuSyncHandle) {
    window.cancelAnimationFrame(menuSyncHandle)
  }
  menuSyncHandle = window.requestAnimationFrame(() => {
    menuSyncHandle = undefined
    scrollOpenedSubMenuIntoView()
  })
}

function setupMenuObserver(): void {
  const menu = document.querySelector('.aitp-menu')
  if (!menu) {
    return
  }
  lastContentHeight = menu.scrollHeight
  menuObserver = new MutationObserver(handleMenuMutation)
  menuObserver.observe(menu, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'style'],
  })
}

onMounted(async () => {
  void ping()
  void loadAiStatus()
  updateVisibleSize()
  window.addEventListener('resize', updateVisibleSize)
  timer = window.setInterval(() => {
    void ping()
    void loadAiStatus()
    // 窗口被拖动时不会触发 resize，定期复查一次可见范围
    updateVisibleSize()
  }, 15000)
  await nextTick()
  setupMenuObserver()
  scrollActiveMenuIntoView()
})

onUnmounted(() => {
  if (timer) {
    window.clearInterval(timer)
  }
  if (menuSyncHandle) {
    window.cancelAnimationFrame(menuSyncHandle)
  }
  if (menuRetryHandle) {
    window.cancelAnimationFrame(menuRetryHandle)
  }
  menuObserver?.disconnect()
  menuObserver = null
  window.removeEventListener('resize', updateVisibleSize)
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
  /* 侧栏本身不滚动：品牌栏固定，菜单区自己滚动，导航全部展开时也能滚到底 */
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.aitp-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 56px;
  padding: 0 16px;
  font-weight: 700;
  font-size: 16px;
  flex-shrink: 0;
  border-bottom: 1px solid var(--aitp-border);
}

.aitp-brand-text {
  white-space: nowrap;
}

.aitp-menu {
  border-right: none;
  /* flex 子项默认 min-height:auto，菜单展开后会撑破侧栏，这里放开让它内部滚动 */
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: #c6ccd6 transparent;
}

/* 滚动条做得明显一点，菜单长的时候一眼能看出可以往下滚 */
.aitp-menu::-webkit-scrollbar {
  width: 8px;
}

.aitp-menu::-webkit-scrollbar-track {
  background: transparent;
}

.aitp-menu::-webkit-scrollbar-thumb {
  background: #c6ccd6;
  border-radius: 4px;
}

.aitp-menu::-webkit-scrollbar-thumb:hover {
  background: #a4abb6;
}

/* 选中的入口：颜色比默认的浅蓝加深一档，并加粗，一眼能看出当前在哪 */
.aitp-menu :deep(.el-menu-item.is-active) {
  color: #1d4ed8;
  font-weight: 700;
}

.aitp-menu :deep(.el-menu-item.is-active .el-icon) {
  color: #1d4ed8;
}

/* 选中项所在的分组标题跟着高亮，层级关系看得清楚 */
.aitp-menu :deep(.el-sub-menu__title .is-active-group) {
  color: #1d4ed8;
  font-weight: 700;
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
