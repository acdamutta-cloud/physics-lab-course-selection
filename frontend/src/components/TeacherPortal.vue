<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { UserProfile } from '../api/auth'
import { api } from '../api/client'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'

type TeacherView = 'home' | 'schedule' | 'classes' | 'adjustments' | 'resources'
type AdjustmentType = '调课申请' | '场地调整申请' | '停课申请'

const props = defineProps<{ user: UserProfile | null }>()
const emit = defineEmits<{ logout: [] }>()
const activeView = ref<TeacherView>('home')
const sidebarOpen = ref(false)
const toast = ref('')
const selectedProjectId = ref('')
const selectedSessionId = ref('')
const projectList = ref<Array<{ project_id: string; project_name: string; sessions: Array<{ session_id: string; week_no: number; day_of_week: number; start_slot: number; end_slot: number; lab_name: string; capacity: number; selected_count: number }> }>>([])
const selectedSessions = computed(() => projectList.value.find(p => p.project_id === selectedProjectId.value)?.sessions || [])
const selectedSession = computed(() => selectedSessions.value.find((s: any) => s.session_id === selectedSessionId.value))

async function fetchProjects() {
  try {
    const data = await api.get<{ projects: any[] }>('/teachers/me/projects')
    projectList.value = data.projects
    if (projectList.value.length && !selectedProjectId.value) {
      selectedProjectId.value = projectList.value[0].project_id
    }
  } catch { projectList.value = [] }
}
const studentKeyword = ref('')
const adjustmentDialog = ref<AdjustmentType | null>(null)
const adjustmentProject = ref('')
const adjustmentTarget = ref('')
const adjustmentReason = ref('')
const reportLocation = ref('')
const reportType = ref('')
const reportLevel = ref('一般')
const reportDescription = ref('')

const navItems: Array<{ id: TeacherView; label: string; icon: string }> = [
  { id: 'home', label: '首页', icon: '⌂' },
  { id: 'schedule', label: '教师课表', icon: '▦' },
  { id: 'classes', label: '项目学生管理', icon: '♙' },
  { id: 'adjustments', label: '教学调整申请', icon: '⇄' },
  { id: 'resources', label: '资源异常上报', icon: '△' },
]

const teacherName = computed(() => props.user?.name || '老师')
const employeeNo = computed(() => props.user?.employee_no || '****')
const department = computed(() => props.user?.department || '物理实验中心')
const teacherTitle = computed(() => props.user?.title || '教师')
const teacherGreeting = computed(() => `下午好，${teacherName.value}`)
const teacherInitial = computed(() => teacherName.value.slice(0, 1))
const homeSubtitle = computed(() => {
  const cw = (teacherProfile.value?.term as any)?.current_week
  const sc = teacherProfile.value?.scheduled_session_count ?? 0
  return `第 ${cw ?? '—'} 教学周 · 已排 ${sc} 个场次`
})

const viewMeta: Record<TeacherView, { title: string; subtitle: string }> = {
  home: { title: teacherGreeting.value, subtitle: '教学周信息加载中...' },
  schedule: { title: '教师课表', subtitle: '按教学周查看个人实验教学安排' },
  classes: { title: '项目学生管理', subtitle: '按实验项目场次查看自主选课学生的基本信息' },
  adjustments: { title: '教学调整申请', subtitle: '提交并跟踪调课、场地调整及停课申请' },
  resources: { title: '资源异常上报', subtitle: '上报实验室、仪器与耗材异常并跟踪处理进度' },
}

// ── API 数据 ──
const teacherProfile = ref<{
  name: string; department: string; title: string
  qualified_projects_count: number; scheduled_session_count: number
  teaching_tasks: Array<{ task_id: string; course_name: string; course_code: string; planned_student_count: number; week_start: number; week_end: number }>
} | null>(null)
const timetableWeek = ref(1)
const timetableCourseFilter = ref('全部课程')
const upcomingSessions = ref<Array<{ id: string; week_no: number; day_of_week: number; start_slot: number; end_slot: number; project_name: string; course_name: string; lab_name: string; selected_count: number; capacity: number }>>([])
const timetableData = ref<{ week: number; sessions: Array<{ id: string; day_of_week: number; start_slot: number; end_slot: number; project_name: string; course_name: string; course_code: string; lab_name: string; capacity: number; selected_count: number }>; total: number }>({ week: 1, sessions: [], total: 0 })
const termInfo = ref<{ academic_year: string; semester_no: number; start_date: string; current_week: number; total_weeks: number } | null>(null)

const weekDayHeaders = computed(() => {
  if (!termInfo.value?.start_date) return ['周日','周一','周二','周三','周四','周五','周六']
  const start = new Date(termInfo.value.start_date + 'T00:00:00')
  const sundayOffset = start.getDay() === 0 ? 0 : (7 - start.getDay())
  const sunday = new Date(start); sunday.setDate(start.getDate() + sundayOffset + (timetableWeek.value - 1) * 7)
  const days = ['日','一','二','三','四','五','六']
  return Array.from({length: 7}, (_, i) => {
    const d = new Date(sunday); d.setDate(sunday.getDate() + i)
    return `周${days[i]} ${String(d.getMonth()+1).padStart(2,'0')}/${String(d.getDate()).padStart(2,'0')}`
  })
})

const weekDates = computed(() => {
  if (!termInfo.value?.start_date) return ''
  const start = new Date(termInfo.value.start_date + 'T00:00:00')
  const sundayOffset = start.getDay() === 0 ? 0 : (7 - start.getDay())
  const ws = new Date(start); ws.setDate(start.getDate() + sundayOffset + (timetableWeek.value - 1) * 7)
  const we = new Date(ws); we.setDate(ws.getDate() + 6)
  const fmt = (d: Date) => `${String(d.getMonth()+1).padStart(2,'0')}.${String(d.getDate()).padStart(2,'0')}`
  return `${fmt(ws)} — ${fmt(we)}`
})

async function fetchTeacherProfile() {
  try { teacherProfile.value = await api.get('/teachers/me/profile') } catch { /* keep empty */ }
}
async function fetchTimetable() {
  try { timetableData.value = await api.get(`/teachers/me/timetable?week=${timetableWeek.value}`) } catch { /* keep empty */ }
}
async function fetchUpcoming() {
  try { upcomingSessions.value = (await api.get<{ sessions: Array<any> }>('/teachers/me/upcoming')).sessions } catch { /* keep empty */ }
}
const allWeeksSessions = ref<any[]>([])
const exportTeacherBusy = ref(false)
const exportTeacherRef = ref<HTMLDivElement | null>(null)

async function loadAllWeeks() {
  try {
    const data = await api.get<{ sessions: any[] }>('/teachers/me/timetable?week=0')
    allWeeksSessions.value = data.sessions
  } catch { allWeeksSessions.value = [] }
}

async function exportSchedule(format: 'png' | 'pdf') {
  if (exportTeacherBusy.value) return
  exportTeacherBusy.value = true
  try {
    await loadAllWeeks()
    await nextTick()
    const el = exportTeacherRef.value
    if (!el) { showToast('导出失败'); return }
    const canvas = await html2canvas(el, { backgroundColor: '#ffffff', scale: 2 })
    if (format === 'png') {
      const link = document.createElement('a')
      link.download = `教师课表_${teacherName.value}.png`
      link.href = canvas.toDataURL('image/png')
      link.click()
    } else {
      const pdf = new jsPDF('l', 'mm', 'a4')
      const w = pdf.internal.pageSize.getWidth()
      const h = (canvas.height * w) / canvas.width
      pdf.addImage(canvas.toDataURL('image/png'), 'PNG', 0, 0, w, h)
      pdf.save(`教师课表_${teacherName.value}.pdf`)
    }
    showToast(format === 'png' ? '图片已导出' : 'PDF已导出')
  } catch { showToast('导出失败，请重试') }
  exportTeacherBusy.value = false
}

const exportListRef = ref<HTMLDivElement | null>(null)
const exportListBusy = ref(false)

async function exportStudentList() {
  if (exportListBusy.value || !projectStudents.value.length) return
  exportListBusy.value = true
  try {
    await nextTick()
    const el = exportListRef.value
    if (!el) { showToast('导出失败'); return }
    const canvas = await html2canvas(el, { backgroundColor: '#ffffff', scale: 2 })
    const pdf = new jsPDF('p', 'mm', 'a4')
    const w = pdf.internal.pageSize.getWidth() - 20
    const h = (canvas.height * w) / canvas.width
    pdf.addImage(canvas.toDataURL('image/png'), 'PNG', 10, 10, w, h)
    const projName = projectList.value.find(p => p.project_id === selectedProjectId.value)?.project_name || '项目'
    pdf.save(`${projName}_学生名单.pdf`)
    showToast('名单已导出')
  } catch { showToast('导出失败') }
  exportListBusy.value = false
}

function weekGroups(sessions: any[]) {
  const groups: Record<number, any[]> = {}
  for (const s of sessions) {
    const w = s.week_no || 1
    if (!groups[w]) groups[w] = []
    groups[w].push(s)
  }
  return Object.entries(groups).sort((a, b) => +a[0] - +b[0])
}
onMounted(async () => {
  await fetchTeacherProfile()
  termInfo.value = teacherProfile.value?.term as any
  await fetchUpcoming()
  await fetchTimetable()
  await fetchProjects()
  await fetchTeacherAdjustments()
})

const apiTeachingTasks = computed(() => {
  if (!teacherProfile.value?.teaching_tasks?.length) return []
  return teacherProfile.value.teaching_tasks.map((t: any, i: number) => ({
    id: i + 1,
    project_id: t.project_id,
    project: t.course_name,
    course: t.course_name,
    courseCode: t.course_code,
    students: t.planned_student_count,
    capacity: t.planned_student_count,
    weekStart: t.week_start,
    week: `第 ${t.week_start}–${t.week_end} 周`,
    time: '待排课',
    room: '待分配',
    completed: 0,
    total: 1,
    color: ['#237f82','#3f789c','#5b6ea6','#7c68a3'][i % 4],
  }))
})

const teachingTasks = [
  {
    id: 1,
    project: '用单摆测量重力加速度',
    course: '大学物理实验（上）',
    courseCode: 'PHYS-LAB-101',
    students: 24,
    capacity: 24,
    week: '第 4 周',
    time: '周二 5–8 节',
    room: '实验楼 A203',
    completed: 1,
    total: 1,
    color: '#237f82',
  },
  {
    id: 2,
    project: '示波器的原理与使用',
    course: '大学物理实验（上）',
    courseCode: 'PHYS-LAB-101',
    students: 22,
    capacity: 24,
    week: '第 7 周',
    time: '周四 5–8 节',
    room: '实验楼 B105',
    completed: 0,
    total: 1,
    color: '#3f789c',
  },
  {
    id: 3,
    project: '光电效应与普朗克常量测定',
    course: '近代物理实验',
    courseCode: 'PHYS-LAB-203',
    students: 16,
    capacity: 16,
    week: '第 6 周',
    time: '周三 5–8 节',
    room: '近代物理实验室 2',
    completed: 1,
    total: 1,
    color: '#5b6ea6',
  },
  {
    id: 4,
    project: '密立根油滴实验',
    course: '近代物理实验',
    courseCode: 'PHYS-LAB-203',
    students: 14,
    capacity: 16,
    week: '第 12 周',
    time: '周二 9–12 节',
    room: '近代物理实验室 3',
    completed: 0,
    total: 1,
    color: '#7c68a3',
  },
]

const courseColorMap: Record<string, string> = {}
const courseColors = ['teal','blue','purple']
function courseColor(name: string) {
  if (!courseColorMap[name]) courseColorMap[name] = courseColors[Object.keys(courseColorMap).length % courseColors.length]
  return courseColorMap[name]
}
const filteredSessions = computed(() => {
  if (timetableCourseFilter.value === '全部课程') return timetableData.value.sessions
  return timetableData.value.sessions.filter(s => s.course_code === timetableCourseFilter.value)
})
const scheduleEvents = computed(() => filteredSessions.value.map((s, i) => {
  // DB: day_of_week 1=Sun..7=Sat，grid column 1=Sun..7=Sat，直接用
  return {
    day: s.day_of_week,
    start: s.start_slot,
    title: s.project_name,
    info: `${s.course_name} · ${s.selected_count}/${s.capacity} 人`,
    room: s.lab_name,
    tone: courseColor(s.course_name),
  }
}))
const students = [
  { projectId: 1, name: '陈同学', no: '2024****01', major: '物理学（师范）', phone: '138****1201' },
  { projectId: 1, name: '林同学', no: '2024****05', major: '电子信息科学与技术', phone: '137****4620' },
  { projectId: 1, name: '周同学', no: '2024****08', major: '材料物理', phone: '159****3048' },
  { projectId: 1, name: '赵同学', no: '2024****11', major: '物理学', phone: '186****7211' },
  { projectId: 1, name: '孙同学', no: '2024****16', major: '光电信息科学与工程', phone: '135****8316' },
  { projectId: 1, name: '何同学', no: '2024****20', major: '物理学（师范）', phone: '188****0920' },
  { projectId: 2, name: '王同学', no: '2024****02', major: '物理学', phone: '139****1102' },
  { projectId: 2, name: '吴同学', no: '2024****09', major: '应用物理学', phone: '136****2309' },
  { projectId: 3, name: '许同学', no: '2023****03', major: '应用物理学', phone: '158****4503' },
  { projectId: 3, name: '郑同学', no: '2023****12', major: '物理学（师范）', phone: '187****6612' },
  { projectId: 4, name: '杨同学', no: '2023****06', major: '应用物理学', phone: '156****7906' },
]

const adjustmentRecords = ref<Array<{ id: string; rawId: string; type: string; project: string; original: string; target: string; date: string; status: string; reason_text: string; student_name: string }>>([])

async function fetchTeacherAdjustments() {
  try {
    const items = await api.get<any[]>('/teachers/me/pending-adjustments')
    adjustmentRecords.value = items.map((item: any) => ({
      id: item.request_no,
      rawId: item.id,
      type: '补做申请',
      project: item.payload?.source?.session?.project_name || '',
      original: item.payload?.source?.session ? `第${item.payload.source.session.week_no}周 · ${item.payload.source.session.project_name}` : '',
      target: item.payload?.target ? `第${item.payload.target.week_no}周 第${item.payload.target.start_slot}–${item.payload.target.end_slot}节 · ${item.payload.target.project_name}` : '',
      date: (item.created_at || '').slice(0, 10),
      status: '待审批',
      reason_text: item.reason_text || '',
      student_name: item.student_name || '',
    }))
  } catch { adjustmentRecords.value = [] }
}

async function approveAdjustment(id: string) {
  try {
    await api.post(`/teachers/me/adjustments/${id}/review`, { decision: 'APPROVED', comment: '' })
    showToast('已通过，转管理员二审')
    await fetchTeacherAdjustments()
  } catch (e: any) { showToast(e?.message || '操作失败') }
}
const teacherOwnAdjustments = ref([
  { id: 'TJ20260318001', type: '调课申请', project: '用单摆测量重力加速度', original: '第 4 周 周二 5–8 节', target: '第 4 周 周五 5–8 节', date: '2026-03-18', status: '审核中' },
  { id: 'TJ20260302003', type: '场地调整申请', project: '示波器的原理与使用', original: '第 7 周 周四 5–8 节 · 实验楼 B105', target: '原时间 · 实验楼 A205', date: '2026-03-02', status: '已通过' },
  { id: 'TJ20260224002', type: '停课申请', project: '光电效应与普朗克常量测定', original: '第 6 周 周三 5–8 节', target: '待重新安排', date: '2026-02-24', status: '已驳回' },
])

async function rejectAdjustment(id: string) {
  const reason = prompt('驳回理由：')
  if (!reason) return
  try {
    await api.post(`/teachers/me/adjustments/${id}/review`, { decision: 'REJECTED', comment: reason })
    showToast('已驳回')
    await fetchTeacherAdjustments()
  } catch (e: any) { showToast(e?.message || '操作失败') }
}

const resourceRecords = ref([
  { id: 'YC20260320004', type: '仪器故障', location: '近代物理实验室 2', detail: '光电效应实验箱电流示数异常', level: '紧急', date: '2026-03-20', status: '处理中' },
  { id: 'YC20260312002', type: '耗材不足', location: '实验楼 A203', detail: '单摆实验备用细线库存不足', level: '一般', date: '2026-03-12', status: '已解决' },
  { id: 'YC20260306001', type: '环境异常', location: '实验楼 B105', detail: '实验台局部照明闪烁', level: '一般', date: '2026-03-06', status: '已解决' },
])

const displayedTasks = computed(() => apiTeachingTasks.value.length ? apiTeachingTasks.value : teachingTasks)
const selectedTask = computed(() => displayedTasks.value.find((task) => task.id === selectedProjectId.value) ?? displayedTasks.value[0])
watch(selectedTask, (task) => { if (task?.project_id) fetchProjectStudents(task.project_id) }, { immediate: true })
const projectStudents = ref<Array<{ name: string; student_no: string; major_name: string; enrollment_year: number }>>([])

async function fetchSessionStudents(sessionId: string) {
  if (!sessionId) { projectStudents.value = []; return }
  try {
    const data = await api.get<{ students: any[] }>(`/teachers/me/session-students?session_id=${sessionId}`)
    projectStudents.value = data.students
  } catch { projectStudents.value = [] }
}

const filteredStudents = computed(() => projectStudents.value.filter((s) => {
  const keyword = studentKeyword.value.trim().toLowerCase()
  return !keyword || `${s.name}${s.student_no}${s.major_name}`.toLowerCase().includes(keyword)
}))

watch(selectedSessionId, (sid) => { if (sid) fetchSessionStudents(sid) })
watch(selectedProjectId, (pid) => {
  if (pid && selectedSessions.value.length) {
    selectedSessionId.value = selectedSessions.value[0].session_id
  }
})

function navigate(view: TeacherView) {
  activeView.value = view
  sidebarOpen.value = false
}

function showToast(text: string) {
  toast.value = text
  window.setTimeout(() => {
    if (toast.value === text) toast.value = ''
  }, 2800)
}

function openAdjustment(type: AdjustmentType) {
  adjustmentDialog.value = type
  adjustmentProject.value = ''
  adjustmentTarget.value = ''
  adjustmentReason.value = ''
}

function submitAdjustment() {
  if (!adjustmentProject.value || !adjustmentTarget.value.trim() || !adjustmentReason.value.trim()) {
    showToast('请完整填写项目、目标安排和申请原因')
    return
  }
  adjustmentRecords.value.unshift({
    id: `TJ-DEMO-${String(adjustmentRecords.value.length + 1).padStart(3, '0')}`,
    type: adjustmentDialog.value ?? '调课申请',
    project: adjustmentProject.value,
    original: '当前教学安排',
    target: adjustmentTarget.value,
    date: '演示日期',
    status: '草稿演示',
  })
  adjustmentDialog.value = null
  showToast('申请已加入演示列表，未提交至真实系统')
}

function submitResourceReport() {
  if (!reportLocation.value || !reportType.value || !reportDescription.value.trim()) {
    showToast('请完整填写异常地点、类型和情况说明')
    return
  }
  resourceRecords.value.unshift({
    id: `YC-DEMO-${String(resourceRecords.value.length + 1).padStart(3, '0')}`,
    type: reportType.value,
    location: reportLocation.value,
    detail: reportDescription.value,
    level: reportLevel.value,
    date: '演示日期',
    status: '草稿演示',
  })
  reportDescription.value = ''
  showToast('异常已加入演示记录，未上报至真实系统')
}
</script>

<template>
  <div class="teacher-app">
    <aside class="teacher-sidebar" :class="{ open: sidebarOpen }">
      <div class="teacher-brand">
        <span class="teacher-brand-atom"><i></i></span>
        <div><strong>物理实验</strong><small>智能选课系统</small></div>
      </div>
      <nav class="teacher-nav" aria-label="教师端主导航">
        <p>教师工作台</p>
        <button v-for="item in navItems" :key="item.id" type="button" :class="{ active: activeView === item.id }" @click="navigate(item.id)">
          <span>{{ item.icon }}</span>{{ item.label }}<i v-if="item.id === 'resources'" class="teacher-nav-dot"></i>
        </button>
      </nav>
      <div class="teacher-semester"><span>当前学期</span><strong>{{ termInfo?.academic_year || '加载中' }} 学年</strong><small>{{ ['','第一学期','第二学期'][termInfo?.semester_no || 2] }} · 第 {{ termInfo?.current_week ?? '—' }} 教学周</small></div>
      <button class="teacher-logout" type="button" @click="emit('logout')"><span>↪</span> 退出演示</button>
    </aside>
    <button v-if="sidebarOpen" class="teacher-sidebar-mask" aria-label="关闭导航" @click="sidebarOpen = false"></button>

    <div class="teacher-main">
      <header class="teacher-topbar">
        <button class="teacher-menu" type="button" aria-label="打开导航" @click="sidebarOpen = true">☰</button>
        <div class="teacher-breadcrumb"><span>教师端</span><b>/</b>{{ activeView === 'home' ? '首页' : viewMeta[activeView].title }}</div>
        <div class="teacher-top-actions">
          <span class="teacher-demo-badge">演示数据</span>
          <button class="teacher-notice" type="button" @click="showToast('你有 3 条教学事项提醒')">♢<i>3</i></button>
          <div class="teacher-profile"><span>{{ teacherInitial }}</span><div><strong>{{ teacherName }}</strong><small>工号 {{ employeeNo }}</small></div></div>
        </div>
      </header>

      <main class="teacher-content">
        <div class="teacher-page-heading">
          <div><h1>{{ viewMeta[activeView].title }}</h1><p>{{ activeView === 'home' ? homeSubtitle : viewMeta[activeView].subtitle }}</p></div>
          <button v-if="activeView === 'home'" type="button" @click="navigate('schedule')">查看本周课表 <span>→</span></button>
          <template v-if="activeView === 'schedule'">
            <button class="teacher-outline-btn" type="button" :disabled="exportTeacherBusy" @click="exportSchedule('png')">↓ 导出图片</button>
            <button class="teacher-outline-btn" type="button" :disabled="exportTeacherBusy" @click="exportSchedule('pdf')" style="margin-left:6px">↓ 导出PDF</button>
          </template>
        </div>

        <template v-if="activeView === 'home'">
          <section class="teacher-hero">
            <div class="teacher-hero-profile">
              <span class="teacher-big-avatar">{{ teacherInitial }}</span>
              <div><small>TEACHING OVERVIEW</small><h2>{{ teacherName }}，欢迎回来</h2><p>{{ department }} · 实验教学岗</p></div>
            </div>
            <div class="teacher-info-grid">
              <div><span>教师姓名</span><strong>{{ teacherName }}</strong></div>
              <div><span>工号</span><strong>{{ employeeNo }}</strong></div>
              <div><span>所属单位</span><strong>{{ department }}</strong></div>
              <div><span>教师职称</span><strong>{{ teacherTitle }}</strong></div>
            </div>
            <span class="teacher-sample-tag">示例信息</span>
            <div class="teacher-hero-rings" aria-hidden="true"><i></i><b></b></div>
          </section>

          <section class="teacher-summary-grid">
            <article><span class="teacher-summary-icon teal">▤</span><div><small>承担课程</small><strong>{{ teacherProfile?.teaching_tasks?.length || 0 }} <i>门</i></strong><p>{{ teacherProfile?.teaching_tasks?.map(t=>t.course_name).join('、') || '加载中...' }}</p></div></article>
            <article><span class="teacher-summary-icon blue">✦</span><div><small>资格项目</small><strong>{{ teacherProfile?.qualified_projects_count || 0 }} <i>项</i></strong><p>已获得授课资格</p></div></article>
            <article><span class="teacher-summary-icon purple">▦</span><div><small>已排课场次</small><strong>{{ teacherProfile?.scheduled_session_count || 0 }} <i>场</i></strong><p>当前学期已排入课表</p></div></article>
            <article><span class="teacher-summary-icon amber">◷</span><div><small>待处理事项</small><strong>0 <i>项</i></strong><p>暂无待处理申请</p></div></article>
          </section>

          <div class="teacher-home-grid">
            <section class="teacher-panel teaching-task-panel">
              <div class="teacher-panel-title"><div><h3>承担课程</h3><p>本学期的实验教学课程安排</p></div></div>
              <article v-for="task in displayedTasks" :key="task.id" class="teaching-task-row">
                <div class="task-color" :style="{ background: task.color }">{{ task.course.slice(0, 1) }}</div>
                <div class="task-primary"><h4>{{ task.course }}</h4><p><span>{{ task.courseCode }}</span></p></div>
                <div class="task-detail"><span>计划学生</span><strong>{{ task.students }}</strong><small>人次</small></div>
                <div class="task-detail"><span>教学周</span><strong>{{ task.week }}</strong></div>
                <button type="button" @click="navigate('schedule'); timetableWeek = task.weekStart || 1; fetchTimetable()">查看课表 →</button>
              </article>
            </section>

            <aside class="teacher-side-column">
              <section class="teacher-panel today-teaching">
                <div class="teacher-panel-title"><div><h3>最近授课</h3><p>{{ upcomingSessions.length ? `即将授课 ${upcomingSessions.length} 场` : '暂无排课' }}</p></div></div>
                <article v-for="s in upcomingSessions.slice(0, 4)" :key="s.id">
                  <span class="time-block" :class="{ evening: s.start_slot >= 9 }">第{{ s.week_no }}周 {{ ['','周一','周二','周三','周四','周五','周六','周日'][s.day_of_week] }} {{ s.start_slot }}–{{ s.end_slot }} 节</span>
                  <div><strong>{{ s.project_name }}</strong><p>{{ s.course_name }} · {{ s.selected_count }}/{{ s.capacity }} 人</p><small>⌖ {{ s.lab_name }}</small></div>
                </article>
                <article v-if="!upcomingSessions.length" style="color:#888;font-size:.9rem;text-align:center;padding:1rem">暂无排课数据</article>
              </section>
              <section class="teacher-panel pending-matters">
                <div class="teacher-panel-title"><div><h3>教学工具</h3></div></div>
                <button type="button" @click="navigate('schedule')"><span class="teal">▦</span><div><strong>我的课表</strong><small>查看已排实验场次</small></div><i>→</i></button>
                <button type="button" @click="navigate('classes')"><span class="blue">♙</span><div><strong>项目学生管理</strong><small>查看选课学生名单</small></div><i>→</i></button>
                <button type="button" @click="navigate('adjustments')"><span class="purple">⇄</span><div><strong>教学调整申请</strong><small>调课、场地调整</small></div><i>→</i></button>
              </section>
            </aside>
          </div>
        </template>

        <template v-else-if="activeView === 'schedule'">
          <section class="teacher-filter-bar">
            <label>教学周<select v-model="timetableWeek" @change="fetchTimetable"><option v-for="w in 18" :key="w" :value="w">第 {{ w }} 教学周</option></select></label>
            <label>课程<select v-model="timetableCourseFilter"><option>全部课程</option><option v-for="t in (teacherProfile?.teaching_tasks || [])" :key="t.task_id" :value="t.course_code">{{ t.course_name }}</option></select></label>
            <div class="teacher-week-nav"><button @click="timetableWeek = Math.max(1, timetableWeek - 1); fetchTimetable()">‹</button><strong>{{ weekDates }}</strong><button @click="timetableWeek = Math.min(18, timetableWeek + 1); fetchTimetable()">›</button></div>
          </section>
          <section class="system-panel system-schedule-wrap">
            <div class="system-schedule">
              <div class="system-time-corner">节次</div>
              <div v-for="(day, index) in weekDayHeaders" :key="day" class="system-day-head" :style="{ gridColumn: index + 2 }"><strong>{{ day.split(' ')[0] }}</strong><span>{{ day.split(' ')[1] }}</span></div>
              <div v-for="slot in 12" :key="slot" class="system-period" :class="{ boundary: slot === 4 || slot === 8 }" :style="{ gridRow: slot + 1 }">第 {{ slot }} 节</div>
              <div v-for="day in 7" :key="day" class="system-day-column" :style="{ gridColumn: day + 1, gridRow: '2 / 14' }"></div>
              <article v-for="event in scheduleEvents" :key="event.title" class="system-schedule-event" :class="event.tone" :style="{ gridColumn: event.day + 1, gridRow: `${event.start + 1} / span 4` }">
                <span>{{ event.info }}</span><strong>{{ event.title }}</strong><small>⌖ {{ event.room }}</small>
              </article>
            </div>
          </section>
          <!-- 导出容器：全部周堆叠 -->
          <div ref="exportTeacherRef" class="export-schedule-container">
            <h2>{{ teacherName }} 教师课表</h2>
            <p class="export-info">{{ (teacherProfile as any)?.department || '' }} · {{ (teacherProfile?.term as any)?.academic_year || '' }} {{ ['','第一学期','第二学期'][(teacherProfile?.term as any)?.semester_no || 2] }}</p>
            <div v-for="[weekStr, sessions] in weekGroups(allWeeksSessions)" :key="'tw'+weekStr" class="export-week">
              <h3>第 {{ weekStr }} 周</h3>
              <div class="export-grid" style="min-height:360px">
                <div class="export-corner">节次</div>
                <div v-for="(d,i) in ['周日','周一','周二','周三','周四','周五','周六']" :key="d" :style="{gridColumn:i+2,gridRow:1}" class="export-day-head">{{ d }}</div>
                <template v-for="slot in 12" :key="slot">
                  <div :style="{gridRow:slot+1}" :class="slot===4||slot===8?'export-slot export-slot-boundary':'export-slot'">{{ slot }}</div>
                  <div v-for="day in 7" :key="day" :style="{gridColumn:day+1,gridRow:slot+1}" class="export-cell"></div>
                </template>
                <template v-for="s in sessions" :key="'ts'+s.id">
                  <div :style="{gridColumn:s.day_of_week+1,gridRow:(s.start_slot+1)+' / span '+(s.end_slot-s.start_slot+1)}" class="export-event">
                    <div class="export-event-sub">{{ s.course_name }} · {{ s.selected_count }}/{{ s.capacity }}人</div>
                    <div class="export-event-title">{{ s.project_name }}</div>
                    <div class="export-event-sub">⌖ {{ s.lab_name }}</div>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </template>

        <template v-else-if="activeView === 'classes'">
          <section class="class-project-picker">
            <label>选择实验项目
              <select v-model="selectedProjectId">
                <option v-for="p in projectList" :key="p.project_id" :value="p.project_id">{{ p.project_name }}</option>
              </select>
            </label>
            <label>选择授课时间
              <select v-model="selectedSessionId" :disabled="!selectedSessions.length">
                <option v-for="s in selectedSessions" :key="s.session_id" :value="s.session_id">
                  第{{ s.week_no }}周 {{ ['','周日','周一','周二','周三','周四','周五','周六'][s.day_of_week] }} 第{{ s.start_slot }}–{{ s.end_slot }}节 · {{ s.lab_name }} · {{ s.selected_count }}/{{ s.capacity }}人
                </option>
              </select>
            </label>
            <div class="selected-project-info" v-if="selectedSession">
              <span class="selected-project-mark" :style="{ background: '#237f82' }">{{ (projectList.find(p=>p.project_id===selectedProjectId)?.project_name || '').slice(0,1) }}</span>
              <div><small>第{{ selectedSession.week_no }}周 · {{ ['','周日','周一','周二','周三','周四','周五','周六'][selectedSession.day_of_week] }}</small><strong>{{ projectList.find(p=>p.project_id===selectedProjectId)?.project_name || '' }}</strong><p>{{ selectedSession.lab_name }}　|　{{ selectedSession.selected_count }}/{{ selectedSession.capacity }} 人已选</p></div>
            </div>
            <div class="class-numbers" v-if="selectedSession"><span><strong>{{ projectStudents.length }}</strong>已选</span><span><strong>{{ selectedSession.capacity }}</strong>容量</span><span><strong>{{ selectedSession.capacity - projectStudents.length }}</strong>余量</span></div>
          </section>

          <section class="teacher-panel student-list-panel">
            <div class="student-list-tools">
              <div><h3>已选学生基本信息</h3></div>
              <div><label class="teacher-search">⌕<input v-model="studentKeyword" placeholder="搜索姓名、学号或专业" /></label><button type="button" @click="exportStudentList" :disabled="!projectStudents.length">↓ 导出名单</button></div>
            </div>
            <div class="teacher-table">
              <div class="teacher-table-row teacher-table-head"><span>姓名</span><span>学号</span><span>专业</span><span>年级</span><span>联系方式</span></div>
              <div v-for="student in filteredStudents" :key="student.student_no" class="teacher-table-row">
                <span class="student-name-cell"><i>{{ student.name.slice(0, 1) }}</i><b>{{ student.name }}</b></span><span>{{ student.student_no }}</span><span><b>{{ student.major_name }}</b></span><span>{{ student.enrollment_year }} 级</span><span>{{ (student as any).phone || '—' }}</span>
              </div>
              <div v-if="!filteredStudents.length" class="empty-students">当前筛选条件下没有学生记录</div>
            </div>
          </section>
          <div ref="exportListRef" style="position:absolute;left:-9999px;top:0;width:700px;padding:20px;background:#fff;font-size:11px;color:#333">
            <h2 style="margin:0 0 8px">{{ projectList.find(p=>p.project_id===selectedProjectId)?.project_name || '' }} 学生名单</h2>
            <p style="margin:0 0 4px;color:#657885">
              授课教师：{{ teacherName }} ·
              第{{ selectedSession?.week_no }}周 {{ ['','周日','周一','周二','周三','周四','周五','周六'][selectedSession?.day_of_week||1] }} 第{{ selectedSession?.start_slot }}–{{ selectedSession?.end_slot }}节 ·
              {{ selectedSession?.lab_name }}
            </p>
            <p style="margin:0 0 12px;color:#657885">共 {{ projectStudents.length }} 名学生</p>
            <table style="width:100%;border-collapse:collapse">
              <thead><tr style="background:#f5f7f9"><th style="padding:6px 8px;border:1px solid #ddd;text-align:left">姓名</th><th style="padding:6px 8px;border:1px solid #ddd;text-align:left">学号</th><th style="padding:6px 8px;border:1px solid #ddd;text-align:left">专业</th><th style="padding:6px 8px;border:1px solid #ddd;text-align:left">年级</th><th style="padding:6px 8px;border:1px solid #ddd;text-align:left">联系方式</th></tr></thead>
              <tbody><tr v-for="s in projectStudents" :key="s.student_no"><td style="padding:5px 8px;border:1px solid #eee">{{ s.name }}</td><td style="padding:5px 8px;border:1px solid #eee">{{ s.student_no }}</td><td style="padding:5px 8px;border:1px solid #eee">{{ s.major_name }}</td><td style="padding:5px 8px;border:1px solid #eee">{{ s.enrollment_year }} 级</td><td style="padding:5px 8px;border:1px solid #eee">{{ (s as any).phone || '' }}</td></tr></tbody>
            </table>
          </div>
        </template>

        <template v-else-if="activeView === 'adjustments'">
          <section class="adjustment-types">
            <button type="button" @click="openAdjustment('调课申请')"><span class="teal">⇄</span><div><strong>调课申请</strong><small>调整教学周、日期或时段</small></div><i>→</i></button>
            <button type="button" @click="openAdjustment('场地调整申请')"><span class="blue">⌖</span><div><strong>场地调整申请</strong><small>调整实验场次使用的实验室</small></div><i>→</i></button>
            <button type="button" @click="openAdjustment('停课申请')"><span class="purple">Ⅱ</span><div><strong>停课申请</strong><small>因特殊情况暂停实验教学</small></div><i>→</i></button>
          </section>
          <section class="teacher-panel adjustment-records" style="margin-bottom:18px">
            <div class="teacher-panel-title"><div><h3>学生补做审批</h3><p>审批学生提交的补做实验申请，通过后转管理员二审</p></div></div>
            <div v-if="adjustmentRecords.length" class="adjustment-list">
              <article v-for="record in adjustmentRecords" :key="record.id" class="adjustment-card">
                <div class="adjustment-card-header">
                  <span class="adjustment-card-type">补做申请</span>
                  <span class="adjustment-card-no">{{ record.id }}</span>
                  <span class="adjustment-card-status pending">待审批</span>
                </div>
                <div class="adjustment-card-body">
                  <div><span>学生</span><strong>{{ record.student_name }}</strong></div>
                  <div><span>项目</span><strong>{{ record.project }}</strong></div>
                  <div><span>目标安排</span><strong>{{ record.target }}</strong></div>
                  <div><span>原因</span><strong>{{ record.reason_text }}</strong></div>
                </div>
                <div class="adjustment-card-actions">
                  <button @click="approveAdjustment(record.rawId)" class="btn-approve">✓ 通过</button>
                  <button @click="rejectAdjustment(record.rawId)" class="btn-reject">✕ 驳回</button>
                </div>
              </article>
            </div>
            <div v-else style="padding:20px;text-align:center;color:#919da7;font-size:9px">暂无待审批的补做申请</div>
          </section>
          <section class="teacher-panel adjustment-records">
            <div class="teacher-panel-title"><div><h3>教师申请记录</h3><p>查看申请进度与实验中心审核意见</p></div><select><option>全部状态</option><option>审核中</option><option>已通过</option><option>已驳回</option></select></div>
            <div class="adjustment-table">
              <div class="adjustment-row adjustment-head"><span>申请编号 / 类型</span><span>实验项目</span><span>原安排</span><span>目标安排</span><span>申请日期</span><span>状态</span></div>
              <div v-for="record in teacherOwnAdjustments" :key="record.id" class="adjustment-row">
                <span><b>{{ record.type }}</b><small>{{ record.id }}</small></span><span>{{ record.project }}</span><span>{{ record.original }}</span><span>{{ record.target }}</span><span>{{ record.date }}</span><span><i class="teacher-status" :class="{ pending: record.status === '审核中', normal: record.status === '已通过', rejected: record.status === '已驳回', draft: record.status === '草稿演示' }">{{ record.status }}</i></span>
              </div>
            </div>
          </section>
          <section class="adjustment-notice"><span>!</span><p>教学调整会影响学生课表及实验室资源安排。当前原型申请不会提交，正式系统需经实验中心审核后生效。</p></section>
        </template>

        <template v-else>
          <div class="resource-layout">
            <form class="teacher-panel resource-form" @submit.prevent="submitResourceReport">
              <div class="teacher-panel-title"><div><h3>上报资源异常</h3><p>请准确描述异常位置和具体情况</p></div><span class="required-note">* 为必填项</span></div>
              <div class="resource-form-grid">
                <label><span>异常地点 *</span><select v-model="reportLocation"><option value="" disabled>请选择实验室或场地</option><option>实验楼 A203</option><option>实验楼 B105</option><option>近代物理实验室 2</option><option>近代物理实验室 3</option></select></label>
                <label><span>异常类型 *</span><select v-model="reportType"><option value="" disabled>请选择异常类型</option><option>仪器故障</option><option>耗材不足</option><option>环境异常</option><option>安全隐患</option><option>其他问题</option></select></label>
                <label><span>紧急程度</span><div class="level-options"><button v-for="level in ['一般','紧急','严重']" :key="level" type="button" :class="{ active: reportLevel === level }" @click="reportLevel = level">{{ level }}</button></div></label>
                <label class="resource-description"><span>异常情况说明 *</span><textarea v-model="reportDescription" rows="5" placeholder="请说明设备编号、异常现象、影响范围等信息（仅用于演示）"></textarea></label>
                <label class="resource-upload"><span>现场图片</span><button type="button" @click="showToast('图片上传将在文件服务接入后启用')"><i>＋</i><strong>点击上传现场照片</strong><small>支持 JPG、PNG，单张不超过 10 MB</small></button></label>
              </div>
              <div class="resource-form-bottom"><p><i>i</i>当前为演示原型，上报不会发送给实验中心。</p><button type="button" @click="reportDescription = ''; reportLocation = ''; reportType = ''">重置</button><button type="submit">加入演示记录</button></div>
            </form>

            <aside class="resource-side">
              <section class="teacher-panel resource-guide">
                <h3>上报指引</h3>
                <ol><li><span>1</span><div><strong>立即停止使用</strong><small>发现仪器或安全异常时先停止操作</small></div></li><li><span>2</span><div><strong>保留现场信息</strong><small>记录设备编号并拍摄异常状态</small></div></li><li><span>3</span><div><strong>准确描述影响</strong><small>说明是否影响当前或后续教学</small></div></li></ol>
                <div><span>紧急联系电话</span><strong>010-****-5678</strong><small>示例号码 · 安全隐患请优先电话联系</small></div>
              </section>
            </aside>
          </div>
          <section class="teacher-panel resource-records">
            <div class="teacher-panel-title"><div><h3>我的异常上报记录</h3><p>跟踪维修与处置进度</p></div><span>共 {{ resourceRecords.length }} 条</span></div>
            <div class="resource-table">
              <div class="resource-row resource-head"><span>编号 / 类型</span><span>异常地点</span><span>情况说明</span><span>级别</span><span>上报日期</span><span>处理状态</span></div>
              <div v-for="record in resourceRecords" :key="record.id" class="resource-row">
                <span><b>{{ record.type }}</b><small>{{ record.id }}</small></span><span>{{ record.location }}</span><span>{{ record.detail }}</span><span><i class="resource-level" :class="{ urgent: record.level === '紧急', severe: record.level === '严重' }">{{ record.level }}</i></span><span>{{ record.date }}</span><span><i class="teacher-status" :class="{ pending: record.status === '处理中', normal: record.status === '已解决', draft: record.status === '草稿演示' }">{{ record.status }}</i></span>
              </div>
            </div>
          </section>
        </template>
      </main>
    </div>

    <div v-if="adjustmentDialog" class="teacher-dialog-backdrop" @click.self="adjustmentDialog = null">
      <form class="teacher-dialog" @submit.prevent="submitAdjustment">
        <div class="teacher-dialog-title"><div><span>⇄</span><div><h3>{{ adjustmentDialog }}</h3><p>创建教学调整演示申请</p></div></div><button type="button" @click="adjustmentDialog = null">×</button></div>
        <label>实验项目<select v-model="adjustmentProject"><option value="" disabled>请选择实验项目</option><option v-for="task in displayedTasks" :key="task.id">{{ task.project }}</option></select></label>
        <label>目标安排<input v-model="adjustmentTarget" placeholder="例如：第 8 周 周五 5–8 节" /></label>
        <label>申请原因<textarea v-model="adjustmentReason" rows="4" placeholder="请说明调整原因及对学生、场地的影响"></textarea></label>
        <div class="teacher-dialog-warning">当前为演示原型，确认后只加入页面记录，不会真实提交。</div>
        <div class="teacher-dialog-actions"><button type="button" @click="adjustmentDialog = null">取消</button><button type="submit">加入演示列表</button></div>
      </form>
    </div>

    <Transition name="toast"><div v-if="toast" class="teacher-toast" role="status"><span>✓</span>{{ toast }}</div></Transition>
  </div>
</template>
