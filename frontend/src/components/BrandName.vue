<template>
  <span class="brand-name" :class="[`is-${size}`, { 'is-plain': !gradient }]">
    <span class="brand-name__ai">AI</span><span class="brand-name__test">Test</span><span class="brand-name__platform">Platform</span>
  </span>
</template>

<script setup lang="ts">
/**
 * 产品名渐变字：AI 蓝 → Test 绿 → Platform 红 沿整词连续过渡。
 * 三个原始配色保留为渐变的三个色标，所以 AI 偏蓝、Test 偏绿、Platform 偏红。
 */
withDefaults(
  defineProps<{
    size?: 'normal' | 'small'
    gradient?: boolean
  }>(),
  { size: 'normal', gradient: true },
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
   * 色标按三个词的实际占比对齐（AI 0-14%、Test 14-43%、Platform 43-100%），
   * 让每个词都以自己的主色为主，过渡只发生在词与词之间，整体仍是一条连贯色带。
   */
  background-image: linear-gradient(
    90deg,
    #2f6fed 0%,
    #2f6fed 14%,
    #67c23a 28%,
    #67c23a 38%,
    #f56c6c 52%,
    #f56c6c 100%
  );
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.brand-name.is-small {
  font-size: 14px;
}

/* 纯色态：收起渐变底图并把词内颜色还原成继承色，避免残留透明字 */
.brand-name.is-plain {
  background-image: none;
  color: var(--el-text-color-primary);
  -webkit-text-fill-color: currentColor;
}

.brand-name.is-plain .brand-name__ai,
.brand-name.is-plain .brand-name__test,
.brand-name.is-plain .brand-name__platform {
  color: inherit;
}

/* 渐变画在父级上并裁切到文字，三段不再各自填色，否则会盖住渐变 */
.brand-name__ai,
.brand-name__test,
.brand-name__platform {
  color: transparent;
}
</style>
