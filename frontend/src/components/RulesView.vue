<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api, type BackupRuleItem, type ChatBackupRule } from '../api'
import type { RealtimeEvent } from '../realtime'
import { serverDate } from '../utils/dateTime'
import HistoryRangeFields from './HistoryRangeFields.vue'
import SavedMessagesIcon from './SavedMessagesIcon.vue'
import ScheduleFields from './ScheduleFields.vue'

const emit = defineEmits<{ browseChats: [] }>()
const rules = ref<BackupRuleItem[]>([])
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const removing = ref(false)
const removeTarget = ref<BackupRuleItem | null>(null)
const removeError = ref('')
const removeButton = ref<HTMLButtonElement | null>(null)
const selected = ref<BackupRuleItem | null>(null)
const runningPeers = ref(new Set<number>())
const historyRunningPeers = ref(new Set<number>())
const retryingPeers = ref(new Map<number, {
  messageId: number
  mediaType: string
  attempt: number
  maximum: number
  error: string
}>())
const brokenAvatars = ref(new Set<number>())
const runBaselines = new Map<number, { messages: number; media: number }>()

const weekdays = [
  { value: 1, label: '一' }, { value: 2, label: '二' }, { value: 3, label: '三' },
  { value: 4, label: '四' }, { value: 5, label: '五' }, { value: 6, label: '六' },
  { value: 7, label: '日' },
]
const mediaOptions = [
  { value: 'photo', label: '图片' }, { value: 'video', label: '视频' },
  { value: 'audio', label: '音频' }, { value: 'voice', label: '语音' },
  { value: 'document', label: '文件' }, { value: 'animation', label: '动图' },
  { value: 'sticker', label: '贴纸' },
]
const kindLabels: Record<string, string> = {
  private: '私聊', bot: '机器人', group: '群组', supergroup: '超级群组', channel: '频道', unknown: '会话',
}
const statusLabels: Record<string, string> = {
  idle: '等待运行', running: '正在备份', success: '备份完成', partial: '部分完成',
  retrying: '正在重试', failed: '备份失败', error: '等待重试', paused: '已暂停', interrupted: '已中断',
}
const mediaTypeLabels: Record<string, string> = {
  photo: '图片', video: '视频', animation: '动图', audio: '音频', voice: '语音',
  document: '文件', sticker: '贴纸',
}
const historyStatusLabels: Record<string, string> = {
  idle: '等待更新', running: '正在更新', success: '更新完成', partial: '部分完成',
  continuing: '等待继续更新', failed: '更新失败', error: '等待重试', paused: '已暂停', interrupted: '已中断',
}

const form = reactive<ChatBackupRule>({
  enabled: true,
  schedule_kind: 'weekly',
  backup_time: '09:00',
  weekdays: [1, 2, 3, 4, 5, 6, 7],
  cron_expression: null,
  media_types: [],
  history_enabled: false,
  history_available: false,
  history_schedule_kind: 'weekly',
  history_time: '03:00',
  history_weekdays: [1, 2, 3, 4, 5, 6, 7],
  history_cron_expression: null,
  history_max_updates: 10,
  history_start_kind: 'earliest',
  history_start_days_ago: null,
  history_end_kind: 'latest',
  history_end_days_ago: null,
})

const enabledCount = computed(() => rules.value.filter((item) => item.rule.enabled).length)
const runningCount = computed(() => runningPeers.value.size)

function avatarText(item: BackupRuleItem) {
  return item.chat_title.trim().slice(0, 1).toUpperCase() || '#'
}

function avatarHue(item: BackupRuleItem) {
  return { '--avatar-hue': String(Math.abs(item.peer_id) % 360) }
}

function scheduleLabel(item: BackupRuleItem) {
  if (item.rule.schedule_kind === 'cron') return `Cron · ${item.rule.cron_expression}`
  if (item.rule.weekdays.length === 7) return `每天 ${item.rule.backup_time}`
  const names = item.rule.weekdays.map((day) => `周${weekdays.find((item) => item.value === day)?.label || day}`)
  return `${names.join('、')} ${item.rule.backup_time}`
}

function itemStatus(item: BackupRuleItem) {
  if (!item.rule.enabled) return 'paused'
  if (retryingPeers.value.has(item.peer_id)) return 'retrying'
  if (runningPeers.value.has(item.peer_id)) return 'running'
  return item.state.status || item.latest_run?.status || 'idle'
}

function historyPercent(item: BackupRuleItem) {
  const run = item.history_update.latest_run
  if (!run) return 0
  if (!run.candidate_count) return run.status === 'success' ? 100 : 0
  return Math.min(100, (run.checked_count / run.candidate_count) * 100)
}

function formatTimestamp(value?: string | null) {
  if (!value) return '尚未完成'
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(serverDate(value))
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}

function messageDelta(item: BackupRuleItem) {
  return item.latest_run?.stored_count || 0
}

function mediaDelta(item: BackupRuleItem) {
  return item.latest_run?.media_count || 0
}

async function loadRules() {
  loading.value = true
  error.value = ''
  try {
    const result = await api<{ items: BackupRuleItem[] }>('/api/rules')
    rules.value = result.items
    runningPeers.value = new Set(
      result.items.filter((item) => item.state.status === 'running').map((item) => item.peer_id),
    )
    historyRunningPeers.value = new Set(
      result.items.filter((item) => item.history_update.status === 'running').map((item) => item.peer_id),
    )
    runBaselines.clear()
    for (const item of result.items) {
      if (item.state.status === 'running' && item.latest_run) {
        runBaselines.set(item.peer_id, {
          messages: Math.max(0, item.message_count - item.latest_run.stored_count),
          media: Math.max(0, item.media_count - item.latest_run.media_count),
        })
      }
    }
    if (selected.value) {
      selected.value = rules.value.find((item) => item.peer_id === selected.value?.peer_id) || null
    }
  } catch (value) {
    error.value = value instanceof Error ? value.message : '无法读取备份规则'
  } finally {
    loading.value = false
  }
}

function editRule(item: BackupRuleItem) {
  selected.value = item
  error.value = ''
  Object.assign(form, item.rule)
  form.weekdays = [...item.rule.weekdays]
  form.history_weekdays = [...item.rule.history_weekdays]
  form.media_types = [...item.rule.media_types]
}

async function confirmRemove(item: BackupRuleItem) {
  removeTarget.value = item
  removeError.value = ''
  await nextTick()
  removeButton.value?.focus()
}

function closeRemoveDialog() {
  if (removing.value) return
  removeTarget.value = null
  removeError.value = ''
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && removeTarget.value) closeRemoveDialog()
}

function toggleMedia(type: string) {
  form.media_types = form.media_types.includes(type)
    ? form.media_types.filter((value) => value !== type)
    : [...form.media_types, type]
}

async function saveRule() {
  if (!selected.value) return
  if (form.schedule_kind === 'weekly' && !form.weekdays.length) {
    error.value = '每周备份至少选择一天'
    return
  }
  if (form.schedule_kind === 'cron' && !form.cron_expression?.trim()) {
    error.value = '请输入备份 Cron 表达式'
    return
  }
  if (form.history_enabled && form.history_schedule_kind === 'weekly' && !form.history_weekdays.length) {
    error.value = '历史消息更新至少选择一天'
    return
  }
  if (form.history_enabled && form.history_schedule_kind === 'cron' && !form.history_cron_expression?.trim()) {
    error.value = '请输入历史消息更新 Cron 表达式'
    return
  }
  if (form.history_start_kind === 'days_ago' && form.history_end_kind === 'days_ago' && (
    form.history_start_days_ago === null
    || form.history_end_days_ago === null
    || form.history_start_days_ago <= form.history_end_days_ago
  )) {
    error.value = '较早边界必须大于较近边界，例如 7 天前至 2 天前'
    return
  }
  saving.value = true
  error.value = ''
  try {
    const result = await api<{ rule: ChatBackupRule }>(`/api/rules/${selected.value.peer_id}`, {
      method: 'PUT',
      body: JSON.stringify(form),
    })
    selected.value.rule = result.rule
    await loadRules()
  } catch (value) {
    error.value = value instanceof Error ? value.message : '规则保存失败'
  } finally {
    saving.value = false
  }
}

async function runNow(item: BackupRuleItem) {
  runBaselines.set(item.peer_id, { messages: item.message_count, media: item.media_count })
  const next = new Set(runningPeers.value).add(item.peer_id)
  runningPeers.value = next
  error.value = ''
  try {
    await api(`/api/backups/${item.peer_id}/run`, { method: 'POST' })
    item.state.status = 'running'
  } catch (value) {
    next.delete(item.peer_id)
    runningPeers.value = new Set(next)
    error.value = value instanceof Error ? value.message : '无法启动备份'
  }
}

async function runHistoryNow(item: BackupRuleItem) {
  const next = new Set(historyRunningPeers.value).add(item.peer_id)
  historyRunningPeers.value = next
  error.value = ''
  try {
    await api(`/api/backups/${item.peer_id}/history/run`, { method: 'POST' })
    item.history_update.status = 'running'
  } catch (value) {
    next.delete(item.peer_id)
    historyRunningPeers.value = new Set(next)
    error.value = value instanceof Error ? value.message : '无法启动历史消息更新'
  }
}

async function removeRule() {
  if (!removeTarget.value) return
  removing.value = true
  removeError.value = ''
  try {
    const peerId = removeTarget.value.peer_id
    await api(`/api/rules/${peerId}`, { method: 'DELETE' })
    rules.value = rules.value.filter((item) => item.peer_id !== peerId)
    if (selected.value?.peer_id === peerId) selected.value = null
    removeTarget.value = null
  } catch (value) {
    removeError.value = value instanceof Error ? value.message : '规则删除失败'
  } finally {
    removing.value = false
  }
}

function handleRealtimeEvent(event: Event) {
  const message = (event as CustomEvent<RealtimeEvent>).detail
  if (!message) return
  if (message.type === 'telegram.rule.updated') {
    loadRules()
    return
  }
  if (message.type === 'telegram.rule.removed') {
    const peerId = Number(message.payload.peer_id)
    rules.value = rules.value.filter((item) => item.peer_id !== peerId)
    if (selected.value?.peer_id === peerId) selected.value = null
    if (removeTarget.value?.peer_id === peerId) removeTarget.value = null
    return
  }
  if (message.type.startsWith('telegram.history.')) {
    const payload = message.payload as {
      peer_id?: number
      run_id?: number
      status?: string
      candidate_count?: number
      checked_count?: number
      changed_count?: number
      deleted_count?: number
      media_completed_count?: number
      error_count?: number
      has_remaining?: boolean
    }
    if (!payload.peer_id) return
    const item = rules.value.find((rule) => rule.peer_id === payload.peer_id)
    if (!item) return
    item.history_update.status = payload.status || item.history_update.status
    item.history_update.has_remaining = payload.has_remaining ?? item.history_update.has_remaining
    item.history_update.latest_run = {
      id: payload.run_id || item.history_update.latest_run?.id || 0,
      status: payload.status || 'running',
      candidate_count: payload.candidate_count || 0,
      checked_count: payload.checked_count || 0,
      changed_count: payload.changed_count || 0,
      deleted_count: payload.deleted_count || 0,
      media_completed_count: payload.media_completed_count || 0,
      error_count: payload.error_count || 0,
      has_remaining: payload.has_remaining || false,
    }
    const running = new Set(historyRunningPeers.value)
    if (message.type === 'telegram.history.started' || message.type === 'telegram.history.progress') {
      running.add(item.peer_id)
    } else {
      running.delete(item.peer_id)
      item.history_update.last_completed_at = message.sent_at
    }
    historyRunningPeers.value = running
    return
  }
  if (!message.type.startsWith('telegram.backup.')) return
  const payload = message.payload as {
    peer_id?: number
    run_id?: number
    status?: string
    cursor?: number
    fetched_count?: number
    stored_count?: number
    skipped_count?: number
    media_count?: number
    error_code?: string | null
    error_message?: string | null
    current_message_id?: number | null
    current_media_type?: string | null
    retry_attempt?: number | null
    retry_max?: number | null
  }
  if (!payload.peer_id) return
  const item = rules.value.find((rule) => rule.peer_id === payload.peer_id)
  if (!item) return
  if (message.type === 'telegram.backup.started' || !runBaselines.has(item.peer_id)) {
    runBaselines.set(item.peer_id, { messages: item.message_count, media: item.media_count })
  }
  const baseline = runBaselines.get(item.peer_id)
  item.state.status = payload.status || item.state.status
  item.state.last_message_id = payload.cursor ?? item.state.last_message_id
  item.latest_run = {
    id: payload.run_id || item.latest_run?.id || 0,
    trigger: item.latest_run?.trigger || 'realtime',
    status: payload.status || 'running',
    start_cursor: item.latest_run?.start_cursor || item.state.last_message_id,
    end_cursor: payload.cursor ?? item.state.last_message_id,
    fetched_count: payload.fetched_count || 0,
    stored_count: payload.stored_count || 0,
    skipped_count: payload.skipped_count || 0,
    media_count: payload.media_count || 0,
    error_code: payload.error_code,
    error_message: payload.error_message,
    started_at: item.latest_run?.started_at || new Date().toISOString(),
    finished_at: ['telegram.backup.started', 'telegram.backup.progress', 'telegram.backup.retrying'].includes(message.type) ? null : message.sent_at,
  }
  if (baseline) {
    item.message_count = baseline.messages + (payload.stored_count || 0)
    item.media_count = baseline.media + (payload.media_count || 0)
  }
  const running = new Set(runningPeers.value)
  const retries = new Map(retryingPeers.value)
  if (message.type === 'telegram.backup.retrying' && payload.current_message_id) {
    retries.set(item.peer_id, {
      messageId: payload.current_message_id,
      mediaType: payload.current_media_type || 'document',
      attempt: payload.retry_attempt || 1,
      maximum: payload.retry_max || 1,
      error: payload.error_message || payload.error_code || 'Telegram 暂时没有响应',
    })
  } else {
    retries.delete(item.peer_id)
  }
  retryingPeers.value = retries
  if (['telegram.backup.started', 'telegram.backup.progress', 'telegram.backup.retrying'].includes(message.type)) {
    running.add(item.peer_id)
  } else {
    running.delete(item.peer_id)
    runBaselines.delete(item.peer_id)
  }
  runningPeers.value = running
}

onMounted(() => {
  window.addEventListener('tg-realtime-event', handleRealtimeEvent)
  window.addEventListener('keydown', handleKeydown)
  loadRules()
})
onBeforeUnmount(() => {
  window.removeEventListener('tg-realtime-event', handleRealtimeEvent)
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <div class="chats-page rules-page">
    <section class="chats-heading">
      <div>
        <p class="eyebrow">规则管理</p>
        <h1>管理每条备份计划。</h1>
        <p class="muted">
          {{ rules.length }} 条规则 · {{ enabledCount }} 条已启用<template v-if="runningCount"> · {{ runningCount }} 条正在备份</template>
        </p>
      </div>
      <button class="button secondary compact" @click="emit('browseChats')">添加规则</button>
    </section>

    <div v-if="loading" class="chat-loading"><span class="spinner"></span><span>正在读取规则</span></div>
    <div v-else-if="error && !rules.length" class="glass-card empty-state">
      <strong>暂时无法读取规则</strong><p>{{ error }}</p>
      <button class="button secondary" @click="loadRules">重试</button>
    </div>
    <div v-else-if="!rules.length" class="glass-card empty-state">
      <strong>还没有备份规则</strong><p>前往会话管理，选择一个会话开始。</p>
      <button class="button primary" @click="emit('browseChats')">选择会话</button>
    </div>
    <div v-else class="rule-grid">
      <article v-for="item in rules" :key="item.id" class="rule-card">
        <div class="rule-card-head">
          <span :class="['chat-avatar', { 'saved-messages-avatar': item.is_self }]" :style="avatarHue(item)">
            <SavedMessagesIcon v-if="item.is_self" />
            <img v-else-if="item.avatar_url && !brokenAvatars.has(item.id)" :src="item.avatar_url" :alt="`${item.chat_title}头像`" loading="lazy" @error="brokenAvatars = new Set(brokenAvatars).add(item.id)" />
            <template v-else>{{ avatarText(item) }}</template>
          </span>
          <div class="rule-card-title"><strong>{{ item.chat_title }}</strong><span>{{ kindLabels[item.chat_kind] || '会话' }} · {{ scheduleLabel(item) }}</span></div>
          <span :class="['backup-status', itemStatus(item)]"><i></i>{{ statusLabels[itemStatus(item)] || itemStatus(item) }}</span>
        </div>

        <div class="rule-progress">
          <div><span>消息数量</span><strong>{{ item.message_count }} <small>+{{ messageDelta(item) }}</small></strong></div>
          <div><span>媒体数量</span><strong>{{ item.media_count }} <small>+{{ mediaDelta(item) }}</small></strong></div>
          <div><span>本地媒体占用</span><strong>{{ formatBytes(item.media_size_bytes) }}</strong></div>
          <div><span>最近备份</span><strong class="time-value">{{ formatTimestamp(item.state.last_completed_at) }}</strong></div>
        </div>
        <p v-if="retryingPeers.get(item.peer_id)" class="rule-retry-detail">
          正在重试消息 #{{ retryingPeers.get(item.peer_id)!.messageId }} 的{{ mediaTypeLabels[retryingPeers.get(item.peer_id)!.mediaType] || '媒体' }}下载
          · 第 {{ retryingPeers.get(item.peer_id)!.attempt }}/{{ retryingPeers.get(item.peer_id)!.maximum }} 次
          <small>{{ retryingPeers.get(item.peer_id)!.error }}</small>
        </p>
        <section v-if="item.rule.history_enabled" class="history-progress-card">
          <div class="history-progress-head">
            <strong>历史消息更新</strong>
            <span :class="['backup-status', item.history_update.status]"><i></i>{{ historyRunningPeers.has(item.peer_id) ? '正在更新' : item.history_update.has_remaining ? '等待继续更新' : historyStatusLabels[item.history_update.latest_run?.status || item.history_update.status] || '等待更新' }}</span>
          </div>
          <div class="history-progress-track" role="progressbar" :aria-valuenow="item.history_update.latest_run?.checked_count || 0" :aria-valuemax="Math.max(item.history_update.latest_run?.candidate_count || 0, 1)">
            <span :style="{ width: `${historyPercent(item)}%` }"></span>
          </div>
          <div class="history-progress-stats">
            <span>已检查 <strong>{{ item.history_update.latest_run?.checked_count || 0 }}/{{ item.history_update.latest_run?.candidate_count || 0 }}</strong></span>
            <span>变更 <strong>{{ item.history_update.latest_run?.changed_count || 0 }}</strong></span>
            <span>删除 <strong>{{ item.history_update.latest_run?.deleted_count || 0 }}</strong></span>
            <span>补全媒体 <strong>{{ item.history_update.latest_run?.media_completed_count || 0 }}</strong></span>
            <span v-if="item.history_update.latest_run?.error_count">错误 <strong>{{ item.history_update.latest_run.error_count }}</strong></span>
          </div>
        </section>
        <p
          v-if="item.state.status === 'error' && item.state.last_error && !runningPeers.has(item.peer_id)"
          class="rule-error"
        >
          {{ item.state.last_error }}
        </p>
        <div class="rule-card-foot">
          <span>{{ item.rule.history_enabled ? '历史消息更新已启用' : '仅增量备份' }}</span>
          <div>
            <button class="button secondary compact" :disabled="runningPeers.has(item.peer_id) || historyRunningPeers.has(item.peer_id) || !item.rule.enabled" @click="runNow(item)">{{ runningPeers.has(item.peer_id) ? '备份中' : '立即备份' }}</button>
            <button v-if="item.rule.history_enabled" class="button secondary compact" :disabled="historyRunningPeers.has(item.peer_id) || runningPeers.has(item.peer_id)" @click="runHistoryNow(item)">{{ historyRunningPeers.has(item.peer_id) ? '更新中' : '立即更新历史消息' }}</button>
            <button class="button secondary compact" @click="editRule(item)">编辑</button>
            <button class="button ghost-danger compact" @click="confirmRemove(item)">删除规则</button>
          </div>
        </div>
      </article>
    </div>

    <Transition name="panel">
      <div v-if="selected" class="config-layer">
        <button class="panel-scrim" aria-label="关闭规则编辑" @click="selected = null"></button>
        <aside class="config-panel" aria-label="编辑备份规则">
          <header class="config-header">
            <button class="icon-button" aria-label="关闭" @click="selected = null"><svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
            <span :class="['chat-avatar', 'config-avatar', { 'saved-messages-avatar': selected.is_self }]" :style="avatarHue(selected)"><SavedMessagesIcon v-if="selected.is_self" /><img v-else-if="selected.avatar_url && !brokenAvatars.has(selected.id)" :src="selected.avatar_url" :alt="`${selected.chat_title}头像`" /><template v-else>{{ avatarText(selected) }}</template></span>
            <div><p class="eyebrow">编辑规则</p><h2>{{ selected.chat_title }}</h2></div>
          </header>

          <form class="config-form" @submit.prevent="saveRule">
            <section class="setting-group">
              <div class="setting-row"><div><strong>启用自动备份</strong><span>关闭后保留规则、已有数据</span></div><button type="button" class="switch" role="switch" :aria-checked="form.enabled" @click="form.enabled = !form.enabled"><span></span></button></div>
            </section>
            <section class="setting-group"><ScheduleFields v-model:kind="form.schedule_kind" v-model:scheduled-time="form.backup_time" v-model:weekdays="form.weekdays" v-model:cron-expression="form.cron_expression" label="备份周期" /></section>
            <section class="setting-group">
              <label class="setting-label">下载的媒体类型</label>
              <div class="choice-grid"><button v-for="option in mediaOptions" :key="option.value" type="button" :class="['choice-chip', { active: form.media_types.includes(option.value) }]" @click="toggleMedia(option.value)"><span class="checkmark">✓</span>{{ option.label }}</button></div>
            </section>
            <section class="setting-group history-settings">
              <div class="setting-row"><div><strong>历史消息更新</strong><span>检测编辑、删除并补全媒体</span></div><button type="button" class="switch" role="switch" :disabled="!form.history_available" :aria-checked="form.history_enabled" @click="form.history_enabled = !form.history_enabled"><span></span></button></div>
              <template v-if="form.history_enabled">
                <ScheduleFields v-model:kind="form.history_schedule_kind" v-model:scheduled-time="form.history_time" v-model:weekdays="form.history_weekdays" v-model:cron-expression="form.history_cron_expression" label="检查周期" />
                <label class="field"><span>每条消息最大更新次数</span><input v-model.number="form.history_max_updates" type="number" min="1" max="100" /></label>
                <HistoryRangeFields v-model:start-kind="form.history_start_kind" v-model:start-days-ago="form.history_start_days_ago" v-model:end-kind="form.history_end_kind" v-model:end-days-ago="form.history_end_days_ago" />
              </template>
            </section>
            <p v-if="error" class="form-error">{{ error }}</p>
            <div class="config-actions"><button type="button" class="text-button danger-text" @click="confirmRemove(selected)">删除规则</button><button class="button primary" :disabled="saving">{{ saving ? '保存中' : '保存更改' }}</button></div>
          </form>
        </aside>
      </div>
    </Transition>

    <Transition name="modal">
      <div v-if="removeTarget" class="modal-layer" role="presentation">
        <button class="modal-scrim" aria-label="取消删除" @click="closeRemoveDialog"></button>
        <section class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="remove-rule-title" aria-describedby="remove-rule-description">
          <div class="confirm-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5" /></svg>
          </div>
          <div class="confirm-copy">
            <h2 id="remove-rule-title">删除“{{ removeTarget.chat_title }}”的备份规则？</h2>
            <p id="remove-rule-description">自动备份与历史消息更新将停止。已经备份的消息、媒体、处理进度和运行记录都会保留。</p>
          </div>
          <p v-if="removeError" class="form-error">{{ removeError }}</p>
          <div class="confirm-actions">
            <button type="button" class="button secondary" :disabled="removing" @click="closeRemoveDialog">取消</button>
            <button ref="removeButton" type="button" class="button danger" :disabled="removing" @click="removeRule">{{ removing ? '正在删除…' : '删除规则' }}</button>
          </div>
        </section>
      </div>
    </Transition>
  </div>
</template>
