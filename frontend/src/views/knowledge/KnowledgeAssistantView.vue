<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">知识库问答助手</h2>
      <el-button :icon="Notebook" @click="router.push('/knowledge')">知识库管理</el-button>
    </div>

    <div class="panel panel-pad">
      <KnowledgeAssistant />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { Notebook } from '@element-plus/icons-vue'
import KnowledgeAssistant from '@/components/KnowledgeAssistant.vue'

const router = useRouter()
</script>
