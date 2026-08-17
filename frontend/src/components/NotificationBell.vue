<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api/client'

interface NotifItem {
  request_no?: string
  title?: string
  msg?: string
  type?: string
  student_name?: string
  status?: string
  time?: string
  [key: string]: unknown
}

const props = withDefaults(defineProps<{
  fetchPath: string
  readPath: string
  pollMs?: number
  onItemClick?: (item: NotifItem) => void
  onCountChange?: (count: number, previous: number) => void
  onRead?: () => void
}>(), { pollMs: 30000 })

const items = ref<NotifItem[]>([])
const open = ref(false)
const fetching = ref(false)

function itemText(n: NotifItem): string {
  if (n.msg) return String(n.msg)
  if (n.title) return String(n.title)
  if (n.student_name) return `${n.student_name} · ${n.type || ''}`
  return String(n.type || '新通知')
}

async function refresh() {
  if (fetching.value) return
  fetching.value = true
  try {
    const previous = items.value.length
    items.value = await api.get<NotifItem[]>(props.fetchPath)
    if (items.value.length !== previous) {
      props.onCountChange?.(items.value.length, previous)
    }
  } catch {
    // 网络异常时保留现有数据，等待下一次轮询
  } finally {
    fetching.value = false
  }
}

async function readOne(index: number, item: NotifItem) {
  items.value.splice(index, 1)
  try {
    await api.post(props.readPath, { value: JSON.stringify(item) })
    props.onRead?.()
  } catch {
    // 删除失败时该条会在下次轮询中重新出现
  }
}

async function readAll() {
  const snapshot = items.value
  items.value = []
  for (const item of snapshot) {
    await api.post(props.readPath, { value: JSON.stringify(item) }).catch(() => {})
  }
  props.onRead?.()
}

function handleItemClick(item: NotifItem) {
  props.onItemClick?.(item)
}

function handleDocumentClick(event: MouseEvent) {
  const root = document.querySelector('.notif-bell')
  if (root && !root.contains(event.target as Node)) open.value = false
}

let timer: ReturnType<typeof setInterval> | undefined

onMounted(() => {
  void refresh()
  timer = setInterval(() => void refresh(), props.pollMs)
  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<template>
  <div class="notif-bell">
    <button
      class="bell-button"
      type="button"
      :aria-label="`通知（${items.length} 条未读）`"
      @click="open = !open"
    >
      🔔
      <i v-if="items.length">{{ items.length }}</i>
    </button>
    <div v-if="open" class="bell-panel">
      <div class="bell-head">
        <span>通知 · {{ items.length }} 条</span>
        <button v-if="items.length" type="button" @click="readAll">全部已读</button>
      </div>
      <div v-if="!items.length" class="bell-empty">暂无通知</div>
      <div
        v-for="(n, i) in items"
        :key="n.request_no || i"
        class="bell-item"
        @click="handleItemClick(n)"
      >
        <div class="bell-item-main">
          <div class="bell-item-text">{{ itemText(n) }}</div>
          <div class="bell-item-meta">
            <span v-if="n.type" class="bell-tag">{{ n.type }}</span>
            <span v-if="n.request_no" class="bell-no">{{ n.request_no }}</span>
            <span class="bell-time">{{ n.time }}</span>
          </div>
        </div>
        <button type="button" class="bell-read" @click.stop="readOne(i, n)">已读</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.notif-bell {
  position: relative;
  display: inline-block;
}
.bell-button {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid #dce4e8;
  background: #fff;
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  position: relative;
}
.bell-button i {
  position: absolute;
  top: -4px;
  right: -6px;
  min-width: 15px;
  height: 15px;
  padding: 0 3px;
  border-radius: 8px;
  background: #f08a4b;
  color: #fff;
  font-size: 9px;
  font-style: normal;
  line-height: 15px;
  text-align: center;
}
.bell-panel {
  position: absolute;
  right: 0;
  top: 40px;
  z-index: 30;
  width: 300px;
  max-height: 320px;
  overflow-y: auto;
  background: #fff;
  border: 1px solid #dce4e8;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 0;
}
.bell-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid #eee;
  font-size: 12px;
  color: #657885;
  position: sticky;
  top: 0;
  background: #fff;
}
.bell-head button {
  padding: 2px 8px;
  border: 1px solid #dce4e8;
  border-radius: 4px;
  background: #fff;
  color: #277e82;
  font-size: 11px;
  cursor: pointer;
}
.bell-empty {
  padding: 24px 12px;
  text-align: center;
  color: #919da7;
  font-size: 12px;
}
.bell-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #f5f5f5;
  cursor: default;
}
.bell-item:hover {
  background: #f7fafb;
}
.bell-item-main {
  flex: 1;
  min-width: 0;
}
.bell-item-text {
  color: #405562;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
}
.bell-item-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  color: #919da7;
  font-size: 10px;
}
.bell-tag {
  padding: 1px 6px;
  border-radius: 3px;
  background: #eef4f4;
  color: #277e82;
}
.bell-read {
  flex-shrink: 0;
  padding: 3px 8px;
  border: 1px solid #dce4e8;
  border-radius: 4px;
  background: #fff;
  color: #277e82;
  font-size: 11px;
  cursor: pointer;
}
</style>
