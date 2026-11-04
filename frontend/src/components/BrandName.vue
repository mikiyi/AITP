<template>
  <span class="brand-name" :class="`is-${size}`">
    <span class="brand-name__ai">AI</span><span class="brand-name__test">Test</span><span class="brand-name__pilot">Pilot</span>
  </span>
</template>

<script setup lang="ts">
/**
 * 产品名渐变字：AI 蓝 → Test 绿 → Pilot 红 沿整词连续过渡。
 * 三个原始配色保留为渐变的三个色标，所以 AI 偏蓝、Test 偏绿、Pilot 偏红。
 */
withDefaults(
  defineProps<{
    size?: 'normal' | 'small'
  }>(),
  { size: 'normal' },
)
</script>

<style scoped>
.brand-name {
  font-weight: 700;
  letter-spacing: 0.2px;
  white-space: nowrap;
  /* 兜底色：不支持 background-clip: text 的老浏览器至少还能看到实色文字 */
  color: #2f6fed;
  /*
   * 色标按三个词的实际占比对齐（AI 0-20%、Test 20-57%、Pilot 57-100%），
   * 让每个词都以自己的主色为主，过渡只发生在词与词之间，整体仍是一条连贯色带。
   */
  background-image: linear-gradient(
    90deg,
    #2f6fed 0%,
    #2f6fed 20%,
    #67c23a 38%,
    #67c23a 55%,
    #f56c6c 68%,
    #f56c6c 100%
  );
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.brand-name.is-small {
  font-size: 14px;
}

/* 渐变画在父级上并裁切到文字，三段不再各自填色，否则会盖住渐变 */
.brand-name__ai,
.brand-name__test,
.brand-name__pilot {
  color: transparent;
}
</style>
