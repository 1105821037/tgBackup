<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, type TelegramStatus } from '../api'
import type { RealtimeEvent } from '../realtime'

type Stage = 'checking' | 'phone' | 'code' | 'password' | 'active'

const stage = ref<Stage>('checking')
const status = ref<TelegramStatus | null>(null)
const phone = ref('+86')
const code = ref('')
const password = ref('')
const phoneMasked = ref('')
const busy = ref(false)
const error = ref('')

const title = computed(() => {
  if (stage.value === 'active' && status.value?.connection === 'reconnecting') return 'Telegram 正在重连'
  if (stage.value === 'active' && status.value?.connection === 'connecting') return '正在连接 Telegram'
  if (stage.value === 'active') return 'Telegram 已连接'
  if (stage.value === 'code') return '输入验证码'
  if (stage.value === 'password') return '两步验证'
  return status.value?.state === 'login_required' ? '重新登录 Telegram' : '连接 Telegram'
})

const connectionLabel = computed(() => {
  if (status.value?.connection === 'reconnecting') return '连接中断，正在自动恢复'
  if (status.value?.connection === 'connecting') return '正在建立后台连接'
  return '连接正常'
})

function onRealtimeEvent(event: Event) {
  const message = (event as CustomEvent<RealtimeEvent>).detail
  if (message.type !== 'telegram.runtime.changed') return
  const connection = message.payload.connection as TelegramStatus['connection']
  if (!connection) return
  if (status.value) status.value = { ...status.value, ...message.payload, connection }
  if (connection === 'login_required' || connection === 'identity_mismatch') {
    if (status.value) status.value.state = connection
    stage.value = 'phone'
  } else if (!status.value && stage.value !== 'checking') {
    refresh()
  }
}

async function refresh() {
  error.value = ''
  try {
    status.value = await api<TelegramStatus>('/api/telegram/status')
    stage.value = status.value.state === 'active' ? 'active' : 'phone'
  } catch (value) {
    error.value = value instanceof Error ? value.message : '无法检查 Telegram 状态'
    stage.value = 'phone'
  }
}

async function submitPhone() {
  busy.value = true
  error.value = ''
  try {
    const result = await api<{ stage: 'code_sent'; phone_masked: string }>('/api/telegram/login/start', {
      method: 'POST',
      body: JSON.stringify({ phone: phone.value }),
    })
    phoneMasked.value = result.phone_masked
    stage.value = 'code'
  } catch (value) {
    error.value = value instanceof Error ? value.message : '无法发送验证码'
  } finally {
    busy.value = false
  }
}

async function submitCode() {
  busy.value = true
  error.value = ''
  try {
    const result = await api<{ stage: 'password_required' | 'complete' }>('/api/telegram/login/code', {
      method: 'POST',
      body: JSON.stringify({ code: code.value }),
    })
    if (result.stage === 'password_required') stage.value = 'password'
    else await refresh()
  } catch (value) {
    error.value = value instanceof Error ? value.message : '验证码校验失败'
  } finally {
    busy.value = false
  }
}

async function submitPassword() {
  busy.value = true
  error.value = ''
  try {
    await api('/api/telegram/login/password', {
      method: 'POST',
      body: JSON.stringify({ password: password.value }),
    })
    password.value = ''
    await refresh()
  } catch (value) {
    error.value = value instanceof Error ? value.message : '密码校验失败'
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  window.addEventListener('tg-realtime-event', onRealtimeEvent)
  refresh()
})
onUnmounted(() => window.removeEventListener('tg-realtime-event', onRealtimeEvent))
</script>

<template>
  <section class="glass-card connect-card">
    <div class="connect-icon" :class="{ active: stage === 'active' }" aria-hidden="true">
      <svg viewBox="0 0 24 24"><path d="M20.5 4.5 3.8 10.9c-1.1.4-1.1 1.1-.2 1.4l4.3 1.3 1.7 5.2c.2.6.1.8.8.8.5 0 .8-.2 1-.4l2.1-2 4.4 3.2c.8.5 1.4.2 1.6-.8l2.9-13.7c.3-1.2-.5-1.8-1.9-1.4Z" /></svg>
    </div>

    <div class="connect-copy">
      <p class="eyebrow">账号连接</p>
      <h2>{{ title }}</h2>
      <p v-if="stage === 'active'" class="muted">
        {{ status?.display_name }}
        <span v-if="status?.username">· @{{ status.username }}</span>
      </p>
      <p v-else-if="stage === 'code'" class="muted">验证码已发送至 {{ phoneMasked }}</p>
      <p v-else-if="stage === 'password'" class="muted">该账号启用了 Telegram 两步验证。</p>
      <p v-else class="muted">每个系统用户只允许绑定一个 Telegram 账号，完成后不可解绑或换绑。</p>
    </div>

    <div v-if="stage === 'checking'" class="status-row"><span class="spinner small"></span>正在确认登录状态</div>

    <form v-else-if="stage === 'phone'" class="inline-form" @submit.prevent="submitPhone">
      <label class="field grow">
        <span>手机号</span>
        <input v-model="phone" type="tel" autocomplete="tel" placeholder="+8613800000000" required />
      </label>
      <button class="button primary" :disabled="busy">{{ busy ? '发送中' : '发送验证码' }}</button>
    </form>

    <form v-else-if="stage === 'code'" class="inline-form" @submit.prevent="submitCode">
      <label class="field grow">
        <span>Telegram 验证码</span>
        <input v-model="code" inputmode="numeric" autocomplete="one-time-code" required autofocus />
      </label>
      <button class="button primary" :disabled="busy">{{ busy ? '验证中' : '继续' }}</button>
    </form>

    <form v-else-if="stage === 'password'" class="inline-form" @submit.prevent="submitPassword">
      <label class="field grow">
        <span>两步验证密码</span>
        <input v-model="password" type="password" autocomplete="current-password" required autofocus />
      </label>
      <button class="button primary" :disabled="busy">{{ busy ? '验证中' : '完成连接' }}</button>
    </form>

    <div v-else-if="stage === 'active'" :class="['binding-lock', status?.connection || 'connected']" aria-live="polite">
      <span :class="['status-dot', status?.connection || 'connected']"></span>
      <span>{{ connectionLabel }}</span>
      <span class="lock-note">绑定已锁定</span>
    </div>

    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
  </section>
</template>
