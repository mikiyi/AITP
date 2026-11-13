<template>
  <div class="sb">
    <!-- 后端连通 -->
    <el-tooltip placement="bottom-end" :show-after="120">
      <template #content>
        <div class="sb-tip">
          <div class="sb-tip__title">后端服务</div>
          <div>状态：{{ online ? '在线' : '未连接' }}</div>
          <div>地址：{{ hostText }}</div>
          <div v-if="health">服务版本：v{{ health.appVersion }}</div>
          <div v-if="health">服务时间：{{ health.serverTime || '-' }}</div>
          <div v-if="health">
            任务队列：排队 {{ health.taskQueue?.queued ?? 0 }} · 执行中 {{ health.taskQueue?.running ?? 0 }}
          </div>
          <div v-if="!online" class="sb-tip__warn">后端未启动或不可达，请确认服务已运行。</div>
        </div>
      </template>
      <button type="button" class="sb__badge" :class="online ? 'is-ok' : 'is-bad'" :aria-label="online ? '后端在线' : '后端未连接'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="3" y="4" width="18" height="7" rx="2" />
          <rect x="3" y="13" width="18" height="7" rx="2" />
          <path d="M7 7.5h.01M7 16.5h.01M11 7.5h2M11 16.5h2" />
        </svg>
        <span class="sb__dot" :class="{ 'is-pulse': online }" />
      </button>
    </el-tooltip>

    <!-- 本机 SQLite -->
    <el-tooltip placement="bottom-end" :show-after="120">
      <template #content>
        <div class="sb-tip">
          <div class="sb-tip__title">本机 SQLite</div>
          <div>状态：{{ dbStateText }}</div>
          <template v-if="db">
            <div class="sb-tip__path">路径：{{ db.path }}</div>
            <div>文件：{{ sizeText }} · 结构版本 v{{ db.version }}</div>
            <div>日志模式：{{ db.journalMode || '-' }} · 探测 {{ db.latencyMs }}ms</div>
            <div v-if="db.error" class="sb-tip__warn">{{ db.error }}</div>
          </template>
          <div v-else class="sb-tip__warn">暂无数据库快照（后端未连接或检测中）。</div>
        </div>
      </template>
      <button type="button" class="sb__badge" :class="dbClass" :aria-label="dbStateText">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <ellipse cx="12" cy="5.5" rx="8" ry="3" />
          <path d="M4 5.5v13c0 1.66 3.58 3 8 3s8-1.34 8-3v-13" />
          <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" />
        </svg>
        <span class="sb__dot" :class="{ 'is-pulse': dbOk }" />
      </button>
    </el-tooltip>

    <!-- AI 配置：本地 / LLM 前缀 + 图标 -->
    <el-tooltip placement="bottom-end" :show-after="120">
      <template #content>
        <div class="sb-tip">
          <div class="sb-tip__title">AI 配置</div>
          <template v-if="aiConfigured">
            <div>类别：{{ aiLocal ? '本地模型（不出本机）' : '第三方 LLM（会上传第三方）' }}</div>
            <div>生效模型：{{ ai!.provider }} / {{ ai!.model || '未指定' }}</div>
            <div v-if="ai!.baseUrl" class="sb-tip__path">接口地址：{{ ai!.baseUrl }}</div>
            <div v-if="ai!.profileName">档案：{{ ai!.profileName }}</div>
            <div>来源层：{{ sourceText }}</div>
            <div v-if="(ai!.candidates ?? []).length > 1">降级顺序：{{ candidateText }}</div>
            <div v-if="ai!.error" class="sb-tip__warn">{{ ai!.error }}</div>
          </template>
          <div v-else class="sb-tip__warn">尚未配置 AI 模型，请到「设置 → AI 设置」添加。</div>
        </div>
      </template>
      <button type="button" class="sb__badge sb__badge--ai" :class="aiClass" :aria-label="aiAria">
        <span v-if="aiConfigured" class="sb__prefix">{{ aiLocal ? '本地' : 'LLM' }}</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="7" y="7" width="10" height="10" rx="2" />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" />
        </svg>
        <span class="sb__dot" :class="{ 'is-pulse': aiConfigured }" />
      </button>
    </el-tooltip>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AiStatus, HealthStatus } from '@/api/types'

const props = defineProps<{
  online: boolean
  health: HealthStatus | null
  aiStatus: AiStatus | null
}>()

const hostText = computed(() => window.location.host || '127.0.0.1:8760')

const db = computed(() => props.health?.database ?? null)
const dbOk = computed(() => props.online && !!db.value?.ok)
const dbClass = computed(() => {
  if (!props.online || !db.value) return 'is-unknown'
  return db.value.ok ? 'is-ok' : 'is-bad'
})
const dbStateText = computed(() => {
  if (!props.online) return '本机SQLite 未知'
  if (!db.value) return '本机SQLite 检测中'
  return db.value.ok ? '本机SQLite 已连接' : '本机SQLite 未连接'
})

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}
const sizeText = computed(() => formatBytes(db.value?.sizeBytes ?? 0))

const ai = computed(() => props.aiStatus)
const aiConfigured = computed(() => !!ai.value?.configured)
const aiLocal = computed(() => !!ai.value?.isLocal)
const aiClass = computed(() => {
  if (!aiConfigured.value) return 'is-unknown'
  return aiLocal.value ? 'is-local' : 'is-llm'
})
const aiAria = computed(() => {
  if (!aiConfigured.value) return 'AI 未配置'
  return `${aiLocal.value ? '本地模型' : '第三方 LLM'}：${ai.value!.provider}/${ai.value!.model || '未指定'}`
})
const sourceText = computed(() => {
  const labels = ai.value?.sourceLabels ?? {}
  const parts = Object.entries(labels)
    .filter(([, label]) => label && label !== '未配置')
    .map(([field, label]) => `${field}：${label}`)
  return parts.length ? parts.join(' / ') : '未配置'
})
const candidateText = computed(() =>
  (ai.value?.candidates ?? [])
    .map((item) => `${item.profileName || item.provider}/${item.model || '未指定'}`)
    .join(' → '),
)
</script>

<style scoped>
.sb {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.sb__badge {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  height: 30px;
  min-width: 30px;
  padding: 0 7px;
  border: 1px solid var(--aitp-border);
  border-radius: 9px;
  background: var(--aitp-panel);
  color: var(--aitp-text-weak);
  cursor: default;
  transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
}

.sb__badge svg {
  width: 17px;
  height: 17px;
  flex: 0 0 auto;
}

.sb__badge:hover {
  transform: translateY(-1px);
}

.sb__dot {
  position: absolute;
  right: -3px;
  top: -3px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-color-info);
  box-shadow: 0 0 0 2px var(--aitp-panel);
}

.sb__dot.is-pulse {
  animation: sb-pulse 2s ease-in-out infinite;
}

@keyframes sb-pulse {
  0%, 100% { box-shadow: 0 0 0 2px var(--aitp-panel), 0 0 0 0 color-mix(in srgb, currentColor 45%, transparent); }
  50% { box-shadow: 0 0 0 2px var(--aitp-panel), 0 0 0 4px transparent; }
}

/* 状态着色：底 / 描边 / 图标同色系，随皮肤混色 */
.sb__badge.is-ok {
  color: var(--el-color-success);
  border-color: color-mix(in srgb, var(--el-color-success) 40%, var(--aitp-border));
  background: color-mix(in srgb, var(--el-color-success) 10%, var(--aitp-panel));
}
.sb__badge.is-ok .sb__dot { background: var(--el-color-success); }
.sb__badge.is-ok:hover { box-shadow: 0 3px 12px color-mix(in srgb, var(--el-color-success) 30%, transparent); }

.sb__badge.is-bad {
  color: var(--el-color-danger);
  border-color: color-mix(in srgb, var(--el-color-danger) 45%, var(--aitp-border));
  background: color-mix(in srgb, var(--el-color-danger) 10%, var(--aitp-panel));
}
.sb__badge.is-bad .sb__dot { background: var(--el-color-danger); }
.sb__badge.is-bad:hover { box-shadow: 0 3px 12px color-mix(in srgb, var(--el-color-danger) 30%, transparent); }

.sb__badge.is-unknown {
  color: var(--aitp-text-weak);
}
.sb__badge.is-unknown .sb__dot { background: var(--el-color-info); }

.sb__badge.is-local {
  color: var(--el-color-success);
  border-color: color-mix(in srgb, var(--el-color-success) 40%, var(--aitp-border));
  background: color-mix(in srgb, var(--el-color-success) 10%, var(--aitp-panel));
}
.sb__badge.is-local .sb__dot { background: var(--el-color-success); }

.sb__badge.is-llm {
  color: var(--el-color-primary);
  border-color: color-mix(in srgb, var(--el-color-primary) 40%, var(--aitp-border));
  background: color-mix(in srgb, var(--el-color-primary) 10%, var(--aitp-panel));
}
.sb__badge.is-llm .sb__dot { background: var(--el-color-primary); }
.sb__badge.is-llm:hover { box-shadow: 0 3px 12px color-mix(in srgb, var(--el-color-primary) 30%, transparent); }

.sb__prefix {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .02em;
  padding: 1px 5px;
  border-radius: 5px;
  background: color-mix(in srgb, currentColor 16%, transparent);
  color: inherit;
}

.sb-tip {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  line-height: 1.5;
  max-width: 340px;
}

.sb-tip__title {
  font-weight: 700;
  font-size: 12.5px;
  margin-bottom: 2px;
}

.sb-tip__path {
  word-break: break-all;
}

.sb-tip__warn {
  color: var(--el-color-warning);
}

@media (prefers-reduced-motion: reduce) {
  .sb__dot.is-pulse { animation: none; }
  .sb__badge { transition: none; }
}
</style>