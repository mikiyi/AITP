<template>
  <div class="ref-refs">
    <el-tag
      :type="used ? 'warning' : 'info'"
      size="small"
      effect="plain"
      class="ref-refs__tag"
      @mouseenter="showTip($event)"
      @mouseleave="scheduleHide"
    >
      {{ used ? '已被引用' : '未被引用' }}
    </el-tag>
    <Teleport to="body">
      <div v-if="tipVisible" class="ref-refs__tip" :style="tipStyle" @mouseenter="cancelHide" @mouseleave="scheduleHide">
        <template v-if="used">
          <span v-for="(item, i) in previewRefs" :key="i">
            {{ refNoOf(item) }}<template v-if="i < previewRefs.length - 1">、</template>
          </span>
          <a v-if="moreThanPreview" class="ref-refs__more" @click.stop="openDialog">查看更多 》</a>
        </template>
        <span v-else>暂无被引用的编号</span>
      </div>
    </Teleport>

    <el-dialog v-model="dialogVisible" title="被引用的编号" width="520px" append-to-body>
      <div v-if="usedRefs.length" class="ref-refs__list">
        <div v-for="(item, i) in usedRefs" :key="i" class="ref-refs__item">
          <span class="ref-refs__no text-mono">{{ refNoOf(item) }}</span>
          <span class="ref-refs__name">{{ item.name || '-' }}</span>
          <el-tag size="small" type="info" effect="plain">{{ kindLabel(item.kind) }}</el-tag>
        </div>
      </div>
      <div v-else class="muted">暂无被引用的编号</div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

interface RefItem {
  kind?: string
  refNo?: string
  name?: string
}

const props = withDefaults(defineProps<{ refs?: RefItem[]; used?: boolean }>(), {
  refs: () => [],
  used: undefined,
})

const PREVIEW_MAX = 10
const used = computed(() => (props.used === undefined ? props.refs.length > 0 : props.used))
const usedRefs = computed(() => props.refs.filter((item) => (item.refNo || item.name || '').trim()))
const previewRefs = computed(() => usedRefs.value.slice(0, PREVIEW_MAX))
const moreThanPreview = computed(() => usedRefs.value.length > PREVIEW_MAX)

const tipVisible = ref(false)
const dialogVisible = ref(false)
const tipStyle = ref<Record<string, string>>({})
let hideTimer: ReturnType<typeof setTimeout> | null = null

function refNoOf(item: RefItem): string {
  return item.refNo || item.name || '-'
}

function kindLabel(kind?: string): string {
  if (kind === 'scenario') return '场景'
  if (kind === 'automation') return '自动化脚本'
  if (kind === 'schedule') return '定时调度'
  return kind || ''
}

function showTip(event: MouseEvent): void {
  cancelHide()
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const style: Record<string, string> = {
    left: `${Math.max(8, Math.round(rect.left))}px`,
  }
  const below = rect.bottom + 6
  if (below + 120 < window.innerHeight) {
    style.top = `${Math.round(below)}px`
  } else {
    style.top = `${Math.max(8, Math.round(rect.top - 120))}px`
  }
  tipStyle.value = style
  tipVisible.value = true
}

function scheduleHide(): void {
  cancelHide()
  hideTimer = setTimeout(() => {
    tipVisible.value = false
  }, 160)
}

function cancelHide(): void {
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
}

function openDialog(): void {
  tipVisible.value = false
  dialogVisible.value = true
}

onBeforeUnmount(() => cancelHide())
</script>

<style scoped>
.ref-refs { display: inline-flex; }
.ref-refs__tag { cursor: default; }
.ref-refs__tip {
  position: fixed;
  z-index: 4000;
  max-width: min(520px, calc(100vw - 16px));
  max-height: 40vh;
  overflow-y: auto;
  padding: 6px 10px;
  border-radius: 4px;
  background: #303133;
  color: #ffffff;
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
  white-space: normal;
  box-shadow: 0 2px 12px rgb(0 0 0 / 24%);
}
.ref-refs__more { color: #e6a23c; margin-left: 4px; cursor: pointer; font-weight: 600; }
.ref-refs__list { display: flex; flex-direction: column; gap: 8px; }
.ref-refs__item { display: flex; align-items: center; gap: 8px; }
.ref-refs__no { min-width: 90px; }
.ref-refs__name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
