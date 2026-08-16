<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, type CronPreview } from '../api'
const props = defineProps<{
  label: string
  kind: 'weekly' | 'cron'
  scheduledTime: string
  weekdays: number[]
  cronExpression: string | null
}>()

const emit = defineEmits<{
  'update:kind': [value: 'weekly' | 'cron']
  'update:scheduledTime': [value: string]
  'update:weekdays': [value: number[]]
  'update:cronExpression': [value: string | null]
}>()

const dayOptions = [
  { value: 1, label: '一' }, { value: 2, label: '二' }, { value: 3, label: '三' },
  { value: 4, label: '四' }, { value: 5, label: '五' }, { value: 6, label: '六' },
  { value: 7, label: '日' },
]

const cronPreview = ref<CronPreview | null>(null)
const previewLoading = ref(false)
const previewFailed = ref(false)
let previewTimer: number | undefined
let previewSequence = 0

const cronValue = computed(() => props.cronExpression?.trim() || '')

function formatRun(value: string) {
  // Keep the wall-clock fields returned by the server. Converting with Date
  // would silently show the browser timezone instead of the scheduler timezone.
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value)
  if (!match) return value
  const [, year, month, day, hour, minute] = match
  const weekday = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][
    new Date(Date.UTC(Number(year), Number(month) - 1, Number(day))).getUTCDay()
  ]
  return `${Number(month)}月${Number(day)}日 ${weekday} ${hour}:${minute}`
}

async function loadCronPreview() {
  const expression = cronValue.value
  const sequence = ++previewSequence
  if (!expression) {
    cronPreview.value = null
    previewLoading.value = false
    previewFailed.value = false
    return
  }
  previewLoading.value = true
  previewFailed.value = false
  try {
    const result = await api<CronPreview>(`/api/rules/cron-preview?expression=${encodeURIComponent(expression)}`)
    if (sequence === previewSequence) cronPreview.value = result
  } catch {
    if (sequence === previewSequence) {
      cronPreview.value = null
      previewFailed.value = true
    }
  } finally {
    if (sequence === previewSequence) previewLoading.value = false
  }
}

watch([() => props.kind, cronValue], ([kind]) => {
  window.clearTimeout(previewTimer)
  if (kind !== 'cron') {
    ++previewSequence
    cronPreview.value = null
    previewLoading.value = false
    return
  }
  previewTimer = window.setTimeout(loadCronPreview, 300)
}, { immediate: true })

onBeforeUnmount(() => window.clearTimeout(previewTimer))

function toggleDay(day: number) {
  const next = props.weekdays.includes(day)
    ? props.weekdays.filter((value) => value !== day)
    : [...props.weekdays, day].sort()
  emit('update:weekdays', next)
}
</script>

<template>
  <div class="schedule-editor">
    <label class="setting-label">{{ label }}</label>
    <div class="segmented full schedule-kind-picker">
      <button type="button" :class="{ active: kind === 'weekly' }" :aria-pressed="kind === 'weekly'" @click="emit('update:kind', 'weekly')">每周指定日期</button>
      <button type="button" :class="{ active: kind === 'cron' }" :aria-pressed="kind === 'cron'" @click="emit('update:kind', 'cron')">Cron 表达式</button>
    </div>
    <template v-if="kind === 'weekly'">
      <div class="weekday-picker" aria-label="选择星期">
        <button v-for="day in dayOptions" :key="day.value" type="button" :class="{ active: weekdays.includes(day.value) }" :aria-pressed="weekdays.includes(day.value)" @click="toggleDay(day.value)">{{ day.label }}</button>
      </div>
      <label class="field"><span>执行时间</span><input :value="scheduledTime" type="time" required @input="emit('update:scheduledTime', ($event.target as HTMLInputElement).value)" /></label>
    </template>
    <template v-else>
      <label class="field cron-field">
        <span>Cron 表达式</span>
        <input :value="cronExpression || ''" type="text" inputmode="text" spellcheck="false" placeholder="0 9 * * 1-5" required @input="emit('update:cronExpression', ($event.target as HTMLInputElement).value || null)" />
        <small>标准 5 段格式：分　时　日　月　周。按服务器系统时区执行。</small>
      </label>
      <div v-if="cronValue" class="cron-preview" aria-live="polite">
        <div class="cron-preview-heading">
          <strong>接下来 5 次运行</strong>
          <span v-if="cronPreview?.valid">{{ cronPreview.timezone }}</span>
        </div>
        <p v-if="previewLoading && !cronPreview" class="cron-preview-status">正在计算…</p>
        <p v-else-if="previewFailed" class="cron-preview-status error">暂时无法获取运行时间</p>
        <p v-else-if="cronPreview && !cronPreview.valid" class="cron-preview-status error">表达式无效，请输入标准的 5 段 Cron 表达式</p>
        <ol v-else-if="cronPreview?.valid">
          <li v-for="(run, index) in cronPreview.runs" :key="run">
            <span>{{ index + 1 }}</span><time :datetime="run">{{ formatRun(run) }}</time>
          </li>
        </ol>
      </div>
    </template>
  </div>
</template>
