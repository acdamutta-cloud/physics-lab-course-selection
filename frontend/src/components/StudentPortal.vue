<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import type { UserProfile } from '../api/auth'
import { api } from '../api/client'
import { marked } from 'marked'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'

marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(text: string): string {
  if (!text) return ''
  return marked.parse(text) as string
}

type View = 'home' | 'schedule' | 'selection' | 'applications' | 'ai'
type ApplicationType = '调课申请' | '换组申请' | '补做申请'
type AdjustmentRequestType = 'RESCHEDULE' | 'PROJECT_CHANGE' | 'MAKEUP'
type AdjustmentSession = {
  session_id: string; project_name: string; course_name: string; week_no: number
  day_name: string; start_slot: number; end_slot: number; session_date: string
  teacher_name: string; laboratory_name: string; remaining: number
}
type AdjustmentSource = { record_id: string; status: string; session: AdjustmentSession; available_for: AdjustmentRequestType[] }
type AdjustmentCandidate = {
  decision: 'ALLOW' | 'BLOCK' | 'REVIEW'; approval_route: 'AUTO' | 'ADMIN' | 'TEACHER'
  target: AdjustmentSession | null; violations: Array<{ code: string; message: string }>
}
type AdjustmentApplication = {
  id: string; request_no: string; request_type: AdjustmentRequestType; reason: string; status: string
  approval_route: string; reservation_status: string; payload: Record<string, any>; validation_result: Record<string, any>
  submitted_at?: string; created_at: string
}
type AiCard = { type: string; title: string; summary: string; data: Record<string, any> }
type AiIntent =
  | 'GENERAL_CHAT'
  | 'OUT_OF_SCOPE'
  | 'BUSINESS_RULE_QUERY'
  | 'CHECK_ELIGIBILITY'
  | 'EXPLAIN_CONFLICT'
  | 'QUERY_TRAINING_PLAN'
  | 'RECOMMEND_SELECTION'
  | 'UNKNOWN'
type AiMessageStatus = 'pending' | 'streaming' | 'completed' | 'error' | 'stopped'
type AiMessage = {
  id: string
  role: 'user' | 'assistant'
  text: string
  cards?: AiCard[]
  status: AiMessageStatus
  traceId?: string
  intent?: AiIntent
  errorMessage?: string
}
type SelectionApiResponse = {
  result: string
  message?: string
  eligibility?: {
    decision?: string
    violations?: Array<{ code?: string; message?: string }>
    warnings?: Array<{ code?: string; message?: string }>
  } | null
  details?: Record<string, unknown>
}

const props = defineProps<{ user: UserProfile | null }>()
const emit = defineEmits<{ logout: [] }>()
const activeView = ref<View>('home')
const sidebarOpen = ref(false)
const toast = ref('')
const selectingProjectId = ref<string | null>(null)
const selectionFeedback = ref<Record<string, string>>({})
const courseFilter = ref('全部课程')
const projectKeyword = ref('')
const projectType = ref('全部')
const selectedProjectIds = ref<(string|number)[]>([])
const applicationDialog = ref<ApplicationType | null>(null)
const applicationReason = ref('')
const adjustmentStep = ref(1)
const adjustmentLoading = ref(false)
const adjustmentMode = ref<'manual' | 'ai'>('manual')
const adjustmentSources = ref<AdjustmentSource[]>([])
const adjustmentCandidates = ref<AdjustmentCandidate[]>([])
const adjustmentSourceId = ref('')
const adjustmentTargetId = ref('')
const adjustmentPreview = ref<AdjustmentCandidate | null>(null)
const adjustmentPreference = ref('')
const adjustmentAiText = ref('')
const adjustmentAiCards = ref<any[]>([])
const adjustmentApplications = ref<AdjustmentApplication[]>([])
const aiInput = ref('')
const aiThread = ref<HTMLDivElement | null>(null)
const isStreaming = ref(false)
const streamPhase = ref('')
const activeController = shallowRef<AbortController | null>(null)
const showJumpToLatest = ref(false)
const studentNotifs = ref<Array<{ request_no: string; msg: string; time: string }>>([])
const showStudentNotifs = ref(false)
const studentUnread = computed(() => studentNotifs.value.length)

async function fetchStudentNotifs() {
  try {
    const prev = studentNotifs.value.length
    studentNotifs.value = await api.get<any[]>('/students/me/notifications')
    // 有新通知（审批通过/驳回）自动刷新课表
    if (studentNotifs.value.length > prev) await fetchDashboard()
  } catch { studentNotifs.value = [] }
}
async function readStudentNotif(idx: number, val: string) {
  studentNotifs.value.splice(idx, 1)
  await api.post('/students/me/notifications/read', { value: val }).catch(() => {})
  await fetchDashboard() // 审批通过后刷新课表
}

const navItems: Array<{ id: View; label: string; icon: string }> = [
  { id: 'home', label: '首页', icon: '⌂' },
  { id: 'schedule', label: '实验课表', icon: '▦' },
  { id: 'selection', label: '在线选课', icon: '✦' },
  { id: 'applications', label: '个人申请', icon: '◇' },
  { id: 'ai', label: 'AI 智能咨询', icon: '✺' },
]

const studentName = computed(() => props.user?.name || '同学')
const studentNo = computed(() => props.user?.student_no || '****')
const studentMajor = computed(() => props.user?.major_name || '未知专业')
const studentGrade = computed(() => {
  if (props.user?.enrollment_year) return `${props.user.enrollment_year} 级`
  return '未知年级'
})
const greetingText = computed(() => `下午好，${studentName.value}`)
const userInitial = computed(() => studentName.value.slice(0, 1))

const viewMeta = computed(() => ({
  home: { title: greetingText.value, subtitle: `今天是第 ${displayTermWeek.value} 教学周，查看你的实验学习进度` },
  schedule: { title: '实验课表查询', subtitle: '查看本学期已选实验的时间与地点安排' },
  selection: { title: '在线选课', subtitle: '根据培养方案选择必做与选做实验项目' },
  applications: { title: '个人申请', subtitle: '提交并跟踪调课、换组与补做申请' },
  ai: { title: 'AI 智能咨询', subtitle: '面向实验选课与教学安排的智能问答助手' },
}))

const semesterCourses = [
  { name: '大学物理实验（上）', code: 'PHYS-LAB-101', teacher: '李老师', required: 2, optionalRequired: 1, color: '#137b80' },
  { name: '近代物理实验', code: 'PHYS-LAB-203', teacher: '周老师', required: 2, optionalRequired: 1, color: '#4769a8' },
]

const projects = [
  { id: 1, course: '大学物理实验（上）', name: '用单摆测量重力加速度', type: '必做', week: '第 4 周', time: '周二 第 5–8 节', room: '实验楼 A203', teacher: '李老师', capacity: 24, remaining: 3 },
  { id: 2, course: '大学物理实验（上）', name: '示波器的原理与使用', type: '必做', week: '第 7 周', time: '周四 第 5–8 节', room: '实验楼 B105', teacher: '王老师', capacity: 20, remaining: 6 },
  { id: 3, course: '大学物理实验（上）', name: '霍尔效应及磁场测量', type: '选做', week: '第 9 周', time: '周一 第 9–12 节', room: '实验楼 A306', teacher: '陈老师', capacity: 18, remaining: 2 },
  { id: 4, course: '近代物理实验', name: '光电效应与普朗克常量测定', type: '必做', week: '第 6 周', time: '周三 第 5–8 节', room: '近代物理实验室 2', teacher: '周老师', capacity: 16, remaining: 0 },
  { id: 5, course: '近代物理实验', name: '弗兰克—赫兹实验', type: '必做', week: '第 10 周', time: '周五 第 1–4 节', room: '近代物理实验室 1', teacher: '孙老师', capacity: 16, remaining: 5 },
  { id: 6, course: '近代物理实验', name: '密立根油滴实验', type: '选做', week: '第 12 周', time: '周二 第 9–12 节', room: '近代物理实验室 3', teacher: '周老师', capacity: 14, remaining: 1 },
]

function fmtSession(s: any) {
  if (!s) return '—'
  const dayNames = ['','周日','周一','周二','周三','周四','周五','周六']
  const w = s.week_no ? `第${s.week_no}周` : ''
  const d = s.day_of_week ? dayNames[s.day_of_week] : ''
  const t = s.start_slot ? `第${s.start_slot}–${s.end_slot || s.start_slot}节` : ''
  return [s.project_name, w, d, t].filter(Boolean).join(' · ')
}

const applications = computed(() => adjustmentApplications.value.map(item => {
  const source = item.payload?.source?.session
  const target = item.payload?.target
  const statusMap: Record<string, string> = { PENDING_REVIEW: '审核中', EXECUTED: '已执行', REJECTED: '已驳回', CANCELLED: '已取消' }
  const typeMap: Record<AdjustmentRequestType, string> = { RESCHEDULE: '调课申请', PROJECT_CHANGE: '换组申请', MAKEUP: '补做申请' }
  return {
    id: item.request_no,
    rawId: item.id,
    type: typeMap[item.request_type],
    project: source || target ? `${fmtSession(source)} → ${fmtSession(target)}` : '实验调整',
    date: (item.submitted_at || item.created_at || '').slice(0, 10),
    status: statusMap[item.status] || item.status,
    note: (item as any).reject_reason || (item.status === 'CANCELLED' ? '已取消' : ''),
    reviewer: (item as any).reviewer || '',
  }
}))

const messages = ref<AiMessage[]>([
  {
    id: 'welcome',
    role: 'assistant',
    text: '你好！我是物理实验 AI 助手。我可以根据你的培养方案和课表，检查选课资格、解释冲突并推荐实验场次。',
    status: 'completed',
  },
])

const quickQuestions = [
  '我的培养方案有哪些实验要求？',
  '我还需要选择哪些项目？',
  '为什么这个场次不能选？',
  '帮我推荐三个选课方案。',
]

function createMessage(role: 'user' | 'assistant', text: string, status: AiMessageStatus): AiMessage {
  return { id: `${role}-${Date.now()}-${Math.random().toString(16).slice(2)}`, role, text, status }
}

function splitGraphemes(text: string): string[] {
  const Segmenter = (Intl as any).Segmenter
  if (Segmenter) {
    return Array.from(new Segmenter('zh-CN', { granularity: 'grapheme' }).segment(text), (item: any) => item.segment)
  }
  return Array.from(text)
}

function delay(ms: number): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, ms))
}

function isNearThreadBottom(): boolean {
  const element = aiThread.value
  return !element || element.scrollHeight - element.scrollTop - element.clientHeight < 80
}

async function scrollToLatest(force = false) {
  await nextTick()
  const element = aiThread.value
  if (!element || (!force && showJumpToLatest.value)) return
  element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
  showJumpToLatest.value = false
}

function handleThreadScroll() {
  showJumpToLatest.value = !isNearThreadBottom()
}

async function appendCharacters(message: AiMessage, text: string, replace = false) {
  if (replace) message.text = ''
  const characters = splitGraphemes(text)
  for (let index = 0; index < characters.length; index += 1) {
    if (activeController.value?.signal.aborted) return
    message.text += characters[index]
    if (!showJumpToLatest.value) await scrollToLatest()
    const remaining = characters.length - index
    const punctuation = /[，。！？；：,.!?]/.test(characters[index])
    await delay(punctuation ? 40 : remaining > 100 ? 8 : 24)
  }
}

function aiCardLines(card: AiCard): string[] {
  if (card.type === 'RECOMMENDATION') {
    const dayNames = ['日', '一', '二', '三', '四', '五', '六']
    const formatSession = (item: any) =>
      `${item.project_name} · 第${item.week_no}周 周${dayNames[item.day_of_week - 1]} 第${item.start_slot}—${item.end_slot}节 · ${item.laboratory_name}`
    const lines: string[] = []
    if (card.data.coverage_status === 'PARTIAL') lines.push('当前为部分方案，尚有要求未覆盖')
    for (const item of (card.data.retained_selections || [])) {
      lines.push(`已选固定：${formatSession(item)}`)
    }
    for (const item of (card.data.sessions || [])) {
      lines.push(`建议新增：${formatSession(item)}`)
    }
    for (const item of (card.data.course_requirements || [])) {
      lines.push(`${item.course_name}：必做 ${item.required_satisfied}/${item.required_total}，选做 ${item.optional_satisfied}/${item.optional_min}`)
    }
    for (const item of (card.data.excluded_courses || [])) {
      lines.push(`暂不推荐 ${item.course_name}：${(item.reasons || []).join('；')}`)
    }
    for (const item of (card.data.unmet_requirements || [])) {
      lines.push(`尚缺 ${item.project_name || item.course_name}：${item.reason}`)
    }
    return lines
  }
  if (card.type === 'ELIGIBILITY' || card.type === 'CONFLICT') {
    const violations = card.data.violations || []
    const warnings = card.data.warnings || []
    return [...violations, ...warnings].map((item: any) => item.message)
  }
  if (Array.isArray(card.data.projects)) {
    return card.data.projects.slice(0, 8).map((item: any) =>
      `${item.project_name} · ${item.category_label || item.requirement_type || ''}`
    )
  }
  if (Array.isArray(card.data.courses)) {
    return card.data.courses.map((item: any) =>
      `${item.course_name} · 必做 ${item.required_project_count} 项 + 选做至少 ${item.optional_project_min_count} 项`
    )
  }
  return []
}

// ── 选课视图：从 API 数据展平项目，按周筛选场次 ──
const displayProjects = computed(() => {
  const dayNames = ['', '周日', '周一', '周二', '周三', '周四', '周五', '周六']
  const result: Array<{
    id: string; course_name: string; course_code: string; name: string; type: string
    week: string; time: string; room: string; teacher: string; capacity: number; remaining: number
  }> = []
  for (const course of displayCourses.value) {
    for (const p of course.projects) {
      for (const s of (p.available_sessions || [])) {
        if (s.week_no !== bitmapWeek.value) continue
        result.push({
          id: s.id,
          course_name: course.course_name,
          course_code: course.course_code,
          name: p.project_name,
          type: p.requirement_type === 'REQUIRED' ? '必做' : '选做',
          week: `第 ${bitmapWeek.value} 周`,
          time: `${dayNames[s.day_of_week] || ''} 第 ${s.start_slot}–${s.end_slot} 节`,
          room: s.lab_name || '',
          teacher: (s as any).teacher_name || '',
          capacity: s.capacity || 0,
          remaining: Math.max(0, (s.capacity || 0) - (s.selected_count || 0)),
        })
      }
    }
  }
  return result
})

const filteredProjects = computed(() => displayProjects.value.filter((project) => {
  const matchesCourse = courseFilter.value === '全部课程' || project.course_name === courseFilter.value
  const matchesType = projectType.value === '全部' || project.type === projectType.value
  const keyword = projectKeyword.value.trim().toLowerCase()
  const matchesKeyword = !keyword || `${project.name}${project.room}`.toLowerCase().includes(keyword)
  return matchesCourse && matchesType && matchesKeyword
}))

// 已选项目概览：从 dashboard.selected_sessions 聚合，不受 bitmapWeek 限制
const selectedProjects = computed(() => {
  const sessions = dashboard.value?.selected_sessions || []
  // 按 project_id 去重，每个项目只取一条
  const seen = new Set<string>()
  const result: Array<{ id: string; project_name: string; course_name: string; type: string; week: string; time: string; room: string }> = []
  for (const s of sessions) {
    const pid = (s as any).project_id || ''
    if (seen.has(pid)) continue
    seen.add(pid)
    const dayNames = ['','周日','周一','周二','周三','周四','周五','周六']
    result.push({
      id: s.session_id,
      project_name: s.project_name,
      course_name: displayCourses.value.find((c: any) =>
        c.projects.some((p: any) => (p.available_sessions || []).some((as: any) => as.id === s.session_id))
      )?.course_name || '',
      type: '已选',
      week: `第 ${s.week_no} 周`,
      time: `${dayNames[s.day_of_week]} 第 ${s.start_slot}–${s.end_slot} 节`,
      room: s.lab_name,
    })
  }
  return result
})
const courseSelectionDetails = computed(() => semesterCourses.map((course) => {
  const courseProjects = projects.filter((project) => project.course === course.name)
  const requiredProjects = courseProjects.filter((project) => project.type === '必做')
  const optionalProjects = courseProjects.filter((project) => project.type === '选做')
  const selectedRequired = requiredProjects.filter((project) => selectedProjectIds.value.includes(project.id)).length
  const selectedOptional = optionalProjects.filter((project) => selectedProjectIds.value.includes(project.id)).length
  const requiredSelectionCount = course.required + course.optionalRequired
  const satisfiedSelectionCount = Math.min(selectedRequired, course.required) + Math.min(selectedOptional, course.optionalRequired)
  return {
    ...course,
    requiredProjects,
    optionalProjects,
    selectedRequired,
    selectedOptional,
    selectedTotal: selectedRequired + selectedOptional,
    requiredSelectionCount,
    satisfiedSelectionCount,
    completionPercent: Math.round((satisfiedSelectionCount / requiredSelectionCount) * 100),
  }
}))
const totalRequiredProjects = computed(() => semesterCourses.reduce((total, course) => total + course.required, 0))
const totalOptionalProjects = computed(() => courseSelectionDetails.value.reduce((total, course) => total + course.optionalProjects.length, 0))
const totalOptionalRequired = computed(() => semesterCourses.reduce((total, course) => total + course.optionalRequired, 0))
// ── 课表位图 ──
const bitmapData = ref<{ weeks: number; days: number; slots: number; data: string | null } | null>(null)
const bitmapWeek = ref(1)

function isSlotBusy(day: number, slot: number): boolean {
  if (!bitmapData.value?.data) return false
  const bytes = Uint8Array.from(atob(bitmapData.value.data), c => c.charCodeAt(0))
  const idx = (bitmapWeek.value - 1) * bitmapData.value.days * bitmapData.value.slots + day * bitmapData.value.slots + slot
  return !!(bytes[idx >> 3] & (1 << (7 - (idx & 7))))
}

async function fetchBitmap() {
  try {
    bitmapData.value = await api.get('/students/me/busy-bitmap')
    if (bitmapData.value?.weeks) bitmapWeek.value = Math.min(bitmapWeek.value, bitmapData.value.weeks)
  } catch { /* ignore */ }
}

// ── Dashboard API ──
const dashboard = ref<{
  profile: { name: string; student_no: string; major_name: string; enrollment_year: number; campus_name: string }
  term: { academic_year: string; semester_no: number; start_date: string; current_week: number; total_weeks: number }
  courses: Array<{
    course_name: string; course_code: string; required_count: number; optional_min: number; completion_status: string
    prerequisites_passed: string[]; prerequisites_failed: string[]
    projects: Array<{ project_id: string; project_name: string; requirement_type: string; available_sessions: Array<{ id: string; week_no: number; day_of_week: number; start_slot: number; end_slot: number; lab_name: string; capacity: number; selected_count: number }> }>
  }>
  selection: { selected_count: number; total_required: number; total_optional_pool: number; total_optional_min: number }
  prerequisites: { passed: string[]; failed: string[] }
  next_lab: { week_no: number; day_of_week: number; start_slot: number; end_slot: number; project_name: string; lab_name: string } | null
  selected_sessions: Array<{ session_id: string; week_no: number; day_of_week: number; start_slot: number; end_slot: number; project_name: string; lab_name: string; teacher_name?: string }>
} | null>(null)

async function fetchDashboard() {
  try {
    dashboard.value = await api.get('/students/me/dashboard')
    // 同步已选场次到本地状态
    selectedProjectIds.value = (dashboard.value?.selected_sessions || []).map((s: any) => s.session_id)
  } catch { /* API 不可用时保持 null */ }
}

// 统一数据源：API 优先，mock 兜底
const displayCourses = computed(() => {
  if (dashboard.value?.courses?.length) return dashboard.value.courses
  // fallback: 把 courseSelectionDetails 转为 API 同构
  return courseSelectionDetails.value.map(c => ({
    course_name: c.name,
    course_code: c.code,
    required_count: c.required,
    optional_min: c.optionalRequired,
    completion_status: 'IN_PROGRESS' as string,
    prerequisites_passed: [] as string[],
    prerequisites_failed: [] as string[],
    projects: [
      ...c.requiredProjects.map(p => ({ project_id: String(p.id), project_name: p.name, requirement_type: 'REQUIRED', available_sessions: [] })),
      ...c.optionalProjects.map(p => ({ project_id: String(p.id), project_name: p.name, requirement_type: 'OPTIONAL', available_sessions: [] })),
    ],
  }))
})

const displaySelection = computed(() => {
  if (dashboard.value?.selection) return dashboard.value.selection
  return {
    selected_count: selectedProjectIds.value.length,
    total_required: totalRequiredProjects.value,
    total_optional_pool: totalOptionalProjects.value,
    total_optional_min: totalOptionalRequired.value,
  }
})

// 每门课程的选课进度（必做/选做独立进度条）
const courseSelectionProgress = computed(() => {
  return displayCourses.value.map(course => {
    let reqTotal = 0, reqSelected = 0, optTotal = 0, optSelected = 0
    const optMin = course.optional_min || 0
    for (const p of course.projects) {
      const isReq = p.requirement_type === 'REQUIRED'
      const hasSelected = (p.available_sessions || []).some((s: any) => selectedProjectIds.value.includes(s.id))
      if (isReq) {
        reqTotal++
        if (hasSelected) reqSelected++
      } else {
        optTotal++
        if (hasSelected) optSelected++
      }
    }
    return { course_name: course.course_name, course_code: course.course_code, reqTotal, reqSelected, optTotal, optSelected, optMin }
  })
})

const displayNextLab = computed(() => {
  if (dashboard.value?.next_lab) return { ...dashboard.value.next_lab, teacher_name: (dashboard.value.next_lab as any).teacher_name || '' }
  return { week_no: 6, day_of_week: 3, start_slot: 5, end_slot: 8, project_name: '光电效应与普朗克常量测定', lab_name: '近代物理实验室 2', teacher_name: '周老师' }
})

const displayTermWeek = computed(() => dashboard.value?.term?.current_week ?? 6)
const scheduleWeek = ref(0) // 0 = 全部周
const scheduleCourseFilter = ref('全部课程')
// 计算课表页 day headers 的日期
const scheduleDayHeaders = computed(() => {
  if (scheduleWeek.value === 0) return ['周日','周一','周二','周三','周四','周五','周六']
  const sd = dashboard.value?.term?.start_date
  if (!sd) return ['周日','周一','周二','周三','周四','周五','周六']
  const start = new Date(sd + 'T00:00:00')
  const startDow = start.getDay() // 0=Sun
  const weekSunday = new Date(start.getTime() + ((scheduleWeek.value - 1) * 7 - startDow) * 86400000)
  return ['周日','周一','周二','周三','周四','周五','周六'].map((name, i) => {
    const d = new Date(weekSunday.getTime() + i * 86400000)
    return `${name} ${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`
  })
})

const tonePalette = ['teal', 'blue', 'purple']
const expandedSlot = ref<string | null>(null)
const scheduleSessions = computed(() => {
  const courseTone: Record<string, string> = {}
  displayCourses.value.forEach((c, i) => { courseTone[c.course_code] = tonePalette[i % 3] })
  const raw = (dashboard.value?.selected_sessions || [])
    .filter((s: any) => scheduleWeek.value === 0 || s.week_no === scheduleWeek.value)
    .filter((s: any) => scheduleCourseFilter.value === '全部课程' || (displayCourses.value.find((c: any) => c.course_code === scheduleCourseFilter.value)?.projects || []).some((p: any) => (p.available_sessions || []).some((as: any) => as.id === s.session_id)))
    .map((s: any) => {
      const course = displayCourses.value.find((c: any) =>
        c.projects.some((p: any) => (p.available_sessions || []).some((as: any) => as.id === s.session_id))
      )
      return {
        ...s,
        day: s.day_of_week,
        startSlot: s.start_slot,
        endSlot: s.end_slot,
        tone: courseTone[course?.course_code || ''] || 'teal',
      }
    })
  // 按 day+slot 分组，合并同位置多场次
  const groups = new Map<string, typeof raw>()
  for (const s of raw) {
    const k = `${s.day}:${s.startSlot}:${s.endSlot}`
    if (!groups.has(k)) groups.set(k, [])
    groups.get(k)!.push(s)
  }
  return [...groups.entries()].map(([k, groupedSessions]) => {
    const sessions = [...groupedSessions].sort((a, b) => a.week_no - b.week_no)
    const hasActualConflict = new Set(sessions.map((session) => session.week_no)).size < sessions.length
    return {
      key: k,
      day: sessions[0].day,
      startSlot: sessions[0].startSlot,
      endSlot: sessions[0].endSlot,
      count: sessions.length,
      sessions,
      hasActualConflict,
      tone: sessions.length > 1 ? (hasActualConflict ? 'conflict' : 'stacked') : sessions[0].tone,
      show: sessions.length === 1 || expandedSlot.value === k,
    }
  })
})

// 导出用：全部周全部场次按 day+slot 分组
const exportSessionsGrouped = computed(() => {
  const raw = (dashboard.value?.selected_sessions || []) as any[]
  const groups = new Map<string, typeof raw>()
  for (const s of raw) {
    const k = `${s.day_of_week}:${s.start_slot}:${s.end_slot}`
    if (!groups.has(k)) groups.set(k, [])
    groups.get(k)!.push(s)
  }
  return [...groups.entries()].map(([k, sessions]) => {
    const s0 = sessions[0]
    return { key: k, day: s0.day_of_week, startSlot: s0.start_slot, endSlot: s0.end_slot, count: sessions.length, sessions }
  })
})

const exportBusy = ref(false)
const exportContainer = ref<HTMLDivElement | null>(null)

async function exportSchedule(format: 'png' | 'pdf') {
  if (exportBusy.value) return
  exportBusy.value = true
  try {
    // 先全部展开冲突
    const prevExpanded = expandedSlot.value
    for (const g of scheduleSessions.value) {
      if (g.count > 1) expandedSlot.value = g.key
    }
    await nextTick()
    const el = exportContainer.value
    if (!el) { showToast('导出失败'); return }
    const canvas = await html2canvas(el, { backgroundColor: '#ffffff', scale: 2 })
    if (format === 'png') {
      const link = document.createElement('a')
      link.download = `实验课表_${studentName.value}.png`
      link.href = canvas.toDataURL('image/png')
      link.click()
    } else {
      const pdf = new jsPDF('l', 'mm', 'a4')
      const w = pdf.internal.pageSize.getWidth()
      const h = (canvas.height * w) / canvas.width
      pdf.addImage(canvas.toDataURL('image/png'), 'PNG', 0, 0, w, h)
      pdf.save(`实验课表_${studentName.value}.pdf`)
    }
    showToast(format === 'png' ? '图片已导出' : 'PDF已导出')
    expandedSlot.value = prevExpanded
  } catch { showToast('导出失败，请重试') }
  exportBusy.value = false
}

const notifTimer = ref<ReturnType<typeof setInterval> | null>(null)
onMounted(() => { fetchBitmap(); fetchDashboard(); fetchAdjustmentApplications(); fetchStudentNotifs(); notifTimer.value = setInterval(fetchStudentNotifs, 30000) })
onBeforeUnmount(() => { if (notifTimer.value) clearInterval(notifTimer.value) })

function navigate(view: View) {
  activeView.value = view
  sidebarOpen.value = false
  if (view === 'applications') fetchAdjustmentApplications()
}

function showToast(text: string) {
  toast.value = text
  window.setTimeout(() => {
    if (toast.value === text) toast.value = ''
  }, 2600)
}

function selectionFailureMessage(resp: SelectionApiResponse, fallback: string) {
  const violations = (resp.eligibility?.violations || [])
    .map(item => item.message?.trim())
    .filter((message): message is string => Boolean(message))
  if (violations.length) return [...new Set(violations)].join('；')
  return resp.message?.trim() || fallback
}

function showProjectFailure(id: string | number, message: string) {
  selectionFeedback.value = {
    ...selectionFeedback.value,
    [String(id)]: message,
  }
  showToast(message)
}

function clearProjectFailure(id: string | number) {
  const key = String(id)
  if (!(key in selectionFeedback.value)) return
  const next = { ...selectionFeedback.value }
  delete next[key]
  selectionFeedback.value = next
}

async function toggleProject(id: string | number) {
  const project = displayProjects.value.find((item) => item.id === id)
  if (!project) return
  if (selectingProjectId.value === String(id)) return
  clearProjectFailure(id)
  if (selectedProjectIds.value.includes(id)) {
    // 退选
    try {
      const resp = await api.post<SelectionApiResponse>('/students/me/deselect-session', { session_id: id })
      if (resp.result === 'ok') {
        selectedProjectIds.value = selectedProjectIds.value.filter((item) => item !== id)
        fetchDashboard() // 刷新数据
        showToast(`已退选”${project.name}”`)
      } else {
        showToast(resp.message || '退选失败')
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '退选请求失败'
      showProjectFailure(id, message)
    }
    return
  }
  if (project.remaining === 0) {
    showProjectFailure(id, '该场次名额已满，当前不能选择。')
    return
  }
  // 选课
  selectingProjectId.value = String(id)
  try {
    const resp = await api.post<SelectionApiResponse>('/students/me/select-session', { session_id: id })
    if (resp.result === 'ok') {
      selectedProjectIds.value = [...selectedProjectIds.value, id]
      fetchDashboard() // 刷新数据
      showToast(`已选择”${project.name}”`)
    } else {
      showProjectFailure(id, selectionFailureMessage(resp, '该场次当前不能选择。'))
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : '选课请求失败'
    showProjectFailure(id, message)
  } finally {
    selectingProjectId.value = null
  }
}

const adjustmentRequestType = computed<AdjustmentRequestType>(() => ({
  '调课申请': 'RESCHEDULE',
  '换组申请': 'PROJECT_CHANGE',
  '补做申请': 'MAKEUP',
}[applicationDialog.value || '调课申请'] as AdjustmentRequestType))

function adjustmentSessionText(item: AdjustmentSession) {
  return `${item.project_name} · 第${item.week_no}周${item.day_name} 第${item.start_slot}—${item.end_slot}节 · ${item.laboratory_name}`
}

async function fetchAdjustmentApplications() {
  try {
    adjustmentApplications.value = await api.get<AdjustmentApplication[]>('/students/me/adjustments')
  } catch (error) {
    showToast(error instanceof Error ? error.message : '申请记录加载失败')
  }
}

async function openApplication(type: ApplicationType) {
  applicationDialog.value = type
  applicationReason.value = ''
  adjustmentStep.value = 1
  adjustmentMode.value = 'manual'
  adjustmentSourceId.value = ''
  adjustmentTargetId.value = ''
  adjustmentPreview.value = null
  adjustmentPreference.value = ''
  adjustmentAiText.value = ''
  adjustmentAiCards.value = []
  adjustmentLoading.value = true
  try {
    const typeCode = ({ '调课申请': 'RESCHEDULE', '换组申请': 'PROJECT_CHANGE', '补做申请': 'MAKEUP' } as const)[type]
    const context = await api.get<{ sources: AdjustmentSource[] }>(`/students/me/adjustments/context?request_type=${typeCode}`)
    adjustmentSources.value = context.sources
  } catch (error) {
    adjustmentSources.value = []
    showToast(error instanceof Error ? error.message : '可申请实验加载失败')
  } finally {
    adjustmentLoading.value = false
  }
}

async function loadAdjustmentTargets() {
  if (!adjustmentSourceId.value) return
  adjustmentLoading.value = true
  adjustmentTargetId.value = ''
  adjustmentPreview.value = null
  try {
    const path = `/students/me/adjustments/context?request_type=${adjustmentRequestType.value}&source_record_id=${adjustmentSourceId.value}`
    const context = await api.get<{ candidates: AdjustmentCandidate[] }>(path)
    adjustmentCandidates.value = [...context.candidates].sort((left, right) =>
      Number(left.decision === 'BLOCK') - Number(right.decision === 'BLOCK')
    )
    adjustmentStep.value = 2
  } catch (error) {
    showToast(error instanceof Error ? error.message : '目标场次加载失败')
  } finally {
    adjustmentLoading.value = false
  }
}

async function previewAdjustment() {
  if (!adjustmentTargetId.value) return showToast('请先选择目标场次')
  adjustmentLoading.value = true
  try {
    adjustmentPreview.value = await api.post<AdjustmentCandidate>('/students/me/adjustments/preview', {
      request_type: adjustmentRequestType.value,
      source_record_id: adjustmentSourceId.value,
      target_session_id: adjustmentTargetId.value,
    })
    adjustmentStep.value = 3
  } catch (error) {
    showToast(error instanceof Error ? error.message : '资格预览失败')
  } finally {
    adjustmentLoading.value = false
  }
}

async function recommendAdjustment() {
  if (!adjustmentPreference.value.trim()) return showToast('请描述你的时间偏好')
  adjustmentLoading.value = true
  adjustmentAiText.value = ''
  adjustmentAiCards.value = []
  try {
    await api.streamPost('/students/me/adjustments/recommend/stream', {
      request_type: adjustmentRequestType.value,
      source_record_id: adjustmentSourceId.value,
      message: adjustmentPreference.value,
      max_options: 3,
    }, { onEvent: async ({ event, data }: any) => {
      if (event === 'delta') adjustmentAiText.value += data.text || ''
      if (event === 'final') adjustmentAiCards.value = data.cards || []
      if (event === 'error') throw new Error(data.message || 'AI推荐失败')
    } })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'AI推荐失败'
    adjustmentAiText.value = `推荐生成失败：${message}`
    showToast(message)
  } finally {
    adjustmentLoading.value = false
  }
}

function useAdjustmentRecommendation(card: any) {
  adjustmentTargetId.value = card.data.target.session_id
  adjustmentPreview.value = {
    decision: card.data.can_submit ? (card.data.approval_route === 'AUTO' ? 'ALLOW' : 'REVIEW') : 'BLOCK',
    approval_route: card.data.approval_route,
    target: card.data.target,
    violations: [],
  }
  adjustmentStep.value = 3
}

async function submitApplication() {
  if (!applicationReason.value.trim() || applicationReason.value.trim().length < 2) return showToast('请填写申请原因')
  if (!adjustmentPreview.value || adjustmentPreview.value.decision === 'BLOCK') return showToast('当前目标场次未通过校验')
  adjustmentLoading.value = true
  try {
    await api.post('/students/me/adjustments', {
      request_type: adjustmentRequestType.value,
      source_record_id: adjustmentSourceId.value,
      target_session_id: adjustmentTargetId.value,
      reason: applicationReason.value.trim(),
      idempotency_key: crypto.randomUUID(),
    })
    await fetchAdjustmentApplications()
    await fetchDashboard()
    applicationDialog.value = null
    showToast(adjustmentRequestType.value === 'RESCHEDULE' ? '换时间已通过规则校验并执行' : '申请已提交审核')
  } catch (error) {
    showToast(error instanceof Error ? error.message : '申请提交失败')
  } finally {
    adjustmentLoading.value = false
  }
}

async function cancelAdjustment(id: string) {
  try {
    await api.post(`/students/me/adjustments/${id}/cancel`, {})
    await fetchAdjustmentApplications()
    showToast('申请已取消，目标名额预留已释放')
  } catch (error) {
    showToast(error instanceof Error ? error.message : '取消失败')
  }
}

async function askAi(preset?: string) {
  if (isStreaming.value) return
  const question = (preset ?? aiInput.value).trim()
  if (!question) return
  const userMessage = createMessage('user', question, 'completed')
  messages.value.push(userMessage)
  aiInput.value = ''
  const requestMessages = messages.value
    .filter(item => item.text.trim())
    .slice(-20)
    .map(item => ({ role: item.role, content: item.text }))
  const assistantMessage = createMessage('assistant', '', 'pending')
  messages.value.push(assistantMessage)
  const controller = new AbortController()
  activeController.value = controller
  isStreaming.value = true
  streamPhase.value = '正在理解你的问题…'
  let pendingCards: AiCard[] = []
  await scrollToLatest(true)
  try {
    await api.streamPost('/students/me/ai-consult/stream', {
      messages: requestMessages,
      page_context: { view: activeView.value },
    }, {
      onEvent: async ({ event, data }) => {
        if (event === 'meta') {
          assistantMessage.traceId = data.trace_id
        } else if (event === 'status') {
          streamPhase.value = data.message || '正在处理…'
        } else if (event === 'delta') {
          assistantMessage.status = 'streaming'
          streamPhase.value = '正在生成回答…'
          await appendCharacters(assistantMessage, String(data.text || ''), Boolean(data.replace))
        } else if (event === 'final') {
          pendingCards = Array.isArray(data.cards) ? data.cards : []
          assistantMessage.intent = data.intent as AiIntent
        } else if (event === 'error') {
          throw new Error(String(data.message || '智能咨询服务暂时不可用'))
        } else if (event === 'done') {
          assistantMessage.cards = pendingCards
          assistantMessage.status = 'completed'
          streamPhase.value = ''
        }
      },
    }, controller.signal)
    if (assistantMessage.status !== 'completed') {
      assistantMessage.cards = pendingCards
      assistantMessage.status = 'completed'
    }
  } catch (error) {
    if (controller.signal.aborted) {
      assistantMessage.status = 'stopped'
      assistantMessage.errorMessage = '已停止生成'
      if (!assistantMessage.text) assistantMessage.text = '已停止生成。'
    } else {
      assistantMessage.status = 'error'
      assistantMessage.errorMessage = error instanceof Error ? error.message : '智能咨询服务暂时不可用'
      if (!assistantMessage.text) assistantMessage.text = assistantMessage.errorMessage
    }
  } finally {
    if (activeController.value === controller) activeController.value = null
    isStreaming.value = false
    streamPhase.value = ''
    await scrollToLatest()
  }
}

function stopGeneration() {
  activeController.value?.abort()
}

function clearConversation() {
  stopGeneration()
  messages.value = messages.value.slice(0, 1)
  showJumpToLatest.value = false
}

function retryMessage(messageIndex: number) {
  const previousUser = [...messages.value.slice(0, messageIndex)].reverse().find(item => item.role === 'user')
  if (previousUser) askAi(previousUser.text)
}

onBeforeUnmount(stopGeneration)
</script>

<template>
  <div class="student-app">
    <aside class="student-sidebar" :class="{ open: sidebarOpen }">
      <div class="student-brand">
        <span class="brand-atom"><i></i></span>
        <div><strong>物理实验</strong><small>智能选课系统</small></div>
      </div>
      <nav class="student-nav" aria-label="学生端主导航">
        <p>学生工作台</p>
        <button v-for="item in navItems" :key="item.id" type="button" :class="{ active: activeView === item.id }" @click="navigate(item.id)">
          <span>{{ item.icon }}</span>{{ item.label }}<i v-if="item.id === 'applications'" class="nav-dot"></i>
        </button>
      </nav>
      <div class="sidebar-help">
        <span>?</span>
        <div><strong>需要帮助？</strong><small>AI 助手随时在线</small></div>
        <button type="button" @click="navigate('ai')">咨询</button>
      </div>
      <button class="logout-button" type="button" @click="emit('logout')"><span>↪</span> 退出演示</button>
    </aside>
    <button v-if="sidebarOpen" class="sidebar-mask" aria-label="关闭导航" @click="sidebarOpen = false"></button>

    <div class="student-main">
      <header class="student-topbar">
        <button class="menu-button" type="button" aria-label="打开导航" @click="sidebarOpen = true">☰</button>
        <div class="breadcrumb"><span>学生端</span><b>/</b>{{ activeView === 'home' ? '首页' : viewMeta[activeView].title }}</div>
        <div class="top-actions">
          <div style="position:relative"><button class="notice-button" type="button" @click="showStudentNotifs=!showStudentNotifs">♢<i v-if="studentUnread">{{ studentUnread }}</i></button><div v-if="showStudentNotifs && studentUnread" style="position:absolute;right:0;top:36px;z-index:20;width:280px;max-height:300px;overflow-y:auto;background:#fff;border:1px solid #dce4e8;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.12);padding:0"><div style="padding:8px 12px;border-bottom:1px solid #eee;font-size:9px;color:#657885">审批通知 · {{ studentUnread }} 条</div><div v-for="(n,i) in studentNotifs" :key="n.request_no" style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid #f5f5f5;font-size:8px;gap:8px"><div style="flex:1;min-width:0"><div style="color:#405562">{{ n.msg }}</div><div style="color:#919da7;margin-top:2px">{{ n.time }}</div></div><button @click="readStudentNotif(i, JSON.stringify(n))" style="flex-shrink:0;padding:2px 6px;border:1px solid #dce4e8;border-radius:3px;background:#fff;color:#277e82;font-size:7px;cursor:pointer">已读</button></div></div></div>
          <div class="student-profile"><span>{{ userInitial }}</span><div><strong>{{ studentName }}</strong><small>{{ studentNo }}</small></div></div>
        </div>
      </header>

      <main class="student-content">
        <div class="page-heading">
          <div><h1>{{ viewMeta[activeView].title }}</h1><p>{{ viewMeta[activeView].subtitle }}</p></div>
          <div v-if="activeView === 'home'" class="term-selector">{{ dashboard?.term?.academic_year || '加载中' }} 学年 {{ ['','第一学期','第二学期'][dashboard?.term?.semester_no || 2] }}⌄</div>
          <template v-if="activeView === 'schedule'">
            <button class="outline-action" type="button" :disabled="exportBusy" @click="exportSchedule('png')">↓ 导出图片</button>
            <button class="outline-action" type="button" :disabled="exportBusy" @click="exportSchedule('pdf')" style="margin-left:6px">↓ 导出PDF</button>
          </template>
        </div>

        <template v-if="activeView === 'home'">
          <section class="student-hero">
            <div class="hero-copy">
              <span class="hero-kicker">MY PHYSICS LAB</span>
              <h2>探索，从一次严谨的实验开始。</h2>
              <p>本学期第 {{ displayTermWeek }} 教学周 · 下一项实验安排在第 {{ displayNextLab.week_no }} 周{{ ['','周日','周一','周二','周三','周四','周五','周六'][displayNextLab.day_of_week] }} 第 {{ displayNextLab.start_slot }}–{{ displayNextLab.end_slot }} 节</p>
              <button type="button" @click="navigate('schedule')">查看我的实验课表 <span>→</span></button>
            </div>
            <div class="hero-orbit" aria-hidden="true"><i></i><b></b><em></em></div>
          </section>

          <section class="profile-strip">
            <div class="profile-avatar">{{ userInitial }}</div>
            <div class="profile-name"><span>学生姓名</span><strong>{{ studentName }}</strong></div>
            <div><span>学号</span><strong>{{ studentNo }}</strong></div>
            <div><span>专业</span><strong>{{ studentMajor }}</strong></div>
            <div><span>所在年级</span><strong>{{ studentGrade }}</strong></div>
            <div><span>就读校区</span><strong>{{ dashboard?.profile?.campus_name || '—' }}</strong></div>
          </section>

          <section class="summary-grid">
            <article><div class="summary-icon teal">▤</div><div><span>应修实验课程</span><strong>{{ displayCourses.length }} <small>门</small></strong><p>{{ displayCourses.map(c=>c.course_name).join('、') || '暂无数据' }}</p></div></article>
            <article><div class="summary-icon blue">✓</div><div><span>必做实验项目</span><strong>{{ displaySelection.total_required }} <small>项</small></strong><p>来自培养方案要求</p></div></article>
            <article><div class="summary-icon purple">✦</div><div><span>选做项目池</span><strong>{{ displaySelection.total_optional_pool }} <small>项</small></strong><p>至少选 {{ displaySelection.total_optional_min }} 项</p></div></article>
            <article><div class="summary-icon amber">◷</div><div><span>当前已选项目</span><strong>{{ displaySelection.selected_count }} <small>项</small></strong><p>第 {{ displayTermWeek }} 教学周</p></div></article>
          </section>

          <div class="home-columns">
            <section class="panel-card course-progress">
              <div class="panel-title"><div><h3>本学期实验课程</h3><p>培养方案要求与当前选课进度</p></div><button type="button" @click="navigate('selection')">去选课 →</button></div>
              <article v-for="(course, ci) in displayCourses" :key="course.course_code" class="student-course-detail">
                <header>
                  <div class="course-letter" :style="{ background: ['#137b80','#4769a8','#5b6ea6'][ci % 3] }">{{ course.course_name.slice(0, 1) }}</div>
                  <div>
                    <h4>
                      {{ course.course_name }}
                      <span v-if="course.completion_status==='PASSED'" style="color:#277861;font-size:.7rem;font-weight:400;margin-left:6px">✓ 已通过</span>
                      <span v-else-if="course.completion_status==='FAILED'" style="color:#a94442;font-size:.7rem;font-weight:400;margin-left:6px">✗ 未通过</span>
                      <span v-else-if="course.completion_status==='IN_PROGRESS'" style="color:#5b6ea6;font-size:.7rem;font-weight:400;margin-left:6px">◷ 修读中</span>
                    </h4>
                    <span>{{ course.course_code }} · 先修要求 {{ (course.prerequisites_passed?.length||0) + (course.prerequisites_failed?.length||0) }} 门</span>
                    <div v-if="course.prerequisites_passed?.length" style="color:#5a8a72;font-size:.65rem;line-height:1.3">✓ {{ course.prerequisites_passed.join('、') }}</div>
                    <div v-if="course.prerequisites_failed?.length" style="color:#b06060;font-size:.65rem;line-height:1.3">✗ {{ course.prerequisites_failed.join('、') }}</div>
                  </div>
                  <div class="course-ratio"><strong>选 {{ course.required_count + course.optional_min }} 项</strong><span>必做 {{ course.required_count }} + 选做≥{{ course.optional_min }}</span><small v-if="(course as any).order_rule" style="display:block;margin-top:2px;color:#919da7;font-size:7px">{{ (course as any).order_rule }}</small></div>
                </header>
                <div class="student-course-requirements">
                  <section>
                    <div class="requirement-title"><strong>必做项目</strong><span>应选 {{ course.required_count }} 项</span></div>
                    <ul><li v-for="p in course.projects.filter((p:any)=>p.requirement_type==='REQUIRED')" :key="p.project_id"><i>{{ (p.available_sessions||[]).some((s:any)=>selectedProjectIds.includes(s.id)) ? '✓' : '○' }}</i><span>{{ p.project_name }}</span><em>{{ (p.available_sessions||[]).some((s:any)=>selectedProjectIds.includes(s.id)) ? '已选' : '待选' }}</em></li></ul>
                  </section>
                  <section>
                    <div class="requirement-title optional"><strong>选做项目池</strong><span>共 {{ course.projects.filter((p:any)=>p.requirement_type==='OPTIONAL').length }} 项 · 至少选 {{ course.optional_min }} 项</span></div>
                    <ul><li v-for="p in course.projects.filter((p:any)=>p.requirement_type==='OPTIONAL')" :key="p.project_id"><i>{{ (p.available_sessions||[]).some((s:any)=>selectedProjectIds.includes(s.id)) ? '✓' : '○' }}</i><span>{{ p.project_name }}</span><em>{{ (p.available_sessions||[]).some((s:any)=>selectedProjectIds.includes(s.id)) ? '已选' : '未选' }}</em></li></ul>
                  </section>
                </div>
              </article>
            </section>

            <section class="panel-card next-lab">
              <div class="panel-title"><div><h3>下一项实验</h3><p>请提前完成实验预习</p></div><span class="week-badge">第 {{ displayNextLab.week_no }} 周</span></div>
              <h4>{{ displayNextLab.project_name }}</h4>
              <ul><li><span>◷</span>{{ ['','周日','周一','周二','周三','周四','周五','周六'][displayNextLab.day_of_week] }} 第 {{ displayNextLab.start_slot }}–{{ displayNextLab.end_slot }} 节</li><li><span>⌖</span>{{ displayNextLab.lab_name }}</li><li v-if="displayNextLab.teacher_name"><span>◎</span>{{ displayNextLab.teacher_name }}</li></ul>
            </section>
          </div>

          <section v-if="selectedProjects.length" class="panel-card selected-overview">
            <div class="panel-title"><div><h3>个人当前已选实验项目</h3><p>当前已选择的实验项目和场次安排</p></div><button type="button" @click="navigate('schedule')">查看完整课表 →</button></div>
            <div class="compact-table">
              <div class="table-row table-head"><span>实验项目</span><span>所属课程</span><span>时间安排</span><span>地点</span><span>状态</span></div>
              <div v-for="project in selectedProjects.slice(0, 5)" :key="project.id" class="table-row">
                <span><b>{{ project.project_name }}</b></span><span>{{ project.course_name }}</span><span>{{ project.week }} · {{ project.time }}</span><span>{{ project.room }}</span><span><i class="status confirmed">已确认</i></span>
              </div>
            </div>
          </section>
        </template>

        <template v-else-if="activeView === 'schedule'">
          <section class="filter-bar">
            <label>教学周<select v-model.number="scheduleWeek"><option :value="0">全部周</option><option v-for="w in (dashboard?.term?.total_weeks || 18)" :key="w" :value="w">第 {{ w }} 周</option></select></label>
            <label>课程<select v-model="scheduleCourseFilter"><option>全部课程</option><option v-for="c in displayCourses" :key="c.course_code" :value="c.course_code">{{ c.course_name }}</option></select></label>
            <div class="schedule-legend"><span v-for="(c,ci) in displayCourses" :key="c.course_code"><i :class="['teal','blue','purple'][ci % 3]"></i>{{ c.course_name }}</span></div>
          </section>
          <section class="panel-card timetable-card">
            <div class="week-switch"><button type="button" @click="scheduleWeek = scheduleWeek === 0 ? (dashboard?.term?.total_weeks || 18) : Math.max(1, scheduleWeek - 1)">‹</button><div><strong>{{ scheduleWeek === 0 ? '全部周' : `第 ${scheduleWeek} 教学周` }}</strong><span>{{ dashboard?.term?.academic_year || '加载中' }} 学年 {{ ['','第一学期','第二学期'][dashboard?.term?.semester_no || 2] }}</span></div><button type="button" @click="scheduleWeek = scheduleWeek === 0 ? 1 : Math.min((dashboard?.term?.total_weeks || 18), scheduleWeek + 1)">›</button></div>
            <div class="timetable">
              <div class="time-corner">节次</div>
              <div v-for="(d,i) in scheduleDayHeaders" :key="d" class="day-head" :style="{ gridColumn: i + 2 }"><strong>{{ d.split(' ')[0] }}</strong><span>{{ d.split(' ')[1] }}</span></div>
              <div v-for="slot in 12" :key="slot" class="time-label" :class="{ 'period-boundary': slot === 4 || slot === 8 }" :style="{ gridRow: slot + 1 }"><strong>第 {{ slot }} 节</strong></div>
              <div v-for="day in 7" :key="`col-${day}`" class="day-column" :style="{ gridColumn: day + 1, gridRow: '2 / 14' }"></div>
              <article v-for="g in scheduleSessions" :key="g.key" class="schedule-event" :class="[g.tone, { expanded: g.count > 1 && g.show }]" :style="{ gridColumn: g.day + 1, gridRow: `${g.startSlot + 1} / span ${g.endSlot - g.startSlot + 1}`, cursor: g.count > 1 ? 'pointer' : '' }" @click="g.count > 1 && (expandedSlot = expandedSlot === g.key ? null : g.key)">
                <template v-if="g.count === 1 || !g.show">
                  <span v-if="g.count === 1">第{{ g.sessions[0].week_no }}周 · {{ g.sessions[0].teacher_name }}</span>
                  <span v-else class="schedule-stack-badge">{{ g.hasActualConflict ? '时间冲突' : `${g.count}周安排` }}</span>
                  <strong v-if="g.count === 1">{{ g.sessions[0].project_name }}</strong>
                  <strong v-else>{{ g.hasActualConflict ? '查看重叠场次' : '查看多周安排' }} <i class="schedule-stack-toggle">⌄</i></strong>
                  <small v-if="g.count === 1">⌖ {{ g.sessions[0].lab_name }}</small>
                </template>
                <template v-else>
                  <div class="schedule-stack-head">
                    <span>{{ g.hasActualConflict ? '时间冲突' : `${g.count}周安排` }}</span>
                    <i>⌃</i>
                  </div>
                  <div class="schedule-stack-list">
                    <div v-for="s in g.sessions" :key="s.session_id" class="schedule-stack-item">
                      <span><b>第{{ s.week_no }}周</b><em>{{ s.teacher_name }}</em></span>
                      <strong :title="s.project_name">{{ s.project_name }}</strong>
                      <small :title="s.lab_name">⌖ {{ s.lab_name }}</small>
                    </div>
                  </div>
                </template>
              </article>
            </div>
          </section>
          <section class="schedule-notice"><span>i</span><div><strong>课表说明</strong><p>展示本学期已选实验场次。可通过教学周切换查看不同周次安排。</p></div></section>
          <!-- 导出用隐藏容器：一张总表，冲突展开 -->
          <div ref="exportContainer" class="export-schedule-container">
            <h2>{{ studentName }} 实验课表</h2>
            <p class="export-info">{{ studentNo }} · {{ studentMajor }} · {{ dashboard?.term?.academic_year || '' }} {{ ['','第一学期','第二学期'][dashboard?.term?.semester_no || 2] }}</p>
            <div class="export-grid" style="grid-template-rows:28px repeat(12,auto);min-height:360px">
              <div class="export-corner">节次</div>
              <div v-for="(d,i) in ['周日','周一','周二','周三','周四','周五','周六']" :key="d" :style="{gridColumn:i+2,gridRow:1}" class="export-day-head">{{ d }}</div>
              <template v-for="slot in 12" :key="slot">
                <div :style="{gridRow:slot+1}" :class="slot===4||slot===8?'export-slot export-slot-boundary':'export-slot'">{{ slot }}</div>
                <div v-for="day in 7" :key="day" :style="{gridColumn:day+1,gridRow:slot+1}" class="export-cell"></div>
              </template>
              <template v-for="g in exportSessionsGrouped" :key="g.key">
                <div :style="{gridColumn:g.day+1,gridRow:(g.startSlot+1)+' / span '+(g.endSlot-g.startSlot+1)}" class="export-event" :class="{ 'export-conflict': g.count > 1 }">
                  <template v-if="g.count === 1">
                    <div class="export-event-sub">第{{ g.sessions[0].week_no }}周 · {{ g.sessions[0].teacher_name }}</div>
                    <div class="export-event-title">{{ g.sessions[0].project_name }}</div>
                    <div class="export-event-sub">⌖ {{ g.sessions[0].lab_name }}</div>
                  </template>
                  <template v-else>
                    <div v-for="s in g.sessions" :key="s.session_id" style="padding:2px 0;border-bottom:1px solid #e0d0cc;margin-bottom:2px">
                      <div class="export-event-sub">第{{ s.week_no }}周 · {{ s.teacher_name }}</div>
                      <div class="export-event-title">{{ s.project_name }}</div>
                      <div class="export-event-sub">⌖ {{ s.lab_name }}</div>
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </div>
        </template>

        <template v-else-if="activeView === 'selection'">
          <!-- 个人课表概览 -->
          <section class="panel-card" style="margin-bottom:16px;padding:16px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
              <div><h3 style="margin:0 0 4px">我的课表</h3><p style="margin:0;color:#919da7;font-size:8px">红色已有安排 | 绿色已选实验</p></div>
              <label style="font-size:9px">第 <select v-model="bitmapWeek" style="margin:0 4px"><option v-for="w in (bitmapData?.weeks || 18)" :key="w" :value="w">{{ w }}</option></select> 周</label>
            </div>
            <div style="display:grid;grid-template-columns:50px repeat(7,1fr);grid-template-rows:28px repeat(12,18px);border:1px solid #e4eaed;border-radius:6px;overflow:hidden;font-size:8px;min-width:600px">
              <div style="grid-row:1;display:flex;align-items:center;justify-content:center;color:#8997a1;border-bottom:1px solid #e4eaed;border-right:1px solid #e4eaed">节次</div>
              <div v-for="(d,i) in ['周日','周一','周二','周三','周四','周五','周六']" :key="d" :style="{gridColumn:i+2,gridRow:1}" style="display:flex;align-items:center;justify-content:center;font-weight:600;color:#405562;background:#f8fafb;border-bottom:1px solid #e4eaed;border-right:1px solid #e4eaed">{{ d }}</div>
              <template v-for="slot in 12" :key="slot">
                <div :style="{gridRow:slot+1, borderBottom: slot===4||slot===8 ? '2px solid #c8d4da' : '1px solid #e4eaed'}" style="display:flex;align-items:center;justify-content:center;color:#8997a1;font-size:7px;border-right:1px solid #e4eaed">第{{ slot }}节</div>
                <div v-for="day in 7" :key="day"
                  :style="{
                    gridColumn:day+1, gridRow:slot+1,
                    background: isSlotBusy(day-1,slot-1) ? '#f28b82' : '',
                    border: isSlotBusy(day-1,slot-1) ? '1px solid #e57373' : '1px solid #f0f3f5'
                  }"
                ></div>
              </template>
              <!-- 已选场次覆盖层 -->
              <div v-for="s in (dashboard?.selected_sessions || []).filter((s:any)=>s.week_no===bitmapWeek)" :key="s.session_id"
                :style="{
                  gridColumn: s.day_of_week + 1,
                  gridRow: (s.start_slot + 1) + ' / span ' + (s.end_slot - s.start_slot + 1),
                  background: '#c8e6c9',
                  border: '1px solid #66bb6a',
                  borderRadius: '3px',
                  margin: '1px',
                  display: 'flex', flexDirection: 'column', justifyContent: 'center',
                  overflow: 'hidden', zIndex: 2,
                }"
                :title="s.project_name + ' · ' + s.lab_name + ' · ' + (s.teacher_name || '')"
              >
                <span style="font-weight:600;color:#2e7d32;font-size:7px;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ s.project_name }}</span>
                <span style="color:#388e3c;font-size:6px;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ s.lab_name }}<span v-if="s.teacher_name"> · {{ s.teacher_name }}</span></span>
              </div>
            </div>
          </section>
          <section v-for="c in courseSelectionProgress" :key="c.course_code" class="selection-summary">
            <div class="selection-summary-content">
              <header class="selection-summary-head">
                <h3>{{ c.course_name }}</h3>
                <span>选课完成进度</span>
              </header>
              <div class="selection-progress-grid">
                <article class="selection-progress-item required">
                  <div class="selection-progress-label"><span><i>必</i>必做项目</span><strong>{{ c.reqSelected }}/{{ c.reqTotal }}</strong></div>
                  <div class="progress-track"><i :style="{ width: c.reqTotal ? `${Math.min(100, Math.round(c.reqSelected / c.reqTotal * 100))}%` : '0%' }"></i></div>
                  <small>已选择 {{ c.reqSelected }} 项，共 {{ c.reqTotal }} 项</small>
                </article>
                <article class="selection-progress-item optional">
                  <div class="selection-progress-label"><span><i>选</i>选做项目</span><strong>{{ c.optSelected }}/{{ c.optTotal }}</strong></div>
                  <div class="progress-track"><i :style="{ width: (c.optMin || c.optTotal) ? `${Math.min(100, Math.round(c.optSelected / (c.optMin || c.optTotal) * 100))}%` : '0%' }"></i></div>
                  <small>{{ c.optMin ? `培养方案至少完成 ${c.optMin} 项` : `项目池共 ${c.optTotal} 项` }}</small>
                </article>
              </div>
            </div>
          </section>
          <section class="selection-tools">
            <div class="course-tabs"><button :class="{ active: courseFilter === '全部课程' }" @click="courseFilter = '全部课程'">全部课程</button><button v-for="course in displayCourses" :key="course.course_code" :class="{ active: courseFilter === course.course_name }" @click="courseFilter = course.course_name">{{ course.course_name }}</button></div>
            <div class="project-filters">
              <label class="search-box">⌕<input v-model="projectKeyword" placeholder="搜索实验名称、教师或实验室" /></label>
              <select v-model="projectType"><option>全部</option><option>必做</option><option>选做</option></select>
            </div>
          </section>
          <div class="project-grid">
            <article v-for="project in filteredProjects" :key="project.id" class="project-card" :class="{ selected: selectedProjectIds.includes(project.id as any) }">
              <div class="project-card-top"><span :class="project.type === '必做' ? 'required' : 'optional'">{{ project.type }}</span><i>{{ project.course_name }}</i></div>
              <h3>{{ project.name }}</h3>
              <ul><li><span>▣</span>{{ project.week }} · {{ project.time }}</li><li><span>⌖</span>{{ project.room }}</li><li v-if="project.teacher"><span>◎</span>{{ project.teacher }}</li></ul>
              <div class="capacity"><span>名额</span><div><i :style="{ width: project.capacity ? `${((project.capacity - project.remaining) / project.capacity) * 100}%` : '0%' }"></i></div><b :class="{ danger: project.remaining <= 2 }">{{ project.remaining ? `余 ${project.remaining}` : '已满' }}</b></div>
              <button type="button" :class="{ remove: selectedProjectIds.includes(project.id as any) }" :disabled="(project.remaining === 0 && !selectedProjectIds.includes(project.id as any)) || selectingProjectId === String(project.id)" @click="toggleProject(project.id)">
                {{ selectingProjectId === String(project.id) ? '正在核验资格…' : selectedProjectIds.includes(project.id as any) ? '已选 · 点击退选' : project.remaining === 0 ? '名额已满' : '选择该项目' }}
              </button>
              <p v-if="selectionFeedback[String(project.id)]" class="selection-card-feedback" role="alert"><span>!</span>{{ selectionFeedback[String(project.id)] }}</p>
            </article>
          </div>
        </template>

        <template v-else-if="activeView === 'applications'">
          <section class="application-types">
            <button type="button" @click="openApplication('调课申请')"><span class="teal">⇄</span><div><strong>调课申请</strong><small>调整实验时间或教学周</small></div><i>→</i></button>
            <button type="button" @click="openApplication('换组申请')"><span class="blue">♙</span><div><strong>换组申请</strong><small>从当前实验项目调整至另一项目</small></div><i>→</i></button>
            <button type="button" @click="openApplication('补做申请')"><span class="purple">↺</span><div><strong>补做申请</strong><small>申请缺席实验补做安排</small></div><i>→</i></button>
          </section>
          <section class="panel-card application-list">
            <div class="panel-title"><div><h3>我的申请记录</h3><p>查看申请处理进度与审核意见</p></div><select><option>全部状态</option><option>审核中</option><option>已通过</option></select></div>
            <div class="compact-table">
              <div class="table-row application-head"><span>申请编号 / 类型</span><span>关联实验项目</span><span>申请日期</span><span>处理状态</span><span>审核人</span><span>审核说明</span></div>
              <div v-for="item in applications" :key="item.id" class="table-row application-row">
                <span><b>{{ item.type }}</b><small>{{ item.id }}</small></span><span>{{ item.project }}</span><span>{{ item.date }}</span><span><i class="status" :class="{ pending: item.status === '审核中', confirmed: item.status === '已执行', rejected: item.status === '已驳回' || item.status === '已取消' }">{{ item.status }}</i></span><span>{{ item.reviewer || '—' }}</span><span>{{ item.note }} <button v-if="item.status === '审核中'" type="button" class="application-cancel" @click="cancelAdjustment(item.rawId)">取消</button></span>
              </div>
            </div>
          </section>
          <section class="application-tip"><span>!</span><p>换时间在实时校验通过后自动执行；换项目由管理员审批；补做由原场次任课教师审批。所有提交都会再次核验时间冲突、项目顺序和目标名额。</p></section>
        </template>

        <template v-else>
          <div class="ai-layout">
            <aside class="ai-guide">
              <div class="ai-intro"><span>✦</span><h3>实验智能助手</h3><p>基于课程规则与实验安排提供咨询</p></div>
              <div class="quick-question"><span>你可以这样问</span>
                <button v-for="question in quickQuestions" :key="question" :disabled="isStreaming" @click="askAi(question)">{{ question }} <i>→</i></button>
              </div>
              <p class="ai-notice">回答依据当前培养方案、课表和选课规则；正式操作请在在线选课页面确认。</p>
            </aside>
            <section class="ai-chat panel-card">
              <header><div><span class="ai-avatar">✦</span><div><strong>物理实验 AI 助手</strong><small><i :class="{ working: isStreaming }"></i> {{ isStreaming ? streamPhase : '已连接 · 基于当前培养方案和课表' }}</small></div></div><div class="ai-header-actions"><button v-if="isStreaming" type="button" class="stop" @click="stopGeneration">停止生成</button><button type="button" @click="clearConversation">清空对话</button></div></header>
              <div ref="aiThread" class="ai-thread" @scroll="handleThreadScroll">
                <div v-for="(item, index) in messages" :key="item.id" class="ai-message" :class="[item.role, item.status]">
                  <span>{{ item.role === 'assistant' ? '✦' : userInitial }}</span><div>
                    <div v-if="item.role === 'assistant' && item.status === 'pending'" class="ai-thinking" aria-label="正在思考"><i></i><i></i><i></i></div>
                    <div v-else class="ai-markdown" v-html="renderMarkdown(item.text)"></div><i v-if="item.role === 'assistant' && item.status === 'streaming'" class="stream-cursor"></i>
                    <div v-if="item.cards?.length" class="ai-result-cards">
                      <article v-for="(card, cardIndex) in item.cards" :key="cardIndex" :class="card.type.toLowerCase()">
                        <strong>{{ card.title }}</strong><small>{{ card.summary }}</small>
                        <ul><li v-for="line in aiCardLines(card)" :key="line">{{ line }}</li></ul>
                      </article>
                    </div>
                    <small>{{ item.role === 'assistant' ? (item.errorMessage || 'AI 助手') : '刚刚' }} <button v-if="item.status === 'error'" type="button" @click="retryMessage(index)">重新发送</button></small>
                  </div>
                </div>
                <button v-if="showJumpToLatest" type="button" class="jump-latest" @click="scrollToLatest(true)">回到最新消息 ↓</button>
              </div>
              <form class="ai-input" @submit.prevent="askAi()">
                <textarea v-model="aiInput" rows="2" placeholder="请输入关于实验选课、课表或申请的问题…" @keydown.enter.exact.prevent="askAi()"></textarea>
                <div><span>{{ isStreaming ? '可以继续编辑，停止后再发送' : 'Enter 发送 · Shift + Enter 换行' }}</span><button v-if="!isStreaming" type="submit" :disabled="!aiInput.trim()">发送 <i>↑</i></button><button v-else type="button" class="stop-send" @click="stopGeneration">停止 <i>■</i></button></div>
              </form>
            </section>
          </div>
        </template>
      </main>
    </div>

    <div v-if="applicationDialog" class="dialog-backdrop" @click.self="applicationDialog = null">
      <form class="application-dialog adjustment-dialog" @submit.prevent="submitApplication">
        <div class="dialog-title"><div><span>◇</span><div><h3>{{ applicationDialog }}</h3><p>选择原实验、目标场次并完成实时资格校验</p></div></div><button type="button" @click="applicationDialog = null">×</button></div>
        <div class="adjustment-steps"><span v-for="step in 4" :key="step" :class="{ active: adjustmentStep >= step }"><i>{{ step }}</i>{{ ['原实验','目标场次','资格影响','确认提交'][step - 1] }}</span></div>

        <section v-if="adjustmentStep === 1" class="adjustment-pane">
          <h4>选择需要调整的原实验</h4>
          <p v-if="adjustmentLoading">正在加载可申请的实验…</p>
          <label v-else v-for="source in adjustmentSources" :key="source.record_id" class="adjustment-choice">
            <input v-model="adjustmentSourceId" type="radio" :value="source.record_id" />
            <span><strong>{{ source.session.project_name }}</strong><small>{{ adjustmentSessionText(source.session) }}</small></span>
          </label>
          <p v-if="!adjustmentLoading && !adjustmentSources.length" class="adjustment-empty">当前没有符合该申请类型时间条件的原实验。</p>
          <div class="dialog-actions"><button type="button" @click="applicationDialog = null">取消</button><button type="button" :disabled="!adjustmentSourceId" @click="loadAdjustmentTargets">下一步</button></div>
        </section>

        <section v-else-if="adjustmentStep === 2" class="adjustment-pane">
          <div class="adjustment-mode"><button type="button" :class="{ active: adjustmentMode === 'manual' }" @click="adjustmentMode = 'manual'">自己选择</button><button type="button" :class="{ active: adjustmentMode === 'ai' }" @click="adjustmentMode = 'ai'">AI帮我推荐</button></div>
          <template v-if="adjustmentMode === 'manual'">
            <label v-for="candidate in adjustmentCandidates" :key="candidate.target?.session_id" class="adjustment-choice" :class="{ blocked: candidate.decision === 'BLOCK' }">
              <input v-model="adjustmentTargetId" type="radio" :value="candidate.target?.session_id" :disabled="candidate.decision === 'BLOCK'" />
              <span><strong>{{ candidate.target ? adjustmentSessionText(candidate.target) : '无效场次' }}</strong><small v-if="candidate.violations.length">{{ candidate.violations.map(item => item.message).join('；') }}</small><small v-else>剩余可申请名额 {{ candidate.target?.remaining }}</small></span>
            </label>
            <p v-if="!adjustmentCandidates.length" class="adjustment-empty">当前没有现有目标场次，需要管理员另行安排。</p>
          </template>
          <template v-else>
            <label>告诉AI你的偏好<textarea v-model="adjustmentPreference" rows="3" placeholder="例如：第8周以后，最好周三下午，不要晚上"></textarea></label>
            <button type="button" class="adjustment-ai-button" :disabled="adjustmentLoading" @click="recommendAdjustment">{{ adjustmentLoading ? '正在核验场次…' : '生成推荐' }}</button>
            <p v-if="adjustmentAiText" class="adjustment-ai-answer">{{ adjustmentAiText }}</p>
            <button v-for="card in adjustmentAiCards" :key="card.data.target.session_id" type="button" class="adjustment-recommendation" @click="useAdjustmentRecommendation(card)"><strong>{{ adjustmentSessionText(card.data.target) }}</strong><small>{{ card.data.reasons?.join('；') || card.summary }}</small><i>采用此场次 →</i></button>
          </template>
          <div class="dialog-actions"><button type="button" @click="adjustmentStep = 1">上一步</button><button v-if="adjustmentMode === 'manual'" type="button" :disabled="!adjustmentTargetId" @click="previewAdjustment">核验资格</button></div>
        </section>

        <section v-else-if="adjustmentStep === 3" class="adjustment-pane">
          <div v-if="adjustmentPreview" class="adjustment-result" :class="adjustmentPreview.decision.toLowerCase()"><strong>{{ adjustmentPreview.decision === 'BLOCK' ? '当前不能提交' : '资格校验通过' }}</strong><p v-if="adjustmentPreview.target">{{ adjustmentSessionText(adjustmentPreview.target) }}</p><ul v-if="adjustmentPreview.violations.length"><li v-for="item in adjustmentPreview.violations" :key="item.code">{{ item.message }}</li></ul><small>审批方式：{{ adjustmentPreview.approval_route === 'AUTO' ? '系统自动审批' : adjustmentPreview.approval_route === 'TEACHER' ? '原场次任课教师审批' : '管理员审批' }}</small></div>
          <div class="dialog-actions"><button type="button" @click="adjustmentStep = 2">上一步</button><button type="button" :disabled="adjustmentPreview?.decision === 'BLOCK'" @click="adjustmentStep = 4">填写原因</button></div>
        </section>

        <section v-else class="adjustment-pane">
          <label>申请原因<textarea v-model="applicationReason" rows="4" placeholder="请说明调整或补做原因"></textarea></label>
          <div class="dialog-warning">提交时系统会重新校验目标名额、时间冲突和项目顺序。{{ adjustmentRequestType === 'RESCHEDULE' ? '通过后将自动执行换时间。' : adjustmentRequestType === 'MAKEUP' ? '提交后由原场次任课教师审批。' : '提交后由管理员审批，原项目在审批前保持不变。' }}</div>
          <div class="dialog-actions"><button type="button" @click="adjustmentStep = 3">上一步</button><button type="submit" :disabled="adjustmentLoading">{{ adjustmentLoading ? '正在提交…' : '确认提交申请' }}</button></div>
        </section>
      </form>
    </div>

    <Transition name="toast"><div v-if="toast" class="portal-toast" role="status"><span>✓</span>{{ toast }}</div></Transition>
  </div>
</template>
