<template>
  <el-dialog
    :model-value="modelValue"
    title="加入知识库"
    width="620px"
    @update:model-value="(value: boolean) => emit('update:modelValue', value)"
    @open="reset"
  >
    <el-form label-width="70px">
      <el-form-item label="标题" required>
        <el-input v-model="form.title" maxlength="300" placeholder="知识条目标题" />
      </el-form-item>
      <el-form-item label="分类">
        <el-select v-model="form.category" filterable allow-create default-first-option>
          <el-option
            v-for="item in categoryStore.options"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="标签">
        <el-input v-model="form.tags" placeholder="逗号分隔" />
      </el-form-item>
      <el-form-item label="正文">
        <el-input v-model="form.content" type="textarea" :rows="9" maxlength="200000" show-word-limit />
      </el-form-item>
    </el-form>
    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="提交后进入待审核池，审核通过才计入知识资产"
    />
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">提交到待审池</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { knowledgeApi } from '@/api'
import { DEFAULT_KNOWLEDGE_CATEGORY, useKnowledgeCategoryStore } from '@/stores/knowledgeCategory'

/**
 * 通用的「加入知识库」弹框：需求、用例、缺陷、回归、报告、自动化脚本共用。
 * 正文默认由调用方拼好，用户可再改；提交走待审核池，不进正式库。
 */
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title?: string
    content?: string
    /** 来源记录类型：requirement / test_case / bug / regression / report / automation_script */
    refType?: string
    refId?: number | null
    refLabel?: string
    moduleId?: number | null
  }>(),
  {
    title: '',
    content: '',
    refType: '',
    refId: null,
    refLabel: '',
    moduleId: null,
  },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'saved'): void
}>()

const categoryStore = useKnowledgeCategoryStore()
const saving = ref(false)
const form = reactive({
  title: '',
  category: DEFAULT_KNOWLEDGE_CATEGORY,
  tags: '',
  content: '',
})

function reset(): void {
  form.title = props.title || props.refLabel || '知识沉淀'
  form.category = DEFAULT_KNOWLEDGE_CATEGORY
  form.tags = ''
  form.content = props.content || ''
  void categoryStore.load()
}

async function submit(): Promise<void> {
  if (!form.title.trim()) {
    ElMessage.warning('标题为必填项')
    return
  }
  if (!form.content.trim()) {
    ElMessage.warning('正文为空，无法提交')
    return
  }
  saving.value = true
  try {
    const category = form.category.trim() || DEFAULT_KNOWLEDGE_CATEGORY
    if (!categoryStore.names.includes(category)) {
      await categoryStore.create(category)
    }
    await knowledgeApi.createDraft({
      title: form.title.trim(),
      content: form.content,
      category,
      tags: form.tags,
      moduleId: props.moduleId,
      sourceType: 'manual',
      refType: props.refType || undefined,
      refId: props.refId || undefined,
      refLabel: props.refLabel || undefined,
    })
    ElMessage.success('已进入待审核池，审核通过后才计入数字资产')
    emit('saved')
    emit('update:modelValue', false)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  } finally {
    saving.value = false
  }
}
</script>
