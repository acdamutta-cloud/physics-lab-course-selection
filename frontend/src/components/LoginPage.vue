<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { loginApi, type TokenResponse } from '../api/auth'
import type { UserProfile } from '../api/auth'
import { api } from '../api/client'

type Role = 'admin' | 'teacher' | 'student'
type LoginMode = 'account' | 'phone'

const emit = defineEmits<{ loginSuccess: [user: UserProfile] }>()
const roles: Array<{ id: Role; label: string }> = [
  { id: 'admin', label: '管理员' },
  { id: 'teacher', label: '教师' },
  { id: 'student', label: '学生' },
]

const activeRole = ref<Role>('student')
const loginMode = ref<LoginMode>('account')
const account = ref('')
const password = ref('')
const phone = ref('')
const code = ref('')
const rememberMe = ref(true)
const showPassword = ref(false)
const countdown = ref(0)
const message = ref('')
const isSubmitting = ref(false)
let countdownTimer: number | undefined
let messageTimer: number | undefined

const roleName = computed(() => roles.find((role) => role.id === activeRole.value)?.label ?? '')

function setMessage(text: string) {
  message.value = text
  window.clearTimeout(messageTimer)
  messageTimer = window.setTimeout(() => (message.value = ''), 2800)
}

async function sendCode() {
  if (!/^1\d{10}$/.test(phone.value)) {
    setMessage('请输入正确的 11 位手机号')
    return
  }
  try {
    await api.post('/auth/send-code', { phone: phone.value })
    countdown.value = 60
    setMessage('验证码已发送，请查看后端终端')
    countdownTimer = window.setInterval(() => {
      countdown.value -= 1
      if (countdown.value <= 0) window.clearInterval(countdownTimer)
    }, 1000)
  } catch (err) {
    setMessage(err instanceof Error ? err.message : '发送失败')
  }
}

async function handleSubmit() {
  if (loginMode.value === 'account') {
    if (!account.value.trim() || !password.value) {
      setMessage('请填写账号和密码')
      return
    }
    isSubmitting.value = true
    try {
      const result = await loginApi(account.value.trim(), password.value)
      localStorage.setItem('access_token', result.access_token)
      localStorage.setItem('refresh_token', result.refresh_token)
      emit('loginSuccess', result.user)
    } catch (err) {
      isSubmitting.value = false
      setMessage(err instanceof Error ? err.message : '登录失败，请重试')
    }
    return
  }
  if (loginMode.value === 'phone') {
    if (!/^1\d{10}$/.test(phone.value) || !/^\d{6}$/.test(code.value)) {
      setMessage('请填写正确的手机号和 6 位验证码')
      return
    }
    isSubmitting.value = true
    try {
      const result = await api.post<TokenResponse>('/auth/login/phone', {
        phone: phone.value,
        code: code.value,
      })
      localStorage.setItem('access_token', result.access_token)
      localStorage.setItem('refresh_token', result.refresh_token)
      emit('loginSuccess', result.user)
    } catch (err) {
      isSubmitting.value = false
      setMessage(err instanceof Error ? err.message : '登录失败，请重试')
    }
    return
  }
}

async function demoLogin(loginName: string) {
  isSubmitting.value = true
  try {
    const result = await loginApi(loginName, 'Demo@123456')
    localStorage.setItem('access_token', result.access_token)
    localStorage.setItem('refresh_token', result.refresh_token)
    emit('loginSuccess', result.user)
  } catch {
    isSubmitting.value = false
    setMessage('登录失败，请确认服务器已启动')
  }
}

// 忘记密码弹窗
const showResetDialog = ref(false)
const resetStep = ref(1) // 1=输入手机号, 2=输入验证码和新密码
const resetPhone = ref('')
const resetCode = ref('')
const resetNewPassword = ref('')
const resetConfirmPassword = ref('')
const resetCountdown = ref(0)
const resetSending = ref(false)
const resetSubmitting = ref(false)
let resetTimer: number | undefined

function editResetPhone() {
  resetStep.value = 1
  window.clearInterval(resetTimer)
  resetCountdown.value = 0
}

function openResetDialog() {
  showResetDialog.value = true
  resetStep.value = 1
  resetPhone.value = ''
  resetCode.value = ''
  resetNewPassword.value = ''
  resetConfirmPassword.value = ''
  resetCountdown.value = 0
  window.clearInterval(resetTimer)
}

function closeResetDialog() {
  showResetDialog.value = false
  window.clearInterval(resetTimer)
}

async function sendResetCode() {
  if (!/^1\d{10}$/.test(resetPhone.value)) {
    setMessage('请输入正确的 11 位手机号')
    return
  }
  resetSending.value = true
  try {
    await api.post('/auth/send-code', { phone: resetPhone.value })
    resetCountdown.value = 60
    setMessage('验证码已发送，请查看后端终端')
    resetTimer = window.setInterval(() => {
      resetCountdown.value -= 1
      if (resetCountdown.value <= 0) window.clearInterval(resetTimer)
    }, 1000)
    resetStep.value = 2
  } catch (err) {
    setMessage(err instanceof Error ? err.message : '发送失败')
  } finally {
    resetSending.value = false
  }
}

async function submitResetPassword() {
  if (!/^\d{6}$/.test(resetCode.value)) {
    setMessage('请输入 6 位验证码')
    return
  }
  if (resetNewPassword.value.length < 6) {
    setMessage('新密码至少 6 位')
    return
  }
  if (resetNewPassword.value !== resetConfirmPassword.value) {
    setMessage('两次输入的密码不一致')
    return
  }
  resetSubmitting.value = true
  try {
    await api.post('/auth/reset-password', {
      phone: resetPhone.value,
      code: resetCode.value,
      new_password: resetNewPassword.value,
    })
    setMessage('密码已重置，请使用新密码登录')
    closeResetDialog()
  } catch (err) {
    setMessage(err instanceof Error ? err.message : '重置失败，请重试')
  } finally {
    resetSubmitting.value = false
  }
}

onBeforeUnmount(() => {
  window.clearInterval(countdownTimer)
  window.clearTimeout(messageTimer)
})
</script>

<template>
  <main class="login-page">
    <section class="brand-panel" aria-label="系统介绍">
      <div class="brand-content">
        <div class="brand-mark" aria-hidden="true">
          <span class="orbit orbit-one"></span><span class="orbit orbit-two"></span><span class="nucleus"></span>
        </div>
        <p class="eyebrow">PHYSICS LAB · SMART CAMPUS</p>
        <h1>物理实验<br />智能选课系统</h1>
        <p class="brand-description">让每一次实验安排更高效，让每一门课程选择更清晰。</p>
        <div class="feature-row">
          <span><i></i> 智能排课</span><span><i></i> 实时选课</span><span><i></i> 统一管理</span>
        </div>
      </div>
      <div class="physics-decoration" aria-hidden="true">
        <span class="wave"></span><span class="formula formula-one">E = mc²</span>
        <span class="formula formula-two">λ = h / p</span><span class="particle particle-one"></span>
        <span class="particle particle-two"></span><span class="particle particle-three"></span>
      </div>
      <p class="brand-footer">探索规律 · 验证真理 · 启迪创新</p>
    </section>

    <section class="form-panel">
      <div class="mobile-brand"><div class="mini-mark"><span></span></div><strong>物理实验智能选课系统</strong></div>
      <div class="login-card">
        <div class="welcome">
          <p class="welcome-label">WELCOME BACK</p>
          <h2>欢迎登录</h2>
          <p>请选择您的身份并登录系统</p>
        </div>

        <div class="role-switch" aria-label="选择登录身份">
          <button v-for="role in roles" :key="role.id" type="button" :class="{ active: activeRole === role.id }" @click="activeRole = role.id">
            <span class="role-icon" aria-hidden="true">{{ role.id === 'admin' ? '◆' : role.id === 'teacher' ? '●' : '▲' }}</span>
            {{ role.label }}
          </button>
        </div>

        <div class="login-tabs" role="tablist" aria-label="登录方式">
          <button type="button" role="tab" :aria-selected="loginMode === 'account'" :class="{ active: loginMode === 'account' }" @click="loginMode = 'account'">账号密码登录</button>
          <button type="button" role="tab" :aria-selected="loginMode === 'phone'" :class="{ active: loginMode === 'phone' }" @click="loginMode = 'phone'">手机验证码登录</button>
        </div>

        <form @submit.prevent="handleSubmit">
          <template v-if="loginMode === 'account'">
            <label class="field">
              <span>账号</span>
              <span class="input-wrap"><span class="input-icon" aria-hidden="true">◎</span>
                <input v-model="account" type="text" autocomplete="username" :placeholder="activeRole === 'student' ? '请输入学号' : activeRole === 'teacher' ? '请输入工号' : '请输入管理员账号'" />
              </span>
            </label>
            <label class="field">
              <span>密码</span>
              <span class="input-wrap"><span class="input-icon lock" aria-hidden="true">◇</span>
                <input v-model="password" :type="showPassword ? 'text' : 'password'" autocomplete="current-password" placeholder="请输入登录密码" />
                <button class="password-toggle" type="button" @click="showPassword = !showPassword">{{ showPassword ? '隐藏' : '显示' }}</button>
              </span>
            </label>
          </template>
          <template v-else>
            <label class="field">
              <span>手机号</span>
              <span class="input-wrap"><span class="input-icon" aria-hidden="true">⌁</span>
                <input v-model="phone" type="tel" inputmode="numeric" maxlength="11" placeholder="请输入手机号" />
              </span>
            </label>
            <label class="field">
              <span>验证码</span>
              <span class="code-row">
                <span class="input-wrap"><span class="input-icon" aria-hidden="true">#</span><input v-model="code" inputmode="numeric" maxlength="6" placeholder="请输入验证码" /></span>
                <button class="code-button" type="button" :disabled="countdown > 0" @click="sendCode">{{ countdown > 0 ? `${countdown}s 后重试` : '获取验证码' }}</button>
              </span>
            </label>
          </template>
          <div class="form-options">
            <label class="checkbox"><input v-model="rememberMe" type="checkbox" /><span>记住我</span></label>
            <button type="button" class="link-button" @click="openResetDialog">忘记密码？</button>
          </div>
          <button class="submit-button" type="submit" :disabled="isSubmitting"><span>{{ isSubmitting ? '正在验证...' : `登录${roleName}端` }}</span><span>→</span></button>
        </form>
        <div class="demo-entries">
          <button class="demo-entry admin-demo-entry" type="button" :disabled="isSubmitting" @click="demoLogin('demo_admin')"><span>新增</span> 进入系统端 <b>→</b></button>
          <button class="demo-entry" type="button" :disabled="isSubmitting" @click="demoLogin('d2024010001')"><span>无需账号</span> 进入学生端 <b>→</b></button>
          <button class="demo-entry teacher-demo-entry" type="button" :disabled="isSubmitting" @click="demoLogin('demo-t001')"><span>新增</span> 进入教师端 <b>→</b></button>
        </div>
      </div>
      <p class="copyright">© 2026 物理实验中心 · 智慧教学服务平台</p>
    </section>
    <Transition name="toast"><div v-if="message" class="toast" role="status">{{ message }}</div></Transition>
  </main>

  <!-- 忘记密码弹窗 -->
  <Teleport to="body">
    <Transition name="toast">
      <div v-if="showResetDialog" class="reset-backdrop" @click.self="closeResetDialog">
        <div class="reset-dialog">
          <div class="reset-dialog-header">
            <h3>重置密码</h3>
            <button type="button" @click="closeResetDialog">×</button>
          </div>
          <template v-if="resetStep === 1">
            <p class="reset-desc">请输入已绑定的手机号</p>
            <label class="field">
              <span>手机号</span>
              <span class="input-wrap">
                <input v-model="resetPhone" type="tel" inputmode="numeric" maxlength="11" placeholder="请输入手机号" />
              </span>
            </label>
            <button class="submit-button" type="button" :disabled="resetSending" @click="sendResetCode" style="margin-top:1rem">
              <span>{{ resetSending ? '发送中...' : '获取验证码' }}</span><span>→</span>
            </button>
          </template>
          <template v-else>
            <p class="reset-desc">手机号 {{ resetPhone }} <button type="button" class="link-button" @click="editResetPhone">修改</button></p>
            <label class="field">
              <span>验证码</span>
              <span class="code-row">
                <span class="input-wrap"><input v-model="resetCode" inputmode="numeric" maxlength="6" placeholder="请输入验证码" /></span>
                <button class="code-button" type="button" :disabled="resetCountdown > 0" @click="sendResetCode">{{ resetCountdown > 0 ? `${resetCountdown}s 后重试` : '重新发送' }}</button>
              </span>
            </label>
            <label class="field">
              <span>新密码</span>
              <span class="input-wrap">
                <input v-model="resetNewPassword" type="password" autocomplete="new-password" placeholder="至少 6 位" />
              </span>
            </label>
            <label class="field">
              <span>确认新密码</span>
              <span class="input-wrap">
                <input v-model="resetConfirmPassword" type="password" autocomplete="new-password" placeholder="再次输入新密码" />
              </span>
            </label>
            <button class="submit-button" type="button" :disabled="resetSubmitting" @click="submitResetPassword" style="margin-top:1rem">
              <span>{{ resetSubmitting ? '重置中...' : '重置密码' }}</span><span>→</span>
            </button>
          </template>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
