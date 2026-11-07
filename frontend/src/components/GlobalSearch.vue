<template>
  <div ref="root" class="global-search">
    <el-input
      v-model="keyword"
      placeholder="全局搜索需求 / 用例 / 缺陷 / 知识"
      clearable
      class="global-search__input"
      @input="onInput"
      @clear="clear"
      @focus="openPanel"
      @keydown="onKeydown"
    >
      <template #prefix>
        <el-icon><Search /></el-icon>
      </template>
    </el-input>

    <div v-if="panelVisible" class="global-search__panel">
      <div v-if="loading" class="global-search__hint">搜索中…</div>
      <div v-else-if="notice && !result.total" class="global-search__hint">{{ notice }}</div>
      <div v-else-if="!keyword.trim()" class="global-search__hint">
        输入关键词，一次检索需求、用例、缺陷与知识库。
      </div>
      <div v-else-if="!result.total" class="global-search__hint">
        没有匹配「{{ keyword }}」的条目。
      </div>
      <template v-else>
        <div v-if="notice" class="global-search__toast">{{ notice }}</div>
        <div v-for="group in result.groups" :key="group.type" class="global-search__group">
          <div class="global-search__group-title">
            {{ group.typeLabel }}
            <span class="muted">{{ group.total }}</span>
            <button
              v-if="group.total > group.list.length && group.list.length < EXPAND_LIMIT"
              type="button"
              class="global-search__more"
              :disabled="expandingType === group.type"
              @click="expandGroup(group)"
            >
              {{ expandingType === group.type ? '加载中…' : `展开全部 ${group.total} 条` }}
            </button>
            <span v-else-if="group.total > group.list.length" class="muted">
              仅展示前 {{ EXPAND_LIMIT }} 条
            </span>
          </div>
          <div role="listbox" :aria-label="group.typeLabel">
            <div
              v-for="hit in group.list"
              :key="`${hit.type}-${hit.id}`"
              class="global-search__hit"
              :class="{ 'is-active': isActive(hit) }"
              role="option"
              :aria-selected="isActive(hit)"
              @click="go(hit)"
              @mouseenter="setActive(hit)"
            >
              <div class="global-search__hit-title">
                <span v-if="hit.no" class="global-search__no text-mono">{{ hit.no }}</span>
                {{ hit.title }}
                <span v-if="showProject(hit)" class="global-search__project">{{ hit.projectName }}</span>
              </div>
              <div v-if="hit.snippet" class="global-search__hit-snippet">{{ hit.snippet }}</div>
            </div>
          </div>
        </div>
        <div class="global-search__footer">
          <span>
            {{
              result.matched > result.total
                ? `共 ${result.matched} 条命中，已展示 ${result.total} 条`
                : `共 ${result.matched} 条命中`
            }}
          </span>
          <span class="global-search__keys">↑↓ 选择 · Enter 打开</span>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import { searchApi } from '@/api'
import type { SearchGroup, SearchHit, SearchResult } from '@/api/types'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const appStore = useAppStore()

/** 哪些实体类型有独立详情页，命中后直接跳过去。 */
const DETAIL_ROUTES: Record<string, string> = {
  case: '/test-cases',
  bug: '/bugs',
  knowledge: '/knowledge',
}

const keyword = ref('')
const loading = ref(false)
const notice = ref('')
const root = ref<HTMLElement | null>(null)
const panelVisible = ref(false)
const result = ref<SearchResult>({ keyword: '', groups: [], total: 0, matched: 0 })
const activeIndex = ref(-1)
const expandingType = ref('')
let timer: number | undefined
let seq = 0

/** 与后端 require_str(..., 200) 保持一致，避免超长词白跑一次请求。 */
const MAX_KEYWORD = 200
/** 首屏每类最多展示多少条。 */
const RESULT_LIMIT = 8
/** 展开分组时每类最多取多少条（与后端 limit 上限一致）。 */
const EXPAND_LIMIT = 50

const flatHits = computed<SearchHit[]>(() => result.value.groups.flatMap((group) => group.list))

function openPanel(): void {
  const text = keyword.value.trim()
  if (!text) {
    return
  }
  panelVisible.value = true
  // 上次按 Esc 取消了检索，重新聚焦时补一次，避免展示旧词的结果
  if (text !== result.value.keyword) {
    search()
  }
}

/** Esc 关闭面板并取消挂起的防抖，否则 300ms 后会被重新弹开。 */
function dismiss(): void {
  if (timer) {
    window.clearTimeout(timer)
    timer = undefined
  }
  panelVisible.value = false
  activeIndex.value = -1
}

function isActive(hit: SearchHit): boolean {
  const current = flatHits.value[activeIndex.value]
  return !!current && current.type === hit.type && current.id === hit.id
}

function setActive(hit: SearchHit): void {
  const index = flatHits.value.findIndex(
    (item) => item.type === hit.type && item.id === hit.id,
  )
  if (index >= 0) {
    activeIndex.value = index
  }
}

function moveActive(step: number): void {
  const length = flatHits.value.length
  if (!length) {
    return
  }
  if (activeIndex.value < 0) {
    activeIndex.value = step > 0 ? 0 : length - 1
  } else {
    activeIndex.value = Math.min(length - 1, Math.max(0, activeIndex.value + step))
  }
  void nextTick(() => {
    root.value
      ?.querySelector('.global-search__hit.is-active')
      ?.scrollIntoView({ block: 'nearest' })
  })
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    if (!panelVisible.value) {
      openPanel()
      return
    }
    if (!result.value.total) {
      return
    }
    event.preventDefault()
    moveActive(event.key === 'ArrowDown' ? 1 : -1)
    return
  }
  if (event.key === 'Escape') {
    dismiss()
    return
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    const hit = flatHits.value[activeIndex.value]
    if (hit) {
      go(hit)
    } else {
      search()
    }
  }
}

async function doSearch(): Promise<void> {
  const text = keyword.value.trim()
  notice.value = ''
  activeIndex.value = -1
  if (!text) {
    seq += 1
    loading.value = false
    result.value = { keyword: '', groups: [], total: 0, matched: 0 }
    panelVisible.value = false
    return
  }
  if (text.length > MAX_KEYWORD) {
    seq += 1
    loading.value = false
    result.value = { keyword: text, groups: [], total: 0, matched: 0 }
    notice.value = `关键词最多 ${MAX_KEYWORD} 字，当前 ${text.length} 字。`
    panelVisible.value = true
    return
  }
  const my = ++seq
  loading.value = true
  panelVisible.value = true
  try {
    const payload = await searchApi.query({ q: text, limit: RESULT_LIMIT })
    if (my === seq) {
      result.value = payload
    }
  } catch {
    if (my === seq) {
      result.value = { keyword: text, groups: [], total: 0, matched: 0 }
      notice.value = '搜索失败，请稍后重试。'
    }
  } finally {
    if (my === seq) {
      loading.value = false
    }
  }
}

function search(): void {
  // 回车立即检索时取消挂起的防抖，否则 300ms 后会再打一次同样的请求
  if (timer) {
    window.clearTimeout(timer)
    timer = undefined
  }
  void doSearch()
}

/** 输入停顿一会儿再检索，避免每敲一个字就打一次库。 */
function onInput(): void {
  // 输入已变化，作废在飞的旧请求，避免旧结果盖住新词
  seq += 1
  notice.value = ''
  activeIndex.value = -1
  if (timer) {
    window.clearTimeout(timer)
  }
  timer = window.setTimeout(() => {
    timer = undefined
    void doSearch()
  }, 300)
}

function clear(): void {
  if (timer) {
    window.clearTimeout(timer)
    timer = undefined
  }
  notice.value = ''
  activeIndex.value = -1
  result.value = { keyword: '', groups: [], total: 0, matched: 0 }
  panelVisible.value = false
}

/** 展开某一类：按类型单独再取一次更多结果，不影响其他分组。 */
async function expandGroup(group: SearchGroup): Promise<void> {
  const text = keyword.value.trim()
  if (!text || expandingType.value) {
    return
  }
  expandingType.value = group.type
  try {
    const payload = await searchApi.query({ q: text, types: group.type, limit: EXPAND_LIMIT })
    const next = payload.groups.find((item) => item.type === group.type)
    if (!next) {
      return
    }
    result.value = {
      ...result.value,
      groups: result.value.groups.map((item) =>
        item.type === group.type ? { ...item, total: next.total, list: next.list } : item,
      ),
      total: result.value.total - group.list.length + next.list.length,
      matched: result.value.matched - group.total + next.total,
    }
  } catch {
    notice.value = '展开失败，请稍后重试。'
  } finally {
    expandingType.value = ''
  }
}

function showProject(hit: SearchHit): boolean {
  return !!hit.projectName && hit.projectId != null && hit.projectId !== appStore.currentProjectId
}

function go(hit: SearchHit): void {
  panelVisible.value = false
  // 命中可能来自其他项目：先切到它的项目，否则列表页按当前项目过滤会查不到
  if (hit.projectId != null && hit.projectId !== appStore.currentProjectId) {
    appStore.selectProject(hit.projectId)
  }
  // 需求没有详情页：带编号进列表，直接筛到这一条（编号比标题更精确）
  if (hit.type === 'requirement') {
    void router.push({ path: hit.route, query: { keyword: hit.no || hit.title } })
    return
  }
  const base = DETAIL_ROUTES[hit.type]
  void router.push(base ? `${base}/${hit.id}` : hit.route)
}

function onDocumentClick(event: MouseEvent): void {
  if (root.value && !root.value.contains(event.target as Node)) {
    panelVisible.value = false
    activeIndex.value = -1
  }
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
})

onUnmounted(() => {
  if (timer) {
    window.clearTimeout(timer)
  }
  document.removeEventListener('click', onDocumentClick)
})
</script>

<style scoped>
.global-search {
  position: relative;
  width: 340px;
}

.global-search__input {
  width: 100%;
}

.global-search__input :deep(.el-input__inner) {
  font-size: 14px;
}

.global-search__panel {
  position: absolute;
  top: 40px;
  right: 0;
  z-index: 20;
  width: 420px;
  max-height: 460px;
  overflow-y: auto;
  padding: 8px;
  background: var(--aitp-panel);
  border: 1px solid var(--aitp-border);
  border-radius: 4px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.12);
}

.global-search__hint {
  padding: 14px 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.global-search__toast {
  padding: 4px 8px;
  color: var(--el-color-warning);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.global-search__group {
  margin-bottom: 6px;
}

.global-search__group-title {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.global-search__more {
  margin-left: auto;
  padding: 0 2px;
  border: 0;
  background: none;
  color: var(--el-color-primary);
  font-size: 12px;
  cursor: pointer;
}

.global-search__more:disabled {
  color: var(--el-text-color-placeholder);
  cursor: default;
}

.global-search__hit {
  padding: 8px;
  border-radius: 4px;
  cursor: pointer;
}

.global-search__hit:hover,
.global-search__hit.is-active {
  background: var(--el-fill-color-light);
}

.global-search__hit-title {
  font-size: 13px;
  color: var(--el-text-color-primary);
}

.global-search__project {
  margin-left: 6px;
  padding: 0 4px;
  border: 1px solid var(--aitp-border);
  border-radius: 3px;
  color: var(--el-text-color-secondary);
  font-size: 11px;
  white-space: nowrap;
}

.global-search__no {
  margin-right: 6px;
  color: var(--el-color-primary);
}

.global-search__hit-snippet {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.global-search__footer {
  position: sticky;
  bottom: 0;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  background: var(--aitp-panel);
  border-top: 1px solid var(--aitp-border);
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.global-search__keys {
  color: var(--el-text-color-placeholder);
}
</style>
