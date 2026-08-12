<script setup lang="ts">
defineProps<{
  startKind: 'earliest' | 'days_ago'
  startDaysAgo: number | null
  endKind: 'latest' | 'days_ago'
  endDaysAgo: number | null
}>()

const emit = defineEmits<{
  'update:startKind': [value: 'earliest' | 'days_ago']
  'update:startDaysAgo': [value: number | null]
  'update:endKind': [value: 'latest' | 'days_ago']
  'update:endDaysAgo': [value: number | null]
}>()

function numberValue(event: Event) {
  const value = (event.target as HTMLInputElement).value
  return value === '' ? null : Number(value)
}
</script>

<template>
  <div class="history-range-editor">
    <div class="history-boundary">
      <label class="field"><span>开始位置</span><select :value="startKind" @change="emit('update:startKind', ($event.target as HTMLSelectElement).value as 'earliest' | 'days_ago')"><option value="earliest">最早一条消息</option><option value="days_ago">指定天数前</option></select></label>
      <label v-if="startKind === 'days_ago'" class="field boundary-days"><span>距离现在</span><span class="unit-input"><input :value="startDaysAgo ?? ''" type="number" min="1" max="36500" required @input="emit('update:startDaysAgo', numberValue($event))" /><i>天前</i></span></label>
    </div>
    <span class="range-direction" aria-hidden="true">→</span>
    <div class="history-boundary">
      <label class="field"><span>结束位置</span><select :value="endKind" @change="emit('update:endKind', ($event.target as HTMLSelectElement).value as 'latest' | 'days_ago')"><option value="latest">最新一条消息</option><option value="days_ago">指定天数前</option></select></label>
      <label v-if="endKind === 'days_ago'" class="field boundary-days"><span>距离现在</span><span class="unit-input"><input :value="endDaysAgo ?? ''" type="number" min="0" max="36500" required @input="emit('update:endDaysAgo', numberValue($event))" /><i>天前</i></span></label>
    </div>
  </div>
</template>
