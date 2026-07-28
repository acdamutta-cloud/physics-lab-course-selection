<script setup lang="ts">
import { ref } from 'vue'
import LoginPage from './components/LoginPage.vue'
import StudentPortal from './components/StudentPortal.vue'
import TeacherPortal from './components/TeacherPortal.vue'
import SystemPortal from './components/SystemPortal.vue'

type Portal = 'student' | 'teacher' | 'admin' | null
const activePortal = ref<Portal>(null)
</script>

<template>
  <StudentPortal v-if="activePortal === 'student'" @logout="activePortal = null" />
  <TeacherPortal v-else-if="activePortal === 'teacher'" @logout="activePortal = null" />
  <SystemPortal v-else-if="activePortal === 'admin'" @logout="activePortal = null" />
  <LoginPage v-else @role-login="activePortal = $event" />
</template>
