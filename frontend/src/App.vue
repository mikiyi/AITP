<template>
  <AccessGate>
    <AppLayout />
  </AccessGate>
  <!-- 兜底页挂在业务之外：任何页面抛未捕获异常时覆盖界面，但下面的内容仍在，不白屏 -->
  <GlobalErrorBoundary />
</template>

<script setup lang="ts">
import AccessGate from '@/components/AccessGate.vue'
import GlobalErrorBoundary from '@/components/GlobalErrorBoundary.vue'
import AppLayout from '@/layouts/AppLayout.vue'
</script>
