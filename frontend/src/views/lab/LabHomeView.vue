<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">AI 实验室</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="Notebook" @click="router.push('/lab/prompts')">提示词库</el-button>
        <el-button :icon="ChatDotRound" @click="router.push('/lab/sessions')">会话记录</el-button>
      </div>
    </div>

    <SmartEntry class="lab-entry" />

    <div class="toolbar lab-filter">
      <el-input
        v-model="keyword"
        placeholder="搜索能力名称 / 说明 / 关键词"
        clearable
        style="width: 260px"
      />
      <el-checkbox v-model="favoriteOnly">只看收藏</el-checkbox>
      <span class="muted">共 {{ filteredCapabilities.length }} 个能力</span>
    </div>

    <section v-for="group in groups" :key="group.category" class="lab-section">
      <div class="lab-section__head">
        <span class="lab-section__title">{{ group.title }}</span>
        <span class="lab-section__count">{{ group.items.length }} 个</span>
        <span class="lab-section__hint">{{ group.hint }}</span>
      </div>

      <div class="lab-grid">
        <button
          v-for="item in group.items"
          :key="item.code"
          class="lab-card panel"
          type="button"
          :aria-label="item.label"
          @click="open(item.code)"
        >
          <div class="lab-card__head">
            <span class="lab-card__title">
              <span
                class="lab-card__fav"
                :class="{ 'is-active': favorites.has(item.code) }"
                :title="favorites.has(item.code) ? '取消收藏' : '收藏该能力'"
                @click.stop="toggleFavorite(item.code)"
              >★</span>
              {{ item.label }}
            </span>
            <span class="lab-card__head-tags">
              <span
                v-if="item.category !== 'extended'"
                class="lab-card__usage"
                :class="{ 'is-zero': !item.useCount }"
                :title="`该能力被使用过 ${item.useCount ?? 0} 次`"
              >
                用过 {{ item.useCount ?? 0 }} 次
              </span>
              <el-tag
                v-if="item.category === 'extended'"
                size="small"
                type="info"
                effect="plain"
              >
                扩展
              </el-tag>
            </span>
          </div>

          <p class="lab-card__goal">{{ item.promptDescription || item.goal }}</p>

          <div class="lab-card__row">
            <span class="lab-card__row-label">输入</span>
            <span class="lab-card__vars">
              <el-tag
                v-for="name in requiredVars(item)"
                :key="`r-${name}`"
                size="small"
                type="warning"
                effect="plain"
              >
                {{ variableLabel(item.code, name) }}
              </el-tag>
              <el-tag
                v-for="name in optionalVars(item)"
                :key="`o-${name}`"
                size="small"
                type="info"
                effect="plain"
              >
                {{ variableLabel(item.code, name) }}
              </el-tag>
              <span v-if="!requiredVars(item).length && !optionalVars(item).length" class="muted">
                无需输入
              </span>
            </span>
          </div>

          <div class="lab-card__row">
            <span class="lab-card__row-label">输出</span>
            <span class="lab-card__vars">
              <el-tag size="small" effect="plain">Markdown</el-tag>
              <el-tag size="small" effect="plain" type="info">JSON</el-tag>
            </span>
          </div>

          <div class="lab-card__row">
            <span class="lab-card__row-label">支持</span>
            <span class="lab-card__vars">
              <el-tag
                v-for="feature in item.features ?? []"
                :key="feature.key"
                size="small"
                :type="featureType(feature.key)"
                effect="plain"
              >
                {{ feature.label }}
              </el-tag>
            </span>
          </div>

          <div class="lab-card__foot">
            <span class="lab-card__prompt" :title="`提示词：${item.promptCode}`">
              {{ item.promptCode }}
            </span>
            <span class="lab-card__keywords">
              <span v-for="keyword in item.keywords.slice(0, 3)" :key="keyword" class="lab-card__keyword">
                {{ keyword }}
              </span>
            </span>
          </div>
        </button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ChatDotRound, Notebook } from '@element-plus/icons-vue'
import SmartEntry from '@/components/SmartEntry.vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { useLabStore } from '@/stores/lab'
import type { CapabilitySpec } from '@/api/types'
import { variableLabel } from './capabilityForms'

const router = useRouter()
const labStore = useLabStore()

// 必须走 computed：store 加载完成后会整体替换 capabilities 数组，
// 直接解构拿到的是旧数组快照，卡片永远渲染不出来。
const capabilities = computed(() => labStore.capabilities)
const keyword = ref('')
const favoriteOnly = ref(false)
const FAVORITES_KEY = 'aitp.lab.favorites'

function readFavorites(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(FAVORITES_KEY) || '[]')
    return new Set(Array.isArray(raw) ? raw.map(String) : [])
  } catch {
    return new Set()
  }
}

const favorites = ref<Set<string>>(readFavorites())

function toggleFavorite(code: string): void {
  const next = new Set(favorites.value)
  if (next.has(code)) next.delete(code)
  else next.add(code)
  favorites.value = next
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify([...next]))
  } catch {
    // 隐私模式禁用存储只影响记忆收藏，不影响本次筛选
  }
}

const filteredCapabilities = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  return capabilities.value.filter((item) => {
    if (favoriteOnly.value && !favorites.value.has(item.code)) return false
    if (!text) return true
    const haystack = [item.label, item.goal, item.promptDescription, item.promptCode, ...(item.keywords ?? [])]
      .join(' ')
      .toLowerCase()
    return haystack.includes(text)
  })
})

const DOMAIN_GROUPS: Record<string, { title: string; hint: string }> = {
  requirement: { title: '需求与设计', hint: '需求确认、测试点、用例生成与评审' },
  quality: { title: '缺陷与回归', hint: '缺陷分析、归类、回归清单与风险评估' },
  report: { title: '报告与计划', hint: '报告、进度看板与测试计划' },
  engineering: { title: '自动化与数据', hint: '接口设计、日志、SQL、脚本与测试数据' },
  explore: { title: '探索与优化', hint: '探索性测试与提示词优化' },
  extended: { title: '扩展能力', hint: '提示词库里已就绪的其他提示词，点开即可用' },
}

function groupKey(code: string): string {
  if (code === 'requirement-confirm' || code === 'testpoint-generation' || code === 'case-generation'
    || code === 'xmind-to-case' || code === 'case-review') return 'requirement'
  if (code === 'bug-analysis' || code === 'bug-classification' || code === 'regression-generation'
    || code === 'risk-assessment') return 'quality'
  if (code === 'report-generation' || code === 'project-progress-board' || code === 'test-plan') return 'report'
  if (code === 'api-test-design' || code === 'log-analysis' || code === 'sql-analysis'
    || code === 'test-data-generation' || code === 'automation-script'
    || code === 'performance-script') return 'engineering'
  if (code === 'exploratory-web' || code === 'prompt-optimizer') return 'explore'
  return 'extended'
}

interface CapabilityGroup {
  category: string
  title: string
  hint: string
  items: CapabilitySpec[]
}

/** 卡片按「核心能力 / 扩展能力」两段展示，扩展能力来自提示词库里其余可直接跑的提示词。 */
const groups = computed<CapabilityGroup[]>(() => {
  const buckets = new Map<string, CapabilitySpec[]>()
  for (const item of filteredCapabilities.value) {
    const key = item.category === 'extended' ? 'extended' : groupKey(item.code)
    const bucket = buckets.get(key) ?? []
    bucket.push(item)
    buckets.set(key, bucket)
  }
  return [...buckets.entries()]
    .map(([category, items]) => ({
      category,
      title: DOMAIN_GROUPS[category]?.title ?? category,
      hint: DOMAIN_GROUPS[category]?.hint ?? '',
      items: [...items].sort((a, b) => (b.useCount ?? 0) - (a.useCount ?? 0)),
    }))
    .filter((group) => group.items.length)
})

/** 产出物模板由页面开关驱动，不算用户要手填的输入，卡片上不列。 */
const SYSTEM_VARIABLES = new Set(['outputTemplate'])

function requiredVars(item: CapabilitySpec): string[] {
  return (item.requiredVariables ?? []).filter((name) => !SYSTEM_VARIABLES.has(name))
}

function optionalVars(item: CapabilitySpec): string[] {
  return (item.optionalVariables ?? []).filter((name) => !SYSTEM_VARIABLES.has(name))
}

/** 特性标签配色：导出 = 蓝，采纳 = 绿，其余 = 中性。 */
function featureType(key: string): 'primary' | 'success' | 'info' {
  if (key === 'excel' || key === 'word' || key === 'xmind') {
    return 'primary'
  }
  if (key === 'adopt' || key === 'adopt_report') {
    return 'success'
  }
  return 'info'
}

function open(code: string): void {
  void router.push(`/lab/${code}`)
}

onMounted(() => {
  void labStore.loadCapabilities()
})
</script>

<style scoped>
.lab-entry {
  margin-bottom: 16px;
}

.lab-section {
  margin-bottom: 20px;
}

.lab-section__head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
}

.lab-section__title {
  font-size: 15px;
  font-weight: 600;
}

.lab-section__count {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.lab-section__hint {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.lab-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}

.lab-card {
  padding: 14px 16px;
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: inherit;
  border: 1px solid var(--aitp-border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.lab-card:hover {
  border-color: var(--aitp-primary);
}

.lab-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

/* 右上角：使用次数 / 扩展标记 */
.lab-card__head-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.lab-card__usage {
  font-size: 12px;
  color: var(--el-color-primary);
}

.lab-card__usage.is-zero {
  color: var(--aitp-text-weak);
  opacity: 0.6;
}

.lab-card__title {
  font-size: 15px;
  font-weight: 600;
}

.lab-card__goal {
  margin: 0;
  color: var(--aitp-text-weak);
  font-size: 13px;
  line-height: 1.6;
  min-height: 42px;
}

/* 输入 / 输出：左侧固定标签，右侧变量标签可换行 */
.lab-card__row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12px;
}

.lab-card__row-label {
  flex: 0 0 28px;
  color: var(--aitp-text-weak);
  line-height: 24px;
}

.lab-card__vars {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  line-height: 1;
}

.lab-card__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--aitp-border);
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.lab-card__prompt {
  font-family: 'Cascadia Mono', Consolas, monospace;
}

.lab-card__keywords {
  display: flex;
  gap: 6px;
  overflow: hidden;
  white-space: nowrap;
}

.lab-card__keyword {
  color: var(--aitp-text-weak);
}

.lab-filter {
  margin-bottom: 12px;
  align-items: center;
}

.lab-card__fav {
  color: var(--aitp-text-weak);
  cursor: pointer;
  margin-right: 4px;
  font-size: 13px;
}

.lab-card__fav.is-active {
  color: var(--el-color-warning);
}
</style>
