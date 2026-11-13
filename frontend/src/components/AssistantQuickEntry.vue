<template>
  <el-tooltip content="知识库问答助手 · 一键直达" placement="bottom">
    <button
      type="button"
      class="aqe"
      aria-label="打开知识库问答助手"
      @click="go"
    >
      <span class="aqe__ring" aria-hidden="true" />
      <span class="aqe__border" aria-hidden="true" />
      <span class="aqe__inner">
        <span class="aqe__icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.3c-1.5 0-2.9-.36-4.1-1L3 20l1.3-5.1A8.3 8.3 0 1 1 21 11.5Z" />
            <path class="aqe__spark" d="M12 7.6l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9.9-2.1Z" fill="currentColor" stroke="none" />
          </svg>
        </span>
        <span class="aqe__label">问答助手</span>
        <span class="aqe__shine" aria-hidden="true" />
      </span>
    </button>
  </el-tooltip>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

function go(): void {
  void router.push('/knowledge/assistant')
}
</script>

<style scoped>
.aqe {
  position: relative;
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0;
  border: none;
  background: transparent;
  border-radius: 999px;
  cursor: pointer;
  isolation: isolate;
  flex-shrink: 0;
}

/* 旋转的锥形渐变描边，让入口在顶栏里“活”起来 */
.aqe__border {
  position: absolute;
  inset: 0;
  border-radius: 999px;
  padding: 1.5px;
  background: conic-gradient(
    from var(--aqe-angle, 0deg),
    var(--el-color-primary),
    var(--el-color-success),
    var(--el-color-warning),
    var(--el-color-danger),
    var(--el-color-primary)
  );
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite: exclude;
  animation: aqe-spin 3.2s linear infinite;
  z-index: 0;
}

@property --aqe-angle {
  syntax: '<angle>';
  initial-value: 0deg;
  inherits: false;
}

@keyframes aqe-spin {
  to { --aqe-angle: 360deg; }
}

/* 外圈呼吸光晕 */
.aqe__ring {
  position: absolute;
  inset: -3px;
  border-radius: 999px;
  background: radial-gradient(closest-side, color-mix(in srgb, var(--el-color-primary) 35%, transparent), transparent);
  animation: aqe-pulse 2.2s ease-in-out infinite;
  z-index: -1;
}

@keyframes aqe-pulse {
  0%, 100% { opacity: .35; transform: scale(.96); }
  50% { opacity: .85; transform: scale(1.05); }
}

.aqe__inner {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 100%;
  padding: 0 14px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--el-color-primary) 12%, var(--aitp-panel));
  color: var(--el-color-primary);
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  z-index: 1;
  transition: transform .15s ease, box-shadow .15s ease;
}

.aqe:hover .aqe__inner {
  transform: translateY(-1px);
  box-shadow: 0 4px 14px color-mix(in srgb, var(--el-color-primary) 35%, transparent);
}

.aqe:active .aqe__inner {
  transform: translateY(0);
}

.aqe__icon {
  display: inline-flex;
  width: 16px;
  height: 16px;
}

.aqe__icon svg {
  width: 100%;
  height: 100%;
}

.aqe__spark {
  transform-origin: 12px 10.5px;
  animation: aqe-twinkle 1.6s ease-in-out infinite;
}

@keyframes aqe-twinkle {
  0%, 100% { opacity: .55; transform: scale(.8); }
  50% { opacity: 1; transform: scale(1.15); }
}

/* 扫过的高光 */
.aqe__shine {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 40%;
  left: -60%;
  background: linear-gradient(105deg, transparent, color-mix(in srgb, #fff 45%, transparent), transparent);
  animation: aqe-shine 2.8s ease-in-out infinite;
  pointer-events: none;
}

@keyframes aqe-shine {
  0% { left: -60%; }
  55%, 100% { left: 130%; }
}

@media (prefers-reduced-motion: reduce) {
  .aqe__border, .aqe__ring, .aqe__spark, .aqe__shine {
    animation: none;
  }
}
</style>