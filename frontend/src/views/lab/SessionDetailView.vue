<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">{{ session?.title ?? '会话详情' }}</h2>
      <div class="toolbar" style="margin: 0">
        <el-tag v-if="session?.module" effect="plain">{{ session.module }}</el-tag>
        <el-button :icon="Back" @click="router.push('/lab/sessions')">返回列表</el-button>
      </div>
    </div>

    <div v-loading="loading" class="panel panel-pad session-panel">
      <el-empty v-if="!loading && !messages.length" description="该会话没有消息" />
      <div v-for="message in messages" :key="message.id" class="session-message">
        <div class="session-message__head">
          <el-tag size="small" :type="message.role === 'user' ? 'primary' : 'success'" effect="plain">
            {{ message.role === 'user' ? '用户' : '助手' }}
          </el-tag>
          <span v-if="message.model" class="muted">{{ message.model }}</span>
          <span v-if="message.createdAt" class="muted">{{ message.createdAt }}</span>
        </div>
        <MarkdownView :source="message.content" />
        <el-collapse v-if="message.reasoning" class="session-message__reasoning">
          <el-collapse-item title="推理过程" name="reasoning">
            <pre class="session-message__pre">{{ message.reasoning }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back } from '@element-plus/icons-vue'
import MarkdownView from '@/components/MarkdownView.vue'
import { labApi } from '@/api'
import type { AiMessage, AiSession } from '@/api/types'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const session = ref<AiSession | null>(null)
const messages = ref<AiMessage[]>([])

onMounted(async () => {
  loading.value = true
  try {
    const detail = await labApi.session(Number(route.params.id))
    session.value = detail
    messages.value = detail.messages ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载会话失败')
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.session-panel {
  min-height: 320px;
}

.session-message {
  padding: 12px 0;
  border-bottom: 1px solid var(--aitp-border);
}

.session-message:last-child {
  border-bottom: none;
}

.session-message__head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  font-size: 12px;
}

.session-message__pre {
  background: #f6f8fa;
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  padding: 10px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  margin: 0;
}
</style>
