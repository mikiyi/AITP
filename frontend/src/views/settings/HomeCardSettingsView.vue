<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">首页卡片定制</h2>
      <div class="page-header__actions">
        <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
        <el-button :icon="RefreshLeft" :disabled="!dirty" @click="resetDefault">恢复默认</el-button>
        <el-button :icon="HomeFilled" @click="router.push('/')">查看首页</el-button>
        <el-button type="primary" :loading="saving" :disabled="!dirty" @click="save">保存布局</el-button>
      </div>
    </div>

    <el-alert
      class="hc__alert"
      type="info"
      show-icon
      :closable="false"
      title="首页分成两块：上面三块固定显示，下面这些卡片可以自己决定显不显示、排第几"
      description="知识库问答助手、数据概览、功能快捷入口属于固定区，不参与定制。调整只影响首页的排布，不会删除任何数据。"
    />

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>卡片清单</h3>
        <span class="muted">
          已显示 {{ visibleCount }} / {{ customCards.length }} 张{{ dirty ? ' · 有未保存的改动' : '' }}
        </span>
      </div>

      <el-table v-loading="loading" :data="customCards" border size="small" row-key="key">
        <el-table-column label="顺序" width="120" align="center">
          <template #default="{ $index }">
            <div class="hc__order">
              <span class="hc__index">{{ $index + 1 }}</span>
              <el-button
                link
                :icon="ArrowUp"
                :disabled="$index === 0"
                title="上移"
                @click="move($index, -1)"
              />
              <el-button
                link
                :icon="ArrowDown"
                :disabled="$index === customCards.length - 1"
                title="下移"
                @click="move($index, 1)"
              />
            </div>
          </template>
        </el-table-column>

        <el-table-column label="卡片" min-width="260">
          <template #default="{ row }">
            <div class="hc__label">{{ row.label }}</div>
            <div class="muted hc__desc">{{ row.description }}</div>
          </template>
        </el-table-column>

        <el-table-column label="占位" width="120" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.width === 'full' ? 'warning' : 'info'" effect="plain">
              {{ row.width === 'full' ? '整行' : '半宽' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="首页显示" width="120" align="center">
          <template #default="{ row }">
            <el-switch v-model="row.visible" size="small" inline-prompt active-text="显示" inactive-text="隐藏" />
          </template>
        </el-table-column>

        <el-table-column label="快捷调整" width="200" align="center">
          <template #default="{ $index }">
            <el-button link type="primary" size="small" :disabled="$index === 0" @click="toTop($index)">
              置顶
            </el-button>
            <el-button
              link
              type="primary"
              size="small"
              :disabled="$index === customCards.length - 1"
              @click="toBottom($index)"
            >
              置底
            </el-button>
            <el-button link type="primary" size="small" @click="toggle($index)">
              {{ customCards[$index].visible ? '隐藏' : '显示' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <p class="muted settings-block__hint">
        隐藏的卡片不会请求额外数据；整行卡片（如测试环境健康监测）单独占一排，半宽卡片两两并排，
        窗口变窄时自动换成一行一张。
      </p>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>固定区</h3>
        <span class="muted">始终显示在首页顶部，不支持定制</span>
      </div>
      <el-table :data="lockedCards" border size="small">
        <el-table-column label="卡片" min-width="260">
          <template #default="{ row }">
            <div class="hc__label">{{ row.label }}</div>
            <div class="muted hc__desc">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column label="占位" width="120" align="center">
          <template #default="{ row }">
            <el-tag size="small" type="info" effect="plain">{{ row.width === 'full' ? '整行' : '半宽' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="首页显示" width="120" align="center">
          <template #default>
            <el-tag size="small" type="success" effect="plain">固定显示</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowDown, ArrowUp, Back, HomeFilled, RefreshLeft } from '@element-plus/icons-vue'
import { settingsApi } from '@/api'
import type { HomeCardItem } from '@/api/types'

const router = useRouter()
const loading = ref(false)
const saving = ref(false)
const lockedCards = ref<HomeCardItem[]>([])
const customCards = ref<HomeCardItem[]>([])
/** 进入页面时的快照，用来判断有没有未保存的改动 */
const snapshot = ref('')

const visibleCount = computed(() => customCards.value.filter((item) => item.visible).length)
const dirty = computed(() => fingerprint(customCards.value) !== snapshot.value)

function fingerprint(items: HomeCardItem[]): string {
  return items.map((item) => `${item.key}:${item.visible ? 1 : 0}`).join('|')
}

function stateOf(items: HomeCardItem[]): HomeCardItem[] {
  return items.map((item) => ({ ...item }))
}

async function load(params: { default?: boolean } = {}): Promise<void> {
  loading.value = true
  try {
    const result = await settingsApi.homeCards(params)
    lockedCards.value = stateOf(result.list.filter((item) => item.locked))
    customCards.value = stateOf(result.list.filter((item) => !item.locked))
    if (!params.default) {
      snapshot.value = fingerprint(customCards.value)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载首页卡片布局失败')
  } finally {
    loading.value = false
  }
}

function move(index: number, offset: number): void {
  const target = index + offset
  const list = customCards.value
  if (target < 0 || target >= list.length) {
    return
  }
  const next = [...list]
  const row = next[index]
  next[index] = next[target]
  next[target] = row
  customCards.value = next
}

function toTop(index: number): void {
  move(index, -index)
}

function toBottom(index: number): void {
  move(index, customCards.value.length - 1 - index)
}

function toggle(index: number): void {
  const row = customCards.value[index]
  if (row) {
    row.visible = !row.visible
  }
}

/** 「恢复默认」只把服务端默认排布填进当前草稿，仍需点「保存布局」才落到首页。 */
async function resetDefault(): Promise<void> {
  await load({ default: true })
  ElMessage.success('已填入默认布局，点击「保存布局」生效')
}

async function save(): Promise<void> {
  saving.value = true
  try {
    await settingsApi.saveHomeCards(
      customCards.value.map((item) => ({ key: item.key, visible: item.visible })),
    )
    ElMessage.success('首页布局已保存')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存首页布局失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void load()
})
// keep-alive 回到本页时重新拉一次，避免留下上一次未保存的排序草稿
onActivated(() => {
  void load()
})
</script>

<style scoped>
.hc__alert {
  margin-bottom: 12px;
}

.hc__order {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
}

.hc__index {
  min-width: 20px;
  margin-right: 4px;
  font-weight: 600;
}

.hc__label {
  font-weight: 600;
}

.hc__desc {
  margin-top: 2px;
  font-size: 12px;
}
</style>
