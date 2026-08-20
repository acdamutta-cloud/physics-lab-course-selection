<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import '../resource-issue.css'
import type { UserProfile } from '../api/auth'
import { api } from '../api/client'
import NotificationBell from './NotificationBell.vue'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'

type TeacherView = 'home' | 'schedule' | 'classes' | 'adjustments' | 'resources'
type AdjustmentType = '调课申请' | '场地调整申请' | '代课申请'

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
const adjustmentTarget = ref('')
const adjustmentReason = ref('')
const reportLocation = ref('')
const reportLevel = ref('一般')
const reportDescription = ref('')
const reportStart = ref('')
const reportEnd = ref('')
const resourceAction = ref<'issue' | 'scrap'>('issue')
const reportEquipmentTypeId = ref('')
const reportAssetId = ref('')
const reportableAssets = ref<any[]>([])
const repairExtensionDialog = ref<{ record: any; currentEnd: string; newEnd: string; minDate: string; note: string } | null>(null)
const repairExtensionBusy = ref(false)
const adjustmentContext = ref<any>({ sessions: [], laboratories: [], substitute_teachers: [], inventories: [] })
const adjustmentOriginalSessionId = ref('')
const adjustmentWeek = ref(1)
const adjustmentDay = ref(2)
const adjustmentStartSlot = ref(1)
const adjustmentLabId = ref('')
const adjustmentTeacherId = ref('')
const adjustmentPreference = ref('')
const adjustmentPreview = ref<any>(null)
const aiOptions = ref<any[]>([])
const aiAnswer = ref('')
const aiLoading = ref(false)
const submitBusy = ref(false)

const selectedReportAsset = computed(() => reportableAssets.value.find((item: any) => item.id === reportAssetId.value))
const resourceLabs = computed(() => adjustmentContext.value.laboratories.filter((lab: any) => reportableAssets.value.some((item: any) => item.laboratory_id === lab.id)))
const reportEquipmentTypes = computed(() => {
  const unique = new Map<string, any>()
  reportableAssets.value.filter((item: any) => item.laboratory_id === reportLocation.value).forEach((item: any) => unique.set(item.equipment_type_id, item))
  return [...unique.values()]
})
const reportAssets = computed(() => reportableAssets.value.filter((item: any) => item.laboratory_id === reportLocation.value && item.equipment_type_id === reportEquipmentTypeId.value))

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
  adjustments: { title: '教学调整申请', subtitle: '提交并跟踪调课及代课申请' },
  resources: { title: '资源异常上报', subtitle: '上报实验室、仪器与耗材异常并跟踪处理进度' },
}

// ── API 数据 ──
const teacherProfile = ref<{
  name: string; department: string; title: string
  term: { academic_year: string; semester_no: number; start_date: string; current_week: number; total_weeks: number }
  qualified_projects_count: number; scheduled_session_count: number
  teaching_tasks: Array<{ task_id: string; course_name: string; course_code: string; planned_student_count: number; week_start: number; week_end: number; project_id: string }>
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

/** 整页截图按 A4 页高纵向切片，生成多页 PDF，避免长内容被压进单页。 */
function canvasToPdf(canvas: HTMLCanvasElement, orientation: 'p' | 'l', marginX = 0) {
  const pdf = new jsPDF(orientation, 'mm', 'a4')
  const pageW = pdf.internal.pageSize.getWidth()
  const pageH = pdf.internal.pageSize.getHeight()
  const drawW = pageW - marginX * 2
  const drawH = (canvas.height * drawW) / canvas.width
  const imgData = canvas.toDataURL('image/png')
  let heightLeft = drawH
  let position = marginX
  pdf.addImage(imgData, 'PNG', marginX, position, drawW, drawH)
  heightLeft -= pageH - marginX
  while (heightLeft > 0) {
    position -= pageH - marginX
    pdf.addPage()
    pdf.addImage(imgData, 'PNG', marginX, position, drawW, drawH)
    heightLeft -= pageH - marginX
  }
  return pdf
}

/** html2canvas 对离屏元素(left:-9999px)和超高 canvas 渲染不可靠，
 *  导出期间把容器临时移入视口，渲染完成后再复位。 */
function withVisibleExportEl<T>(el: HTMLElement, render: () => Promise<T>): Promise<T> {
  const prev = {
    position: el.style.position,
    left: el.style.left,
    top: el.style.top,
    zIndex: el.style.zIndex,
  }
  el.style.position = 'fixed'
  el.style.left = '0'
  el.style.top = '0'
  el.style.zIndex = '-1'
  return render().finally(() => {
    el.style.position = prev.position
    el.style.left = prev.left
    el.style.top = prev.top
    el.style.zIndex = prev.zIndex
  })
}

async function exportSchedule(format: 'png' | 'pdf') {
  if (exportTeacherBusy.value) return
  exportTeacherBusy.value = true
  try {
    await loadAllWeeks()
    await nextTick()
    const el = exportTeacherRef.value
    if (!el) { showToast('导出失败'); return }
    if (format === 'png') {
      const canvas = await withVisibleExportEl(el, () => html2canvas(el, { backgroundColor: '#ffffff', scale: 2 }))
      const link = document.createElement('a')
      link.download = `教师课表_${teacherName.value}.png`
      link.href = canvas.toDataURL('image/png')
      link.click()
    } else {
      // 逐周截图拼多页 PDF：一次截图全部周堆叠会生成超高 canvas，
      // html2canvas 对超高/超大面积渲染输出不完整，导致内容缺失。
      const weeks = Array.from(el.querySelectorAll<HTMLElement>('.export-week'))
      if (!weeks.length) { showToast('导出失败'); return }
      const pdf = new jsPDF('l', 'mm', 'a4')
      const pageW = pdf.internal.pageSize.getWidth()
      await withVisibleExportEl(el, async () => {
        for (const [i, weekEl] of weeks.entries()) {
          const canvas = await html2canvas(weekEl, { backgroundColor: '#ffffff', scale: 2 })
          const imgData = canvas.toDataURL('image/png')
          const drawW = pageW - 16
          const drawH = (canvas.height * drawW) / canvas.width
          if (i > 0) pdf.addPage()
          if (i === 0) {
            pdf.setFontSize(14)
            pdf.text(`${teacherName.value} 教师课表`, 8, 6)
          }
          pdf.addImage(imgData, 'PNG', 8, 10, drawW, drawH)
        }
      })
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
    const canvas = await withVisibleExportEl(el, () => html2canvas(el, { backgroundColor: '#ffffff', scale: 2 }))
    const pdf = canvasToPdf(canvas, 'p', 10)
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
  await fetchAdjustmentContext()
  await fetchOwnTeacherAdjustments()
  await fetchSubstitutionTasks()
  await fetchSubstitutionResults()
  await fetchResourceIssues()
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
const scheduleEvents = computed(() => filteredSessions.value.map((s) => {
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

const adjustmentRecords = ref<Array<{ id: string; rawId: string; type: string; project: string; original: string; target: string; date: string; status: string; reason_text: string; student_name: string }>>([])
const teacherOwnAdjustments = ref<any[]>([])
const substitutionTasks = ref<any[]>([])
const substitutionResults = ref<any[]>([])

// 提示铃：点击通知跳转到对应视图
function handleNotifItemClick(item: Record<string, unknown>) {
  const type = String(item.type || '')
  navigate(type === '资源异常' ? 'resources' : 'adjustments')
}

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

async function rejectAdjustment(id: string) {
  const reason = prompt('驳回理由：')
  if (!reason) return
  try {
    await api.post(`/teachers/me/adjustments/${id}/review`, { decision: 'REJECTED', comment: reason })
    showToast('已驳回')
    await fetchTeacherAdjustments()
  } catch (e: any) { showToast(e?.message || '操作失败') }
}

const resourceRecords = ref<any[]>([])
const resourceRecordFilter = ref<'ALL' | 'EQUIPMENT_FAILURE' | 'EQUIPMENT_SCRAP'>('ALL')
const resourceRecordKeyword = ref('')
const filteredResourceRecords = computed(() => {
  const keyword = resourceRecordKeyword.value.trim().toLowerCase()
  return resourceRecords.value.filter((record: any) =>
    (resourceRecordFilter.value === 'ALL' || record.issueType === resourceRecordFilter.value)
    && (!keyword || `${record.id}${record.instrumentNo}${record.equipmentName}${record.location}${record.detail}`.toLowerCase().includes(keyword)),
  )
})
const resourceRecordSummary = computed(() => ({
  total: resourceRecords.value.length,
  pending: resourceRecords.value.filter((item: any) => ['PENDING_REVIEW', 'SCRAP_REVIEW', 'RELOCATION_REQUIRED', 'READY_TO_EXECUTE'].includes(item.rawStatus)).length,
  processing: resourceRecords.value.filter((item: any) => item.rawStatus === 'PROCESSING').length,
  finished: resourceRecords.value.filter((item: any) => ['RESOLVED', 'SCRAPPED', 'REJECTED'].includes(item.rawStatus)).length,
}))

const displayedTasks = computed(() => apiTeachingTasks.value.length ? apiTeachingTasks.value : teachingTasks)
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
  adjustmentOriginalSessionId.value = adjustmentContext.value.sessions[0]?.id || ''
  adjustmentTarget.value = ''
  adjustmentReason.value = ''
  adjustmentPreference.value = ''
  adjustmentPreview.value = null
  aiOptions.value = []
  const source = adjustmentContext.value.sessions[0]
  if (source) {
    adjustmentWeek.value = source.week_no
    adjustmentDay.value = source.day_of_week
    adjustmentStartSlot.value = source.start_slot
  }
}

function adjustmentTypeCode() {
  return adjustmentDialog.value === '调课申请' ? 'TEACHER_ADJUSTMENT' : adjustmentDialog.value === '场地调整申请' ? 'LAB_CHANGE' : 'TEACHER_SUBSTITUTION'
}

function timeTarget() {
  return { week_no: adjustmentWeek.value, day_of_week: adjustmentDay.value, start_slot: adjustmentStartSlot.value, end_slot: adjustmentStartSlot.value + 3 }
}

async function fetchAdjustmentContext() {
  try {
    const [context, assets] = await Promise.all([
      api.get<any>('/teachers/me/adjustments/context'),
      api.get<any[]>('/teachers/me/reportable-equipment-assets').catch(() => []),
    ])
    adjustmentContext.value = context; reportableAssets.value = assets
  } catch { adjustmentContext.value = { sessions: [], laboratories: [], substitute_teachers: [], inventories: [] }; reportableAssets.value = [] }
}

async function previewTeacherAdjustment() {
  if (!adjustmentOriginalSessionId.value) return showToast('请选择原实验场次')
  try {
    if (adjustmentDialog.value === '调课申请') {
      adjustmentPreview.value = await api.post('/teachers/me/adjustments/reschedule/preview', { original_session_id: adjustmentOriginalSessionId.value, target: timeTarget() })
    } else if (adjustmentDialog.value === '场地调整申请') {
      adjustmentPreview.value = await api.post('/teachers/me/adjustments/lab/preview', { original_session_id: adjustmentOriginalSessionId.value, target_laboratory_id: adjustmentLabId.value })
    } else {
      adjustmentPreview.value = await api.post('/teachers/me/adjustments/substitution/preview', { original_session_id: adjustmentOriginalSessionId.value, substitute_teacher_id: adjustmentTeacherId.value })
    }
  } catch (e: any) { showToast(e?.message || '预校验失败') }
}

async function askAiForReschedule() {
  if (!adjustmentOriginalSessionId.value || aiLoading.value) return
  aiLoading.value = true; aiAnswer.value = ''; aiOptions.value = []
  try {
    await api.streamPost('/teachers/me/adjustments/reschedule/recommend/stream', {
      original_session_id: adjustmentOriginalSessionId.value, message: adjustmentPreference.value, max_options: 3,
    }, { onEvent({ event, data }: any) {
      if (event === 'delta') aiAnswer.value += data.text || ''
      if (event === 'final') aiOptions.value = (data.cards || []).map((card: any) => card.data)
      if (event === 'error') throw new Error(data.message || 'AI推荐失败')
    }})
  } catch (e: any) { showToast(e?.message || 'AI推荐失败') }
  aiLoading.value = false
}

function useAiOption(option: any) {
  adjustmentWeek.value = option.target.week_no
  adjustmentDay.value = option.target.day_of_week
  adjustmentStartSlot.value = option.target.start_slot
  adjustmentPreview.value = { allowed: option.affected_student_count === 0, can_submit_for_review: true, affected_students: option.affected_students, warnings: option.warnings, conflicts: [] }
}

async function submitAdjustment() {
  if (!adjustmentOriginalSessionId.value || !adjustmentReason.value.trim()) return showToast('请选择原场次并填写申请原因')
  const isAuto = adjustmentTypeCode() !== 'TEACHER_SUBSTITUTION'
  if (!window.confirm(isAuto ? '确认提交该申请吗？校验通过且不影响他人时将自动执行。' : '确认提交该代课申请吗？提交后需代课教师确认，再由管理员批准。')) return
  submitBusy.value = true
  try {
    const resp = await api.post<any>('/teachers/me/adjustments', {
      request_type: adjustmentTypeCode(), original_session_id: adjustmentOriginalSessionId.value,
      reason: adjustmentReason.value,
      target_time: adjustmentDialog.value === '调课申请' ? timeTarget() : null,
      target_laboratory_id: adjustmentDialog.value === '场地调整申请' ? adjustmentLabId.value : null,
      substitute_teacher_id: adjustmentDialog.value === '代课申请' ? adjustmentTeacherId.value : null,
      idempotency_key: crypto.randomUUID(),
    })
    adjustmentDialog.value = null
    showToast(resp?.status === 'EXECUTED' ? '校验通过，已自动执行' : '申请已提交，等待审核')
    await fetchOwnTeacherAdjustments()
  } catch (e: any) { showToast(e?.message || '提交失败') }
  submitBusy.value = false
}

const TEACHER_ADJUST_PAGE_SIZE = 10
const teacherAdjustPage = ref(0)
const teacherAdjustTotal = ref(0)

async function fetchOwnTeacherAdjustments() {
  try {
    const data = await api.get<{ items: any[]; total: number }>(
      `/teachers/me/adjustments?limit=${TEACHER_ADJUST_PAGE_SIZE}&offset=${teacherAdjustPage.value * TEACHER_ADJUST_PAGE_SIZE}`
    )
    const items = data.items.filter((item: any) => item.request_type !== 'TEACHER_SUBSTITUTION')
    teacherAdjustTotal.value = data.total
    const typeName: any = { TEACHER_ADJUSTMENT: '调课申请', LAB_CHANGE: '场地调整申请', TEACHER_SUBSTITUTION: '代课申请' }
    const statusName: any = { PENDING_REVIEW: '审核中', EXECUTED: '已通过', REJECTED: '已驳回', FAILED: '执行失败' }
    teacherOwnAdjustments.value = items.map((item: any) => {
      const src = item.source_info
      const tgt = item.target_info
      const projName = src?.project_name || ''
      const origText = src ? `${projName} · ${formatTime(src)}` : ''
      let targetText = ''
      if (item.request_type === 'TEACHER_ADJUSTMENT') {
        targetText = tgt ? `${tgt.project_name || projName} · ${formatTime(tgt)}` : ''
      } else if (item.request_type === 'LAB_CHANGE') {
        targetText = tgt?.laboratory_name || ''
      } else {
        targetText = tgt?.teacher_name || ''
      }
      return {
        id: item.request_no, type: typeName[item.request_type] || item.request_type,
        project: projName, original: origText, target: targetText,
        date: (item.submitted_at || '').slice(0, 10), status: statusName[item.status] || item.status, reason_text: item.reason,
      }
    })
  } catch { teacherOwnAdjustments.value = [] }
}

function changeTeacherAdjustPage(delta: number) {
  const maxPage = Math.max(0, Math.ceil(teacherAdjustTotal.value / TEACHER_ADJUST_PAGE_SIZE) - 1)
  const next = Math.min(maxPage, Math.max(0, teacherAdjustPage.value + delta))
  if (next === teacherAdjustPage.value) return
  teacherAdjustPage.value = next
  fetchOwnTeacherAdjustments()
}

async function fetchSubstitutionTasks() {
  try {
    const items = await api.get<any[]>('/teachers/me/substitution-tasks')
    substitutionTasks.value = items.filter((item: any) => item.task_status === 'PENDING').map((item: any) => ({
      id: item.id, requestNo: item.request_no, reason: item.reason, source: item.original_session || {},
    }))
  } catch { substitutionTasks.value = [] }
}

async function confirmSubstitutionTask(id: string, approved: boolean) {
  if (!window.confirm(approved ? '确认接受本次代课安排吗？' : '确认拒绝本次代课安排吗？')) return
  try {
    await api.post(`/teachers/me/substitution-tasks/${id}/confirm`, { approved, comment: '' })
    showToast(approved ? '已接受代课安排' : '已拒绝代课安排')
    await fetchSubstitutionTasks()
  } catch (e: any) { showToast(e?.message || '确认失败') }
}

async function fetchSubstitutionResults() {
  try {
    const data = await api.get<{ items: any[] }>('/teachers/me/adjustments')
    substitutionResults.value = data.items.filter((item: any) => item.request_type === 'TEACHER_SUBSTITUTION').map((item: any) => ({
      id: item.request_no,
      substituteTeacherName: item.target_info?.teacher_name || '',
      courseName: item.source_info?.project_name || '',
      status: item.status,
      date: (item.submitted_at || '').slice(0, 10),
      timeText: formatTime(item.source_info),
    }))
  } catch { substitutionResults.value = [] }
}

function formatTime(value: any) {
  if (!value) return ''
  const dayNames = ['', '周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return `第${value.week_no}周 ${dayNames[value.day_of_week] || ''} 第${value.start_slot}—${value.end_slot}节`
}
async function fetchResourceIssues() {
  try {
    const items = await api.get<any[]>('/teachers/me/resource-issues')
    const status: any = { PENDING_REVIEW: '待审核', PROCESSING: '检修中', SCRAP_REVIEW: '报废审批中', RESOLVED: '已解决', RELOCATION_REQUIRED: '待学生分流', READY_TO_EXECUTE: '待执行报废', SCRAPPED: '已报废', REJECTED: '已驳回' }
    const sev: any = { NORMAL: '一般', HIGH: '紧急', CRITICAL: '严重' }
    resourceRecords.value = items.map((item: any) => ({
      id: item.report_no, rawId: item.id, type: item.asset ? `${item.asset.equipment_name} · ${item.asset.instrument_no}` : (adjustmentContext.value.inventories.find((inventory: any) => inventory.id === item.inventory_id)?.equipment_name || '资源异常'),
      issueType: item.issue_type, rawStatus: item.status,
      equipmentName: item.asset?.equipment_name || '历史汇总资源', instrumentNo: item.asset?.instrument_no || '—',
      location: adjustmentContext.value.laboratories.find((lab: any) => lab.id === item.laboratory_id)?.name || '实验室',
      detail: item.description, level: sev[item.severity] || item.severity, date: (item.created_at || '').slice(0, 10),
      affected_quantity: item.affected_quantity, impact_end: item.impact_end ? (item.impact_end || '').slice(0, 10) : '',
      status: status[item.status] || item.status, approved_quantity: item.approved_quantity, restored_quantity: item.restored_quantity,
      pending_update: item.pending_update || null,
    }))
  } catch { resourceRecords.value = [] }
}
// 工单卡片友好化：预计完成倒计时 / 逾期判断 / 恢复进度
function recordRemainingText(record: any): string {
  if (!record.impact_end || record.rawStatus === 'RESOLVED' || record.rawStatus === 'SCRAPPED') return ''
  const end = new Date(`${record.impact_end}T00:00:00`).getTime()
  if (Number.isNaN(end)) return ''
  const days = Math.ceil((end - Date.now()) / 86400000)
  if (days > 0) return `距预计完成还有 ${days} 天`
  if (days === 0) return '今天预计完成'
  return `已逾期 ${-days} 天`
}
function recordOverdue(record: any): boolean {
  if (!record.impact_end || record.rawStatus === 'RESOLVED' || record.rawStatus === 'SCRAPPED') return false
  const end = new Date(`${record.impact_end}T00:00:00`).getTime()
  return !Number.isNaN(end) && end < Date.now()
}
function recordRestorePercent(record: any): string {
  const total = Number(record.approved_quantity || 0)
  const restored = Number(record.restored_quantity || 0)
  if (!total) return '0%'
  return `${Math.min(100, Math.round((restored / total) * 100))}%`
}

async function submitResourceReport() {
  if (!reportLocation.value || !reportAssetId.value || !reportDescription.value.trim()) return showToast('请选择实验室和具体仪器号，并填写说明')
  if (resourceAction.value === 'issue' && (!reportStart.value || !reportEnd.value)) return showToast('请填写预计检修周期')
  if (!window.confirm(resourceAction.value === 'scrap' ? `确认申请报废 ${selectedReportAsset.value?.instrument_no || ''} 吗？提交后仪器将立即隔离。` : '确认上报仪器故障吗？提交后仪器将立即隔离。')) return
  try {
    const severity: any = { '一般': 'NORMAL', '紧急': 'HIGH', '严重': 'CRITICAL' }
    if (resourceAction.value === 'scrap') {
      await api.post('/teachers/me/equipment-scrap-requests', { asset_id: reportAssetId.value, reason: reportDescription.value, severity: severity[reportLevel.value] || 'NORMAL' })
      showToast('报废申请已提交，仪器已隔离并等待管理员审批')
    } else {
      const result = await api.post<any>('/teachers/me/resource-issues', {
        laboratory_id: reportLocation.value, asset_id: reportAssetId.value,
        issue_type: 'EQUIPMENT_FAILURE', affected_quantity: 1,
        impact_start: new Date(reportStart.value).toISOString(), impact_end: new Date(reportEnd.value).toISOString(),
        severity: severity[reportLevel.value] || 'NORMAL', description: reportDescription.value,
      })
      showToast(result.deduplicated ? `该仪器已有工单 ${result.report_no}，本次说明已追加` : '仪器故障已上报，等待管理员审核')
    }
    reportDescription.value = ''; reportAssetId.value = ''
    await fetchResourceIssues(); await fetchAdjustmentContext()
  } catch (e: any) { showToast(e?.message || '上报失败') }
}

async function submitRepairProgress(record: any) {
  const remaining = Math.max(0, (record.approved_quantity || 0) - (record.restored_quantity || 0))
  if (!remaining) return showToast('该异常没有待恢复的停用数量')
  if (!window.confirm('确认该仪器已检修完成吗？管理员确认后将恢复为可用状态。')) return
  try {
    await api.post(`/teachers/me/resource-issues/${record.rawId}/repair-updates`, {
      update_type: 'COMPLETE_RESTORE', restored_quantity: 0, note: '单台仪器检修完成',
    })
    showToast('检修完成已报备，等待管理员确认')
    await fetchResourceIssues()
  } catch (e: any) { showToast(e?.message || '报备失败') }
}

function openRepairExtension(record: any) {
  const currentEnd = record.impact_end || new Date().toISOString().slice(0, 10)
  const nextDate = new Date(`${currentEnd}T12:00:00`)
  nextDate.setDate(nextDate.getDate() + 1)
  const minDate = nextDate.toISOString().slice(0, 10)
  repairExtensionDialog.value = { record, currentEnd, newEnd: minDate, minDate, note: '' }
}

async function submitRepairExtension() {
  const dialog = repairExtensionDialog.value
  if (!dialog) return
  if (!dialog.newEnd || dialog.newEnd <= dialog.currentEnd) return showToast('新的完成日期必须晚于当前预计完成日期')
  const proposed = new Date(`${dialog.newEnd}T23:59:59+08:00`)
  if (Number.isNaN(proposed.getTime())) return showToast('日期格式不正确')
  repairExtensionBusy.value = true
  try {
    await api.post(`/teachers/me/resource-issues/${dialog.record.rawId}/repair-updates`, {
      update_type: 'EXTEND_REPAIR', restored_quantity: 0, proposed_end_time: proposed.toISOString(),
      note: dialog.note.trim() || `申请将检修完成日期延期至 ${dialog.newEnd}`,
    })
    repairExtensionDialog.value = null
    showToast('检修延期已报备，等待管理员确认')
    await fetchResourceIssues()
  } catch (e: any) { showToast(e?.message || '延期报备失败') }
  finally { repairExtensionBusy.value = false }
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
      <button class="teacher-logout" type="button" @click="emit('logout')"><span>↪</span> 退出登录</button>
    </aside>
    <button v-if="sidebarOpen" class="teacher-sidebar-mask" aria-label="关闭导航" @click="sidebarOpen = false"></button>

    <div class="teacher-main">
      <header class="teacher-topbar">
        <button class="teacher-menu" type="button" aria-label="打开导航" @click="sidebarOpen = true">☰</button>
        <div class="teacher-breadcrumb"><span>教师端</span><b>/</b>{{ activeView === 'home' ? '首页' : viewMeta[activeView].title }}</div>
        <div class="teacher-top-actions">
          <NotificationBell fetch-path="/teachers/me/notifications" read-path="/teachers/me/notifications/read" :on-item-click="handleNotifItemClick" />
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
                <button type="button" @click="navigate('schedule'); timetableWeek = (task as any).weekStart || 1; fetchTimetable()">查看课表 →</button>
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
                <button type="button" @click="navigate('adjustments')"><span class="purple">⇄</span><div><strong>教学调整申请</strong><small>调课、代课申请</small></div><i>→</i></button>
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
            <button type="button" @click="openAdjustment('代课申请')"><span class="purple">Ⅱ</span><div><strong>代课申请</strong><small>因特殊情况暂停实验教学</small></div><i>→</i></button>
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
            <div class="adjustment-table-wrap">
              <table class="adjustment-table">
                <thead><tr><th>申请编号 / 类型</th><th>实验项目</th><th>原安排</th><th>目标安排</th><th>申请日期</th><th>状态</th></tr></thead>
                <tbody>
                <tr v-for="record in teacherOwnAdjustments" :key="record.id">
                  <td><b>{{ record.type }}</b></td>
                  <td>{{ record.project }}</td>
                  <td class="adjustment-schedule-cell">{{ record.original }}</td>
                  <td class="adjustment-schedule-cell">{{ record.target }}</td>
                  <td>{{ record.date }}</td>
                  <td><i class="teacher-status" :class="{ pending: record.status === '审核中', normal: record.status === '已通过', rejected: record.status === '已驳回' }">{{ record.status }}</i></td>
                </tr>
                </tbody>
              </table>
              <div v-if="teacherAdjustTotal > TEACHER_ADJUST_PAGE_SIZE" class="pagination-bar">
                <button type="button" :disabled="teacherAdjustPage === 0" @click="changeTeacherAdjustPage(-1)">‹ 上一页</button>
                <span>第 {{ teacherAdjustPage + 1 }} / {{ Math.max(1, Math.ceil(teacherAdjustTotal / TEACHER_ADJUST_PAGE_SIZE)) }} 页 · 共 {{ teacherAdjustTotal }} 条</span>
                <button type="button" :disabled="(teacherAdjustPage + 1) * TEACHER_ADJUST_PAGE_SIZE >= teacherAdjustTotal" @click="changeTeacherAdjustPage(1)">下一页 ›</button>
              </div>
            </div>
          </section>
          <section v-if="substitutionTasks.length" class="teacher-panel adjustment-records" style="margin-bottom:18px">
            <div class="teacher-panel-title"><div><h3>待确认代课</h3><p>其他教师申请由您代课，请确认是否接受</p></div></div>
            <article v-for="task in substitutionTasks" :key="task.id" class="adjustment-card" style="margin-bottom:10px">
              <div class="adjustment-card-header">
                <span class="adjustment-card-type">代课确认</span>
                <span class="adjustment-card-no">{{ task.requestNo }}</span>
              </div>
              <div class="adjustment-card-body">
                <div><span>原教师</span><strong>{{ task.source?.teacher_name || '—' }}</strong></div>
                <div><span>实验项目</span><strong>{{ task.source?.project_name || '—' }}</strong></div>
                <div><span>时间</span><strong>{{ task.source?.week_no ? '第'+task.source.week_no+'周' : '' }} 第{{ task.source?.start_slot || '—' }}—{{ task.source?.end_slot || '—' }}节</strong></div>
                <div><span>原因</span><strong>{{ task.reason || '—' }}</strong></div>
              </div>
              <div class="adjustment-card-actions">
                <button @click="confirmSubstitutionTask(task.id, false)" class="btn-reject">✕ 拒绝</button>
                <button @click="confirmSubstitutionTask(task.id, true)" class="btn-approve">✓ 接受</button>
              </div>
            </article>
          </section>
          <section v-if="substitutionResults.length" class="teacher-panel adjustment-records">
            <div class="teacher-panel-title"><div><h3>代课审批记录</h3><p>其他教师提交的代课申请中您作为代课教师的记录</p></div></div>
            <div class="adjustment-table-wrap">
              <table class="adjustment-table">
                <thead><tr><th>申请编号</th><th>实验项目</th><th>上课时间</th><th>代课教师</th><th>申请日期</th><th>审批状态</th></tr></thead>
                <tbody>
                <tr v-for="rec in substitutionResults" :key="rec.id">
                  <td><b>{{ rec.id }}</b></td>
                  <td>{{ rec.courseName }}</td>
                  <td>{{ rec.timeText || '—' }}</td>
                  <td>{{ rec.substituteTeacherName }}</td>
                  <td>{{ rec.date || '—' }}</td>
                  <td><i class="teacher-status" :class="{ normal: rec.status === 'EXECUTED', pending: rec.status === 'PENDING_REVIEW', rejected: rec.status === 'REJECTED' }">{{ rec.status === 'EXECUTED' ? '已通过' : rec.status === 'PENDING_REVIEW' ? '审核中' : rec.status === 'REJECTED' ? '已驳回' : rec.status }}</i></td>
                </tr>
                </tbody>
              </table>
            </div>
          </section>
          <section class="adjustment-notice"><span>!</span><p>教学调整会影响学生课表及实验室资源安排。当前原型申请不会提交，正式系统需经实验中心审核后生效。</p></section>
        </template>

        <template v-else>
          <div class="resource-layout">
            <form class="teacher-panel resource-form" @submit.prevent="submitResourceReport">
              <div class="teacher-panel-title resource-form-title"><div><h3>{{ resourceAction === 'scrap' ? '申请仪器报废' : '上报仪器故障' }}</h3><p>必须选择本人课程相关的具体仪器号</p></div><span class="required-note">* 为必填项</span></div>
              <div class="resource-action-tabs"><button type="button" :class="{ active: resourceAction === 'issue' }" @click="resourceAction = 'issue'">故障上报</button><button type="button" :class="{ active: resourceAction === 'scrap' }" @click="resourceAction = 'scrap'">报废申请</button></div>
              <div class="resource-form-grid">
                <label><span>异常实验室 *</span><select v-model="reportLocation" @change="reportEquipmentTypeId = ''; reportAssetId = ''"><option value="" disabled>请选择实验室</option><option v-for="lab in resourceLabs" :key="lab.id" :value="lab.id">{{ lab.name }}</option></select></label>
                <label><span>设备类型 *</span><select v-model="reportEquipmentTypeId" :disabled="!reportLocation" @change="reportAssetId = ''"><option value="" disabled>请选择设备类型</option><option v-for="item in reportEquipmentTypes" :key="item.equipment_type_id" :value="item.equipment_type_id">{{ item.equipment_name }}{{ item.model ? ` · ${item.model}` : '' }}</option></select></label>
                <label><span>仪器号 *</span><select v-model="reportAssetId" :disabled="!reportEquipmentTypeId"><option value="" disabled>请选择具体仪器号</option><option v-for="item in reportAssets" :key="item.id" :value="item.id">{{ item.instrument_no }}{{ item.active_issue ? `（${item.active_issue.report_no} · ${item.active_issue.status}）` : '' }}</option></select></label>
                <label><span>设备信息</span><input :value="selectedReportAsset ? `${selectedReportAsset.equipment_name} ${selectedReportAsset.model || ''}` : '选择编号后显示'" disabled /></label>
                <label><span>紧急程度</span><div class="level-options"><button v-for="level in ['一般','紧急','严重']" :key="level" type="button" :class="{ active: reportLevel === level }" @click="reportLevel = level">{{ level }}</button></div></label>
                <template v-if="resourceAction === 'issue'"><label><span>预计检修开始 *</span><input v-model="reportStart" type="datetime-local" /></label><label><span>预计检修完成 *</span><input v-model="reportEnd" type="datetime-local" /></label></template>
                <label class="resource-description"><span>{{ resourceAction === 'scrap' ? '报废原因' : '异常情况说明' }} *</span><textarea v-model="reportDescription" rows="5" :placeholder="resourceAction === 'scrap' ? '请说明无法继续使用或不具备维修价值的原因' : '请说明仪器异常现象、影响范围和初步处置情况'"></textarea></label>
              </div>
              <div class="resource-form-bottom"><p><i>i</i>提交后仪器立即隔离；报废影响学生时，管理员必须先完成分流。</p><button type="button" @click="reportDescription = ''; reportLocation = ''; reportEquipmentTypeId = ''; reportAssetId = ''">重置</button><button type="submit">{{ resourceAction === 'scrap' ? '提交报废申请' : '提交故障上报' }}</button></div>
            </form>

            <aside class="resource-side">
              <section class="teacher-panel resource-guide">
                <h3>上报指引</h3>
                <ol><li><span>1</span><div><strong>立即停止使用</strong><small>发现仪器或安全异常时先停止操作</small></div></li><li><span>2</span><div><strong>保留现场信息</strong><small>记录设备编号并拍摄异常状态</small></div></li><li><span>3</span><div><strong>准确描述影响</strong><small>说明是否影响当前或后续教学</small></div></li></ol>
                <div><span>紧急联系电话</span><strong>010-****-5678</strong><small>号码已脱敏 · 安全隐患请优先电话联系</small></div>
              </section>
            </aside>
          </div>
          <section class="teacher-panel resource-records">
            <div class="teacher-panel-title resource-record-title"><div><h3>我的资源上报</h3><p>故障工单与报废申请统一跟踪</p></div><span>共 {{ resourceRecordSummary.total }} 条</span></div>
            <div class="resource-record-summary">
              <article><small>全部记录</small><strong>{{ resourceRecordSummary.total }}</strong></article>
              <article class="pending"><small>等待处理</small><strong>{{ resourceRecordSummary.pending }}</strong></article>
              <article class="processing"><small>检修处理中</small><strong>{{ resourceRecordSummary.processing }}</strong></article>
              <article class="finished"><small>已结束</small><strong>{{ resourceRecordSummary.finished }}</strong></article>
            </div>
            <div class="resource-record-toolbar">
              <div><button type="button" :class="{ active: resourceRecordFilter === 'ALL' }" @click="resourceRecordFilter = 'ALL'">全部</button><button type="button" :class="{ active: resourceRecordFilter === 'EQUIPMENT_FAILURE' }" @click="resourceRecordFilter = 'EQUIPMENT_FAILURE'">故障上报</button><button type="button" :class="{ active: resourceRecordFilter === 'EQUIPMENT_SCRAP' }" @click="resourceRecordFilter = 'EQUIPMENT_SCRAP'">报废申请</button></div>
              <label><span>⌕</span><input v-model="resourceRecordKeyword" placeholder="搜索工单、仪器号或说明" /></label>
            </div>
            <div class="resource-record-list">
              <article v-for="record in filteredResourceRecords" :key="record.id" class="resource-record-card" :class="{ scrap: record.issueType === 'EQUIPMENT_SCRAP' }">
                <header><div><i>{{ record.issueType === 'EQUIPMENT_SCRAP' ? '报废' : '故障' }}</i><span><b>{{ record.id }}</b><small>{{ record.date }} 上报</small></span></div><em :class="record.rawStatus.toLowerCase()">{{ record.status }}</em></header>
                <div class="resource-record-main"><section><small>仪器设备</small><strong>{{ record.equipmentName }}</strong><span class="record-instrument-no">{{ record.instrumentNo }}</span></section><section><small>所在实验室</small><strong>{{ record.location }}</strong><span>影响 {{ record.affected_quantity }} 台 / 套</span></section><section><small>{{ record.issueType === 'EQUIPMENT_SCRAP' ? '申请原因' : '预计完成' }}</small><strong :class="{ overdue: recordOverdue(record) }">{{ record.issueType === 'EQUIPMENT_SCRAP' ? record.level : (record.impact_end || '待评估') }}</strong><template v-if="record.issueType === 'EQUIPMENT_SCRAP'"><span>管理员审批后执行</span></template><template v-else><span class="record-restore"><i class="record-restore-track"><b :style="{ width: recordRestorePercent(record) }"></b></i><em>恢复 {{ record.restored_quantity }}/{{ record.approved_quantity }} 台</em></span><small class="record-deadline" :class="{ overdue: recordOverdue(record) }">{{ recordRemainingText(record) }}</small></template></section></div>
                <p class="resource-record-description">{{ record.detail }}</p>
                <footer><div><i class="resource-level" :class="{ urgent: record.level === '紧急', severe: record.level === '严重' }">{{ record.level === '紧急' || record.level === '严重' ? '⚠ ' : '' }}{{ record.level }}</i><small v-if="record.pending_update" class="pending-update-badge">{{ record.pending_update[0].update_type === 'EXTEND_REPAIR' ? '延期报备待确认' : '修复完成待确认' }}</small></div><div v-if="record.rawStatus === 'PROCESSING' && !record.pending_update"><button type="button" class="primary" @click="submitRepairProgress(record)">确认检修完成</button><button type="button" @click="openRepairExtension(record)">延期检修</button></div></footer>
              </article>
              <div v-if="!filteredResourceRecords.length" class="resource-record-empty"><span>✓</span><strong>暂无符合条件的上报记录</strong><p>调整筛选条件，或在上方提交新的故障与报废申请。</p></div>
            </div>
          </section>
        </template>
      </main>
    </div>

    <Teleport to="body">
      <div v-if="repairExtensionDialog" class="teacher-dialog-backdrop" @click.self="repairExtensionDialog = null">
        <form class="teacher-dialog repair-extension-dialog" @submit.prevent="submitRepairExtension">
          <div class="teacher-dialog-title"><div><span>↗</span><div><h3>申请延期检修</h3><p>{{ repairExtensionDialog.record.id }} · {{ repairExtensionDialog.record.instrumentNo }}</p></div></div><button type="button" aria-label="关闭" @click="repairExtensionDialog = null">×</button></div>
          <div class="repair-extension-summary"><div><small>异常仪器</small><strong>{{ repairExtensionDialog.record.equipmentName }}</strong></div><div><small>当前预计完成</small><strong>{{ repairExtensionDialog.currentEnd }}</strong></div></div>
          <label><span>新的预计完成日期 *</span><input v-model="repairExtensionDialog.newEnd" type="date" :min="repairExtensionDialog.minDate" required /><small>新日期必须晚于当前预计完成日期。</small></label>
          <label><span>延期说明</span><textarea v-model.trim="repairExtensionDialog.note" rows="4" placeholder="请说明延期原因、当前检修进度和后续安排"></textarea></label>
          <div class="teacher-dialog-warning">提交后由管理员确认；管理员批准前，原预计完成日期保持不变。</div>
          <div class="teacher-dialog-actions"><button type="button" @click="repairExtensionDialog = null">取消</button><button type="submit" :disabled="repairExtensionBusy">{{ repairExtensionBusy ? '提交中…' : '确认提交延期' }}</button></div>
        </form>
      </div>
    </Teleport>

    <div v-if="adjustmentDialog" class="teacher-dialog-backdrop" @click.self="adjustmentDialog = null">
      <form class="teacher-dialog teacher-adjustment-dialog" @submit.prevent="submitAdjustment">
        <div class="teacher-dialog-title"><div><span>⇄</span><div><h3>{{ adjustmentDialog }}</h3><p>系统会先完成确定性校验，再进入相应审核流程</p></div></div><button type="button" @click="adjustmentDialog = null">×</button></div>
        <label>原实验场次<select v-model="adjustmentOriginalSessionId"><option value="" disabled>请选择尚未开始的场次</option><option v-for="item in adjustmentContext.sessions" :key="item.id" :value="item.id">{{ item.project_name }} · {{ formatTime(item) }} · {{ item.laboratory_name }}</option></select></label>
        <template v-if="adjustmentDialog === '调课申请'">
          <div class="teacher-ai-box">
            <strong>AI帮我推荐</strong><p>可输入"第8周以后，最好周三下午，不要晚上"等偏好；留空则推荐综合最优的三组方案。</p>
            <div><input v-model="adjustmentPreference" placeholder="描述你的调课偏好（可留空）" /><button type="button" :disabled="aiLoading" @click="askAiForReschedule">{{ aiLoading ? '推荐中…' : '生成方案' }}</button></div>
            <p v-if="aiAnswer" class="teacher-ai-answer">{{ aiAnswer }}</p>
            <button v-for="(option, index) in aiOptions" :key="index" type="button" class="teacher-ai-option" :class="{ conflict: option.affected_student_count }" @click="useAiOption(option)"><b>方案{{ index + 1 }}</b><span>{{ formatTime(option.target) }}</span><small>{{ option.affected_student_count ? `影响 ${option.affected_student_count} 名学生` : '无冲突' }}</small><i aria-hidden="true">›</i></button>
          </div>
          <div class="teacher-time-grid">
            <label>教学周<select v-model.number="adjustmentWeek"><option v-for="week in (adjustmentContext.term?.total_weeks || 18)" :key="week" :value="week">第{{ week }}周</option></select></label>
            <label>星期<select v-model.number="adjustmentDay"><option v-for="(name, index) in ['星期日','星期一','星期二','星期三','星期四','星期五','星期六']" :key="name" :value="index + 1">{{ name }}</option></select></label>
            <label>时段<select v-model.number="adjustmentStartSlot"><option :value="1">上午 1—4节</option><option :value="5">下午 5—8节</option><option :value="9">晚上 9—12节</option></select></label>
          </div>
        </template>
        <label v-else-if="adjustmentDialog === '场地调整申请'">目标实验室<select v-model="adjustmentLabId"><option value="" disabled>请选择目标实验室</option><option v-for="lab in adjustmentContext.laboratories" :key="lab.id" :value="lab.id">{{ lab.name }} · 容量 {{ lab.capacity }}</option></select></label>
        <label v-else>代课教师<select v-model="adjustmentTeacherId"><option value="" disabled>请选择代课教师</option><option v-for="teacher in adjustmentContext.substitute_teachers" :key="teacher.id" :value="teacher.id">{{ teacher.name }} · {{ teacher.department || '物理实验中心' }}</option></select></label>
        <button type="button" class="teacher-preview-button" @click="previewTeacherAdjustment">校验目标安排</button>
        <div v-if="adjustmentPreview" class="teacher-validation" :class="{ blocked: !adjustmentPreview.allowed }"><strong>{{ adjustmentPreview.allowed ? '校验通过' : adjustmentPreview.can_submit_for_review ? '可提交，但需要冲突安置' : '当前不能提交' }}</strong><p v-for="text in [...(adjustmentPreview.conflicts || []), ...(adjustmentPreview.warnings || [])]" :key="text">{{ text }}</p><small v-if="adjustmentPreview.affected_students?.length">受影响学生 {{ adjustmentPreview.affected_students.length }} 人，管理员审核时可查看具体名单和原因。</small></div>
        <label>申请原因<textarea v-model="adjustmentReason" rows="4" placeholder="请说明调整原因及对学生、场地的影响"></textarea></label>
        <div class="teacher-dialog-warning">调课和场地调整由管理员审核；代课申请需代课教师确认后再由管理员审核。</div>
        <div class="teacher-dialog-actions"><button type="button" @click="adjustmentDialog = null">取消</button><button type="submit" :disabled="submitBusy">{{ submitBusy ? '提交中…' : '确认提交申请' }}</button></div>
      </form>
    </div>

    <Transition name="toast"><div v-if="toast" class="teacher-toast" role="status"><span>✓</span>{{ toast }}</div></Transition>
  </div>
</template>

<style scoped>
.resource-table :deep(thead th) {
  color: #5d6f7e;
  background: #f0f3f6;
  font-weight: 600;
  font-size: 8px;
  border-bottom: 2px solid #dce3e8;
}
.resource-table :deep(tbody td) {
  text-align: center;
  font-size: 9px;
}
.pending-update-badge {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 4px;
  color: #fff;
  background: #e67e22;
  font-size: 8px;
  font-weight: 700;
  white-space: nowrap;
}
</style>
