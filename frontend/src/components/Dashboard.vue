<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, type UserInfo } from '../api'
import { realtimeClient, type RealtimeConnectionState, type RealtimeEvent } from '../realtime'
import ChatsView from './ChatsView.vue'
import ArchiveView from './ArchiveView.vue'
import OverviewView from './OverviewView.vue'
import RulesView from './RulesView.vue'
import UserManagement from './UserManagement.vue'

type DashboardPage = 'overview' | 'chats' | 'rules' | 'archive' | 'account'

const dashboardPages = new Set<DashboardPage>(['overview', 'chats', 'rules', 'archive', 'account'])

function pageFromLocation(): DashboardPage {
  const page = window.location.hash.replace(/^#\/?/, '').split(/[/?]/, 1)[0]
  return dashboardPages.has(page as DashboardPage) ? page as DashboardPage : 'overview'
}

function locationHasDashboardPage() {
  const page = window.location.hash.replace(/^#\/?/, '').split(/[/?]/, 1)[0]
  return dashboardPages.has(page as DashboardPage)
}

const props = defineProps<{ user: UserInfo }>()
const emit = defineEmits<{ logout: [] }>()
const appVersion = __APP_VERSION__
const loggingOut = ref(false)
const activePage = ref<DashboardPage>(pageFromLocation())
const archiveMobileDetailOpen = ref(false)
let applyingBrowserRoute = false
// Keep the user's sidebar preference separate from the temporary compact
// layout used while the message archive is open.
const userSidebarCollapsed = ref(window.localStorage.getItem('tg-backup-sidebar-user-collapsed') === 'true')
const archiveAutoCollapsed = ref(false)
const sidebarCollapsed = computed(() => userSidebarCollapsed.value || archiveAutoCollapsed.value)
const realtimeState = ref<RealtimeConnectionState>(realtimeClient.state)
const accountEntityId = ref(props.user.telegram?.entity_id ?? null)
const accountTelegramUserId = ref(props.user.telegram?.telegram_user_id ?? null)
const accountPhotoId = ref(props.user.telegram?.photo_id ?? null)
const accountAvatarUrl = ref(props.user.telegram?.avatar_url ?? null)
const accountAvatarBroken = ref(false)

function toggleSidebar() {
  if (archiveAutoCollapsed.value) {
    archiveAutoCollapsed.value = false
    userSidebarCollapsed.value = false
    return
  }
  userSidebarCollapsed.value = !userSidebarCollapsed.value
}

watch(userSidebarCollapsed, (value) => {
  window.localStorage.setItem('tg-backup-sidebar-user-collapsed', String(value))
})

watch(activePage, (page, previousPage) => {
  if (page === 'archive' && previousPage !== 'archive') {
    archiveAutoCollapsed.value = !userSidebarCollapsed.value
  } else if (previousPage === 'archive' && page !== 'archive') {
    archiveAutoCollapsed.value = false
    archiveMobileDetailOpen.value = false
  }

  const targetHash = `#/${page}`
  if (!applyingBrowserRoute && window.location.hash !== targetHash) {
    window.history.pushState(null, '', targetHash)
  }
})

function syncPageFromLocation() {
  applyingBrowserRoute = true
  activePage.value = pageFromLocation()
  applyingBrowserRoute = false
}

const realtimeLabel = computed(() => ({
  connected: '已连接',
  connecting: '正在建立实时同步',
  reconnecting: '重连中',
  disconnected: '未连接',
}[realtimeState.value]))

function onRealtimeState(event: Event) {
  realtimeState.value = (event as CustomEvent<{ state: RealtimeConnectionState }>).detail.state
}

function onRealtimeEvent(event: Event) {
  const message = (event as CustomEvent<RealtimeEvent>).detail
  if (!message || ![
    'telegram.entity.updated',
    'telegram.entity.avatar.updated',
  ].includes(message.type)) return
  const payload = message.payload as {
    entity_id?: number
    peer_id?: number
    photo_id?: number | null
    variants?: string[]
  }
  const isAccount = (
    (accountEntityId.value && payload.entity_id === accountEntityId.value)
    || payload.peer_id === accountTelegramUserId.value
  )
  if (!isAccount) return
  if (payload.entity_id) accountEntityId.value = payload.entity_id
  if (message.type === 'telegram.entity.updated') {
    if (payload.photo_id !== accountPhotoId.value) {
      accountPhotoId.value = payload.photo_id ?? null
      accountAvatarUrl.value = null
      accountAvatarBroken.value = false
    }
  } else if (
    payload.entity_id
    && payload.photo_id
    && payload.variants?.includes('small')
  ) {
    accountPhotoId.value = payload.photo_id
    accountAvatarUrl.value = `/api/entities/${payload.entity_id}/avatar/${payload.photo_id}/small`
    accountAvatarBroken.value = false
  }
}

async function refreshAccountAvatar() {
  try {
    const latest = await api<UserInfo>('/api/auth/me')
    accountTelegramUserId.value = latest.telegram?.telegram_user_id ?? null
    accountEntityId.value = latest.telegram?.entity_id ?? null
    accountPhotoId.value = latest.telegram?.photo_id ?? null
    accountAvatarUrl.value = latest.telegram?.avatar_url ?? null
    accountAvatarBroken.value = false
  } catch {
    // The global authentication handler owns session-expiry feedback.
  }
}

onMounted(() => {
  const canonicalHash = `#/${activePage.value}`
  if (!locationHasDashboardPage()) {
    window.history.replaceState(null, '', canonicalHash)
  }
  window.addEventListener('popstate', syncPageFromLocation)
  window.addEventListener('hashchange', syncPageFromLocation)
  window.addEventListener('tg-realtime-state', onRealtimeState)
  window.addEventListener('tg-realtime-event', onRealtimeEvent)
  refreshAccountAvatar()
})
onUnmounted(() => {
  window.removeEventListener('popstate', syncPageFromLocation)
  window.removeEventListener('hashchange', syncPageFromLocation)
  window.removeEventListener('tg-realtime-state', onRealtimeState)
  window.removeEventListener('tg-realtime-event', onRealtimeEvent)
})

async function logout() {
  loggingOut.value = true
  try {
    await api('/api/auth/logout', { method: 'POST' })
    emit('logout')
  } finally {
    loggingOut.value = false
  }
}
</script>

<template>
  <div :class="['dashboard-shell', { 'archive-mode': activePage === 'archive', 'archive-mobile-detail-open': activePage === 'archive' && archiveMobileDetailOpen, 'sidebar-collapsed': sidebarCollapsed }]">
    <aside class="sidebar" aria-label="主导航">
      <div class="sidebar-brand">
        <img class="brand-mark tiny" src="/app-icon.png" alt="" aria-hidden="true" />
        <span>tgBackup</span>
      </div>
      <button
        class="sidebar-toggle"
        type="button"
        :class="{ collapsed: sidebarCollapsed }"
        :aria-label="sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'"
        :aria-expanded="!sidebarCollapsed"
        :title="sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'"
        @click="toggleSidebar"
      ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 5-7 7 7 7" /></svg></button>

      <nav class="sidebar-nav">
        <button :class="['nav-item', { active: activePage === 'overview' }]" :title="sidebarCollapsed ? '概览' : undefined" @click="activePage = 'overview'">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 13h6V4H4v9Zm0 7h6v-5H4v5Zm10 0h6v-9h-6v9Zm0-16v5h6V4h-6Z" /></svg>
          <span>概览</span>
        </button>
        <button :class="['nav-item', { active: activePage === 'chats' }]" :title="sidebarCollapsed ? '会话管理' : undefined" @click="activePage = 'chats'">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v11H8.7L5 19.1V5Zm-2-2v20l6.4-5H21V3H3Z" /></svg>
          <span>会话管理</span>
        </button>
        <button :class="['nav-item', { active: activePage === 'rules' }]" :title="sidebarCollapsed ? '规则管理' : undefined" @click="activePage = 'rules'">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14v3H5V4Zm0 6h14v3H5v-3Zm0 6h9v3H5v-3Zm12.2-.2 1.3 1.3 2.8-3 1.4 1.4-4.2 4.5-2.7-2.8 1.4-1.4Z" /></svg>
          <span>规则管理</span>
        </button>
        <button :class="['nav-item', { active: activePage === 'archive' }]" :title="sidebarCollapsed ? '聊天记录' : undefined" @click="activePage = 'archive'">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H9l-5 4V5.5Zm4 2h8v2H8v-2Zm0 4h6v2H8v-2Z" /></svg>
          <span>聊天记录</span>
        </button>
      </nav>

      <div class="sidebar-meta">
        <div class="sidebar-foot" aria-live="polite">
          <span :class="['status-dot', realtimeState]"></span>
          <span>{{ realtimeLabel }}</span>
        </div>
        <div class="sidebar-version" :title="`tgBackup ${appVersion}`">v{{ appVersion }}</div>
      </div>
    </aside>

    <div :class="['workspace', { 'archive-workspace': activePage === 'archive' }]">
      <header v-if="activePage !== 'archive'" class="topbar">
        <div class="mobile-page-title">{{ activePage === 'overview' ? '概览' : activePage === 'chats' ? '会话管理' : activePage === 'rules' ? '规则管理' : '账户' }}</div>
        <div class="topbar-account-actions">
          <button
            :class="['account-button', { active: activePage === 'account' }]"
            type="button"
            aria-label="打开账户管理"
            :aria-current="activePage === 'account' ? 'page' : undefined"
            @click="activePage = 'account'"
          >
            <span class="avatar">
              <img
                v-if="accountAvatarUrl && !accountAvatarBroken"
                :src="accountAvatarUrl"
                :alt="`${user.telegram?.display_name || user.username}头像`"
                decoding="async"
                @error="accountAvatarBroken = true"
              />
              <template v-else>{{ user.username.slice(0, 1).toUpperCase() }}</template>
            </span>
            <span class="account-name">{{ user.username }}</span>
          </button>
          <button class="topbar-logout-button" type="button" :disabled="loggingOut" @click="logout">
            {{ loggingOut ? '正在退出' : '退出' }}
          </button>
        </div>
      </header>

      <Transition name="workspace-page" mode="out-in">
      <OverviewView
        v-if="activePage === 'overview'"
        key="overview"
        :username="user.username"
        @open-chats="activePage = 'chats'"
        @open-rules="activePage = 'rules'"
        @open-archive="activePage = 'archive'"
      />

      <ChatsView v-else-if="activePage === 'chats'" key="chats" @manage-rule="activePage = 'rules'" />
      <RulesView v-else-if="activePage === 'rules'" key="rules" @browse-chats="activePage = 'chats'" />
      <ArchiveView v-else-if="activePage === 'archive'" key="archive" @mobile-detail-change="archiveMobileDetailOpen = $event" />
      <UserManagement
        v-else
        key="account"
        :user="user"
        :avatar-url="accountAvatarUrl && !accountAvatarBroken ? accountAvatarUrl : null"
        @logout="logout"
      />
      </Transition>
    </div>
  </div>
</template>
