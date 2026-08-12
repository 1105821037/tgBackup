<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api, type ChatBackupRule, type TelegramChat, type TelegramEntityDetail } from '../api'
import type { RealtimeEvent } from '../realtime'
import HistoryRangeFields from './HistoryRangeFields.vue'
import SavedMessagesIcon from './SavedMessagesIcon.vue'
import ScheduleFields from './ScheduleFields.vue'

const emit = defineEmits<{ manageRule: [] }>()

const chats = ref<TelegramChat[]>([])
const loading = ref(true)
const refreshing = ref(false)
const saving = ref(false)
const historyRunning = ref(false)
const historyStarted = ref(false)
const error = ref('')
const saved = ref(false)
const query = ref('')
const selected = ref<TelegramChat | null>(null)
const brokenAvatars = ref(new Set<string>())
const entityDetail = ref<TelegramEntityDetail | null>(null)
const entityLoading = ref(false)
const entityError = ref('')
let entityRequestId = 0

const mediaOptions = [
  { value: 'photo', label: '图片' }, { value: 'video', label: '视频' },
  { value: 'audio', label: '音频' }, { value: 'voice', label: '语音' },
  { value: 'document', label: '文件' }, { value: 'animation', label: '动图' },
  { value: 'sticker', label: '贴纸' },
]
const kindLabels: Record<string, string> = {
  private: '私聊', user: '用户', bot: '机器人', group: '群组', supergroup: '超级群组', channel: '频道', unknown: '会话',
}

const form = reactive<ChatBackupRule>({
  enabled: true,
  schedule_kind: 'weekly',
  backup_time: '09:00',
  weekdays: [1, 2, 3, 4, 5, 6, 7],
  cron_expression: null,
  media_types: ['photo', 'video', 'voice', 'document'],
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

const visibleChats = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  return chats.value.filter((chat) => {
    if (!needle) return true
    return `${chat.title} ${chat.username || ''}`.toLocaleLowerCase().includes(needle)
  })
})

const configuredCount = computed(() => chats.value.filter((chat) => chat.rule).length)

function avatarText(chat: TelegramChat) {
  return chat.title.trim().slice(0, 1).toUpperCase() || '#'
}

function avatarHue(chat: TelegramChat) {
  const hue = Math.abs(chat.peer_id) % 360
  return { '--avatar-hue': String(hue) }
}

function avatarKey(chat: TelegramChat) {
  return `${chat.entity_id || chat.peer_id}:${chat.photo_id || 0}`
}

function showAvatar(chat: TelegramChat) {
  return Boolean(chat.avatar_url && !brokenAvatars.value.has(avatarKey(chat)))
}

function markAvatarBroken(chat: TelegramChat) {
  brokenAvatars.value = new Set(brokenAvatars.value).add(avatarKey(chat))
}

function clearBrokenAvatar(chat: TelegramChat) {
  const next = new Set(brokenAvatars.value)
  next.delete(avatarKey(chat))
  brokenAvatars.value = next
}

function handleRealtimeEvent(event: Event) {
  const message = (event as CustomEvent<RealtimeEvent>).detail
  if (!message || ![
    'telegram.entity.updated',
    'telegram.entity.avatar.updated',
  ].includes(message.type)) return
  const payload = message.payload as {
    entity_id?: number
    peer_id?: number
    display_name?: string
    username?: string | null
    photo_id?: number | null
    current_version?: number
    variants?: string[]
  }
  const chat = chats.value.find((item) => (
    payload.entity_id ? item.entity_id === payload.entity_id : item.peer_id === payload.peer_id
  ))
  if (!chat) return
  if (message.type === 'telegram.entity.updated') {
    if (payload.display_name && !chat.is_self) chat.title = payload.display_name
    if ('username' in payload) chat.username = payload.username
    if (payload.current_version) chat.entity_version = payload.current_version
    if (payload.photo_id !== undefined && payload.photo_id !== chat.photo_id) {
      chat.photo_id = payload.photo_id
      chat.avatar_url = null
    }
    if (selected.value?.entity_id === chat.entity_id) loadEntityDetail(chat)
  } else if (
    payload.entity_id
    && payload.photo_id
    && payload.variants?.includes('small')
  ) {
    chat.photo_id = payload.photo_id
    chat.avatar_url = `/api/entities/${payload.entity_id}/avatar/${payload.photo_id}/small`
    clearBrokenAvatar(chat)
  }
}

function formatDate(value?: string | null) {
  if (!value) return '暂无消息'
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' }).format(new Date(value))
}

function formatTimestamp(value?: string | null) {
  if (!value) return '尚未完成'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}

async function loadEntityDetail(chat: TelegramChat) {
  const requestId = ++entityRequestId
  entityDetail.value = null
  entityError.value = ''
  if (!chat.entity_id) {
    entityError.value = '该会话尚未建立本地资料'
    return
  }
  entityLoading.value = true
  try {
    const detail = await api<TelegramEntityDetail>(`/api/entities/${chat.entity_id}`)
    if (requestId === entityRequestId && selected.value?.entity_id === chat.entity_id) {
      entityDetail.value = detail
    }
  } catch (value) {
    if (requestId === entityRequestId) {
      entityError.value = value instanceof Error ? value.message : '无法读取会话资料'
    }
  } finally {
    if (requestId === entityRequestId) entityLoading.value = false
  }
}

function selectChat(chat: TelegramChat) {
  selected.value = chat
  saved.value = false
  historyStarted.value = false
  loadEntityDetail(chat)
  const rule = chat.rule
  Object.assign(form, rule || {
    enabled: true,
    schedule_kind: 'weekly',
    backup_time: '09:00',
    weekdays: [1, 2, 3, 4, 5, 6, 7],
    cron_expression: null,
    media_types: ['photo', 'video', 'voice', 'document'],
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
  form.weekdays = [...(rule?.weekdays || [1, 2, 3, 4, 5, 6, 7])]
  form.history_weekdays = [...(rule?.history_weekdays || [1, 2, 3, 4, 5, 6, 7])]
  form.media_types = [...(rule?.media_types || ['photo', 'video', 'voice', 'document'])]
}

function toggleMedia(type: string) {
  form.media_types = form.media_types.includes(type)
    ? form.media_types.filter((value) => value !== type)
    : [...form.media_types, type]
}

async function loadChats(isRefresh = false) {
  if (isRefresh) refreshing.value = true
  else loading.value = true
  error.value = ''
  try {
    if (isRefresh) {
      await api('/api/chats/refresh', { method: 'POST' })
    }
    const result = await api<{ items: TelegramChat[] }>('/api/chats?limit=300')
    chats.value = result.items
    if (selected.value) {
      selected.value = chats.value.find((chat) => chat.peer_id === selected.value?.peer_id) || null
    }
  } catch (value) {
    error.value = value instanceof Error ? value.message : '无法加载会话'
  } finally {
    loading.value = false
    refreshing.value = false
  }
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
  saved.value = false
  error.value = ''
  try {
    const result = await api<{ rule: ChatBackupRule }>(`/api/chats/${selected.value.peer_id}/rule`, {
      method: 'PUT',
      body: JSON.stringify(form),
    })
    selected.value.rule = result.rule
    saved.value = true
    window.setTimeout(() => { saved.value = false }, 2200)
  } catch (value) {
    error.value = value instanceof Error ? value.message : '规则保存失败'
  } finally {
    saving.value = false
  }
}

async function runHistoryUpdate() {
  if (!selected.value) return
  historyRunning.value = true
  historyStarted.value = false
  error.value = ''
  try {
    await api(`/api/backups/${selected.value.peer_id}/history/run`, { method: 'POST' })
    historyStarted.value = true
    window.setTimeout(() => { historyStarted.value = false }, 2200)
  } catch (value) {
    error.value = value instanceof Error ? value.message : '历史消息更新启动失败'
  } finally {
    historyRunning.value = false
  }
}

onMounted(() => {
  window.addEventListener('tg-realtime-event', handleRealtimeEvent)
  loadChats()
})
onBeforeUnmount(() => {
  window.removeEventListener('tg-realtime-event', handleRealtimeEvent)
})
</script>

<template>
  <div class="chats-page">
    <section class="chats-heading">
      <div>
        <p class="eyebrow">会话管理</p>
        <h1>选择需要守护的对话。</h1>
        <p class="muted">{{ chats.length }} 个会话 · {{ configuredCount }} 个已配置</p>
      </div>
      <button class="button secondary compact" :disabled="refreshing" @click="loadChats(true)">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17.7 6.3A8 8 0 1 0 20 12h-2a6 6 0 1 1-1.8-4.3L13 11h8V3l-3.3 3.3Z" /></svg>
        {{ refreshing ? '刷新中' : '刷新会话' }}
      </button>
    </section>

    <div class="chat-toolbar">
      <label class="search-field">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m20.7 19.3-4.1-4.1a7.5 7.5 0 1 0-1.4 1.4l4.1 4.1 1.4-1.4ZM5 11a6 6 0 1 1 12 0 6 6 0 0 1-12 0Z" /></svg>
        <input v-model="query" placeholder="搜索会话或用户名" aria-label="搜索会话" />
      </label>
    </div>

    <div v-if="loading" class="chat-loading" aria-live="polite">
      <span class="spinner"></span><span>正在从 Telegram 读取会话</span>
    </div>
    <div v-else-if="error && !chats.length" class="glass-card empty-state">
      <strong>暂时无法读取会话</strong><p>{{ error }}</p>
      <button class="button secondary" @click="loadChats()">重试</button>
    </div>
    <div v-else class="chat-list" role="list">
      <button
        v-for="chat in visibleChats"
        :key="chat.peer_id"
        class="chat-row"
        role="listitem"
        @click="selectChat(chat)"
      >
        <span :class="['chat-avatar', { 'saved-messages-avatar': chat.is_self }]" :style="avatarHue(chat)">
          <SavedMessagesIcon v-if="chat.is_self" />
          <img
            v-else-if="showAvatar(chat)"
            :src="chat.avatar_url || ''"
            :alt="`${chat.title}头像`"
            loading="lazy"
            decoding="async"
            @error="markAvatarBroken(chat)"
          />
          <template v-else>{{ avatarText(chat) }}</template>
        </span>
        <span class="chat-main">
          <span class="chat-title-line">
            <strong>{{ chat.title }}</strong>
            <span v-if="chat.rule" :class="['rule-badge', { paused: !chat.rule.enabled }]">
              {{ chat.rule.enabled ? '已配置' : '已暂停' }}
            </span>
          </span>
          <span class="chat-subline">
            {{ kindLabels[chat.kind] }}<template v-if="chat.username"> · @{{ chat.username }}</template>
          </span>
        </span>
        <span class="chat-meta">
          <span>{{ formatDate(chat.last_message_date) }}</span>
          <span v-if="chat.unread_count" class="unread">{{ Math.min(chat.unread_count, 99) }}</span>
        </span>
        <svg class="chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6" /></svg>
      </button>
      <div v-if="!visibleChats.length" class="empty-list">没有匹配的会话</div>
    </div>

    <Transition name="panel">
      <div v-if="selected" class="config-layer">
        <button class="panel-scrim" aria-label="关闭配置" @click="selected = null"></button>
        <aside class="config-panel" aria-label="备份规则配置">
          <header class="config-header">
            <button class="icon-button" aria-label="关闭" @click="selected = null">
              <svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg>
            </button>
            <span :class="['chat-avatar', 'config-avatar', { 'saved-messages-avatar': selected.is_self }]" :style="avatarHue(selected)">
              <SavedMessagesIcon v-if="selected.is_self" />
              <img
                v-else-if="showAvatar(selected)"
                :src="selected.avatar_url || ''"
                :alt="`${selected.title}头像`"
                decoding="async"
                @error="markAvatarBroken(selected)"
              />
              <template v-else>{{ avatarText(selected) }}</template>
            </span>
            <div>
              <p class="eyebrow">{{ selected.rule ? '会话资料' : '创建规则' }}</p>
              <h2>{{ selected.title }}</h2>
            </div>
          </header>

          <form class="config-form" @submit.prevent="saveRule">
            <section class="setting-group entity-info-card">
              <div class="entity-section-head">
                <div>
                  <strong>会话信息</strong>
                  <span>来自本地缓存的 Telegram 资料</span>
                </div>
                <span v-if="entityDetail" class="profile-version">v{{ entityDetail.current_version }}</span>
              </div>

              <div v-if="entityLoading" class="entity-info-state" aria-live="polite">
                <span class="spinner"></span><span>正在读取会话资料</span>
              </div>
              <div v-else-if="entityError" class="entity-info-state error-inline">
                <span>{{ entityError }}</span>
                <button type="button" class="text-button" @click="selected && loadEntityDetail(selected)">重试</button>
              </div>
              <template v-else-if="entityDetail">
                <div class="entity-badges">
                  <span>{{ kindLabels[entityDetail.kind] || '会话' }}</span>
                  <span v-if="entityDetail.is_verified">已认证</span>
                  <span v-if="entityDetail.is_contact">联系人</span>
                  <span v-if="entityDetail.is_scam || entityDetail.is_fake" class="warning-badge">风险标记</span>
                </div>
                <p v-if="entityDetail.about" class="entity-about">{{ entityDetail.about }}</p>
                <dl class="entity-info-grid">
                  <div><dt>Telegram ID</dt><dd>{{ entityDetail.telegram_id }}</dd></div>
                  <div><dt>用户名</dt><dd>{{ entityDetail.username ? `@${entityDetail.username}` : '未设置' }}</dd></div>
                  <div v-if="entityDetail.phone_masked"><dt>手机号</dt><dd>{{ entityDetail.phone_masked }}</dd></div>
                  <div><dt>资料状态</dt><dd>{{ entityDetail.access_state === 'available' ? '可访问' : '访问受限' }}</dd></div>
                  <div class="wide"><dt>首次发现</dt><dd>{{ formatTimestamp(entityDetail.first_observed_at) }}</dd></div>
                  <div class="wide"><dt>完整资料更新</dt><dd>{{ formatTimestamp(entityDetail.last_full_refreshed_at) }}</dd></div>
                </dl>
              </template>
            </section>

            <section v-if="selected.rule" class="setting-group managed-rule-callout">
              <div><strong>此会话已有备份规则</strong><span>编辑、移除和实时运行状态已集中到规则管理。</span></div>
              <button type="button" class="button primary" @click="selected = null; emit('manageRule')">前往规则管理</button>
            </section>

            <template v-else>
            <section class="setting-group">
              <div class="setting-row">
                <div><strong>启用自动备份</strong><span>关闭后保留规则与已有数据</span></div>
                <button type="button" class="switch" role="switch" :aria-checked="form.enabled" @click="form.enabled = !form.enabled"><span></span></button>
              </div>
            </section>

            <section class="setting-group history-settings">
              <div class="setting-row">
                <div>
                  <strong>历史消息更新</strong>
                  <span v-if="form.history_available">检测编辑与删除，并补全已选择的媒体</span>
                  <span v-else>完成首次自动备份后可开启</span>
                </div>
                <button
                  type="button"
                  class="switch"
                  role="switch"
                  :disabled="!form.history_available"
                  :aria-checked="form.history_enabled"
                  @click="form.history_enabled = !form.history_enabled"
                ><span></span></button>
              </div>

              <template v-if="form.history_enabled">
                <ScheduleFields v-model:kind="form.history_schedule_kind" v-model:scheduled-time="form.history_time" v-model:weekdays="form.history_weekdays" v-model:cron-expression="form.history_cron_expression" label="检查周期" />
                <label class="field"><span>每条消息最大更新次数</span><input v-model.number="form.history_max_updates" type="number" min="1" max="100" required /></label>
                <HistoryRangeFields v-model:start-kind="form.history_start_kind" v-model:start-days-ago="form.history_start_days_ago" v-model:end-kind="form.history_end_kind" v-model:end-days-ago="form.history_end_days_ago" />
                <p class="setting-note">高频指标按天更新，不占用消息版本次数。缺少或丢失的已选媒体会自动补全。</p>
                <button type="button" class="button secondary compact" :disabled="historyRunning" @click="runHistoryUpdate">
                  {{ historyRunning ? '正在启动' : '立即检查一次' }}
                </button>
              </template>
            </section>

            <section class="setting-group"><ScheduleFields v-model:kind="form.schedule_kind" v-model:scheduled-time="form.backup_time" v-model:weekdays="form.weekdays" v-model:cron-expression="form.cron_expression" label="备份周期" /></section>

            <section class="setting-group">
              <label class="setting-label">下载的媒体类型</label>
              <div class="choice-grid">
                <button v-for="media in mediaOptions" :key="media.value" type="button" :class="['choice-chip', { active: form.media_types.includes(media.value) }]" :aria-pressed="form.media_types.includes(media.value)" @click="toggleMedia(media.value)">
                  <span class="checkmark">✓</span>{{ media.label }}
                </button>
              </div>
            </section>

            <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
            <div class="config-actions">
              <span v-if="historyStarted" class="save-confirmation"><span class="status-dot"></span>历史检查已启动</span>
              <span v-if="saved" class="save-confirmation"><span class="status-dot"></span>规则已保存</span>
              <button class="button primary" :disabled="saving">{{ saving ? '保存中' : '保存备份规则' }}</button>
            </div>
            </template>
          </form>
        </aside>
      </div>
    </Transition>
  </div>
</template>
