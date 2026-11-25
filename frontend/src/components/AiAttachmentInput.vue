<script setup lang="ts">
/**
 * AI 入口统一的附件输入：文档导入 + 图片上传/粘贴。
 *
 * - 文档：抽取为纯文本后通过 `document` 事件交给父组件并入输入框；
 * - 图片：上传时后端已去水印，通过 `update:attachmentIds` 回传编号；
 * - Ctrl+V 粘贴图片同样先走上传接口处理，再进入附件列表。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Paperclip, Picture } from '@element-plus/icons-vue'
import { labApi } from '@/api'
import type { DocumentAttachment, ImageAttachment } from '@/api/types'

const props = withDefaults(defineProps<{
  attachmentIds?: string[]
  compact?: boolean
  /** 内嵌到输入框内使用：按钮改为轻量链接样式并带图标 */
  inline?: boolean
  paste?: boolean
}>(), {
  attachmentIds: () => [],
  compact: false,
  inline: false,
  paste: true,
})

const emit = defineEmits<{
  'update:attachmentIds': [string[]]
  document: [DocumentAttachment]
}>()

const DOC_ACCEPT = '.docx,.doc,.xlsx,.xlsm,.csv,.md,.markdown,.txt,.log,.json,.html,.htm,.pdf'
const IMAGE_ACCEPT =
  '.jpg,.jpeg,.png,.apng,.gif,.svg,.webp,.bmp,.tif,.tiff,.psd,.heif,.heic,.avif'

const docInput = ref<HTMLInputElement | null>(null)
const imageInput = ref<HTMLInputElement | null>(null)
const documents = ref<DocumentAttachment[]>([])
const images = ref<ImageAttachment[]>([])
const importing = ref(false)
const uploading = ref(false)
/** 与后端 ai_input_service.MAX_IMAGES 保持一致。 */
const MAX_IMAGES = 4

function syncIds(): void {
  // 文档与图片都交给后端统一解析：文档读正文、图片走多模态
  emit('update:attachmentIds', [
    ...documents.value.map((item) => item.attachmentId),
    ...images.value.map((item) => item.attachmentId),
  ])
}

async function uploadDocumentFile(file: File): Promise<void> {
  importing.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const result = await labApi.uploadDocument(form)
    documents.value = [...documents.value, result]
    syncIds()
    emit('document', result)
    if (result.truncated) {
      ElMessage.warning(`文档过长，已截取前 ${result.chars} 字`)
    } else {
      ElMessage.success(`已导入《${result.fileName}》${result.chars} 字`)
    }
    if (result.note) ElMessage.info(result.note)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '文档导入失败')
  } finally {
    importing.value = false
  }
}

async function uploadImageFile(file: File): Promise<void> {
  if (images.value.length >= MAX_IMAGES) {
    ElMessage.warning(`单次最多 ${MAX_IMAGES} 张图片`)
    return
  }
  uploading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const result = await labApi.uploadImage(form)
    images.value = [...images.value, result]
    syncIds()
    ElMessage.success(result.watermarkApplied ? '图片已去水印' : result.note || '图片已处理')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '图片处理失败')
  } finally {
    uploading.value = false
  }
}

function onDocPick(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) void uploadDocumentFile(file)
}

function onImagePick(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) void uploadImageFile(file)
}

async function removeDocument(index: number): Promise<void> {
  const target = documents.value[index]
  documents.value = documents.value.filter((_, i) => i !== index)
  syncIds()
  if (!target) return
  try {
    await labApi.deleteAttachment(target.attachmentId)
  } catch {
    // 附件可能已被清理，移除 UI 即可
  }
}

async function removeImage(index: number): Promise<void> {
  const target = images.value[index]
  images.value = images.value.filter((_, i) => i !== index)
  syncIds()
  if (!target) return
  try {
    await labApi.deleteAttachment(target.attachmentId)
  } catch {
    // 附件可能已被清理，移除 UI 即可
  }
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(new Error('读取剪贴板图片失败'))
    reader.readAsDataURL(file)
  })
}

async function onPaste(event: ClipboardEvent): Promise<void> {
  const items = event.clipboardData?.items
  if (!items) return
  for (const item of Array.from(items)) {
    if (item.kind !== 'file' || !item.type.startsWith('image/')) continue
    const file = item.getAsFile()
    if (!file) continue
    event.preventDefault()
    if (images.value.length >= MAX_IMAGES) {
      ElMessage.warning(`单次最多 ${MAX_IMAGES} 张图片`)
      return
    }
    uploading.value = true
    try {
      const dataUrl = await readAsDataUrl(file)
      const result = await labApi.uploadImageDataUrl(dataUrl)
      images.value = [...images.value, result]
      syncIds()
      ElMessage.success(result.watermarkApplied ? '图片已去水印' : result.note || '图片已处理')
    } catch (err) {
      ElMessage.error(err instanceof Error ? err.message : '粘贴图片失败')
    } finally {
      uploading.value = false
    }
    return
  }
}

function reset(): void {
  documents.value = []
  images.value = []
  emit('update:attachmentIds', [])
}

/** 清空并删除服务端附件；发送完成后或组件卸载时调用，避免 data/attachments 越积越多。 */
async function dispose(extraIds: string[] = []): Promise<void> {
  const ids = new Set(extraIds)
  documents.value.forEach((item) => ids.add(item.attachmentId))
  images.value.forEach((item) => ids.add(item.attachmentId))
  reset()
  if (!ids.size) return
  await Promise.allSettled([...ids].map((id) => labApi.deleteAttachment(id)))
}

onMounted(() => {
  if (props.paste) window.addEventListener('paste', onPaste)
})
onBeforeUnmount(() => {
  window.removeEventListener('paste', onPaste)
  void dispose()
})

defineExpose({ reset, dispose })
</script>

<template>
  <div class="ai-attach" :class="[{ 'ai-attach--compact': compact }, { 'ai-attach--inline': inline }]">
    <div class="ai-attach__actions">
      <el-button
        size="small"
        :link="inline"
        :icon="inline ? Paperclip : undefined"
        :loading="importing"
        @click="docInput?.click()"
      >导入文档</el-button>
      <el-button
        size="small"
        :link="inline"
        :icon="inline ? Picture : undefined"
        :loading="uploading"
        @click="imageInput?.click()"
      >上传图片</el-button>
      <span class="ai-attach__hint">文档自动抽取文本；图片先去水印，支持 Ctrl+V 粘贴</span>
    </div>
    <input ref="docInput" type="file" :accept="DOC_ACCEPT" hidden @change="onDocPick" />
    <input ref="imageInput" type="file" :accept="IMAGE_ACCEPT" hidden @change="onImagePick" />
    <div v-if="documents.length" class="ai-attach__docs">
      <el-tag
        v-for="(item, index) in documents"
        :key="item.attachmentId"
        closable
        size="small"
        @close="removeDocument(index)"
      >{{ item.fileName }} · {{ item.chars }} 字</el-tag>
    </div>
    <div v-if="images.length" class="ai-attach__images">
      <div v-for="(item, index) in images" :key="item.attachmentId" class="ai-attach__image">
        <el-image
          :src="item.previewUrl"
          :preview-src-list="images.map((img) => img.previewUrl)"
          :initial-index="index"
          fit="cover"
        />
        <span class="ai-attach__badge">{{ item.watermarkApplied ? '已去水印' : '原图' }}</span>
        <button type="button" class="ai-attach__remove" title="移除" @click="removeImage(index)">×</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-attach {
  width: 100%;
}
.ai-attach__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.ai-attach__hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.ai-attach__docs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.ai-attach__images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.ai-attach__image {
  position: relative;
  width: 72px;
  height: 72px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--el-border-color);
}
.ai-attach__image :deep(.el-image) {
  width: 100%;
  height: 100%;
  display: block;
}
.ai-attach__badge {
  position: absolute;
  left: 0;
  bottom: 0;
  padding: 0 4px;
  font-size: 11px;
  line-height: 18px;
  color: #fff;
  background: rgba(0, 0, 0, 0.45);
}
.ai-attach__remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  cursor: pointer;
  line-height: 18px;
}
.ai-attach--compact .ai-attach__hint {
  display: none;
}
/* 内嵌输入框的变体：链接式按钮与更小的图片预览，不撑高问题框 */
.ai-attach--inline .ai-attach__actions {
  gap: 12px;
}
.ai-attach--inline .ai-attach__docs,
.ai-attach--inline .ai-attach__images {
  margin-top: 4px;
}
.ai-attach--inline .ai-attach__image {
  width: 52px;
  height: 52px;
}
</style>
