<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '../api'

const props = defineProps<{ mode: 'setup' | 'login' }>()
const emit = defineEmits<{ authenticated: [] }>()

const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
const isSetup = computed(() => props.mode === 'setup')

async function submit() {
  error.value = ''
  busy.value = true
  try {
    await api(isSetup.value ? '/api/auth/setup' : '/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: username.value, password: password.value }),
    })
    emit('authenticated')
  } catch (value) {
    error.value = value instanceof Error ? value.message : '操作失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="auth-layout">
    <div class="auth-intro">
      <div class="brand-lockup">
        <span class="brand-mark small" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M20.5 4.5 3.8 10.9c-1.1.4-1.1 1.1-.2 1.4l4.3 1.3 1.7 5.2c.2.6.1.8.8.8.5 0 .8-.2 1-.4l2.1-2 4.4 3.2c.8.5 1.4.2 1.6-.8l2.9-13.7c.3-1.2-.5-1.8-1.9-1.4Z" /></svg>
        </span>
        <span>tgBackup</span>
      </div>
      <p class="eyebrow">私有 · 本地 · 可恢复</p>
      <h1>{{ isSetup ? '把对话留在本地。' : '欢迎回来。' }}</h1>
      <p class="lede">
        {{ isSetup
          ? '创建第一个管理员。完成后，公开注册将永久关闭。'
          : '登录后继续查看备份状态与 Telegram 连接。' }}
      </p>
    </div>

    <form class="glass-card auth-card" @submit.prevent="submit">
      <div>
        <p class="eyebrow">{{ isSetup ? '首次设置' : '系统登录' }}</p>
        <h2>{{ isSetup ? '创建管理员' : '继续使用 tgBackup' }}</h2>
      </div>

      <label class="field">
        <span>用户名</span>
        <input v-model="username" autocomplete="username" minlength="3" maxlength="64" required autofocus />
      </label>
      <label class="field">
        <span>密码</span>
        <input
          v-model="password"
          type="password"
          :autocomplete="isSetup ? 'new-password' : 'current-password'"
          minlength="10"
          maxlength="256"
          required
        />
        <small v-if="isSetup">至少 10 个字符。密码仅保存为 Argon2 哈希。</small>
      </label>

      <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
      <button class="button primary wide" :disabled="busy">
        <span v-if="busy" class="button-spinner" aria-hidden="true"></span>
        {{ busy ? '请稍候' : isSetup ? '创建并进入' : '登录' }}
      </button>
    </form>
  </section>
</template>
