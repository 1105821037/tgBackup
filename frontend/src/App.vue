<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import AuthPanel from './components/AuthPanel.vue'
import Dashboard from './components/Dashboard.vue'
import { ApiError, api, type UserInfo } from './api'
import { realtimeClient } from './realtime'

type Screen = 'loading' | 'setup' | 'login' | 'dashboard'

const screen = ref<Screen>('loading')
const user = ref<UserInfo | null>(null)
const fatalError = ref('')

async function resolveScreen() {
  fatalError.value = ''
  try {
    const bootstrap = await api<{ needs_setup: boolean }>('/api/auth/bootstrap')
    if (bootstrap.needs_setup) {
      screen.value = 'setup'
      return
    }
    try {
      user.value = await api<UserInfo>('/api/auth/me')
      screen.value = 'dashboard'
      realtimeClient.connect()
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        screen.value = 'login'
        return
      }
      throw error
    }
  } catch (error) {
    fatalError.value = error instanceof Error ? error.message : '无法启动应用'
    screen.value = 'loading'
  }
}

async function onAuthenticated() {
  user.value = await api<UserInfo>('/api/auth/me')
  screen.value = 'dashboard'
  realtimeClient.connect()
}

async function onLogout() {
  realtimeClient.stop()
  user.value = null
  screen.value = 'login'
}

function onAuthExpired() {
  realtimeClient.stop()
  user.value = null
  screen.value = 'login'
}

onMounted(() => {
  window.addEventListener('tg-auth-expired', onAuthExpired)
  resolveScreen()
})
onUnmounted(() => {
  realtimeClient.stop()
  window.removeEventListener('tg-auth-expired', onAuthExpired)
})
</script>

<template>
  <main class="app-shell">
    <div class="ambient ambient-a" aria-hidden="true"></div>
    <div class="ambient ambient-b" aria-hidden="true"></div>

    <section v-if="screen === 'loading'" class="center-stage" aria-live="polite">
      <div class="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M20.5 4.5 3.8 10.9c-1.1.4-1.1 1.1-.2 1.4l4.3 1.3 1.7 5.2c.2.6.1.8.8.8.5 0 .8-.2 1-.4l2.1-2 4.4 3.2c.8.5 1.4.2 1.6-.8l2.9-13.7c.3-1.2-.5-1.8-1.9-1.4Z" /></svg>
      </div>
      <div v-if="!fatalError" class="spinner" aria-label="正在启动"></div>
      <div v-else class="error-card">
        <p>{{ fatalError }}</p>
        <button class="button secondary" @click="resolveScreen">重新连接</button>
      </div>
    </section>

    <AuthPanel
      v-else-if="screen === 'setup' || screen === 'login'"
      :mode="screen"
      @authenticated="onAuthenticated"
    />

    <Dashboard
      v-else-if="user"
      :user="user"
      @logout="onLogout"
    />
  </main>
</template>
