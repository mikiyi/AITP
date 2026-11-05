<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">提示词库</h2>
      <div class="toolbar" style="margin: 0">
        <el-input
          v-model="keyword"
          placeholder="搜索提示词"
          clearable
          style="width: 200px"
          @input="filter"
        />
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="lib-alert"
      title="提示词模板存在 backend/prompts 目录，Markdown 正文带 YAML 头；必填变量缺一个，后端会返回 120102。"
    />

    <div v-loading="loading" class="prompt-grid">
      <div
        v-for="item in filtered"
        :key="item.code"
        class="prompt-card"
        @click="open(item)"
      >
        <div class="prompt-card__head">
          <span class="prompt-card__title">{{ item.title }}</span>
          <span class="prompt-card__tags">
            <el-tag size="small" effect="plain">{{ item.code }}</el-tag>
          </span>
        </div>
        <p class="prompt-card__desc">{{ item.description || '（无说明）' }}</p>
        <div class="prompt-card__vars">
          <el-tag v-for="name in item.variables" :key="name" size="small" :type="optionalOf(item, name) ? 'info' : 'warning'">
            {{ name }}
          </el-tag>
        </div>
        <div class="prompt-card__foot">
          <span class="prompt-card__usage" :class="{ 'is-zero': !item.useCount }">
            用过 {{ item.useCount ?? 0 }} 次
          </span>
        </div>
      </div>
      <el-empty v-if="!filtered.length && !loading" description="没有匹配的提示词" :image-size="80" />
    </div>

    <el-drawer v-model="detailVisible" :title="current?.title ?? '提示词详情'" size="52%">
      <template v-if="current">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="编码">
            <span class="text-mono">{{ current.code }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="文件">
            <span class="text-mono">{{ current.path }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="必填变量">
            <el-tag
              v-for="name in requiredOf(current)"
              :key="name"
              size="small"
              type="warning"
              class="var-tag"
            >
              {{ name }}
            </el-tag>
            <span v-if="!requiredOf(current).length" class="muted">无</span>
          </el-descriptions-item>
          <el-descriptions-item label="可选变量">
            <el-tag v-for="name in optionalVarsOf(current)" :key="name" size="small" class="var-tag">
              {{ name }}
            </el-tag>
            <span v-if="!optionalVarsOf(current).length" class="muted">无</span>
          </el-descriptions-item>
        </el-descriptions>

        <h4 class="detail-title">正文</h4>
        <pre class="prompt-body">{{ current.body || '（正文加载中…）' }}</pre>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { labApi } from '@/api'
import type { PromptTemplate } from '@/api/types'

const templates = ref<PromptTemplate[]>([])
const keyword = ref('')
const loading = ref(false)
const detailVisible = ref(false)
const current = ref<PromptTemplate | null>(null)

const filtered = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  const list = key
    ? templates.value.filter((item) =>
        [item.code, item.title, item.description].some((field) =>
          String(field ?? '').toLowerCase().includes(key),
        ),
      )
    : templates.value
  // 用得越多越靠前；次数相同按名称首字母排序
  return [...list].sort((a, b) => {
    const diff = (b.useCount ?? 0) - (a.useCount ?? 0)
    if (diff !== 0) {
      return diff
    }
    return a.title.localeCompare(b.title, 'zh-Hans-CN')
  })
})

function optionalOf(item: PromptTemplate, name: string): boolean {
  return (item.optional ?? []).includes(name)
}

function requiredOf(item: PromptTemplate): string[] {
  return (item.variables ?? []).filter((name) => !optionalOf(item, name))
}

function optionalVarsOf(item: PromptTemplate): string[] {
  return item.optional ?? []
}

async function load(): Promise<void> {
  loading.value = true
  try {
    templates.value = await labApi.prompts()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function filter(): void {
  /* computed 自动跟随 keyword，这里只占位以便后续扩展 */
}

async function open(item: PromptTemplate): Promise<void> {
  current.value = { ...item }
  detailVisible.value = true
  try {
    const full = await labApi.prompt(item.code)
    current.value = full
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '正文加载失败')
  }
}

onMounted(load)
</script>

<style scoped>
.lib-alert {
  margin-bottom: 12px;
}

.prompt-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  min-height: 200px;
}

.prompt-card {
  background: var(--aitp-panel);
  border: 1px solid var(--aitp-border);
  border-radius: var(--aitp-radius);
  box-shadow: var(--aitp-shadow);
  padding: 14px 16px;
  cursor: pointer;
  transition: border-color 0.15s;
}

.prompt-card:hover {
  border-color: var(--aitp-primary);
}

.prompt-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.prompt-card__tags {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.prompt-card__title {
  font-weight: 600;
}

.prompt-card__desc {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--aitp-text-weak);
  line-height: 1.6;
  min-height: 42px;
}

.prompt-card__vars {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* 使用次数放卡片右下角 */
.prompt-card__foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
}

.prompt-card__usage {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.prompt-card__usage.is-zero {
  opacity: 0.6;
}

.var-tag {
  margin-right: 4px;
}

.detail-title {
  margin: 16px 0 8px;
  font-size: 14px;
}

.prompt-body {
  background: var(--aitp-subtle);
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  margin: 0;
  line-height: 1.7;
}
</style>
