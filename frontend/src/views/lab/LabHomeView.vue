<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">AI 实验室</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="ChatDotRound" @click="router.push('/lab/sessions')">会话记录</el-button>
      </div>
    </div>

    <SmartEntry class="lab-entry" />

    <div class="lab-grid">
      <button
        v-for="item in capabilities"
        :key="item.code"
        class="lab-card panel"
        type="button"
        @click="open(item.code)"
      >
        <div class="lab-card__head">
          <span class="lab-card__title">{{ item.label }}</span>
          <el-tag v-if="item.outputFormat === 'json'" size="small" type="warning" effect="plain">JSON</el-tag>
          <el-tag v-if="item.mindmap" size="small" type="success" effect="plain">脑图</el-tag>
        </div>
        <p class="lab-card__goal">{{ item.goal }}</p>
        <div class="lab-card__keywords">
          <el-tag v-for="keyword in item.keywords.slice(0, 4)" :key="keyword" size="small" effect="plain">
            {{ keyword }}
          </el-tag>
        </div>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ChatDotRound } from '@element-plus/icons-vue'
import SmartEntry from '@/components/SmartEntry.vue'
import { useLabStore } from '@/stores/lab'

const router = useRouter()
const labStore = useLabStore()

const capabilities = labStore.capabilities

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

.lab-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.lab-card {
  padding: 14px 16px;
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: inherit;
  border: 1px solid var(--aitp-border);
}

.lab-card:hover {
  border-color: var(--aitp-primary);
}

.lab-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.lab-card__title {
  font-size: 15px;
  font-weight: 600;
}

.lab-card__goal {
  margin: 8px 0 10px;
  color: var(--aitp-text-weak);
  font-size: 13px;
  min-height: 38px;
}

.lab-card__keywords {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
</style>
