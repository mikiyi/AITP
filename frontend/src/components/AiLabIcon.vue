<script setup lang="ts">
import { useId } from 'vue'

const maskId = `ai-lab-screen-${useId()}`
</script>

<template>
  <svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true">
    <defs>
      <mask :id="maskId" maskUnits="userSpaceOnUse" x="0" y="0" width="24" height="24">
        <rect width="24" height="24" fill="#fff" />
        <text
          x="12"
          y="13"
          text-anchor="middle"
          font-family="Arial, Helvetica, sans-serif"
          font-size="8.4"
          font-weight="700"
          letter-spacing="0.2"
          fill="#000"
        >AI</text>
      </mask>
    </defs>
    <rect x="2.5" y="3.5" width="19" height="13" rx="2.2" fill="currentColor" :mask="`url(#${maskId})`" />
    <rect x="11" y="16.5" width="2" height="2.8" fill="currentColor" />
    <rect x="8" y="19.3" width="8" height="1.4" rx="0.7" fill="currentColor" />
  </svg>
</template>
