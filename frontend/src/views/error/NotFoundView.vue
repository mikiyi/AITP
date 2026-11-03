<template>
  <div class="not-found">
    <div class="not-found__code">404</div>
    <p class="not-found__text">页面不存在，或已被移除</p>
    <el-button type="primary" @click="router.push('/')">回到工作台</el-button>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 60vh;
}

.not-found__code {
  font-size: 72px;
  font-weight: 700;
  color: var(--aitp-primary);
  line-height: 1;
}

.not-found__text {
  color: var(--aitp-text-weak);
  margin: 0;
}
</style>
