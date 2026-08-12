<script setup lang="ts">
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
    <label v-else class="field cron-field">
      <span>Cron 表达式</span>
      <input :value="cronExpression || ''" type="text" inputmode="text" spellcheck="false" placeholder="0 9 * * 1-5" required @input="emit('update:cronExpression', ($event.target as HTMLInputElement).value || null)" />
      <small>标准 5 段格式：分　时　日　月　周。按服务器系统时区执行。</small>
    </label>
  </div>
</template>
