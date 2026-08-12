<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ArchiveMessageMedia } from '../api'

const props = defineProps<{
  media: ArchiveMessageMedia
  content: Record<string, any>
  own?: boolean
}>()

const audio = ref<HTMLAudioElement | null>(null)
const playing = ref(false)
const currentTime = ref(0)
const actualDuration = ref(0)
const playbackRate = ref(1)
const failed = ref(false)
const playerId = `archive-audio-${props.media.id}`

const isVoice = computed(() => props.media.type === 'voice' || Boolean(props.content.voice))
const duration = computed(() => actualDuration.value || Number(props.content.duration || 0))
const progress = computed(() => duration.value > 0 ? Math.min(1, currentTime.value / duration.value) : 0)
const title = computed(() => props.content.title || props.media.name || '音频')
const performer = computed(() => props.content.performer || '未知艺术家')

function formatDuration(value: number) {
  const seconds = Math.max(0, Math.floor(Number.isFinite(value) ? value : 0))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const rest = seconds % 60
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
    : `${minutes}:${String(rest).padStart(2, '0')}`
}

function decodeWaveform(value: unknown) {
  if (typeof value !== 'string' || !/^[0-9a-f]+$/i.test(value)) return []
  const bytes = new Uint8Array(value.match(/.{1,2}/g)?.map((part) => Number.parseInt(part, 16)) || [])
  const count = Math.floor(bytes.length * 8 / 5)
  const result: number[] = []
  for (let index = 0; index < count; index += 1) {
    const bitIndex = index * 5
    const byteIndex = Math.floor(bitIndex / 8)
    const shift = bitIndex % 8
    const word = bytes[byteIndex] | ((bytes[byteIndex + 1] || 0) << 8)
    result.push((word >> shift) & 0x1f)
  }
  return result
}

function fitWaveform(values: number[], count: number) {
  if (!values.length) {
    return Array.from({ length: count }, (_, index) => 5 + Math.abs(Math.sin(index * 1.73)) * 11)
  }
  const result: number[] = []
  const factor = values.length / count
  for (let index = 0; index < count; index += 1) {
    const source = Math.floor(index * factor)
    result.push(((values[source - 1] ?? values[0]) + values[source] + (values[source + 1] ?? values.at(-1)!)) / 3)
  }
  return result
}

const waveform = computed(() => {
  const fitted = fitWaveform(decodeWaveform(props.content.waveform), 44)
  const peak = Math.max(1, ...fitted)
  return fitted.map((value) => Math.max(2, Math.round(22 * value / peak)))
})

async function togglePlayback() {
  if (!audio.value || failed.value) return
  if (audio.value.paused) {
    window.dispatchEvent(new CustomEvent('archive-audio-play', { detail: playerId }))
    try {
      await audio.value.play()
    } catch {
      failed.value = true
    }
  } else {
    audio.value.pause()
  }
}

function seek(event: MouseEvent | PointerEvent) {
  if (!audio.value || !duration.value) return
  const target = event.currentTarget as HTMLElement
  const bounds = target.getBoundingClientRect()
  audio.value.currentTime = Math.max(0, Math.min(duration.value, duration.value * ((event.clientX - bounds.left) / bounds.width)))
}

function cyclePlaybackRate() {
  const rates = [1, 1.5, 2]
  const next = rates[(rates.indexOf(playbackRate.value) + 1) % rates.length]
  playbackRate.value = next
  if (audio.value) audio.value.playbackRate = next
}

function handleOtherPlayer(event: Event) {
  if ((event as CustomEvent<string>).detail !== playerId) audio.value?.pause()
}

function syncMetadata() {
  if (audio.value && Number.isFinite(audio.value.duration)) actualDuration.value = audio.value.duration
}

onMounted(() => window.addEventListener('archive-audio-play', handleOtherPlayer))
onBeforeUnmount(() => window.removeEventListener('archive-audio-play', handleOtherPlayer))
</script>

<template>
  <div :class="['telegram-audio', { voice: isVoice, music: !isVoice, own }]">
    <audio
      ref="audio"
      preload="metadata"
      :src="media.url"
      @loadedmetadata="syncMetadata"
      @durationchange="syncMetadata"
      @timeupdate="currentTime = audio?.currentTime || 0"
      @play="playing = true"
      @pause="playing = false"
      @ended="playing = false; currentTime = 0"
      @error="failed = true"
    ></audio>
    <button class="telegram-audio-toggle" :aria-label="playing ? '暂停' : '播放'" :disabled="failed" @click="togglePlayback">
      <svg v-if="playing" viewBox="0 0 24 24"><path d="M7 5h4v14H7zM14 5h4v14h-4z" /></svg>
      <svg v-else viewBox="0 0 24 24"><path d="m8 5 11 7-11 7V5Z" /></svg>
    </button>

    <div v-if="isVoice" class="telegram-voice-content">
      <div class="telegram-waveform" role="slider" aria-label="语音进度" :aria-valuenow="Math.round(progress * 100)" tabindex="0" @click="seek">
        <i
          v-for="(height, index) in waveform"
          :key="index"
          :class="{ played: index / waveform.length <= progress }"
          :style="{ height: `${height}px` }"
        ></i>
      </div>
      <span class="telegram-audio-duration">{{ formatDuration(currentTime || duration) }}</span>
    </div>

    <div v-else class="telegram-music-content">
      <strong :title="title">{{ title }}</strong>
      <div v-if="playing || currentTime > 0" class="telegram-music-progress-row">
        <span>{{ formatDuration(currentTime) }}</span>
        <div class="telegram-music-seek" role="slider" aria-label="音频进度" :aria-valuenow="Math.round(progress * 100)" tabindex="0" @click="seek"><i :style="{ width: `${progress * 100}%` }"></i></div>
      </div>
      <small v-else><span>{{ formatDuration(duration) }}</span><b v-if="performer">·</b>{{ performer }}</small>
    </div>

    <button v-if="isVoice && (playing || currentTime > 0)" class="telegram-audio-speed" aria-label="切换播放速度" @click="cyclePlaybackRate">{{ playbackRate }}×</button>
    <span v-if="failed" class="telegram-audio-error">无法播放</span>
  </div>
</template>

<style scoped>
.telegram-audio { --audio-active: #3390ec; --audio-inactive: rgba(74, 113, 145, .3); position: relative; min-width: min(20rem, 72vw); min-height: 3rem; display: flex; align-items: center; gap: .7rem; color: inherit; }
.telegram-audio.own { --audio-active: #fff; --audio-inactive: rgba(255, 255, 255, .38); }
.telegram-audio audio { display: none; }
.telegram-audio-toggle { width: 3rem; height: 3rem; padding: 0; flex: none; border: 0; border-radius: 50%; display: grid; place-items: center; color: white; background: var(--audio-active); cursor: pointer; transition: transform 90ms ease, opacity 130ms ease; }
.telegram-audio-toggle:active { transform: scale(.92); }.telegram-audio-toggle:disabled { opacity: .5; cursor: default; }
.telegram-audio-toggle svg { width: 1.45rem; height: 1.45rem; fill: currentColor; }.telegram-audio-toggle svg path[d^="m8"] { transform: translateX(1px); }
.telegram-voice-content, .telegram-music-content { min-width: 0; flex: 1; }
.telegram-voice-content { display: grid; align-content: center; gap: .12rem; }
.telegram-waveform { height: 1.45rem; display: flex; align-items: center; gap: 2px; cursor: pointer; touch-action: none; }
.telegram-waveform i { width: 2px; min-height: 2px; flex: 1 1 2px; max-width: 3px; border-radius: 999px; background: var(--audio-inactive); transition: background 80ms linear; }
.telegram-waveform i.played { background: var(--audio-active); }
.telegram-audio-duration { color: color-mix(in srgb, currentColor 70%, transparent); font-size: .7rem; font-variant-numeric: tabular-nums; }
.telegram-music-content { display: grid; gap: .13rem; }
.telegram-music-content strong { overflow: hidden; color: inherit; text-overflow: ellipsis; white-space: nowrap; font-size: .78rem; line-height: 1.25; }
.telegram-music-content small, .telegram-music-progress-row { min-width: 0; display: flex; align-items: center; gap: .3rem; color: color-mix(in srgb, currentColor 68%, transparent); font-size: .68rem; font-weight: 500; font-variant-numeric: tabular-nums; }
.telegram-music-content small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.telegram-music-content small b { font-weight: 400; }
.telegram-music-progress-row > span { min-width: 2.45rem; }
.telegram-music-seek { position: relative; height: 1.1rem; flex: 1; cursor: pointer; }
.telegram-music-seek::before, .telegram-music-seek i { content: ''; position: absolute; top: .5rem; left: 0; height: 2px; border-radius: 999px; }
.telegram-music-seek::before { width: 100%; background: var(--audio-inactive); }.telegram-music-seek i { background: var(--audio-active); }
.telegram-music-seek i::after { content: ''; position: absolute; top: -4px; right: -5px; width: 10px; height: 10px; border-radius: 50%; background: var(--audio-active); }
.telegram-audio-speed { min-width: 2.25rem; height: 1.65rem; padding: 0 .35rem; border: 0; border-radius: 999px; color: var(--audio-active); background: color-mix(in srgb, var(--audio-active) 13%, transparent); cursor: pointer; font-size: .64rem; font-weight: 800; }
.telegram-audio-error { position: absolute; right: 0; bottom: -.1rem; color: #ef6b72; font-size: .58rem; }
@media (prefers-reduced-motion: reduce) { .telegram-audio-toggle { transition: none; } }
</style>
