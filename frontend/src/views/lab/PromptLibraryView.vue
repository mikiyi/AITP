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
      description="卡片里可直接改正文并保存，改过的正文存在本地库、立即对 AI 调用生效；点「恢复默认」回到磁盘模板。正文只能用模板已有的变量，写了新变量会被拦下。「用过 N 次」按真实模型调用统计：能力类提示词取自 AI 执行记录，能力路由、功能点路由、知识库问答助手、自动整理这四条幕后模板单独累加，不进 AI 执行记录。"
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
            <el-tag v-if="item.customized" size="small" type="warning" effect="plain">
              已自定义
            </el-tag>
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
          <span
            class="prompt-card__usage"
            :class="{ 'is-zero': !item.useCount }"
            :title="`该模板被真实调用过 ${item.useCount ?? 0} 次`"
          >
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
        <div class="body-toolbar">
          <span class="body-source">
            {{ current.customized ? '已自定义正文（存在本地库）' : '默认正文（backend/prompts）' }}
          </span>
          <div class="spacer" />
          <el-button size="small" :disabled="!dirty || saving" @click="discard">放弃修改</el-button>
          <el-button
            size="small"
            type="warning"
            plain
            :disabled="!current.customized || saving"
            @click="resetToDefault"
          >
            恢复默认
          </el-button>
          <el-button
            size="small"
            type="primary"
            :disabled="!dirty"
            :loading="saving"
            @click="save"
          >
            保存正文
          </el-button>
        </div>
        <el-input
          v-model="draft"
          type="textarea"
          :autosize="{ minRows: 14, maxRows: 26 }"
          class="prompt-editor"
          spellcheck="false"
          placeholder="（正文加载中…）"
        />
        <p v-if="dirty" class="body-hint">正文已改动，保存后才会生效。</p>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { labApi } from '@/api'
import type { PromptTemplate } from '@/api/types'

const templates = ref<PromptTemplate[]>([])
const keyword = ref('')
const loading = ref(false)
const detailVisible = ref(false)
const current = ref<PromptTemplate | null>(null)
/** 抽屉里的正文草稿：保存成功后才写回 current.body。 */
const draft = ref('')
const saving = ref(false)

const dirty = computed(() => {
  if (!current.value) {
    return false
  }
  return draft.value !== (current.value.body ?? '')
})

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

let openSeq = 0

async function open(item: PromptTemplate): Promise<void> {
  const seq = ++openSeq
  current.value = { ...item }
  draft.value = item.body ?? ''
  detailVisible.value = true
  try {
    const full = await labApi.prompt(item.code)
    if (seq === openSeq) {
      current.value = full
      draft.value = full.body ?? ''
    }
  } catch (err) {
    if (seq === openSeq) {
      ElMessage.error(err instanceof Error ? err.message : '正文加载失败')
    }
  }
}

/** 列表卡片上的「已自定义」跟着最新状态走，省得刷新整页。 */
function syncCard(template: PromptTemplate): void {
  const index = templates.value.findIndex((item) => item.code === template.code)
  if (index >= 0) {
    templates.value[index] = { ...templates.value[index], ...template }
  }
}

function discard(): void {
  draft.value = current.value?.body ?? ''
}

async function save(): Promise<void> {
  const target = current.value
  if (!target || !dirty.value) {
    return
  }
  saving.value = true
  try {
    const saved = await labApi.savePrompt(target.code, draft.value)
    current.value = { ...saved, defaultBody: target.defaultBody }
    draft.value = saved.body ?? ''
    syncCard(saved)
    ElMessage.success('正文已保存，之后的 AI 调用都用这一版')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function resetToDefault(): Promise<void> {
  const target = current.value
  if (!target) {
    return
  }
  try {
    await ElMessageBox.confirm(
      '将删除这个提示词的自定义正文，回到 backend/prompts 里的默认模板，不可撤销。',
      '恢复默认正文',
      { type: 'warning', confirmButtonText: '恢复默认', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  saving.value = true
  try {
    const back = await labApi.resetPrompt(target.code)
    current.value = { ...back, defaultBody: target.defaultBody }
    draft.value = back.body ?? ''
    syncCard(back)
    ElMessage.success('已恢复默认正文')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '恢复失败')
  } finally {
    saving.value = false
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
  /* 纵向布局：让「使用次数」固定贴在卡片右下角 */
  display: flex;
  flex-direction: column;
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

/* 使用次数固定贴在卡片右下角；有数据用主题绿，0 次用浅灰不抢眼 */
.prompt-card__foot {
  display: flex;
  justify-content: flex-end;
  margin-top: auto;
  padding-top: 12px;
}

.prompt-card__usage {
  font-size: 12px;
  line-height: 1.4;
  color: var(--el-color-success);
}

.prompt-card__usage.is-zero {
  color: var(--aitp-text-weak);
  opacity: 0.7;
}

.var-tag {
  margin-right: 4px;
}

.detail-title {
  margin: 16px 0 8px;
  font-size: 14px;
}

/* 正文工具条：左边说清当前是哪一版，右边三个动作按「改→存→复位」排 */
.body-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.body-source {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.body-hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--el-color-warning);
}

.prompt-editor :deep(.el-textarea__inner) {
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
  /* 提示词正文是纯文本，不需要自动换行以外的换行处理 */
  white-space: pre-wrap;
}
</style>
