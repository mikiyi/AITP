<template>
  <div v-if="globalError" class="error-boundary" role="alertdialog" aria-label="页面异常提示">
    <div class="error-boundary__card">
      <div class="error-boundary__head">
        <el-icon class="error-boundary__icon" :size="22"><WarningFilled /></el-icon>
        <p class="error-boundary__title">页面出错了</p>
      </div>
      <p class="error-boundary__message">{{ globalError.message }}</p>
      <dl class="error-boundary__meta">
        <div>
          <dt>追踪号</dt>
          <dd>
            <code>{{ globalError.clientTraceId }}</code>
            <span v-if="globalError.traceId"> ｜后端 <code>{{ globalError.traceId }}</code></span>
          </dd>
        </div>
        <div>
          <dt>发生位置</dt>
          <dd>{{ globalError.source }}</dd>
        </div>
        <div>
          <dt>时间</dt>
          <dd>{{ globalError.at }}</dd>
        </div>
      </dl>
      <p class="error-boundary__tip">
        界面已保留，可以继续操作。本机日志在
        <code>data/aitp.log</code>，按上面的追踪号能检索到完整堆栈。
      </p>
      <div class="error-boundary__actions">
        <el-button @click="dismiss">关闭提示</el-button>
        <el-button type="primary" @click="reload">刷新页面</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { WarningFilled } from '@element-plus/icons-vue'
import { useGlobalError } from '@/composables/useGlobalError'

const { globalError, dismiss } = useGlobalError()

function reload(): void {
  window.location.reload()
}
</script>

<style scoped>
.error-boundary {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgb(15 23 42 / 45%);
}

.error-boundary__card {
  width: min(560px, 100%);
  max-height: min(80vh, 640px);
  overflow: auto;
  padding: 22px 24px;
  border: 1px solid var(--aitp-border);
  border-radius: 14px;
  background: var(--aitp-panel);
  box-shadow: 0 18px 48px rgb(15 23 42 / 22%);
}

.error-boundary__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.error-boundary__icon {
  color: var(--el-color-danger);
}

.error-boundary__title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: var(--aitp-text);
}

.error-boundary__message {
  margin: 12px 0 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--aitp-text);
  word-break: break-word;
}

.error-boundary__meta {
  display: grid;
  gap: 6px;
  margin: 14px 0 0;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--aitp-subtle);
  font-size: 12px;
}

.error-boundary__meta > div {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.error-boundary__meta dt {
  flex: 0 0 56px;
  color: var(--aitp-text-weak);
}

.error-boundary__meta dd {
  margin: 0;
  color: var(--aitp-text);
  word-break: break-all;
}

.error-boundary__meta code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--aitp-primary-soft);
  font-family: var(--el-font-family-mono, ui-monospace, monospace);
  font-size: 12px;
}

.error-boundary__tip {
  margin: 12px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--aitp-text-weak);
}

.error-boundary__tip code {
  color: var(--aitp-text);
}

.error-boundary__actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}
</style>
