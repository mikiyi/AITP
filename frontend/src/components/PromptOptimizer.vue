<template>
  <div class="prompt-optimizer">
    <el-form-item label="提示词优化">
      <el-switch v-model="enabled" />
      <span class="muted optimizer-hint">
        开启后点「优化提示词」只回填输入框，需你确认再执行
      </span>
    </el-form-item>

    <el-form-item v-if="enabled">
      <el-button :icon="MagicStick" :loading="optimizing" @click="optimize">
        优化提示词
      </el-button>
    </el-form-item>

    <el-form-item v-if="original" label="改写前原文">
      <el-input :model-value="original" type="textarea" :rows="4" readonly />
      <el-button link type="primary" @click="restore">还原原文</el-button>
    </el-form-item>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { MagicStick } from '@element-plus/icons-vue'
import { aiApi } from '@/api'
import { useAiGuardStore } from '@/stores/aiGuard'

/**
 * 提示词自动优化：AI 改写后回填输入框，原文保留可一键还原，
 * 是否执行由用户确认（不自动跑能力）。
 */
const props = defineProps<{
  capability: string
  /** 主输入框当前文本。 */
  modelValue: string
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
}>()

const aiGuard = useAiGuardStore()
const enabled = ref(false)
const optimizing = ref(false)
const original = ref('')

async function optimize(): Promise<void> {
  const raw = props.modelValue ?? ''
  if (!raw.trim()) {
    ElMessage.warning('请先填写主要输入内容')
    return
  }
  if (!(await aiGuard.ensureExternalConfirmed())) {
    return
  }
  optimizing.value = true
  try {
    const result = await aiApi.optimize(props.capability, raw)
    if (result.failed) {
      ElMessage.warning(result.message || '优化失败，已保留原文')
      return
    }
    original.value = result.original
    emit('update:modelValue', result.optimized)
    ElMessage.success('已回填优化后的提示词，确认后再执行')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '优化失败')
  } finally {
    optimizing.value = false
  }
}

function restore(): void {
  emit('update:modelValue', original.value)
  original.value = ''
  ElMessage.info('已还原为原文')
}
</script>

<style scoped>
.optimizer-hint {
  margin-left: 10px;
  font-size: 12px;
}
</style>
