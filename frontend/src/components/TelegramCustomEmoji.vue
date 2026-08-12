<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import TgsSticker from './TgsSticker.vue'

const props = defineProps<{
  documentId: string
  fallback: string
}>()

const source = ref('')
const mimeType = ref('')
const failed = ref(false)
const errorDetail = ref('')

onMounted(async () => {
  try {
    const response = await fetch(`/api/archive/custom-emojis/${encodeURIComponent(props.documentId)}`, {
      credentials: 'include',
    })
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new Error(body?.detail || `自定义 Emoji 请求失败（${response.status}）`)
    }
    const blob = await response.blob()
    mimeType.value = blob.type || response.headers.get('Content-Type') || ''
    source.value = URL.createObjectURL(blob)
  } catch (error) {
    failed.value = true
    errorDetail.value = error instanceof Error ? error.message : '自定义 Emoji 加载失败'
  }
})

onBeforeUnmount(() => {
  if (source.value) URL.revokeObjectURL(source.value)
})
</script>

<template>
  <span class="telegram-custom-emoji" role="img" :aria-label="fallback" :title="failed ? errorDetail : undefined">
    <TgsSticker v-if="source && mimeType === 'application/x-tgsticker'" :src="source" :alt="fallback" />
    <video v-else-if="source && mimeType === 'video/webm'" :src="source" autoplay muted loop playsinline></video>
    <img v-else-if="source" :src="source" :alt="fallback" />
    <span v-else :class="{ loading: !failed }">{{ fallback }}</span>
  </span>
</template>

<style scoped>
.telegram-custom-emoji { width: 1.25em; height: 1.25em; margin: 0 .02em; display: inline-grid; place-items: center; overflow: hidden; vertical-align: -.27em; line-height: 1; }
.telegram-custom-emoji img, .telegram-custom-emoji video { width: 100%; height: 100%; display: block; object-fit: contain; }
.telegram-custom-emoji > span { display: inline-block; line-height: 1; }.telegram-custom-emoji > span.loading { opacity: .52; animation: custom-emoji-pulse 1.1s ease-in-out infinite alternate; }
@keyframes custom-emoji-pulse { to { opacity: .9; } }
@media (prefers-reduced-motion: reduce) { .telegram-custom-emoji > span.loading { animation: none; } }
</style>
