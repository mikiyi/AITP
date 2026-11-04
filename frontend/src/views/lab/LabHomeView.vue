<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">AI 实验室</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Notebook" @click="router.push('/lab/prompts')">提示词库</el-button>
        <el-button :icon="ChatDotRound" @click="router.push('/lab/sessions')">会话记录</el-button>
      </div>
    </div>

    <SmartEntry class="lab-entry" />

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
          @click="open(item.code)"
        >
          <div class="lab-card__head">
            <span class="lab-card__title">{{ item.label }}</span>
            <el-tag
              v-if="item.category === 'extended'"
              size="small"
              type="info"
              effect="plain"
            >
              扩展
            </el-tag>
            <el-tag v-if="item.outputFormat === 'json'" size="small" type="warning" effect="plain">JSON</el-tag>
            <el-tag v-if="item.mindmap" size="small" type="success" effect="plain">脑图</el-tag>
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
              <el-tag size="small" effect="plain">{{ outputText(item) }}</el-tag>
              <el-tag v-if="item.adoptable" size="small" type="success" effect="plain">
                可采纳为用例
              </el-tag>
              <el-tag v-if="item.code === 'report_generation'" size="small" type="success" effect="plain">
                可采纳为报告
              </el-tag>
              <el-tag size="small" effect="plain">可加入知识库</el-tag>
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
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ChatDotRound, Notebook } from '@element-plus/icons-vue'
import SmartEntry from '@/components/SmartEntry.vue'
import { useLabStore } from '@/stores/lab'
import type { CapabilitySpec } from '@/api/types'
import { variableLabel } from './capabilityForms'

const router = useRouter()
const labStore = useLabStore()

// 必须走 computed：store 加载完成后会整体替换 capabilities 数组，
// 直接解构拿到的是旧数组快照，卡片永远渲染不出来。
const capabilities = computed(() => labStore.capabilities)

interface CapabilityGroup {
  category: string
  title: string
  hint: string
  items: CapabilitySpec[]
}

/** 卡片按「核心能力 / 扩展能力」两段展示，扩展能力来自提示词库里其余可直接跑的提示词。 */
const groups = computed<CapabilityGroup[]>(() => {
  const core = capabilities.value.filter((item) => item.category !== 'extended')
  const extended = capabilities.value.filter((item) => item.category === 'extended')
  return [
    {
      category: 'core',
      title: '核心能力',
      hint: '质量闭环主干：需求 → 用例 → 缺陷 → 回归 → 报告',
      items: core,
    },
    {
      category: 'extended',
      title: '扩展能力',
      hint: '提示词库里已就绪的其他提示词，点开即可用',
      items: extended,
    },
  ].filter((group) => group.items.length)
})

function requiredVars(item: CapabilitySpec): string[] {
  return item.requiredVariables ?? []
}

function optionalVars(item: CapabilitySpec): string[] {
  return item.optionalVariables ?? []
}

function outputText(item: CapabilitySpec): string {
  if (item.mindmap) {
    return '脑图'
  }
  return item.outputFormat === 'json' ? 'JSON' : 'Markdown'
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
  flex-wrap: wrap;
  gap: 8px;
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
</style>
