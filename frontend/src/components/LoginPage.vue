<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

type Role = 'admin' | 'teacher' | 'student'
type LoginMode = 'account' | 'phone'

const emit = defineEmits<{ roleLogin: [role: Role] }>()
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

function sendCode() {
  if (!/^1\d{10}$/.test(phone.value)) {
    setMessage('请输入正确的 11 位手机号')
    return
  }
  countdown.value = 60
  setMessage('验证码已发送（演示）')
  countdownTimer = window.setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0) window.clearInterval(countdownTimer)
  }, 1000)
}

function handleSubmit() {
  if (loginMode.value === 'account' && (!account.value.trim() || !password.value)) {
    setMessage('请填写账号和密码，或直接进入学生端演示')
    return
  }
  if (loginMode.value === 'phone' && (!/^1\d{10}$/.test(phone.value) || !/^\d{6}$/.test(code.value))) {
    setMessage('请填写正确的手机号和 6 位验证码')
    return
  }
  const targetRole = activeRole.value
  isSubmitting.value = true
  window.setTimeout(() => {
    isSubmitting.value = false
    emit('roleLogin', targetRole)
  }, 450)
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
            <button type="button" class="link-button" @click="setMessage('请联系系统管理员重置密码')">忘记密码？</button>
          </div>
          <button class="submit-button" type="submit" :disabled="isSubmitting"><span>{{ isSubmitting ? '正在验证...' : `登录${roleName}端` }}</span><span>→</span></button>
        </form>
        <div class="demo-entries">
          <button class="demo-entry admin-demo-entry" type="button" @click="emit('roleLogin', 'admin')"><span>新增</span> 进入系统端演示 <b>→</b></button>
          <button class="demo-entry" type="button" @click="emit('roleLogin', 'student')"><span>无需账号</span> 进入学生端演示 <b>→</b></button>
          <button class="demo-entry teacher-demo-entry" type="button" @click="emit('roleLogin', 'teacher')"><span>新增</span> 进入教师端演示 <b>→</b></button>
        </div>
        <p class="service-tip">当前为交互原型，页面数据均为演示数据</p>
      </div>
      <p class="copyright">© 2026 物理实验中心 · 智慧教学服务平台</p>
    </section>
    <Transition name="toast"><div v-if="message" class="toast" role="status">{{ message }}</div></Transition>
  </main>
</template>
