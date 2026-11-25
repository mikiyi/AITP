<script setup lang="ts">
/**
 * 业务对象持久附件面板：缺陷 / 用例 / 轮次 / 探索会话等统一复用。
 *
 * - ownerId 已存在时：直接上传到服务端，并支持删除；
 * - ownerId 为空（新建表单）时：文件先留在本地，调用 `flush(ownerId)` 在保存成功后统一上传；
 * - 重复内容由后端按 sha256 幂等跳过，重试不会产生重复附件。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { attachmentApi } from '@/api'
import type { BusinessAttachment } from '@/api/types'

interface PendingFile {
  key: string
  file: File
  previewUrl: string
}

interface AttachmentRow {
  key: string
  name: string
  size: number
  kind: 'image' | 'file'
  previewUrl: string
  downloadUrl: string
  pending: boolean
  attachmentId: number | null
  pendingIndex: number
}

const props = withDefaults(defineProps<{
  ownerType: string
  ownerId: number | null
  title?: string
  hint?: string
}>(), {
  title: '附件',
  hint: '',
})

const MAX_FILES = 50
const MAX_BYTES = 32 * 1024 * 1024
const defaultHint = '支持文档、日志、接口报文、录屏、图片等；单文件 ≤ 32MB，单个对象最多 50 个'
const previewSuffixes = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']

const existing = ref<BusinessAttachment[]>([])
const pending = ref<PendingFile[]>([])
const loading = ref(false)
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const total = computed(() => existing.value.length + pending.value.length)

function previewableFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return previewSuffixes.some((suffix) => name.endsWith(suffix))
}

function previewUrlOf(file: File): string {
  return previewableFile(file) || file.type.startsWith('image/') ? URL.createObjectURL(file) : ''
}

function clearPending(): void {
  pending.value.forEach((item) => {
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
  })
  pending.value = []
}

async function reload(): Promise<void> {
  if (!props.ownerId) {
    existing.value = []
    return
  }
  loading.value = true
  try {
    const data = await attachmentApi.list(props.ownerType, props.ownerId)
    existing.value = data.list ?? []
  } catch {
    existing.value = []
  } finally {
    loading.value = false
  }
}

watch(() => [props.ownerType, props.ownerId], () => {
  void reload()
}, { immediate: true })

/** 新建表单保存成功后调用：把本地待上传文件统一上传到刚创建的对象上。 */
async function flush(ownerId: number): Promise<number> {
  if (!pending.value.length) {
    return 0
  }
  uploading.value = true
  try {
    const form = new FormData()
    form.append('ownerType', props.ownerType)
    form.append('ownerId', String(ownerId))
    pending.value.forEach((item) => form.append('files', item.file))
    const result = await attachmentApi.upload(form)
    existing.value = [...existing.value, ...result.list]
    clearPending()
    return result.list.length
  } finally {
    uploading.value = false
  }
}

async function uploadNow(files: File[]): Promise<void> {
  if (!props.ownerId) {
    return
  }
  uploading.value = true
  try {
    const form = new FormData()
    form.append('ownerType', props.ownerType)
    form.append('ownerId', String(props.ownerId))
    files.forEach((file) => form.append('files', file))
    const result = await attachmentApi.upload(form)
    existing.value = [...existing.value, ...result.list]
    if (result.skipped.length) {
      ElMessage.info(`已跳过 ${result.skipped.length} 个重复附件`)
    }
    if (result.list.length) {
      ElMessage.success(`已上传 ${result.list.length} 个附件`)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '附件上传失败')
  } finally {
    uploading.value = false
  }
}

function addFiles(files: File[]): void {
  if (!files.length) {
    return
  }
  if (total.value + files.length > MAX_FILES) {
    ElMessage.warning(`单个对象最多 ${MAX_FILES} 个附件（当前已有 ${total.value} 个）`)
    return
  }
  const oversize = files.find((file) => file.size > MAX_BYTES)
  if (oversize) {
    ElMessage.warning(`附件「${oversize.name}」超过 32MB 限制`)
    return
  }
  if (props.ownerId) {
    void uploadNow(files)
    return
  }
  files.forEach((file, index) => {
    pending.value.push({
      key: `${Date.now()}-${index}-${file.name}`,
      file,
      previewUrl: previewUrlOf(file),
    })
  })
  ElMessage.success(`已选择 ${files.length} 个附件，保存后上传`)
}

function pick(): void {
  fileInput.value?.click()
}

function onPick(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  addFiles(files)
}

async function remove(row: AttachmentRow): Promise<void> {
  if (row.pending) {
    const target = pending.value[row.pendingIndex]
    if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl)
    pending.value = pending.value.filter((_, index) => index !== row.pendingIndex)
    return
  }
  if (!row.attachmentId) {
    return
  }
  try {
    await attachmentApi.remove(row.attachmentId)
    existing.value = existing.value.filter((item) => item.id !== row.attachmentId)
    ElMessage.success(`已删除 ${row.name}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除附件失败')
  }
}

const rows = computed<AttachmentRow[]>(() => {
  const result: AttachmentRow[] = existing.value.map((item) => ({
    key: `saved-${item.id}`,
    name: item.fileName,
    size: item.sizeBytes,
    kind: item.previewable ? 'image' : 'file',
    previewUrl: attachmentApi.fileUrl(item.id),
    downloadUrl: attachmentApi.fileUrl(item.id),
    pending: false,
    attachmentId: item.id,
    pendingIndex: -1,
  }))
  pending.value.forEach((item, index) => {
    result.push({
      key: item.key,
      name: item.file.name,
      size: item.file.size,
      kind: previewableFile(item.file) ? 'image' : 'file',
      previewUrl: item.previewUrl,
      downloadUrl: item.previewUrl,
      pending: true,
      attachmentId: null,
      pendingIndex: index,
    })
  })
  return result
})

const imageRows = computed(() => rows.value.filter((row) => row.kind === 'image'))
const imagePreviewUrls = computed(() => imageRows.value.map((row) => row.previewUrl))

function imageIndex(row: AttachmentRow): number {
  return Math.max(0, imageRows.value.findIndex((item) => item.key === row.key))
}

function sizeText(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }
  if (bytes >= 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  return `${bytes} B`
}

function extensionText(name: string): string {
  const parts = name.split('.')
  return parts.length > 1 ? (parts.pop() ?? '').toUpperCase().slice(0, 5) : 'FILE'
}

onBeforeUnmount(clearPending)

/** 清空本地状态：新建/编辑弹窗重新打开时调用，避免上一轮未保存的文件残留。 */
function reset(): void {
  clearPending()
  existing.value = []
}

defineExpose({ flush, reload, reset })
</script>

<template>
  <div class="attachment-manager">
    <div class="attachment-manager__head">
      <div class="attachment-manager__title">
        <span class="attachment-manager__label">{{ title }}</span>
        <el-tag size="small" effect="plain">{{ total }} 个</el-tag>
      </div>
      <div>
        <el-button size="small" :loading="uploading" @click="pick">上传附件</el-button>
        <input ref="fileInput" type="file" multiple hidden @change="onPick" />
      </div>
    </div>
    <p class="muted attachment-manager__hint">{{ hint || defaultHint }}</p>

    <el-skeleton v-if="loading" :rows="1" animated />
    <el-empty v-else-if="!rows.length" description="暂无附件" :image-size="48" />
    <div v-else class="attachment-manager__list">
      <div v-for="row in rows" :key="row.key" class="attachment-item">
        <el-image
          v-if="row.kind === 'image'"
          class="attachment-item__thumb"
          :src="row.previewUrl"
          :preview-src-list="imagePreviewUrls"
          :initial-index="imageIndex(row)"
          fit="cover"
          preview-teleported
        >
          <template #error>
            <div class="attachment-item__thumb-fallback">{{ extensionText(row.name) }}</div>
          </template>
        </el-image>
        <div v-else class="attachment-item__icon">{{ extensionText(row.name) }}</div>

        <div class="attachment-item__body">
          <div class="attachment-item__name" :title="row.name">{{ row.name }}</div>
          <div class="attachment-item__meta">
            <span>{{ sizeText(row.size) }}</span>
            <el-tag v-if="row.pending" size="small" type="warning" effect="plain">保存后上传</el-tag>
          </div>
        </div>

        <div class="attachment-item__actions">
          <a
            class="attachment-item__download"
            :href="row.downloadUrl"
            :download="row.name"
            target="_blank"
            rel="noopener"
          >下载</a>
          <el-button
            link
            type="danger"
            size="small"
            class="attachment-item__remove"
            @click="remove(row)"
          >删除</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.attachment-manager {
  width: 100%;
}
.attachment-manager__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.attachment-manager__title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.attachment-manager__label {
  font-weight: 600;
  font-size: 13px;
}
.attachment-manager__hint {
  margin: 6px 0 10px;
  line-height: 1.6;
  font-size: 12px;
}
.attachment-manager__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.attachment-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  background: var(--aitp-subtle);
}
.attachment-item__thumb {
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  border-radius: 4px;
  overflow: hidden;
  cursor: zoom-in;
}
.attachment-item__thumb-fallback,
.attachment-item__icon {
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  font-size: 11px;
  font-weight: 600;
}
.attachment-item__body {
  flex: 1;
  min-width: 0;
}
.attachment-item__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.attachment-item__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.attachment-item__actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.attachment-item__download {
  color: var(--el-color-primary);
  font-size: 12px;
  text-decoration: none;
}
</style>
