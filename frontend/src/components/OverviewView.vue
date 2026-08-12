<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, type OverviewActivity, type OverviewSummary } from '../api'
import type { RealtimeEvent } from '../realtime'
import TelegramConnect from './TelegramConnect.vue'

const props = defineProps<{ username: string }>()
const emit = defineEmits<{ openChats: []; openRules: []; openArchive: [] }>()

const summary = ref<OverviewSummary | null>(null)
const loading = ref(true)
const error = ref('')
let refreshTimer: number | null = null

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 12) return '早上好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

const healthTone = computed(() => {
  if (!summary.value) return 'neutral'
  if (!summary.value.account_bound) return 'warning'
  if (summary.value.attention_task_count) return 'warning'
  if (summary.value.running_task_count) return 'active'
  return 'healthy'
})

const healthLabel = computed(() => {
  if (!summary.value) return '正在检查'
  if (!summary.value.account_bound) return '等待连接 Telegram'
  if (summary.value.attention_task_count) return `${summary.value.attention_task_count} 项需要留意`
  if (summary.value.running_task_count) return `${summary.value.running_task_count} 项任务正在运行`
  if (!summary.value.rule_count) return '尚未配置备份规则'
  return '所有备份规则运行正常'
})

const heroSubtitle = computed(() => {
  if (!summary.value) return '正在汇总本地归档和备份任务。'
  if (!summary.value.account_bound) return '连接 Telegram 后，即可开始保存聊天记录。'
  if (!summary.value.rule_count) return '选择需要保存的会话，创建第一条自动备份规则。'
  if (summary.value.attention_task_count) return '本地归档保持可用，但有备份任务需要你的留意。'
  return '你的 Telegram 消息正在安全地保存到本地归档。'
})

async function loadOverview(background = false) {
  if (!background) loading.value = true
  error.value = ''
  try {
    summary.value = await api<OverviewSummary>('/api/overview')
  } catch (value) {
    error.value = value instanceof Error ? value.message : '无法读取备份概览'
  } finally {
    loading.value = false
  }
}

function scheduleRefresh() {
  if (refreshTimer !== null) window.clearTimeout(refreshTimer)
  refreshTimer = window.setTimeout(() => loadOverview(true), 280)
}

function onRealtimeEvent(event: Event) {
  const message = (event as CustomEvent<RealtimeEvent>).detail
  if (!message?.type.startsWith('telegram.backup.') && !message?.type.startsWith('telegram.history.')) return
  scheduleRefresh()
}

function formatNumber(value: number) {
  return new Intl.NumberFormat('zh-CN').format(value)
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}

function formatTime(value?: string | null) {
  if (!value) return '尚无记录'
  const date = new Date(value)
  const elapsed = Date.now() - date.getTime()
  if (elapsed >= 0 && elapsed < 60_000) return '刚刚'
  if (elapsed >= 0 && elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)} 分钟前`
  if (elapsed >= 0 && elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)} 小时前`
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

function statusLabel(status: string) {
  return ({
    running: '进行中', success: '已完成', partial: '部分完成', failed: '失败',
    error: '等待重试', interrupted: '已中断', paused: '已暂停',
  } as Record<string, string>)[status] || '已记录'
}

function activityDescription(activity: OverviewActivity) {
  if (activity.status === 'running') {
    return activity.kind === 'backup' ? '正在获取新消息和媒体' : '正在检查历史消息变化'
  }
  if (activity.kind === 'backup') {
    const details = []
    if (activity.message_count) details.push(`新增 ${activity.message_count} 条消息`)
    if (activity.media_count) details.push(`下载 ${activity.media_count} 个媒体`)
    return details.join(' · ') || '没有发现新消息'
  }
  const details = []
  if (activity.changed_count) details.push(`更新 ${activity.changed_count} 条`)
  if (activity.deleted_count) details.push(`发现 ${activity.deleted_count} 条已删除`)
  if (activity.media_count) details.push(`补全 ${activity.media_count} 个媒体`)
  return details.join(' · ') || '历史消息没有变化'
}

onMounted(() => {
  window.addEventListener('tg-realtime-event', onRealtimeEvent)
  loadOverview()
})

onBeforeUnmount(() => {
  window.removeEventListener('tg-realtime-event', onRealtimeEvent)
  if (refreshTimer !== null) window.clearTimeout(refreshTimer)
})
</script>

<template>
  <div class="overview-page dashboard-content">
    <section class="overview-hero">
      <div>
        <p class="eyebrow">概览</p>
        <h1>{{ greeting }}，{{ props.username }}。</h1>
        <p class="lede">{{ heroSubtitle }}</p>
      </div>
      <div v-if="summary" :class="['overview-health', healthTone]" aria-live="polite">
        <span></span>{{ healthLabel }}
      </div>
    </section>

    <TelegramConnect />

    <div v-if="loading" class="overview-loading glass-card" aria-live="polite">
      <span class="spinner"></span><span>正在汇总备份数据</span>
    </div>
    <section v-else-if="error" class="overview-error glass-card">
      <span><strong>暂时无法读取概览</strong><small>{{ error }}</small></span>
      <button class="button secondary compact" @click="loadOverview()">重试</button>
    </section>

    <template v-else-if="summary">
      <section class="overview-stat-grid glass-card" aria-label="本地归档统计">
        <article>
          <span class="overview-stat-icon messages"><svg viewBox="0 0 24 24"><path d="M5 5h14v11H8.7L5 19.1V5Z" /></svg></span>
          <span><small>已备份消息</small><strong>{{ formatNumber(summary.message_count) }}</strong></span>
        </article>
        <article>
          <span class="overview-stat-icon media"><svg viewBox="0 0 24 24"><path d="M4 6h16v12H4V6Zm3 9 3.2-3.2 2.3 2.3 1.7-1.7L18 16H6l1-1Zm8-5.2a1.3 1.3 0 1 0 0-2.6 1.3 1.3 0 0 0 0 2.6Z" /></svg></span>
          <span><small>本地媒体</small><strong>{{ formatNumber(summary.media_count) }}</strong></span>
        </article>
        <article>
          <span class="overview-stat-icon storage"><svg viewBox="0 0 24 24"><path d="M5 5.5C5 4.7 8.1 4 12 4s7 .7 7 1.5V18c0 .8-3.1 1.5-7 1.5S5 18.8 5 18V5.5Zm0 0C5 6.3 8.1 7 12 7s7-.7 7-1.5M5 12c0 .8 3.1 1.5 7 1.5s7-.7 7-1.5" /></svg></span>
          <span><small>占用空间</small><strong>{{ formatBytes(summary.media_size_bytes) }}</strong></span>
        </article>
        <article>
          <span class="overview-stat-icon chats"><svg viewBox="0 0 24 24"><path d="M4 5.5h16v11H9l-5 4v-15Z" /></svg></span>
          <span><small>归档会话</small><strong>{{ formatNumber(summary.archive_chat_count) }}</strong></span>
        </article>
      </section>

      <section class="overview-detail-grid">
        <article class="overview-rules-card glass-card">
          <header>
            <span><small>自动备份</small><strong>{{ summary.rule_count ? `${summary.active_rule_count} 条规则正在守护` : '还没有自动备份规则' }}</strong></span>
            <span :class="['overview-status-orb', healthTone]"><i></i></span>
          </header>
          <div class="overview-rule-stats">
            <span><small>已启用</small><strong>{{ summary.active_rule_count }}</strong></span>
            <span><small>已暂停</small><strong>{{ summary.paused_rule_count }}</strong></span>
            <span><small>正在运行</small><strong>{{ summary.running_task_count }}</strong></span>
            <span :class="{ attention: summary.attention_task_count }"><small>需要留意</small><strong>{{ summary.attention_task_count }}</strong></span>
          </div>
          <div class="overview-card-foot">
            <span>最近完成：{{ formatTime(summary.last_completed_at) }}</span>
            <button class="overview-link-button" @click="summary.rule_count ? emit('openRules') : emit('openChats')">
              {{ summary.rule_count ? '管理规则' : '选择会话' }} <b>→</b>
            </button>
          </div>
        </article>

        <article class="overview-activity-card glass-card">
          <header>
            <span><small>运行记录</small><strong>最近活动</strong></span>
            <button v-if="summary.activities.length" class="overview-link-button" @click="emit('openRules')">查看全部</button>
          </header>
          <div v-if="summary.activities.length" class="overview-activity-list">
            <div v-for="activity in summary.activities.slice(0, 4)" :key="activity.id">
              <span :class="['activity-kind-icon', activity.kind, activity.status]"><svg v-if="activity.kind === 'backup'" viewBox="0 0 24 24"><path d="M12 4v11m-4-4 4 4 4-4M5 19h14" /></svg><svg v-else viewBox="0 0 24 24"><path d="M12 6v6l4 2M5.6 6.2A8 8 0 1 1 4 11" /></svg></span>
              <span class="activity-copy"><strong>{{ activity.chat_title }}</strong><small>{{ activityDescription(activity) }}</small></span>
              <span class="activity-meta"><i :class="activity.status">{{ statusLabel(activity.status) }}</i><small>{{ formatTime(activity.finished_at || activity.started_at) }}</small></span>
            </div>
          </div>
          <div v-else class="overview-empty-activity">
            <span>完成第一次备份后，运行记录会显示在这里。</span>
          </div>
        </article>
      </section>

      <section class="overview-shortcuts" aria-label="快捷操作">
        <button @click="emit('openChats')"><span><svg viewBox="0 0 24 24"><path d="M4 5.5h16v11H9l-5 4v-15Z" /></svg></span><strong>选择备份会话</strong><small>从会话列表创建规则</small><b>›</b></button>
        <button @click="emit('openArchive')"><span><svg viewBox="0 0 24 24"><path d="M4 7h16v12H4V7Zm2-3h12v3H6V4Zm3 7h6" /></svg></span><strong>浏览聊天记录</strong><small>查看已保存的消息与媒体</small><b>›</b></button>
        <button @click="emit('openRules')"><span><svg viewBox="0 0 24 24"><path d="M5 5h14M5 12h14M5 19h9M17 17l2 2 3-4" /></svg></span><strong>管理备份规则</strong><small>调整时间、媒体和历史更新</small><b>›</b></button>
      </section>
    </template>
  </div>
</template>
