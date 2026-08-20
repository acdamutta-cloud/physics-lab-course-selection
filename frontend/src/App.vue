<script setup lang="ts">
import { ref } from 'vue'
import LoginPage from './components/LoginPage.vue'
import StudentPortal from './components/StudentPortal.vue'
import TeacherPortal from './components/TeacherPortal.vue'
import SystemPortal from './components/SystemPortal.vue'
import type { UserProfile } from './api/auth'

type Portal = 'student' | 'teacher' | 'admin' | null

const portalMap: Record<string, Portal> = {
  STUDENT: 'student',
  TEACHER: 'teacher',
  ADMIN: 'admin',
}

function loadStoredUser(): UserProfile | null {
  try {
    const raw = localStorage.getItem('user_profile')
    return raw ? (JSON.parse(raw) as UserProfile) : null
  } catch {
    return null
  }
}

const savedUser = loadStoredUser()
const activePortal = ref<Portal>(savedUser ? portalMap[savedUser.user_type] ?? null : null)
const currentUser = ref<UserProfile | null>(savedUser)

function handleLoginSuccess(user: UserProfile) {
  currentUser.value = user
  localStorage.setItem('user_profile', JSON.stringify(user))
  activePortal.value = portalMap[user.user_type] ?? null
}

function handleLogout() {
  currentUser.value = null
  activePortal.value = null
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  localStorage.removeItem('user_profile')
}
</script>

<template>
  <StudentPortal v-if="activePortal === 'student'" :user="currentUser" @logout="handleLogout" />
  <TeacherPortal v-else-if="activePortal === 'teacher'" :user="currentUser" @logout="handleLogout" />
  <SystemPortal v-else-if="activePortal === 'admin'" :user="currentUser" @logout="handleLogout" />
  <LoginPage v-else @login-success="handleLoginSuccess" />
</template>
