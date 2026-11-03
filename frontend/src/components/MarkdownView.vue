<template>
  <!-- markdown-it 渲染 + DOMPurify 消毒：报告里的 <script> 必须被转义且不执行 -->
  <div class="markdown-body" v-html="rendered" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = withDefaults(
  defineProps<{
    source: string
    breaks?: boolean
    linkify?: boolean
  }>(),
  { breaks: true, linkify: true },
)

const md = new MarkdownIt({
  html: false,
  breaks: props.breaks,
  linkify: props.linkify,
  typographer: false,
})

const rendered = computed(() =>
  DOMPurify.sanitize(md.render(props.source || ''), {
    ADD_ATTR: ['target', 'rel'],
  }),
)
</script>
