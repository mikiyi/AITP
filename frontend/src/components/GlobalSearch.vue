<template>
  <div class="global-search">
    <el-input
      v-model="keyword"
      placeholder="全局搜索需求 / 用例 / 缺陷 / 知识"
      clearable
      size="small"
      class="global-search__input"
      @keyup.enter="search"
      @input="onInput"
      @clear="clear"
      @focus="openPanel"
    >
      <template #prefix>
        <el-icon><Search /></el-icon>
      </template>
    </el-input>

    <div v-if="panelVisible" class="global-search__panel">
      <div v-if="loading" class="global-search__hint">搜索中…</div>
      <div v-else-if="!keyword.trim()" class="global-search__hint">
        输入关键词，一次检索需求、用例、缺陷与知识库。
      </div>
      <div v-else-if="!result.total" class="global-search__hint">
        没有匹配「{{ keyword }}」的条目。
      </div>
      <template v-else>
        <div v-for="group in result.groups" :key="group.type" class="global-search__group">
          <div class="global-search__group-title">
            {{ group.typeLabel }}
            <span class="muted">{{ group.list.length }}</span>
          </div>
          <div
            v-for="hit in group.list"
            :key="`${hit.type}-${hit.id}`"
            class="global-search__hit"
            @click="go(hit)"
          >
            <div class="global-search__hit-title">
              <span v-if="hit.no" class="global-search__no text-mono">{{ hit.no }}</span>
              {{ hit.title }}
            </div>
            <div v-if="hit.snippet" class="global-search__hit-snippet">{{ hit.snippet }}</div>
          </div>
        </div>
        <div class="global-search__footer">共 {{ result.total }} 条命中，点击条目跳转到对应页面</div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import { searchApi } from '@/api'
import type { SearchHit, SearchResult } from '@/api/types'

const router = useRouter()

/** 哪些实体类型有独立详情页，命中后直接跳过去。 */
const DETAIL_ROUTES: Record<string, string> = {
  case: '/test-cases',
  bug: '/bugs',
  knowledge: '/knowledge',
}

const keyword = ref('')
const loading = ref(false)
const panelVisible = ref(false)
const result = ref<SearchResult>({ keyword: '', groups: [], total: 0 })
let timer: number | undefined

function openPanel(): void {
  if (keyword.value.trim()) {
    panelVisible.value = true
  }
}

async function doSearch(): Promise<void> {
  const text = keyword.value.trim()
  if (!text) {
    result.value = { keyword: '', groups: [], total: 0 }
    panelVisible.value = false
    return
  }
  loading.value = true
  panelVisible.value = true
  try {
    result.value = await searchApi.query({ q: text, limit: 8 })
  } catch {
    result.value = { keyword: text, groups: [], total: 0 }
  } finally {
    loading.value = false
  }
}

function search(): void {
  void doSearch()
}

/** 输入停顿一会儿再检索，避免每敲一个字就打一次库。 */
function onInput(): void {
  if (timer) {
    window.clearTimeout(timer)
  }
  timer = window.setTimeout(() => {
    void doSearch()
  }, 300)
}

function clear(): void {
  result.value = { keyword: '', groups: [], total: 0 }
  panelVisible.value = false
}

function go(hit: SearchHit): void {
  panelVisible.value = false
  // 有详情页的实体直接跳到详情，需求目前只有列表页
  const base = DETAIL_ROUTES[hit.type]
  void router.push(base ? `${base}/${hit.id}` : hit.route)
}

onUnmounted(() => {
  if (timer) {
    window.clearTimeout(timer)
  }
})
</script>

<style scoped>
.global-search {
  position: relative;
  width: 280px;
}

.global-search__input {
  width: 100%;
}

.global-search__panel {
  position: absolute;
  top: 34px;
  right: 0;
  z-index: 20;
  width: 420px;
  max-height: 460px;
  overflow-y: auto;
  padding: 8px;
  background: #ffffff;
  border: 1px solid var(--aitp-border);
  border-radius: 4px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.12);
}

.global-search__hint {
  padding: 14px 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
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

.global-search__hit {
  padding: 8px;
  border-radius: 4px;
  cursor: pointer;
}

.global-search__hit:hover {
  background: var(--el-fill-color-light);
}

.global-search__hit-title {
  font-size: 13px;
  color: var(--el-text-color-primary);
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
  padding: 6px 8px;
  border-top: 1px solid var(--aitp-border);
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
