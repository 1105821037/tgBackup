<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import type { AnimationItem } from 'lottie-web'

const props = defineProps<{
  src: string
  alt?: string | null
}>()

const host = ref<HTMLElement | null>(null)
const failed = ref(false)
const errorDetail = ref('')
let animation: AnimationItem | null = null
let observer: IntersectionObserver | null = null
let visible = false

async function unpackTgs(buffer: ArrayBuffer) {
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('当前浏览器不支持 TGS 解压')
  }
  const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'))
  return JSON.parse(await new Response(stream).text())
}

function syncPlayback() {
  if (!animation) return
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (visible && document.visibilityState === 'visible' && !reduceMotion) animation.play()
  else {
    animation.pause()
    if (reduceMotion) animation.goToAndStop(0, true)
  }
}

function handleVisibility() {
  syncPlayback()
}

onMounted(async () => {
  if (!host.value) return
  try {
    const [response, lottieModule] = await Promise.all([
      fetch(props.src, { credentials: 'include' }),
      import('lottie-web/build/player/lottie_light'),
    ])
    if (!response.ok) throw new Error(`TGS ${response.status}`)
    const animationData = await unpackTgs(await response.arrayBuffer())
    animation = lottieModule.default.loadAnimation({
      container: host.value,
      renderer: 'svg',
      loop: true,
      autoplay: false,
      animationData,
      rendererSettings: { preserveAspectRatio: 'xMidYMid meet' },
    })
    observer = new IntersectionObserver(([entry]) => {
      visible = Boolean(entry?.isIntersecting)
      syncPlayback()
    }, { threshold: .12 })
    observer.observe(host.value)
    document.addEventListener('visibilitychange', handleVisibility)
  } catch (error) {
    failed.value = true
    errorDetail.value = error instanceof Error ? error.message : 'TGS 动画加载失败'
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
  animation?.destroy()
  document.removeEventListener('visibilitychange', handleVisibility)
})
</script>

<template>
  <span ref="host" class="tgs-sticker" role="img" :aria-label="alt || '动态贴纸'" :title="failed ? errorDetail : undefined">
    <span v-if="failed" class="tgs-sticker-fallback">{{ alt || '✨' }}</span>
  </span>
</template>

<style scoped>
.tgs-sticker { width: 100%; height: 100%; display: grid; place-items: center; overflow: hidden; }
.tgs-sticker :deep(canvas), .tgs-sticker :deep(svg) { width: 100% !important; height: 100% !important; display: block; }
.tgs-sticker-fallback { font-size: clamp(3rem, 15vw, 7rem); line-height: 1; filter: drop-shadow(0 2px 3px rgba(0,0,0,.16)); }
</style>
