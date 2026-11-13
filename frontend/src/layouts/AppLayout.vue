<template>
  <el-container class="aitp-layout" :style="viewportStyle">
    <el-aside :width="appStore.sidebarCollapsed ? '64px' : '216px'" class="aitp-aside">
      <div class="aitp-aside-inner">
        <div class="aitp-brand" :class="{ 'is-collapsed': appStore.sidebarCollapsed }">
          <img class="aitp-brand-logo" src="/brand-logo.svg" alt="" aria-hidden="true" />
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
              <template #title>
                {{ node.title }}
                <span v-if="badgeStore.countOf(node.path)" class="menu-badge">
                  {{ badgeStore.countOf(node.path) }}
                </span>
              </template>
            </el-menu-item>
            <el-sub-menu v-else :index="node.key" popper-class="aitp-menu-popper">
              <template #title>
                <el-icon><component :is="node.icon" /></el-icon>
                <span :class="{ 'is-active-group': isGroupActive(node, activePath) }">{{ node.title }}</span>
              </template>
              <el-menu-item v-for="child in node.children" :key="child.path" :index="child.path">
                <el-icon><component :is="child.icon" /></el-icon>
                <span class="menu-item-text">{{ child.title }}</span>
                <span v-if="badgeStore.countOf(child.path)" class="menu-badge">
                  {{ badgeStore.countOf(child.path) }}
                </span>
              </el-menu-item>
            </el-sub-menu>
          </template>
        </el-menu>
      </div>
      <el-tooltip
        :content="appStore.sidebarCollapsed ? '展开导航' : '收起导航'"
        placement="right"
        :show-after="300"
      >
        <button
          type="button"
          class="aitp-collapse-toggle"
          :aria-label="appStore.sidebarCollapsed ? '展开导航' : '收起导航'"
          :aria-expanded="!appStore.sidebarCollapsed"
          @click="appStore.toggleSidebar()"
        >
          <el-icon :size="13">
            <ArrowRight v-if="appStore.sidebarCollapsed" />
            <ArrowLeft v-else />
          </el-icon>
        </button>
      </el-tooltip>
    </el-aside>

    <el-container class="aitp-body">
      <el-header class="aitp-header">
        <div class="aitp-header-left">
          <!--
            面包屑自己拼而不直接用 el-breadcrumb-item：
            Element Plus 的可点击项渲染成 role="link" 的 <span>，键盘 Tab 走不到。
            这里保持它的类名与层级（样式照旧），链接换成真正的 <a>，可以聚焦和回车跳转。
          -->
          <nav class="el-breadcrumb aitp-breadcrumb" aria-label="面包屑导航">
            <span class="el-breadcrumb__item">
              <span class="el-breadcrumb__inner">
                <RouterLink to="/" aria-label="AITestPlatform 首页">
                  <BrandName size="small" :gradient="appStore.sidebarCollapsed" />
                </RouterLink>
              </span>
              <span class="el-breadcrumb__separator" role="presentation">/</span>
            </span>
            <span v-if="groupTitle" class="el-breadcrumb__item">
              <span class="el-breadcrumb__inner">
                <RouterLink :to="groupPath">{{ groupTitle }}</RouterLink>
              </span>
              <span class="el-breadcrumb__separator" role="presentation">/</span>
            </span>
            <span v-if="pageTitle" class="el-breadcrumb__item" aria-current="page">
              <span class="el-breadcrumb__inner">{{ pageTitle }}</span>
              <span class="el-breadcrumb__separator" role="presentation">/</span>
            </span>
          </nav>
        </div>
        <div class="aitp-header-right">
          <AssistantQuickEntry />
          <GlobalSearch />
          <StatusBadges :online="online" :health="health" :ai-status="aiStatus" />
          <el-tooltip :content="`切换皮肤（当前：${themeStore.current.label}）`" placement="bottom-end">
            <el-dropdown trigger="click" @command="onThemeChange">
              <button type="button" class="theme-switch" aria-label="切换皮肤">
                <svg class="theme-switch__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path
                    d="M12 3.2a8.8 8.8 0 1 0 0 17.6c1.1 0 1.8-.8 1.8-1.8 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.1 0-1 .8-1.8 1.8-1.8h1.5a4.8 4.8 0 0 0 4.8-4.8c0-3.9-4-7-8.9-7Z"
                  />
                  <circle cx="7.4" cy="11.4" r="1.1" fill="currentColor" stroke="none" />
                  <circle cx="9.2" cy="7.8" r="1.1" fill="currentColor" stroke="none" />
                  <circle cx="13.2" cy="7.2" r="1.1" fill="currentColor" stroke="none" />
                  <circle cx="16.4" cy="9.6" r="1.1" fill="currentColor" stroke="none" />
                </svg>
                <span
                  class="theme-switch__dot"
                  :style="{ background: themeStore.current.swatch.primary }"
                />
              </button>
              <template #dropdown>
                <el-dropdown-menu class="theme-menu">
                  <el-dropdown-item
                    v-for="item in themeStore.themes"
                    :key="item.key"
                    :command="item.key"
                    class="theme-menu__item"
                  >
                    <span
                      class="theme-menu__swatch"
                      :style="{
                        background: item.swatch.bg,
                        borderColor: item.swatch.border,
                      }"
                    />
                    <span class="theme-menu__label">{{ item.label }}</span>
                    <el-icon v-if="item.key === themeStore.theme" class="theme-menu__check"><Check /></el-icon>
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </el-tooltip>
        </div>
      </el-header>

      <el-main class="aitp-main">
        <OnboardingTour ref="tourRef" />
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
import { useRoute, RouterLink } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Check, ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import { get } from '@/api/request'
import { settingsApi } from '@/api'
import type { AiStatus, HealthStatus } from '@/api/types'
import GlobalSearch from '@/components/GlobalSearch.vue'
import AssistantQuickEntry from '@/components/AssistantQuickEntry.vue'
import StatusBadges from '@/components/StatusBadges.vue'
import OnboardingTour from '@/components/OnboardingTour.vue'
import BrandName from '@/components/BrandName.vue'
import { useAppStore } from '@/stores/app'
import { useThemeStore, type ThemeKey } from '@/stores/theme'
import { useBadgeStore } from '@/stores/badges'
import { MENU_NODES, isGroupActive, matchMenu } from '@/router/menu'

const route = useRoute()
const appStore = useAppStore()
const themeStore = useThemeStore()
const badgeStore = useBadgeStore()
const online = ref(true)
const health = ref<HealthStatus | null>(null)
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

/**
 * 收起侧栏后分组变成浮层（teleport 到 body），它不受应用收口的 max-height 约束。
 * 把当前真正可见的高度写给浮层用，浮层才有办法自己出滚动条。
 */
function publishVisibleHeight(): void {
  const height = visibleHeight.value ?? window.innerHeight
  if (height > 0) {
    document.documentElement.style.setProperty('--aitp-visible-height', `${Math.round(height)}px`)
  }
}

function updateVisibleSize(): void {
  const screen = (window.screen ?? undefined) as (Screen & { availLeft?: number; availTop?: number }) | undefined
  const availWidth = screen?.availWidth ?? 0
  const availHeight = screen?.availHeight ?? 0
  if (!screen || !availWidth || !availHeight) {
    visibleWidth.value = null
    visibleHeight.value = null
    publishVisibleHeight()
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
  publishVisibleHeight()
  refitOpenedPoppers()
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

const menuNodes = MENU_NODES

// 进入资产页面即视为已查看，清掉该分区未读角标
watch(
  () => route.path,
  (path) => {
    void badgeStore.clearForPath(path)
  },
  { immediate: true },
)

/** 当前路由命中的导航项，用来驱动侧栏高亮。 */
const menuMatch = computed(() => matchMenu(route.path))
const activePath = computed(() => menuMatch.value.path)

/**
 * 页面标题优先取导航项上的名字，
 * 这样「/automation/web」这类参数路由显示的是「Web 自动化管理」而不是通用路由名；
 * 详情页仍用路由自己的标题（如「用例详情」），不跟上级入口混淆。
 */
const pageTitle = computed(() => {
  if (menuMatch.value.exact && menuMatch.value.title) {
    return menuMatch.value.title
  }
  return (route.meta.title as string | undefined) ?? ''
})

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

/** 帮助页等入口通过事件总线重开首启引导（引导组件现挂在布局层，跨页存活）。 */
const tourRef = ref<InstanceType<typeof OnboardingTour> | null>(null)
function onReopenTour(): void {
  tourRef.value?.reopen()
}

/** 切换皮肤：写入 <html data-theme>，并持久化到本地存储。 */
function onThemeChange(key: ThemeKey): void {
  themeStore.apply(key)
}

async function ping(): Promise<void> {
  try {
    health.value = await get<HealthStatus>('/health')
    online.value = true
  } catch {
    online.value = false
    health.value = null
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

// 收起/展开会换一套菜单宽度与结构，当前入口可能落到滚动区外，展开后重新定位
watch(
  () => appStore.sidebarCollapsed,
  async () => {
    await nextTick()
    scrollActiveMenuIntoView()
  },
)

/**
 * 菜单较长时把当前所在入口滚进可视区，避免"看不到自己在哪一页"。
 * 菜单没出滚动条时（collapsed 或窗口很高）写了也不会动，安全。
 */
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
    autoScrollMenu(menu, menu.scrollTop - (menuRect.top - itemRect.top))
  } else if (itemRect.bottom > menuRect.bottom) {
    autoScrollMenu(menu, menu.scrollTop + (itemRect.bottom - menuRect.bottom))
  }
}

/** 顶层每一项（一级菜单 / 分组）在 DOM 里的下标就是它的稳定身份，顺序不会变。 */
function openedIndexesOf(menu: HTMLElement): number[] {
  const opened: number[] = []
  Array.from(menu.children).forEach((node, index) => {
    if (node.classList.contains('is-opened')) {
      opened.push(index)
    }
  })
  return opened
}

/**
 * 程序自己写的滚动都走这里：记下目标值，滚动事件里凭它区分"我滚的"还是"用户滚的"。
 */
function autoScrollMenu(menu: HTMLElement, value: number): void {
  const limit = Math.max(0, menu.scrollHeight - menu.clientHeight)
  const next = Math.min(Math.max(value, 0), limit)
  if (Math.abs(next - menu.scrollTop) <= 0.5) {
    return
  }
  lastAutoScrollTop = next
  menu.scrollTop = next
}

/** 元素顶部在菜单内容坐标系里的位置。 */
function contentTopOf(menu: HTMLElement, el: HTMLElement): number {
  return el.getBoundingClientRect().top - menu.getBoundingClientRect().top + menu.scrollTop
}

/**
 * 把 scrollTop 收敛进 [lower, upper]，只动"刚好越界"的那部分。
 * 区间无解（要保住的东西比菜单还高）时退化成贴住上界；菜单还没出滚动条时限幅为 0，写了也不会有跳动。
 */
function clampMenuScroll(menu: HTMLElement, lower: number, upper: number): void {
  const limit = Math.max(0, menu.scrollHeight - menu.clientHeight)
  const from = Math.min(Math.max(lower, 0), limit)
  const to = Math.min(Math.max(upper, from), limit)
  autoScrollMenu(menu, Math.min(Math.max(menu.scrollTop, from), to))
}

/**
 * 展开/收起分组后的定位：锚点是"刚刚被点开或收起的那一个分组"，
 * 不是 DOM 里最后一个展开的分组——否则收起上面的分组时会被拽到最底下那个分组那里去，
 * 表现就是导航栏一收起二级菜单就滚到最底，连刚收起的一级菜单都找不到了。
 *
 * 展开：整组能在可视区里放下就整组露出来；放不下就只把该组标题对齐到顶部，子项往下顺排。
 * 收起：只保证这一项一级菜单还在视野里，绝不往下滚。
 */
const MENU_SETTLE_MS = 360

let menuAnchor: { index: number; open: boolean; since: number } | null = null
let prevOpenedIndexes: number[] = []
let lastMenuHeight = 0
let menuPumpHandle: number | undefined
let menuObserver: MutationObserver | null = null
/** 自己写进 scrollTop 的值：滚动事件里比对得上就是自己滚的，对不上就是用户在滚，立刻让路。 */
let lastAutoScrollTop: number | null = null

function applyMenuAnchor(menu: HTMLElement): void {
  if (!menuAnchor) {
    return
  }
  const group = menu.children[menuAnchor.index] as HTMLElement | undefined
  if (!group) {
    menuAnchor = null
    return
  }
  const title = group.querySelector<HTMLElement>(':scope > .el-sub-menu__title') ?? group
  const titleTop = contentTopOf(menu, title)
  const titleBottom = titleTop + title.offsetHeight
  if (menuAnchor.open) {
    const items = group.querySelectorAll<HTMLElement>('.el-menu-item')
    const last = items[items.length - 1] as HTMLElement | undefined
    const groupBottom = last ? contentTopOf(menu, last) + last.offsetHeight : titleBottom
    const revealAll = groupBottom - menu.clientHeight
    if (revealAll <= titleTop) {
      // 整组装得下：既让最后一项露出来，又不把标题顶出视野
      clampMenuScroll(menu, revealAll, titleTop)
    } else {
      // 整组比可视区高：标题对齐到顶部，子项往下顺排，不要一路翻到底
      clampMenuScroll(menu, titleTop, titleTop)
    }
  } else {
    clampMenuScroll(menu, titleBottom - menu.clientHeight, titleTop)
  }
}

function pumpMenuScroll(): void {
  menuPumpHandle = undefined
  const menu = document.querySelector<HTMLElement>('.aitp-menu')
  if (!menu || !menuAnchor) {
    lastMenuHeight = menu ? menu.scrollHeight : 0
    return
  }
  const heightChanged = menu.scrollHeight !== lastMenuHeight
  lastMenuHeight = menu.scrollHeight
  applyMenuAnchor(menu)
  // 折叠动画期间 Element Plus 只改 CSS 计算值、不再产生 DOM 变更，得自己逐帧复查到高度稳定
  if (heightChanged || performance.now() - menuAnchor.since < MENU_SETTLE_MS) {
    startMenuPump()
  } else {
    menuAnchor = null
  }
}

function startMenuPump(): void {
  if (menuPumpHandle === undefined) {
    menuPumpHandle = window.requestAnimationFrame(pumpMenuScroll)
  }
}

function handleMenuMutation(): void {
  const menu = document.querySelector<HTMLElement>('.aitp-menu')
  if (!menu) {
    return
  }
  const opened = openedIndexesOf(menu)
  const added = opened.filter((index) => !prevOpenedIndexes.includes(index))
  const removed = prevOpenedIndexes.filter((index) => !opened.includes(index))
  if (!added.length && !removed.length) {
    // 只是 hover、选中态之类的变化，不是展开/收起，别去动用户的滚动位置
    return
  }
  menuAnchor = {
    index: (added.length ? added[added.length - 1] : removed[removed.length - 1]) as number,
    open: added.length > 0,
    since: performance.now(),
  }
  prevOpenedIndexes = opened
  startMenuPump()
}

function onMenuScroll(event: Event): void {
  const menu = event.target as HTMLElement
  if (lastAutoScrollTop !== null && Math.abs(menu.scrollTop - lastAutoScrollTop) < 1.5) {
    return
  }
  // 用户自己在滚导航栏，立刻停止自动定位，不要跟滚轮抢位置
  menuAnchor = null
}

/** 滚轮/触摸板落在导航栏上就是用户的意图，自动定位马上让路。 */
function onMenuUserWheel(): void {
  menuAnchor = null
}

function setupMenuObserver(): void {
  const menu = document.querySelector<HTMLElement>('.aitp-menu')
  if (!menu) {
    return
  }
  prevOpenedIndexes = openedIndexesOf(menu)
  lastMenuHeight = menu.scrollHeight
  menu.addEventListener('scroll', onMenuScroll)
  menu.addEventListener('wheel', onMenuUserWheel, { passive: true })
  // 分组标题的鼠标事件会被 Element Plus 截停，冒泡到菜单上收不到，改在捕获阶段监听
  document.addEventListener('mouseover', onMenuHover, true)
  document.addEventListener('focusin', onMenuHover, true)
  menuObserver = new MutationObserver(handleMenuMutation)
  menuObserver.observe(menu, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'style'],
  })
}

/** 指针/焦点进入导航栏时补一次浮层收口（收起态分组浮层由 hover 打开）。 */
function onMenuHover(event: Event): void {
  const menu = document.querySelector<HTMLElement>('.aitp-menu')
  const target = event.target
  if (menu && target instanceof Node && menu.contains(target)) {
    scheduleRefitPoppers()
  }
}

/*
 * 收起态分组浮层自己也会越界：Element Plus 按整个布局视口定位，
 * 而我们可能已经把应用收口到更矮的可见区，浮层顶边偏低时底部就落到屏幕外。
 * 所以按"浮层顶边 → 可见区底边"的剩余空间再收一次，超出部分让它内部滚动。
 */
let popperObserver: MutationObserver | null = null
const popperTimers = new Set<number>()

const POPPER_MAX_HEIGHT = 480
const POPPER_MARGIN = 8

function fitMenuPopper(popper: HTMLElement): void {
  if (!popper.classList.contains('el-popper')) {
    return
  }
  const popup = popper.querySelector<HTMLElement>('.el-menu--popup')
  if (!popup || !popup.offsetHeight) {
    return
  }
  const limit = Math.min(visibleHeight.value ?? window.innerHeight, window.innerHeight)
  const rect = popper.getBoundingClientRect()
  if (!rect.height) {
    return
  }
  // 先按内容自然高度算：最多占满可见区，也不超过预设上限
  const contentHeight = popup.scrollHeight || popup.offsetHeight
  const desired = Math.min(contentHeight, limit - POPPER_MARGIN * 2, POPPER_MAX_HEIGHT)
  let top = rect.top
  if (top + desired > limit - POPPER_MARGIN) {
    // 顶边太低会把底部顶出可见区，直接上移到刚好放得下
    top = Math.max(POPPER_MARGIN, limit - POPPER_MARGIN - desired)
    popper.style.top = `${Math.round(top)}px`
  }
  const room = `${Math.max(80, Math.floor(Math.min(desired, limit - top - POPPER_MARGIN)))}px`
  // 两层都写：浮层定位元素不随内容重渲染，内层菜单列表才是实际滚动区
  popper.style.maxHeight = room
  popup.style.maxHeight = room
}

/**
 * 收起模式下浮层多数时候早就挂载好了，hover 只是切换显隐，
 * 单靠 MutationObserver 抓不到，所以悬停/聚焦时也补一次收口。
 */
let popperRefitScheduled = false
const POPPER_REFIT_DELAYS = [16, 120, 360, 700, 1100]

function schedulePopperFit(popper: HTMLElement): void {
  // 浮层刚挂载时还没定位，等一两帧拿到最终 top 再收口
  const run = (): void => fitMenuPopper(popper)
  window.requestAnimationFrame(run)
  for (const delay of [80, 240, 480]) {
    const handle = window.setTimeout(() => {
      popperTimers.delete(handle)
      run()
    }, delay)
    popperTimers.add(handle)
  }
}

function refitOpenedPoppers(): void {
  document.querySelectorAll<HTMLElement>('.el-popper.aitp-menu-popper').forEach(fitMenuPopper)
}

function scheduleRefitPoppers(): void {
  if (popperRefitScheduled) {
    return
  }
  popperRefitScheduled = true
  POPPER_REFIT_DELAYS.forEach((delay, index) => {
    const handle = window.setTimeout(() => {
      popperTimers.delete(handle)
      if (index === POPPER_REFIT_DELAYS.length - 1) {
        popperRefitScheduled = false
      }
      refitOpenedPoppers()
    }, delay)
    popperTimers.add(handle)
  })
}

function setupPopperObserver(): void {
  popperObserver?.disconnect()
  popperObserver = new MutationObserver((records) => {
    for (const record of records) {
      record.addedNodes.forEach((node) => {
        if (
          node instanceof HTMLElement &&
          node.classList.contains('el-popper') &&
          node.classList.contains('aitp-menu-popper')
        ) {
          schedulePopperFit(node)
        }
      })
    }
  })
  popperObserver.observe(document.body, { childList: true })
}

onMounted(async () => {
  void appStore.loadProjects()
  void badgeStore.load()
  void ping()
  void loadAiStatus()
  updateVisibleSize()
  window.addEventListener('resize', updateVisibleSize)
  window.addEventListener('aitp:reopen-tour', onReopenTour)
  timer = window.setInterval(() => {
    void ping()
    void loadAiStatus()
    // 窗口被拖动时不会触发 resize，定期复查一次可见范围
    updateVisibleSize()
  }, 15000)
  await nextTick()
  setupMenuObserver()
  setupPopperObserver()
  scrollActiveMenuIntoView()
})

onUnmounted(() => {
  if (timer) {
    window.clearInterval(timer)
  }
  if (menuPumpHandle !== undefined) {
    window.cancelAnimationFrame(menuPumpHandle)
    menuPumpHandle = undefined
  }
  menuAnchor = null
  menuObserver?.disconnect()
  menuObserver = null
  popperObserver?.disconnect()
  popperObserver = null
  popperTimers.forEach((handle) => window.clearTimeout(handle))
  popperTimers.clear()
  document.removeEventListener('mouseover', onMenuHover, true)
  document.removeEventListener('focusin', onMenuHover, true)
  window.removeEventListener('resize', updateVisibleSize)
  window.removeEventListener('aitp:reopen-tour', onReopenTour)
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
  position: relative;
  z-index: 5;
  background: var(--aitp-panel);
  border-right: 1px solid var(--aitp-border);
  transition: width 0.2s;
  /*
   * 侧栏本身不滚动：品牌栏固定，菜单区自己滚动，导航全部展开时也能滚到底。
   * overflow 留给内层裁切，按钮才能骑在右边框上不被切掉。
   */
  display: flex;
  flex-direction: column;
  overflow: visible;
}

.aitp-aside-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

/* 收起/展开手柄：贴在侧栏右边框上，位于品牌行高度，滚动区域之外不会挡菜单 */
.aitp-collapse-toggle {
  position: absolute;
  top: 16px;
  right: -11px;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  color: var(--aitp-text-weak);
  background: var(--aitp-panel);
  border: 1px solid var(--aitp-border);
  border-radius: 50%;
  cursor: pointer;
  box-shadow: 0 1px 4px rgb(0 0 0 / 10%);
  transition: color 0.15s, border-color 0.15s, background 0.15s, box-shadow 0.15s;
}

.aitp-collapse-toggle:hover {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary);
  box-shadow: 0 2px 6px rgb(0 0 0 / 14%);
}

.aitp-collapse-toggle:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.aitp-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 64px;
  padding: 0 16px;
  font-weight: 700;
  /* 216px 侧栏减去 LOGO 与收起按钮后只剩约 146px，20px 字号放不下 AITestPlatform */
  font-size: 18px;
  flex-shrink: 0;
}

/* 收起态侧栏只有 64px，左右内边距再留 16px 就会把放大的 LOGO 挤出去 */
.aitp-brand.is-collapsed {
  justify-content: center;
  padding: 0 12px;
}

.aitp-brand-logo {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
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
  scrollbar-color: var(--aitp-scrollbar) transparent;

  /*
   * 层级对齐（Element Plus 按 base + 层级 × levelPadding 算缩进）：
   *   一级：缩进 = 20(基础内边距)，图标占 24 + 5(右间距)，文字起点 = 49px
   *   二级：多缩进 28px（两个字符位），图标起点 = 48px，文字起点 = 77px
   * 所以 levelPadding 取 28px，一级保持 20px 不变。
   */
  --el-menu-base-level-padding: 20px;
  --el-menu-level-padding: 28px;
}

/* 滚动条做得明显一点，菜单长的时候一眼能看出可以往下滚 */
.aitp-menu::-webkit-scrollbar {
  width: 8px;
}

.aitp-menu::-webkit-scrollbar-track {
  background: transparent;
}

.aitp-menu::-webkit-scrollbar-thumb {
  background: var(--aitp-scrollbar);
  border-radius: 4px;
}

.aitp-menu::-webkit-scrollbar-thumb:hover {
  background: var(--aitp-scrollbar-hover);
}

/*
 * 选中的入口：底纹色块 + 主题色字 + 加粗 + 左侧色条。
 * 底纹是关键——鼠标挪开后字色变化不明显，靠底纹才能一眼看出当前在哪一页。
 */
.aitp-menu :deep(.el-menu-item.is-active) {
  color: var(--el-color-primary);
  font-weight: 700;
  background: var(--el-color-primary-light-9);
  border-radius: 4px;
}

.aitp-menu :deep(.el-menu-item.is-active .el-icon) {
  color: var(--el-color-primary);
}

/* 选中项所在的分组标题跟着高亮，层级关系看得清楚 */
.aitp-menu :deep(.el-sub-menu__title .is-active-group) {
  color: var(--el-color-primary);
  font-weight: 700;
}

.aitp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 64px;
  padding: 0 16px;
  background: var(--aitp-panel);
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
  font-size: 15px;
}

.aitp-header-left :deep(.brand-name.is-small) {
  font-size: 16px;
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

/* 链接换成真正的 <a>，键盘 Tab 能走到，聚焦时给一个明显的焦点框 */
.aitp-header-left :deep(.el-breadcrumb__inner a:focus-visible) {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
  border-radius: 2px;
}

/* 末级标题可能很长，链接里的文字也要能截断 */
.aitp-header-left :deep(.el-breadcrumb__inner a) {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  white-space: nowrap;
  vertical-align: bottom;
}

.aitp-header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.aitp-header-right :deep(.el-tag) {
  height: 28px;
  padding: 0 10px;
  font-size: 13px;
}

/* 未读角标：红底白字，数字超过 99 显示 99+ */
.menu-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  margin-left: 6px;
  padding: 0 5px;
  font-size: 11px;
  font-weight: 600;
  line-height: 18px;
  color: #fff;
  background: var(--el-color-danger);
  border-radius: 9px;
}

/* 皮肤切换：方形调色板图标，右下角小圆点显示当前皮肤主色 */
.theme-switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  color: var(--aitp-text-weak);
  background: var(--aitp-panel);
  border: 1px solid var(--aitp-border);
  border-radius: 4px;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}

.theme-switch:hover {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary);
}

.theme-switch__icon {
  width: 18px;
  height: 18px;
}

.theme-switch__dot {
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  border: 1px solid var(--aitp-panel);
}

/* 窄窗口给面包屑留位置：搜索框先收窄，状态徽标和皮肤入口保持完整 */
@media (max-width: 1320px) {
  .aitp-header-right :deep(.global-search) {
    width: 240px;
  }
}

.aitp-main {
  padding: 0;
  background: var(--aitp-bg);
  overflow-y: auto;
  min-width: 0;
}
</style>

<style>
/*
 * 收起态的分组浮层由 Element Plus teleport 到 body，scoped 样式够不到。
 * 浮层原来没有高度上限，分组一长（如「设置」）就直接超出屏幕，底部入口看不到也滚不到；
 * 这里按实际可见高度收口并让它自己出滚动条，最后一项总能滚进来。
 */
.aitp-menu-popper .el-menu--popup {
  max-height: calc(var(--aitp-visible-height, 100vh) - 24px);
  overflow-y: auto;
  scrollbar-width: thin;
}

.aitp-menu-popper .el-menu--popup::-webkit-scrollbar {
  width: 8px;
}

.aitp-menu-popper .el-menu--popup::-webkit-scrollbar-thumb {
  background: var(--aitp-scrollbar);
  border-radius: 4px;
}
</style>
