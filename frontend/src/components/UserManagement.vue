<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type ManagedUser, type UserInfo } from '../api'
import { serverDate } from '../utils/dateTime'

const props = defineProps<{ user: UserInfo; avatarUrl?: string | null }>()
const emit = defineEmits<{ logout: [] }>()

const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const passwordSaving = ref(false)
const passwordMessage = ref('')
const passwordError = ref('')

const users = ref<ManagedUser[]>([])
const usersLoading = ref(false)
const usersError = ref('')
const createOpen = ref(false)
const createUsername = ref('')
const createPassword = ref('')
const createAdmin = ref(false)
const createSaving = ref(false)
const createError = ref('')

const editing = ref<ManagedUser | null>(null)
const editUsername = ref('')
const editPassword = ref('')
const editAdmin = ref(false)
const editSaving = ref(false)
const editError = ref('')

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'short', day: 'numeric' }).format(serverDate(value))
}

async function changePassword() {
  passwordError.value = ''
  passwordMessage.value = ''
  if (newPassword.value !== confirmPassword.value) {
    passwordError.value = '两次输入的新密码不一致'
    return
  }
  passwordSaving.value = true
  try {
    await api('/api/users/me/password', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword.value, new_password: newPassword.value }),
    })
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    passwordMessage.value = '密码已更新，其他设备上的登录已退出'
  } catch (error) {
    passwordError.value = error instanceof Error ? error.message : '无法修改密码'
  } finally {
    passwordSaving.value = false
  }
}

async function loadUsers() {
  if (!props.user.is_owner) return
  usersLoading.value = true
  usersError.value = ''
  try {
    users.value = (await api<{ items: ManagedUser[] }>('/api/users')).items
  } catch (error) {
    usersError.value = error instanceof Error ? error.message : '无法读取账户'
  } finally {
    usersLoading.value = false
  }
}

async function createUser() {
  createSaving.value = true
  createError.value = ''
  try {
    const created = await api<ManagedUser>('/api/users', {
      method: 'POST',
      body: JSON.stringify({ username: createUsername.value, password: createPassword.value, is_owner: createAdmin.value }),
    })
    users.value = [...users.value, created]
    createUsername.value = ''
    createPassword.value = ''
    createAdmin.value = false
    createOpen.value = false
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '无法创建账户'
  } finally {
    createSaving.value = false
  }
}

function openEdit(user: ManagedUser) {
  editing.value = user
  editUsername.value = user.username
  editPassword.value = ''
  editAdmin.value = user.is_owner
  editError.value = ''
}

async function saveUser() {
  if (!editing.value) return
  editSaving.value = true
  editError.value = ''
  const payload: Record<string, unknown> = {
    username: editUsername.value,
    is_owner: editAdmin.value,
  }
  if (editPassword.value) payload.password = editPassword.value
  try {
    const updated = await api<ManagedUser>(`/api/users/${editing.value.id}`, {
      method: 'PUT', body: JSON.stringify(payload),
    })
    users.value = users.value.map((item) => item.id === updated.id ? updated : item)
    editing.value = null
  } catch (error) {
    editError.value = error instanceof Error ? error.message : '无法保存账户'
  } finally {
    editSaving.value = false
  }
}

onMounted(loadUsers)
</script>

<template>
  <div class="account-page dashboard-content">
    <section class="page-heading account-heading">
      <p class="eyebrow">账户</p>
      <h1>账户与安全</h1>
      <p class="lede">管理登录密码和本系统中的用户账户。</p>
    </section>

    <section class="account-identity glass-card">
      <span class="account-page-avatar"><img v-if="avatarUrl" :src="avatarUrl" alt="账户头像" /><template v-else>{{ user.username.slice(0, 1).toUpperCase() }}</template></span>
      <span><strong>{{ user.username }}</strong><small>{{ user.is_owner ? '管理员账户' : '普通账户' }}</small></span>
      <button class="button secondary compact" @click="emit('logout')">退出登录</button>
    </section>

    <section class="account-section glass-card">
      <header><span><strong>修改密码</strong><small>修改后，其他设备上的登录会立即失效。</small></span></header>
      <form class="account-password-form" @submit.prevent="changePassword">
        <label class="field"><span>当前密码</span><input v-model="currentPassword" type="password" autocomplete="current-password" required /></label>
        <label class="field"><span>新密码</span><input v-model="newPassword" type="password" autocomplete="new-password" minlength="10" required /><small>至少 10 个字符</small></label>
        <label class="field"><span>确认新密码</span><input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="10" required /></label>
        <p v-if="passwordError" class="inline-error">{{ passwordError }}</p>
        <p v-if="passwordMessage" class="account-success">{{ passwordMessage }}</p>
        <button type="submit" class="button primary account-submit" :disabled="passwordSaving">{{ passwordSaving ? '正在更新' : '更新密码' }}</button>
      </form>
    </section>

    <section v-if="user.is_owner" class="account-section account-users glass-card">
      <header><span><strong>用户管理</strong><small>每个系统用户可以绑定一个独立的 Telegram 账户。</small></span><button class="button secondary compact" @click="createOpen = true">新增用户</button></header>
      <div v-if="usersLoading" class="account-state"><span class="spinner small"></span>正在读取用户</div>
      <div v-else-if="usersError" class="account-state error">{{ usersError }}<button class="button secondary compact" @click="loadUsers">重试</button></div>
      <div v-else class="account-user-list">
        <article v-for="item in users" :key="item.id">
          <span class="managed-avatar">{{ item.username.slice(0, 1).toUpperCase() }}</span>
          <span class="managed-copy"><strong>{{ item.username }} <i v-if="item.id === user.id">当前账户</i></strong><small>{{ item.is_owner ? '管理员' : '普通用户' }} · {{ item.has_telegram ? '已绑定 Telegram' : '未绑定 Telegram' }} · 创建于 {{ formatDate(item.created_at) }}</small></span>
          <button class="account-edit-button" :disabled="item.id === user.id" :title="item.id === user.id ? '请在上方管理当前账户' : '管理账户'" :aria-label="`管理 ${item.username}`" @click="openEdit(item)"><svg viewBox="0 0 24 24"><path d="m4 20 4.5-1 10-10-3.5-3.5-10 10L4 20Zm9.5-13 3.5 3.5" /></svg></button>
        </article>
      </div>
    </section>

    <Transition name="modal">
      <div v-if="createOpen" class="modal-layer" @click.self="createOpen = false">
        <form class="account-dialog" role="dialog" aria-modal="true" aria-labelledby="create-user-title" @submit.prevent="createUser">
          <header><span><strong id="create-user-title">新增用户</strong><small>用户首次登录后可以绑定自己的 Telegram 账户。</small></span><button type="button" aria-label="关闭" @click="createOpen = false">×</button></header>
          <label class="field"><span>用户名</span><input v-model="createUsername" autocomplete="username" minlength="3" maxlength="64" required /></label>
          <label class="field"><span>初始密码</span><input v-model="createPassword" type="password" autocomplete="new-password" minlength="10" required /></label>
          <label class="account-check"><input v-model="createAdmin" type="checkbox" /><span><strong>管理员账户</strong><small>可以新增并管理其它系统用户</small></span></label>
          <p v-if="createError" class="inline-error">{{ createError }}</p>
          <footer><button type="button" class="button secondary" @click="createOpen = false">取消</button><button class="button primary" :disabled="createSaving">{{ createSaving ? '正在创建' : '创建用户' }}</button></footer>
        </form>
      </div>
    </Transition>

    <Transition name="modal">
      <div v-if="editing" class="modal-layer" @click.self="editing = null">
        <form class="account-dialog" role="dialog" aria-modal="true" aria-labelledby="edit-user-title" @submit.prevent="saveUser">
          <header><span><strong id="edit-user-title">管理 {{ editing.username }}</strong><small>留空新密码将保留原密码。</small></span><button type="button" aria-label="关闭" @click="editing = null">×</button></header>
          <label class="field"><span>用户名</span><input v-model="editUsername" minlength="3" maxlength="64" required /></label>
          <label class="field"><span>重置密码（可选）</span><input v-model="editPassword" type="password" autocomplete="new-password" minlength="10" /></label>
          <label class="account-check"><input v-model="editAdmin" type="checkbox" /><span><strong>管理员账户</strong><small>允许管理其它系统用户</small></span></label>
          <p v-if="editError" class="inline-error">{{ editError }}</p>
          <footer><button type="button" class="button secondary" @click="editing = null">取消</button><button class="button primary" :disabled="editSaving">{{ editSaving ? '正在保存' : '保存更改' }}</button></footer>
        </form>
      </div>
    </Transition>
  </div>
</template>
