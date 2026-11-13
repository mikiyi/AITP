<template>
  <el-dialog
    v-model="visible"
    title="生产环境执行确认"
    width="480px"
    :close-on-click-modal="false"
    append-to-body
    @close="onClose"
  >
    <el-alert type="warning" :closable="false" show-icon :title="message" />
    <el-checkbox v-model="ack" style="margin-top: 12px">
      已知晓风险，确认在生产环境执行本次操作
    </el-checkbox>
    <template #footer>
      <el-button @click="settle(false)">取消</el-button>
      <el-button type="primary" :disabled="!ack" @click="settle(true)">确认执行</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 统一的生产环境执行确认（勾选式）。
 * 所有「向生产环境发起真实执行」的入口共用这一种交互：
 * 必须显式勾选「已知晓风险」才能点确认，默认不勾选。
 * 用法：const ok = await prodDialog.value.open('提示文案')。
 */
import { ref } from 'vue'

const visible = ref(false)
const ack = ref(false)
const message = ref('')
let resolver: ((value: boolean) => void) | null = null

function open(text: string): Promise<boolean> {
  message.value = text
  ack.value = false
  visible.value = true
  return new Promise((resolve) => {
    resolver = resolve
  })
}

function settle(value: boolean): void {
  visible.value = false
  const resolve = resolver
  resolver = null
  resolve?.(value)
}

/** 点 X / 遮罩关闭视为取消，避免悬挂的 Promise。 */
function onClose(): void {
  if (resolver) {
    settle(false)
  }
}

defineExpose({ open })
</script>
