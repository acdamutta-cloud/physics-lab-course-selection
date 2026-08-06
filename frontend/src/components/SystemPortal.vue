<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import type { UserProfile } from '../api/auth'

type SystemView = 'plans' | 'courses' | 'labs' | 'schedule' | 'approvals'

const props = defineProps<{ user: UserProfile | null }>()
const adminName = computed(() => props.user?.name || '系统管理员')
const adminLoginId = computed(() => props.user?.login_name || 'ADMIN-001')
const adminInitial = computed(() => adminName.value.slice(0, 1))
type ApprovalStatus = '待审批' | '已通过' | '已驳回' | '已取消'
type PlanCourseRequirement = {
  id: number
  courseId: string
  name: string
  studyYear: string
  semester: string
  prerequisites: string[]
  requiredCount: number
  optionalCount: number
  requiredProjects: string[]
  optionalProjects: string[]
  orderRule: string
  orderConstraints: string[]
  allowOrderOverride: boolean
}
type MajorInfo = { id: string; code: string; name: string }
type CourseInfo = { id: string; course_code: string; course_name: string; course_type: 'EXPERIMENT' | 'THEORY' }
type ProjectGroupMode = 'INDIVIDUAL' | 'GROUP'
type ProjectInfo = {
  id: string
  project_code: string
  project_name: string
  category: string | null
  required_slots: number
  group_mode: ProjectGroupMode
  default_group_size: number
  historical_selection_ratio: number
}
type PlanStatus = 'DRAFT' | 'PUBLISHED' | 'ARCHIVED'
type PlanCard = {
  id: string
  plan_code: string
  majorInfo: MajorInfo
  major: string
  year: string
  version: number
  courses: number
  required: number
  optional: number
  prerequisite: string
  updated: string
  rawStatus: PlanStatus
  status: '草稿' | '已发布' | '已停用'
  completeness: 'COMPLETE' | 'INCOMPLETE'
}
type PlanDetail = {
  id: string
  major: MajorInfo
  enrollment_year: number
  status: PlanStatus
  courses: Array<{
    id: string
    course: CourseInfo
    study_year: number
    semester_no: number
    prerequisite_course: CourseInfo | null
    prerequisite_courses: CourseInfo[]
    required_project_count: number
    optional_project_min_count: number
    order_rule_text: string | null
    allow_order_override: boolean
    projects: Array<{ project: ProjectInfo; requirement_type: 'REQUIRED' | 'OPTIONAL'; display_order: number }>
    order_constraints: Array<{ before_project: ProjectInfo; after_project: ProjectInfo; description: string | null; allow_override: boolean }>
  }>
}
type ScheduleSession = {
  id: string
  project_name: string
  week_no: number
  day_of_week: number
  start_slot: number
  end_slot: number
  teacher_name: string
  laboratory_name: string
  capacity: number
  selected_count: number
}
type ScheduleCandidate = {
  id: string
  version_no: number
  status: string
  profile_code: string
  hard_constraint_passed: boolean
  soft_score: number
  session_count: number
  runtime_weights: Record<string, number>
  score_details: {
    validation?: {
      soft_constraint_review?: {
        advantages: Array<{ rule_code: string; text: string; penalty: number }>
        tradeoffs: Array<{ rule_code: string; text: string; penalty: number }>
      }
    }
  }
  sessions: ScheduleSession[]
}
type ScheduleJob = {
  id: string
  status: string
  progress: number
  preference_text: string
  parsed_preferences: Array<{ rule_code: string; preference_level: string }>
  comparison_weights: Record<string, number>
  warnings: string[]
  selected_candidate_version_id: string | null
  candidates: ScheduleCandidate[]
}

const emit = defineEmits<{ logout: [] }>()
const activeView = ref<SystemView>('plans')
const sidebarOpen = ref(false)
const toast = ref('')
const selectedCampus = ref(localStorage.getItem('system_campus') || '主校区')
const campusOptions = ['主校区', '东校区']
watch(selectedCampus, (v) => localStorage.setItem('system_campus', v))
const planEditorOpen = ref(false)
const selectedPlan = ref('物理学（师范）')
const planMajor = ref('物理学（师范）')
const planYear = ref('2024')
const activePlanCourseId = ref(1)
const planCourseSeed = ref(3)
const customRequiredProject = ref('')
const customOptionalProject = ref('')
const selectedRequiredProject = ref('')
const selectedOptionalProject = ref('')
const planCourses = ref<PlanCourseRequirement[]>([])
const majors = ref<MajorInfo[]>([])
const courseCatalog = ref<CourseInfo[]>([])
const projectCatalog = ref<Record<string, ProjectInfo[]>>({})
const plans = ref<PlanCard[]>([])
const planDetails = ref<Record<string, PlanCourseRequirement[]>>({})
const currentPlanId = ref<string | null>(null)
const planKeyword = ref('')
const planYearFilter = ref('全部年份')
const plansLoading = ref(false)
const savingPlan = ref(false)
const projectEditorOpen = ref(false)
const pendingProjectKind = ref<'required' | 'optional'>('required')
const newProject = ref({
  project_code: '',
  project_name: '',
  category: '',
  required_slots: '',
  group_mode: 'INDIVIDUAL' as ProjectGroupMode,
  default_group_size: '',
  historical_selection_ratio: '',
})
const scheduleWeek = ref(1)

// 根据学期开学日期和选中周号计算具体日期范围
const weekDates = computed(() => {
  if (!termInfo.value || !termInfo.value.start_date) return ''
  const start = new Date(termInfo.value.start_date + 'T00:00:00')
  const dayOfWeek = start.getDay() // 0=Sunday
  // 找到第一个周日
  const sundayOffset = dayOfWeek === 0 ? 0 : (7 - dayOfWeek)
  const weekStart = new Date(start)
  weekStart.setDate(start.getDate() + sundayOffset + (scheduleWeek.value - 1) * 7)
  const weekEnd = new Date(weekStart)
  weekEnd.setDate(weekStart.getDate() + 6) // 周六
  const fmt = (d: Date) => `${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`
  return `${fmt(weekStart)} — ${fmt(weekEnd)}`
})

const weekDayHeaders = computed(() => {
  if (!termInfo.value || !termInfo.value.start_date) return ['周日','周一','周二','周三','周四','周五','周六']
  const start = new Date(termInfo.value.start_date + 'T00:00:00')
  const dayOfWeek = start.getDay()
  const sundayOffset = dayOfWeek === 0 ? 0 : (7 - dayOfWeek)
  const sunday = new Date(start)
  sunday.setDate(start.getDate() + sundayOffset + (scheduleWeek.value - 1) * 7)
  const days = ['日','一','二','三','四','五','六']
  return Array.from({length: 7}, (_, i) => {
    const d = new Date(sunday); d.setDate(sunday.getDate() + i)
    return `周${days[i]} ${String(d.getMonth()+1).padStart(2,'0')}/${String(d.getDate()).padStart(2,'0')}`
  })
})

const activeLab = ref('实验楼 A203')
const selectedLabIds = ref<Set<string>>(new Set())
const equipKeyword = ref('')
const scheduleLab = ref('实验楼 A203')
const aiGenerating = ref(false)
const aiGenerated = ref(false)
const aiPreference = ref('')
const aiJob = ref<ScheduleJob | null>(null)
const previewCandidateId = ref('')
const selectingCandidate = ref(false)
const publishingSchedule = ref(false)
const approvalFilter = ref('全部状态')
const selectedApprovalId = ref<string | null>(null)

const navItems: Array<{ id: SystemView; label: string; icon: string }> = [
  { id: 'plans', label: '培养方案管理', icon: '▤' },
  { id: 'courses', label: '实验课程设置', icon: '✦' },
  { id: 'labs', label: '实验室设备管理', icon: '⌂' },
  { id: 'schedule', label: '实验课表管理', icon: '▦' },
  { id: 'approvals', label: '申请审批管理', icon: '✓' },
]

const viewMeta: Record<SystemView, { title: string; subtitle: string }> = {
  plans: { title: '培养方案管理', subtitle: '按专业与培养年份维护物理实验课程修读要求' },
  courses: { title: '实验课程设置', subtitle: '配置本学期开课课程、项目容量、教师与实验器材' },
  labs: { title: '实验室设备管理', subtitle: '维护实验室容量、设备台账与可用状态' },
  schedule: { title: '实验课表管理', subtitle: '按实验室查看课表，或使用 AI 生成排课方案' },
  approvals: { title: '申请审批管理', subtitle: '统一处理学生与教师申请，查看审批方案和驳回理由' },
}

const semesterCourses = ref<Array<{
  _taskId: string
  _courseId: string
  _totalStudents: number
  name: string; code: string; target: string; weeks: string;
  projects: Array<{
    _demandId: string
    _projectId: string
    name: string
    groupMode: ProjectGroupMode
    groupSize: number
    expected: number
    teachers: string[]
    equipment: string[]
  }>
}>>([])

const termInfo = ref<{ id: string; academic_year: string; semester_no: number; start_date: string; end_date: string; total_weeks: number; current_week: number } | null>(null)

// ── 学期设置 ──
const termEditorOpen = ref(false)
const termEdit = ref({ academic_year: '', semester_no: 1, start_date: '', end_date: '', total_weeks: 18 })

function openTermEditor() {
  if (termInfo.value) {
    termEdit.value = {
      academic_year: termInfo.value.academic_year,
      semester_no: termInfo.value.semester_no,
      start_date: termInfo.value.start_date,
      end_date: termInfo.value.end_date,
      total_weeks: termInfo.value.total_weeks,
    }
  }
  termEditorOpen.value = true
}

async function saveTermSettings() {
  try {
    await api.put('/admin/active-term', termEdit.value)
    termEditorOpen.value = false
    showToast('学期设置已保存')
    await fetchSemesterCourses(true)
  } catch (err) { showToast(err instanceof Error ? err.message : '保存失败') }
}
const totalStudents = ref(0)

async function fetchSemesterCourses(syncAll = false) {
  try {
    if (syncAll) await api.post('/admin/teaching-tasks/sync-all', {})
    const [term, tasksRes, stuRes] = await Promise.all([
      api.get<{ id: string; academic_year: string; semester_no: number; start_date: string; end_date: string; total_weeks: number; current_week: number }>('/admin/active-term'),
      api.get<{ items: Array<{
        id: string; task_code: string
        course: { id: string; course_code: string; course_name: string }
        planned_student_count: number
        week_start: number; week_end: number
        cohorts: Array<{ major: { name: string }; enrollment_year: number; student_count: number }>
        demands: Array<{
          id: string
          project: ProjectInfo
          requirement_type: string
          required_capacity: number
          teachers: string[]
          equipment: string[]
        }>
      }>; total: number }>('/admin/teaching-tasks'),
      api.get<{ total: number }>('/admin/students/total'),
    ])
    termInfo.value = term
    totalStudents.value = stuRes.total
    semesterCourses.value = tasksRes.items.map(t => {
      const targetSet = [...new Set(t.cohorts.map(c => `${c.enrollment_year} 级${c.major.name}`))]
      return {
        _taskId: t.id,
        _courseId: t.course.id,
        _totalStudents: t.planned_student_count,
        name: t.course.course_name,
        code: t.course.course_code,
        target: targetSet.join('、') || '未配置',
        weeks: `第 ${t.week_start}–${t.week_end} 周`,
        projects: t.demands.map(d => ({
          _demandId: d.id,
          _projectId: d.project.id,
          name: d.project.project_name,
          groupMode: d.project.group_mode || 'INDIVIDUAL',
          groupSize: d.project.default_group_size || 1,
          expected: d.required_capacity,
          teachers: d.teachers || [],
          equipment: d.equipment || [],
        })),
      }
    })
  } catch { /* silently fail, keep empty state */ }
}

// ── 添加/编辑教学任务 ──
const addCourseDialogOpen = ref(false)
const selectedCourseId = ref('')
const newWeekStart = ref(2)
const newWeekEnd = ref(16)
const editTaskDialog = ref<{ id: string; code: string; name: string; weekStart: number; weekEnd: number } | null>(null)

const availableCourses = computed(() => {
  if (!courseCatalog.value.length || !semesterCourses.value.length) return courseCatalog.value
  const used = new Set(semesterCourses.value.map(c => c.code))
  return courseCatalog.value.filter(c => !used.has(c.course_code) && c.course_type === 'EXPERIMENT')
})

function openAddCourseDialog() {
  selectedCourseId.value = ''
  newWeekStart.value = 2
  newWeekEnd.value = 16
  addCourseDialogOpen.value = true
}

async function addTeachingTask() {
  if (!selectedCourseId.value) { showToast('请选择实验课程'); return }
  try {
    await api.post('/admin/teaching-tasks', { course_id: selectedCourseId.value, week_start: newWeekStart.value, week_end: newWeekEnd.value })
    addCourseDialogOpen.value = false
    showToast('教学任务已创建')
    await fetchSemesterCourses()
  } catch (err) { showToast(err instanceof Error ? err.message : '创建失败') }
}

function openEditTaskDialog(course: any) {
  const w = (course.weeks || '').match(/(\d+).*?(\d+)/)
  editTaskDialog.value = { id: course._taskId || '', code: course.code, name: course.name, weekStart: w ? +w[1] : 2, weekEnd: w ? +w[2] : 16 }
}

async function saveEditTask() {
  if (!editTaskDialog.value) return
  try {
    await api.put(`/admin/teaching-tasks/${editTaskDialog.value.id}`, { week_start: editTaskDialog.value.weekStart, week_end: editTaskDialog.value.weekEnd })
    editTaskDialog.value = null
    showToast('已更新')
    await fetchSemesterCourses()
  } catch (err) { showToast(err instanceof Error ? err.message : '更新失败') }
}

async function deleteTeachingTask(course: any) {
  if (!course._taskId) return
  if (!confirm(`确定删除"${course.name}"的教学任务吗？`)) return
  try {
    await api.delete(`/admin/teaching-tasks/${course._taskId}`)
    showToast('已删除')
    await fetchSemesterCourses()
  } catch (err) { showToast(err instanceof Error ? err.message : '删除失败') }
}

// ── 编辑项目需求 ──
const editProjectDialog = ref<{
  demandId: string
  projectId: string
  courseId: string
  name: string
  capacity: number
  taskId: string
  groupMode: ProjectGroupMode
  groupSize: number
} | null>(null)

function openEditProjectDialog(project: any, course: any) {
  editProjectDialog.value = {
    demandId: project._demandId,
    projectId: project._projectId,
    courseId: course._courseId,
    name: project.name,
    capacity: project.expected,
    taskId: course._taskId,
    groupMode: project.groupMode || 'INDIVIDUAL',
    groupSize: project.groupMode === 'GROUP' ? project.groupSize : 1,
  }
}

async function saveProjectDemand() {
  if (!editProjectDialog.value) return
  const dialog = editProjectDialog.value
  if (dialog.groupMode === 'GROUP' && dialog.groupSize < 2) {
    showToast('多人分组实验的每组人数至少为 2')
    return
  }
  try {
    const groupSize = dialog.groupMode === 'INDIVIDUAL' ? 1 : dialog.groupSize
    await api.put(
      `/admin/courses/${dialog.courseId}/projects/${dialog.projectId}/grouping`,
      { group_mode: dialog.groupMode, default_group_size: groupSize },
    )
    await api.put(
      `/admin/teaching-tasks/${dialog.taskId}/demands/${dialog.demandId}`,
      { required_capacity: dialog.capacity },
    )
    editProjectDialog.value = null
    showToast('项目实验形式和需求已更新')
    await fetchSemesterCourses()
  } catch (err) { showToast(err instanceof Error ? err.message : '更新失败') }
}

async function deleteProjectDemand(project: any, course: any) {
  if (!confirm(`确定删除项目"${project.name}"吗？`)) return
  try {
    await api.delete(`/admin/teaching-tasks/${course._taskId}/demands/${project._demandId}`)
    showToast('项目已删除')
    await fetchSemesterCourses()
  } catch (err) { showToast(err instanceof Error ? err.message : '删除失败') }
}

const addProjectDialog = ref<{
  course: any
  // new project fields
  project_code: string; project_name: string; category: string
  required_slots: number; group_mode: ProjectGroupMode
  default_group_size: number; historical_selection_ratio: number
  reqType: string
} | null>(null)

function openAddProjectDialog(course: any) {
  addProjectDialog.value = {
    course, reqType: 'REQUIRED',
    project_code: '', project_name: '', category: 'BASIC',
    required_slots: 4, group_mode: 'INDIVIDUAL',
    default_group_size: 1, historical_selection_ratio: 0.5,
  }
}

async function saveAddProject() {
  const d = addProjectDialog.value
  if (!d || !d.project_code || !d.project_name) { showToast('请填写项目编号和名称'); return }
  try {
    // 1. 找课程 ID
    const courseOpt = courseCatalog.value.find(c => c.course_code === d.course.code)
    if (!courseOpt) { showToast('未找到课程信息'); return }
    // 2. 创建项目
    const project = await api.post<{ id: string }>(`/admin/courses/${courseOpt.id}/projects`, {
      project_code: d.project_code,
      project_name: d.project_name,
      category: d.category,
      required_slots: d.required_slots,
      group_mode: d.group_mode,
      default_group_size: d.group_mode === 'INDIVIDUAL' ? 1 : d.default_group_size,
      historical_selection_ratio: d.historical_selection_ratio,
    })
    // 3. 加入教学任务
    await api.post(`/admin/teaching-tasks/${d.course._taskId}/demands`, { project_id: project.id, requirement_type: d.reqType })
    addProjectDialog.value = null
    showToast('项目已创建并添加')
    await fetchSemesterCourses()
  } catch (err) { showToast(err instanceof Error ? err.message : '添加失败') }
}

const labs = ref<Array<{
  id: string; name: string; room_type: string; safety_capacity: number; status: string
  equipment: Array<{ id: string; equipment_name: string; model: string; total_quantity: number; usable_quantity: number; note?: string }>
}>>([])

async function fetchLabs() {
  try {
    labs.value = await api.get('/admin/labs')
    if (labs.value.length && !labs.value.some(lab => lab.name === scheduleLab.value)) {
      scheduleLab.value = labs.value[0].name
      activeLab.value = labs.value[0].name
    }
  } catch { /* keep empty */ }
}

// ── 添加实验室 ──
const addLabDialogOpen = ref(false)
const newLab = ref({ name: '', safety_capacity: 24 })
const newLabEquip = ref<Array<{ name: string; model: string; total: number; usable: number; note: string }>>([])

function openAddLabDialog() {
  newLab.value = { name: '', safety_capacity: 24 }
  newLabEquip.value = []
  addLabDialogOpen.value = true
}

function addEquipRow() {
  newLabEquip.value.push({ name: '', model: '', total: 1, usable: 1, note: '' })
}

function removeEquipRow(idx: number) {
  newLabEquip.value.splice(idx, 1)
}

function toggleLabSelect(labId: string) {
  const s = selectedLabIds.value
  if (s.has(labId)) { s.delete(labId) } else { s.add(labId) }
  selectedLabIds.value = new Set(s)
}
function toggleAllLabs() {
  if (selectedLabIds.value.size === labs.value.length) {
    selectedLabIds.value = new Set()
  } else {
    selectedLabIds.value = new Set(labs.value.map(l => l.id))
  }
}
async function batchDeleteLabs() {
  if (selectedLabIds.value.size === 0) { showToast('请先选择实验室'); return }
  if (!confirm(`确定删除 ${selectedLabIds.value.size} 间实验室吗？`)) return
  try {
    for (const id of selectedLabIds.value) {
      await api.delete(`/admin/labs/${id}`)
    }
    selectedLabIds.value = new Set()
    showToast('已删除')
    await fetchLabs()
  } catch (err) { showToast(err instanceof Error ? err.message : '删除失败') }
}

async function scrapEquip(lab: any, equip: any) {
  const n = prompt(`报废数量（当前可用 ${equip.usable_quantity} 台）：`, '1')
  if (!n || isNaN(+n) || +n <= 0) return
  const qty = Math.min(+n, equip.usable_quantity)
  if (!confirm(`确认报废 ${qty} 台"${equip.equipment_name}"？`)) return
  try {
    await api.put(`/admin/labs/${lab.id}/equipment/${equip.id}`, {
      usable_quantity: equip.usable_quantity - qty,
      total_quantity: equip.total_quantity - qty,
    })
    showToast(`已报废 ${qty} 台`)
    await fetchLabs()
  } catch (err) { showToast(err instanceof Error ? err.message : '操作失败') }
}

async function addEquip(lab: any, equip: any) {
  const n = prompt(`新增数量（当前账面 ${equip.total_quantity} 台）：`, '1')
  if (!n || isNaN(+n) || +n <= 0) return
  const qty = +n
  try {
    await api.put(`/admin/labs/${lab.id}/equipment/${equip.id}`, {
      usable_quantity: equip.usable_quantity + qty,
      total_quantity: equip.total_quantity + qty,
    })
    showToast(`已新增 ${qty} 台`)
    await fetchLabs()
  } catch (err) { showToast(err instanceof Error ? err.message : '操作失败') }
}

async function deleteEquipment(lab: any, equip: any) {
  if (!confirm(`确定删除"${equip.equipment_name}"吗？`)) return
  try {
    await api.delete(`/admin/labs/${lab.id}/equipment/${equip.id}`)
    showToast('设备已删除')
    await fetchLabs()
  } catch (err) { showToast(err instanceof Error ? err.message : '删除失败') }
}

async function saveNewLab() {
  if (!newLab.value.name) { showToast('请填写实验室名称'); return }
  try {
    await api.post('/admin/labs/batch-create', {
      name: newLab.value.name,
      safety_capacity: newLab.value.safety_capacity,
      equipment: newLabEquip.value.map(e => ({
        name: e.name, model: e.model,
        total_quantity: e.total, usable_quantity: e.usable,
      })),
    })
    addLabDialogOpen.value = false
    showToast('实验室已创建')
    await fetchLabs()
  } catch (err) { showToast(err instanceof Error ? err.message : '创建失败') }
}

const notifications = ref<Array<{ request_no: string; student_name: string; type: string; time: string }>>([])
const showNotifications = ref(false)
const unreadCount = computed(() => notifications.value.length)

async function fetchNotifications() {
  try { notifications.value = await api.get<any[]>('/admin/notifications') } catch { notifications.value = [] }
}
async function readOne(idx: number, val: string) {
  notifications.value.splice(idx, 1)
  await api.post('/admin/notifications/read', { value: val }).catch(() => {})
}

const approvals = ref<Array<{ id: string; rawId: string; source: string; applicant: string; type: string; subject: string; submitted: string; status: ApprovalStatus; result: string; reason_text: string; source_info: Record<string,any>; target_info: Record<string,any> }>>([])
const rejectReason = ref('')
function formatAdjustSession(info: any) {
  if (!info) return '—'
  const dayNames = ['','周日','周一','周二','周三','周四','周五','周六']
  const w = info.week_no ? `第${info.week_no}周` : ''
  const d = info.day_of_week ? dayNames[info.day_of_week] : ''
  const s = info.start_slot ? `第${info.start_slot}–${info.end_slot || info.start_slot}节` : ''
  const p = info.project_name || ''
  return [w, d, s, p].filter(Boolean).join(' · ') || '—'
}

const rejectDialogOpen = ref(false)
const rejectTargetId = ref('')

async function fetchApprovals() {
  try {
    const items = await api.get<any[]>('/admin/adjustments')
    const statusMap: Record<string, ApprovalStatus> = {
      PENDING_REVIEW: '待审批', EXECUTED: '已通过', APPROVED: '已通过',
      REJECTED: '已驳回', CANCELLED: '已取消', AUTO_EXECUTED: '已通过',
    }
    approvals.value = items.map((item: any) => ({
      id: item.request_no,
      rawId: item.id,
      source: '学生申请',
      applicant: item.student_name || '',
      type: item.request_type === 'RESCHEDULE' ? '调课申请' : item.request_type === 'PROJECT_CHANGE' ? '换组申请' : '补做申请',
      subject: item.project_name || '',
      submitted: (item.created_at || '').slice(0, 16).replace('T', ' '),
      status: statusMap[item.status] || item.status,
      result: item.status === 'REJECTED' ? (item.review_comment || `驳回 · ${item.reviewed_at?.slice(0, 10) || ''}`) : item.executed_at ? `已于 ${item.executed_at.slice(0, 10)} 执行` : item.status === 'CANCELLED' ? '已取消' : '等待审核',
      reason_text: item.reason_text || '',
      source_info: item.source_info || {},
      target_info: item.target_info || {},
    }))
  } catch { /* keep mock */ }
}

async function approveApplication(id: string) {
  try {
    await api.post(`/admin/adjustments/${id}/review`, { decision: 'APPROVED', comment: '' })
    showToast('已通过')
    selectedApprovalId.value = null
    await fetchApprovals()
  } catch (e: any) { showToast(e?.message || '操作失败') }
}
function openReject(id: string) {
  rejectTargetId.value = id
  rejectReason.value = ''
  rejectDialogOpen.value = true
}
async function confirmReject() {
  if (!rejectReason.value.trim()) return showToast('请填写驳回理由')
  try {
    await api.post(`/admin/adjustments/${rejectTargetId.value}/review`, { decision: 'REJECTED', comment: rejectReason.value.trim() })
    showToast('已驳回')
    rejectDialogOpen.value = false
    selectedApprovalId.value = null
    await fetchApprovals()
  } catch (e: any) { showToast(e?.message || '操作失败') }
}

const currentLab = computed(() => labs.value.find((lab) => lab.name === activeLab.value) ?? labs.value[0])
const filteredEquipment = computed(() => {
  if (!currentLab.value) return []
  const kw = equipKeyword.value.trim().toLowerCase()
  if (!kw) return currentLab.value.equipment
  return currentLab.value.equipment.filter(e =>
    e.equipment_name.toLowerCase().includes(kw) || e.model.toLowerCase().includes(kw)
  )
})
const activePlanCourse = computed(() => planCourses.value.find((course) => course.id === activePlanCourseId.value) ?? planCourses.value[0])
const activeProjectCatalog = computed(() => {
  const course = activePlanCourse.value
  if (!course) return []
  return (projectCatalog.value[course.courseId] ?? []).map((project) => project.project_name)
})
const summary = computed(() => ({
  plans: plans.value.length,
  majors: new Set(plans.value.map((item) => item.majorInfo.id)).size,
  published: plans.value.filter((item) => item.rawStatus === 'PUBLISHED').length,
  courses: new Set(Object.values(planDetails.value).flat().map((course) => course.courseId).filter(Boolean)).size,
  projects: new Set(
    Object.entries(projectCatalog.value)
      .filter(([courseId]) => courseCatalog.value.find((item) => item.id === courseId)?.course_type === 'EXPERIMENT')
      .flatMap(([, projects]) => projects.map((project) => project.id)),
  ).size,
  incomplete: plans.value.filter((item) => item.completeness === 'INCOMPLETE').length,
}))
const planYears = computed(() => [...new Set(plans.value.map((item) => item.year))].sort().reverse())
const enrollmentYearOptions = computed(() => Array.from(
  { length: new Date().getFullYear() - 1998 },
  (_, index) => new Date().getFullYear() + 1 - index,
))
const activeCandidate = computed(() =>
  aiJob.value?.candidates.find(candidate => candidate.id === previewCandidateId.value) ?? null,
)
const visibleScheduleEvents = computed(() =>
  (activeCandidate.value?.sessions ?? [])
    .filter(session => session.laboratory_name === scheduleLab.value && session.week_no === scheduleWeek.value)
    .map((session, index) => ({
      id: session.id,
      day: session.day_of_week,
      start: session.start_slot,
      span: session.end_slot - session.start_slot + 1,
      name: session.project_name,
      teacher: session.teacher_name,
      selected: session.selected_count,
      capacity: session.capacity,
      tone: ['teal', 'blue', 'purple', 'ai'][index % 4],
    })),
)
const visibleApprovals = computed(() => approvals.value.filter((item) => approvalFilter.value === '全部状态' || item.status === approvalFilter.value))
const selectedApproval = computed(() => approvals.value.find((item) => item.id === selectedApprovalId.value) ?? null)

function navigate(view: SystemView) {
  activeView.value = view
  sidebarOpen.value = false
}

function showToast(text: string) {
  toast.value = text
  window.setTimeout(() => {
    if (toast.value === text) toast.value = ''
  }, 2800)
}

function statusLabel(status: PlanStatus): PlanCard['status'] {
  return status === 'PUBLISHED' ? '已发布' : status === 'ARCHIVED' ? '已停用' : '草稿'
}

function formatDate(value: string) {
  return new Date(value).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).replace(/\//g, '-')
}

function normalizeCourse(course: CourseInfo): CourseInfo {
  return {
    ...course,
    course_type: course.course_type ||
      (course.course_code.startsWith('DEMO-TH-') ? 'THEORY' : 'EXPERIMENT'),
  }
}

function courseDetailsForPlan(plan: PlanCard) {
  return planDetails.value[plan.id] ?? []
}

function blankPlanCourse(): PlanCourseRequirement {
  return {
    id: planCourseSeed.value++,
    courseId: '',
    name: '',
    studyYear: '第 1 学年',
    semester: '第一学期',
    prerequisites: [],
    requiredCount: 0,
    optionalCount: 0,
    requiredProjects: [],
    optionalProjects: [],
    orderRule: '',
    orderConstraints: [],
    allowOrderOverride: false,
  }
}

async function loadLookups() {
  const [majorItems, courseItems] = await Promise.all([
    api.get<MajorInfo[]>('/admin/majors'),
    api.get<CourseInfo[]>('/admin/courses'),
  ])
  majors.value = majorItems
  courseCatalog.value = courseItems.map(normalizeCourse)
  if (!planMajor.value || !majorItems.some((item) => item.name === planMajor.value)) {
    planMajor.value = majorItems[0]?.name ?? ''
  }
  await Promise.all(courseCatalog.value.map(async (course) => {
    projectCatalog.value[course.id] = await api.get<ProjectInfo[]>(`/admin/courses/${course.id}/projects`)
  }))
}

async function loadPlans() {
  plansLoading.value = true
  try {
    const query = new URLSearchParams({ limit: '200' })
    const selectedMajor = majors.value.find((item) => item.name === selectedPlan.value)
    if (selectedMajor) query.set('major_id', selectedMajor.id)
    if (planYearFilter.value !== '全部年份') query.set('enrollment_year', planYearFilter.value)
    if (planKeyword.value.trim()) query.set('keyword', planKeyword.value.trim())
    const response = await api.get<{ items: Array<{
      id: string; plan_code: string; major: MajorInfo; enrollment_year: number; version_no: number
      status: PlanStatus; updated_at: string; courses_count: number; required_projects_count: number
      optional_projects_count: number; prerequisite_names: string[]; completeness: 'COMPLETE' | 'INCOMPLETE'
      courses: PlanDetail['courses']
    }>; total: number }>(`/training-plans?${query}`)
    const discoveredCourses = new Map(
      courseCatalog.value.map((course) => [course.id, course]),
    )
    for (const plan of response.items) {
      for (const planCourse of plan.courses ?? []) {
        discoveredCourses.set(
          planCourse.course.id,
          normalizeCourse(planCourse.course),
        )
        for (const prerequisite of planCourse.prerequisite_courses ?? []) {
          discoveredCourses.set(prerequisite.id, normalizeCourse(prerequisite))
        }
        const existingProjects = new Map(
          (projectCatalog.value[planCourse.course.id] ?? [])
            .map((project) => [project.id, project]),
        )
        for (const item of planCourse.projects ?? []) {
          existingProjects.set(item.project.id, item.project)
        }
        projectCatalog.value[planCourse.course.id] = [...existingProjects.values()]
      }
    }
    courseCatalog.value = [...discoveredCourses.values()]
      .sort((left, right) => left.course_name.localeCompare(right.course_name, 'zh-CN'))
    plans.value = response.items.map((item) => ({
      id: item.id,
      plan_code: item.plan_code,
      majorInfo: item.major,
      major: item.major.name,
      year: String(item.enrollment_year),
      version: item.version_no,
      courses: item.courses_count,
      required: item.required_projects_count,
      optional: item.optional_projects_count,
      prerequisite: item.prerequisite_names.join('、') || '无先修要求',
      updated: formatDate(item.updated_at),
      rawStatus: item.status,
      status: statusLabel(item.status),
      completeness: item.completeness,
    }))
    planDetails.value = Object.fromEntries(
      response.items.map((item) => [
        item.id,
        mapDetailCourses({
          id: item.id,
          major: item.major,
          enrollment_year: item.enrollment_year,
          status: item.status,
          courses: item.courses,
        }),
      ]),
    )
  } catch (error) {
    showToast(error instanceof Error ? error.message : '培养方案加载失败')
  } finally {
    plansLoading.value = false
  }
}

function mapDetailCourses(detail: PlanDetail): PlanCourseRequirement[] {
  return detail.courses.map((course) => ({
    id: planCourseSeed.value++,
    courseId: course.course.id,
    name: course.course.course_name,
    studyYear: `第 ${course.study_year} 学年`,
    semester: course.semester_no === 1 ? '第一学期' : course.semester_no === 2 ? '第二学期' : '小学期',
    prerequisites: (course.prerequisite_courses?.length ? course.prerequisite_courses : (course.prerequisite_course ? [course.prerequisite_course] : [])).map((item) => item.id),
    requiredCount: course.required_project_count,
    optionalCount: course.optional_project_min_count,
    requiredProjects: course.projects.filter((item) => item.requirement_type === 'REQUIRED').map((item) => item.project.project_name),
    optionalProjects: course.projects.filter((item) => item.requirement_type === 'OPTIONAL').map((item) => item.project.project_name),
    orderRule: course.order_rule_text ?? '',
    orderConstraints: (course.order_constraints ?? []).map((item) => item.description || `${item.before_project.project_name} → ${item.after_project.project_name}`),
    allowOrderOverride: course.allow_order_override,
  }))
}

async function loadPlanDetail(planId: string) {
  const detail = await api.get<PlanDetail>(`/training-plans/${planId}`)
  const courses = mapDetailCourses(detail)
  planDetails.value[planId] = courses
  return { detail, courses }
}

function openNewPlan() {
  currentPlanId.value = null
  planYear.value = String(new Date().getFullYear())
  planCourses.value = [blankPlanCourse()]
  activePlanCourseId.value = planCourses.value[0].id
  planEditorOpen.value = true
}

async function editPlan(plan: PlanCard) {
  try {
    let detail: PlanDetail
    if (plan.rawStatus === 'DRAFT') {
      detail = (await loadPlanDetail(plan.id)).detail
    } else {
      detail = await api.post<PlanDetail>(`/training-plans/${plan.id}/draft-copy`, {})
      await loadPlans()
    }
    currentPlanId.value = detail.id
    planMajor.value = detail.major.name
    planYear.value = String(detail.enrollment_year)
    planCourses.value = mapDetailCourses(detail)
    activePlanCourseId.value = planCourses.value[0]?.id ?? 0
    planEditorOpen.value = true
  } catch (error) {
    showToast(error instanceof Error ? error.message : '培养方案编辑失败')
  }
}

function toggleListItem(which: 'required' | 'optional', item: string) {
  const course = activePlanCourse.value
  if (!course) return
  const list = which === 'required' ? course.requiredProjects : course.optionalProjects
  const isRemoving = list.includes(item)
  const next = isRemoving ? list.filter((value) => value !== item) : [...list, item]
  if (which === 'required') {
    course.requiredProjects = next
    if (!isRemoving) course.optionalProjects = course.optionalProjects.filter((value) => value !== item)
  } else {
    course.optionalProjects = next
    if (!isRemoving) course.requiredProjects = course.requiredProjects.filter((value) => value !== item)
  }
}

function addCustomProject(which: 'required' | 'optional') {
  const input = which === 'required' ? customRequiredProject : customOptionalProject
  const name = input.value.trim()
  if (!name) {
    showToast('请先输入实验项目名称')
    return
  }
  const course = activePlanCourse.value
  if (!course) return
  if (!course.courseId) {
    showToast('请先选择实验课程')
    return
  }
  const existing = (projectCatalog.value[course.courseId] ?? []).find((item) => item.project_name === name)
  if (existing) {
    toggleListItem(which, existing.project_name)
    input.value = ''
    return
  }
  pendingProjectKind.value = which
  newProject.value = {
    project_code: '',
    project_name: name,
    category: '',
    required_slots: '',
    group_mode: 'INDIVIDUAL' as ProjectGroupMode,
    default_group_size: '',
    historical_selection_ratio: '',
  }
  projectEditorOpen.value = true
}

function addPlanCourse() {
  const course = blankPlanCourse()
  planCourses.value.push(course)
  activePlanCourseId.value = course.id
}

function removePlanCourse(id: number) {
  if (planCourses.value.length <= 1) {
    showToast('培养方案至少需要保留一门修读课程')
    return
  }
  planCourses.value = planCourses.value.filter((course) => course.id !== id)
  activePlanCourseId.value = planCourses.value[0].id
}

function syncCourseSelection(course: PlanCourseRequirement) {
  const selected = courseCatalog.value.find((item) => item.id === course.courseId)
  course.name = selected?.course_name ?? ''
  course.requiredProjects = []
  course.optionalProjects = []
  selectedRequiredProject.value = ''
  selectedOptionalProject.value = ''
}

function experimentCourseOptions(course: PlanCourseRequirement) {
  const selectedByOtherRows = new Set(
    planCourses.value
      .filter((item) => item.id !== course.id)
      .map((item) => item.courseId)
      .filter(Boolean),
  )
  return courseCatalog.value
    .filter((item) =>
      item.course_type === 'EXPERIMENT' &&
      (!selectedByOtherRows.has(item.id) || item.id === course.courseId),
    )
    .sort((left, right) => left.course_name.localeCompare(right.course_name, 'zh-CN'))
}

function availableProjectOptions(which: 'required' | 'optional') {
  const course = activePlanCourse.value
  if (!course) return []
  const selectedProjects = new Set(which === 'required'
    ? [...course.requiredProjects, ...course.optionalProjects]
    : [...course.optionalProjects, ...course.requiredProjects])
  return activeProjectCatalog.value.filter((item) => !selectedProjects.has(item))
}

function addSelectedProject(which: 'required' | 'optional') {
  const selection = which === 'required'
    ? selectedRequiredProject
    : selectedOptionalProject
  if (!selection.value) {
    showToast('请先从当前课程的项目库中选择实验项目')
    return
  }
  toggleListItem(which, selection.value)
  selection.value = ''
}

function displayPrerequisites(course: PlanCourseRequirement) {
  const names = course.prerequisites
    .map((id) => courseCatalog.value.find((item) => item.id === id)?.course_name)
    .filter((item): item is string => Boolean(item))
  return names.length ? names.join('、') : '无先修要求'
}

function displayOrderConstraints(course: PlanCourseRequirement) {
  const source = course.orderConstraints.length
    ? course.orderConstraints
    : (course.orderRule ?? '').split(/[；;]/u)
  const constraints = source
    .map((item) => item.trim().replace(/[。；;]+$/u, ''))
    .filter(Boolean)
  if (constraints.length) return `${constraints.join('；')}。`
  return '尚未设置项目顺序要求'
}

function addPrerequisite(course: PlanCourseRequirement) {
  course.prerequisites.push('')
}

function removePrerequisite(course: PlanCourseRequirement, index: number) {
  course.prerequisites.splice(index, 1)
}

function prerequisiteOptions(course: PlanCourseRequirement, index: number) {
  return courseCatalog.value
    .filter((item) =>
      item.id !== course.courseId &&
      (!course.prerequisites.includes(item.id) || course.prerequisites[index] === item.id),
    )
    .sort((left, right) => {
      if (left.course_type !== right.course_type) return left.course_type === 'THEORY' ? -1 : 1
      return left.course_name.localeCompare(right.course_name, 'zh-CN')
    })
}

async function savePlanDraft() {
  if (!planMajor.value || !planYear.value || planCourses.value.some((course) => !course.name || !course.studyYear || !course.semester)) {
    showToast('请完整填写专业、培养年份及每门课程的修读学年和学期')
    return
  }
  const major = majors.value.find((item) => item.name === planMajor.value)
  if (!major) return showToast('请选择有效专业')
  try {
    savingPlan.value = true
    const payload = {
      major_id: major.id,
      enrollment_year: Number(planYear.value),
      effective_from: null,
      courses: planCourses.value.map((course) => {
        const catalog = projectCatalog.value[course.courseId] ?? []
        const prerequisiteIds = course.prerequisites.filter(Boolean)
        return {
          course_id: course.courseId,
          course_nature: 'REQUIRED',
          study_year: Number(course.studyYear.match(/\d+/)?.[0] ?? 1),
          semester_no: course.semester === '第一学期' ? 1 : course.semester === '第二学期' ? 2 : 3,
          prerequisite_course_ids: prerequisiteIds,
          required_project_count: course.requiredCount,
          optional_project_min_count: course.optionalCount,
          order_rule_text: course.orderRule || null,
          allow_order_override: course.allowOrderOverride,
          projects: [
            ...course.requiredProjects.map((name, index) => ({ project_id: catalog.find((item) => item.project_name === name)?.id, requirement_type: 'REQUIRED', display_order: index + 1 })),
            ...course.optionalProjects.map((name, index) => ({ project_id: catalog.find((item) => item.project_name === name)?.id, requirement_type: 'OPTIONAL', display_order: course.requiredProjects.length + index + 1 })),
          ],
        }
      }),
    }
    if (payload.courses.some((course) => !course.course_id || course.projects.some((item) => !item.project_id) || course.prerequisite_course_ids.some((item) => !item))) {
      throw new Error('课程、项目或先修课程必须从后台基础资料中选择')
    }
    const saved = currentPlanId.value
      ? await api.put<PlanDetail>(`/training-plans/${currentPlanId.value}`, payload)
      : await api.post<PlanDetail>('/training-plans', payload)
    currentPlanId.value = saved.id
    planEditorOpen.value = false
    await loadPlans()
    showToast('培养方案草稿已保存')
  } catch (error) {
    showToast(error instanceof Error ? error.message : '培养方案保存失败')
  } finally {
    savingPlan.value = false
  }
}

async function submitNewProject() {
  const course = activePlanCourse.value
  if (!course?.courseId) return
  const item = newProject.value
  if (!item.project_code || !item.project_name || !item.category || !item.required_slots || (item.group_mode === 'GROUP' && !item.default_group_size) || item.historical_selection_ratio === '') {
    showToast('请完整填写实验项目资料')
    return
  }
  try {
    const created = await api.post<ProjectInfo>(`/admin/courses/${course.courseId}/projects`, {
      project_code: item.project_code,
      project_name: item.project_name,
      category: item.category,
      required_slots: Number(item.required_slots),
      group_mode: item.group_mode,
      default_group_size: item.group_mode === 'INDIVIDUAL' ? 1 : Number(item.default_group_size),
      historical_selection_ratio: Number(item.historical_selection_ratio),
    })
    projectCatalog.value[course.courseId] = [...(projectCatalog.value[course.courseId] ?? []), created]
    toggleListItem(pendingProjectKind.value, created.project_name)
    customRequiredProject.value = ''
    customOptionalProject.value = ''
    projectEditorOpen.value = false
    showToast('实验项目已创建并加入当前培养方案')
  } catch (error) {
    showToast(error instanceof Error ? error.message : '实验项目创建失败')
  }
}

async function handlePlanStatus(plan: PlanCard) {
  if (plan.rawStatus === 'ARCHIVED') return showToast('该培养方案已经归档')
  const action = plan.rawStatus === 'DRAFT' ? '发布' : '归档'
  if (!window.confirm(`确认${action}"${plan.major} ${plan.year} 级 V${plan.version}"培养方案？`)) return
  try {
    await api.post(`/training-plans/${plan.id}/${plan.rawStatus === 'DRAFT' ? 'publish' : 'archive'}`, {})
    await loadPlans()
    showToast(`培养方案已${action}`)
  } catch (error) {
    showToast(error instanceof Error ? error.message : `培养方案${action}失败`)
  }
}

async function deletePlan(plan: PlanCard) {
  if (!window.confirm(`确认删除"${plan.major} ${plan.year} 级 V${plan.version}"培养方案？删除后将归档保留审计记录，不会物理删除数据。`)) return
  try {
    await api.post(`/training-plans/${plan.id}/archive`, {})
    await loadPlans()
    showToast('培养方案已删除')
  } catch (error) {
    showToast(error instanceof Error ? error.message : '培养方案删除失败')
  }
}

let filterTimer: number | undefined
watch([selectedPlan, planYearFilter, planKeyword], () => {
  window.clearTimeout(filterTimer)
  filterTimer = window.setTimeout(loadPlans, 250)
})

async function loadPublishedSchedule() {
  if (!termInfo.value?.id) return
  try {
    const result = await api.get<ScheduleJob>(
      `/schedule-jobs/published?term_id=${termInfo.value.id}`,
    )
    const published = result.candidates.find(
      candidate => candidate.status === 'PUBLISHED',
    )
    if (!published) return
    aiJob.value = result
    previewCandidateId.value = published.id
    aiGenerated.value = false
  } catch {
    // 当前学期没有正式课表时保持空白视图。
  }
}

onMounted(async () => {
  try {
    await loadLookups()
    selectedPlan.value = '全部专业'
    await loadPlans()
    await fetchSemesterCourses(true)
    await fetchLabs()
    await loadPublishedSchedule()
    await fetchApprovals()
    await fetchNotifications()
  } catch (error) {
    showToast(error instanceof Error ? error.message : '培养方案基础数据加载失败')
  }
})

async function generateSchedule() {
  if (aiGenerating.value) return
  aiGenerating.value = true
  try {
    const result = await api.post<ScheduleJob>('/schedule-jobs/generate', {
      term_id: termInfo.value?.id || null,
      preference_text: aiPreference.value.trim(),
    })
    aiJob.value = result
    previewCandidateId.value = result.candidates[0]?.id || ''
    aiGenerated.value = true
    showToast(`AI 已生成 ${result.candidates.length} 版候选课表，尚未发布`)
  } catch (error) {
    showToast(error instanceof Error ? error.message : 'AI 排课生成失败')
  } finally {
    aiGenerating.value = false
  }
}

async function chooseCandidate(candidate: ScheduleCandidate) {
  if (!aiJob.value || selectingCandidate.value) return
  selectingCandidate.value = true
  previewCandidateId.value = candidate.id
  try {
    aiJob.value = await api.post<ScheduleJob>(`/schedule-jobs/${aiJob.value.id}/select`, {
      schedule_version_id: candidate.id,
    })
    showToast('已选择候选方案，确认后可发布')
  } catch (error) {
    showToast(error instanceof Error ? error.message : '候选方案选择失败')
  } finally {
    selectingCandidate.value = false
  }
}

function applyPreferenceExample(example: string) {
  const current = aiPreference.value.trim()
  aiPreference.value = current ? `${current}，${example}` : example
}

async function publishSelectedSchedule() {
  if (!aiJob.value?.selected_candidate_version_id || publishingSchedule.value) return
  const confirmed = window.confirm('确认发布当前选择的课表？同学期原正式课表将被归档，并同步更新教师课表。')
  if (!confirmed) return
  publishingSchedule.value = true
  try {
    aiJob.value = await api.post<ScheduleJob>(`/schedule-jobs/${aiJob.value.id}/publish`, {
      schedule_version_id: aiJob.value.selected_candidate_version_id,
    })
    const published = aiJob.value.candidates.find(
      candidate => candidate.status === 'PUBLISHED',
    )
    if (published) previewCandidateId.value = published.id
    aiGenerated.value = false
    showToast('课表已发布，并已同步教师课表')
  } catch (error) {
    showToast(error instanceof Error ? error.message : '课表发布失败')
  } finally {
    publishingSchedule.value = false
  }
}

const selectedCandidate = computed(() => {
  const selectedId = aiJob.value?.selected_candidate_version_id
  return aiJob.value?.candidates.find(candidate => candidate.id === selectedId) ?? null
})
</script>

<template>
  <div class="system-app">
    <aside class="system-sidebar" :class="{ open: sidebarOpen }">
      <div class="system-brand"><span class="system-logo"><i></i></span><div><strong>物理实验</strong><small>智能选课系统</small></div></div>
      <nav class="system-nav">
        <p>系统管理中心</p>
        <button v-for="item in navItems" :key="item.id" :class="{ active: activeView === item.id }" @click="navigate(item.id)"><span>{{ item.icon }}</span>{{ item.label }}<i v-if="item.id === 'approvals'" class="system-nav-count">{{ approvals.filter(a=>a.status==='待审批').length || '' }}</i></button>
      </nav>
      <div class="system-status"><span><i></i>系统运行正常</span><small>当前为前端演示环境</small></div>
      <button class="system-logout" @click="emit('logout')">↪　退出演示</button>
    </aside>
    <button v-if="sidebarOpen" class="system-mask" @click="sidebarOpen = false"></button>

    <div class="system-main">
      <header class="system-topbar">
        <button class="system-menu" @click="sidebarOpen = true">☰</button>
        <div class="system-breadcrumb"><span>系统端</span><b>/</b>{{ viewMeta[activeView].title }}</div>
        <div class="system-top-actions"><div style="position:relative"><button class="system-notice" @click="showNotifications=!showNotifications">♢<i v-if="unreadCount">{{ unreadCount }}</i></button><div v-if="showNotifications && unreadCount" style="position:absolute;right:0;top:38px;z-index:20;width:300px;max-height:340px;overflow-y:auto;background:#fff;border:1px solid #dce4e8;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.12);padding:0"><div style="padding:8px 12px;border-bottom:1px solid #eee;font-size:9px;color:#657885">未读通知 · {{ unreadCount }} 条</div><div v-for="(n,i) in notifications" :key="n.request_no" style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid #f5f5f5;font-size:8px;gap:8px"><div @click="navigate('approvals'); showNotifications=false" style="cursor:pointer;flex:1;min-width:0"><div style="color:#405562;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ n.student_name }} · {{ n.type }}</div><div style="color:#919da7;margin-top:2px">{{ n.time }}</div></div><button @click="readOne(i, JSON.stringify(n))" style="flex-shrink:0;padding:2px 6px;border:1px solid #dce4e8;border-radius:3px;background:#fff;color:#277e82;font-size:7px;cursor:pointer">已读</button></div></div></div><div class="system-profile"><span>{{ adminInitial }}</span><div><strong>{{ adminName }}</strong><small>{{ adminLoginId }}</small></div></div></div>
      </header>

      <main class="system-content">
        <div class="system-page-heading">
          <div><h1>{{ viewMeta[activeView].title }}</h1><p>{{ viewMeta[activeView].subtitle }}</p></div>
          <div style="display:flex;align-items:center;gap:10px">
            <label style="font-size:8px;color:#657885;display:flex;align-items:center;gap:4px">校区
              <select v-model="selectedCampus" style="font-size:8px;padding:3px 6px;border:1px solid #d0d7de;border-radius:4px;color:#405562">
                <option v-for="c in campusOptions" :key="c" :value="c">{{ c }}</option>
              </select>
            </label>
          </div>
          <button v-if="activeView === 'plans'" @click="openNewPlan">＋ 新建培养方案</button>
          <button v-else-if="activeView === 'courses'" @click="openAddCourseDialog">＋ 添加实验课程</button>
          <button v-else-if="activeView === 'labs'" @click="openAddLabDialog">＋ 添加实验室</button>
          <button v-if="activeView === 'schedule'" @click="openTermEditor" style="padding:8px 14px;border:1px solid #dce4e8;border-radius:6px;color:#607480;background:#fff;font-size:9px;margin-left:8px">⚙ 学期设置</button>
        </div>

        <template v-if="activeView === 'plans'">
          <section class="system-summary-grid">
            <article><span class="cyan">▤</span><div><small>培养方案</small><strong>{{ summary.plans }} <i>套</i></strong><p>覆盖 {{ summary.majors }} 个专业方向</p></div></article>
            <article><span class="blue">◫</span><div><small>已发布</small><strong>{{ summary.published }} <i>套</i></strong><p>学生选课规则已生效</p></div></article>
            <article><span class="purple">✦</span><div><small>实验课程</small><strong>{{ summary.courses }} <i>门</i></strong><p>共关联 {{ summary.projects }} 个实验项目</p></div></article>
            <article><span class="amber">!</span><div><small>待完善</small><strong>{{ summary.incomplete }} <i>套</i></strong><p>缺少项目或顺序要求</p></div></article>
          </section>
          <section class="system-panel plan-toolbar">
            <div class="plan-tabs"><button v-for="major in ['全部专业', ...majors.map(item => item.name)]" :key="major" :class="{ active: selectedPlan === major }" @click="selectedPlan = major">{{ major }}</button></div>
            <div><label class="system-search">⌕<input v-model="planKeyword" placeholder="搜索专业或培养年份" /></label><select v-model="planYearFilter"><option>全部年份</option><option v-for="year in planYears" :key="year">{{ year }}</option></select></div>
          </section>
          <div class="plan-card-grid">
            <article v-for="plan in plans" :key="plan.id" class="system-panel plan-card">
              <div class="plan-card-head"><span>{{ plan.major.slice(0, 1) }}</span><div><h3>{{ plan.major }}</h3><p>{{ plan.year }} 级培养方案 V{{ plan.version }} · 最近更新 {{ plan.updated }}</p></div><i :class="plan.status">{{ plan.status }}</i></div>
              <div v-if="courseDetailsForPlan(plan).length" class="plan-course-detail-grid">
                <section v-for="(course, index) in courseDetailsForPlan(plan)" :key="course.id" class="plan-course-detail">
                  <header><span>{{ String(index + 1).padStart(2, '0') }}</span><div><small>实验课程</small><h4>{{ course.name || '未命名课程' }}</h4></div></header>
                  <dl>
                    <div><dt>建议修读时间</dt><dd>{{ course.studyYear }} · {{ course.semester }}</dd></div>
                    <div><dt>先修课程要求</dt><dd>{{ displayPrerequisites(course) }}</dd></div>
                  </dl>
                  <div class="plan-project-group required"><strong>必选项目 <i>要求 {{ course.requiredCount }} 项</i></strong><p><span v-for="item in course.requiredProjects" :key="item">{{ item }}</span><em v-if="!course.requiredProjects.length">尚未配置</em></p></div>
                  <div class="plan-project-group optional"><strong>选做项目 <i>最低 {{ course.optionalCount }} 项</i></strong><p><span v-for="item in course.optionalProjects" :key="item">{{ item }}</span><em v-if="!course.optionalProjects.length">尚未配置</em></p></div>
                  <div class="plan-order-rule"><small>项目顺序要求</small><p>{{ displayOrderConstraints(course) }}</p></div>
                </section>
              </div>
              <div v-else class="plan-detail-empty"><span>＋</span><div><strong>尚未录入课程明细</strong><p>请进入"编辑要求"，逐门维护修读学期、必选项目、选做项目和顺序要求。</p></div></div>
              <div class="plan-card-actions"><button @click="editPlan(plan)">编辑要求</button><button :title="plan.rawStatus === 'DRAFT' ? '发布' : '归档'" @click="handlePlanStatus(plan)">•••</button><button type="button" @click="deletePlan(plan)">删除</button></div>
            </article>
            <p v-if="!plansLoading && !plans.length">暂无符合条件的培养方案</p>
          </div>
        </template>

        <template v-else-if="activeView === 'courses'">
          <section class="semester-banner">
            <div><span>当前开课学期</span><strong>{{ termInfo ? `${termInfo.academic_year} 学年 第${termInfo.semester_no === 1 ? '一' : termInfo.semester_no === 2 ? '二' : '三'}学期` : '加载中...' }}</strong><p>总教学周数：{{ termInfo?.total_weeks ?? '—' }} 周 · 教学任务配置</p></div>
            <div><span>已设置课程</span><strong>{{ semesterCourses.length }} <i>门</i></strong></div><div><span>实验项目</span><strong>{{ semesterCourses.reduce((s, c) => s + c.projects.length, 0) }} <i>项</i></strong></div><div><span>在籍学生总人数</span><strong>{{ totalStudents }} <i>人</i></strong></div>
          </section>
          <section v-for="course in semesterCourses" :key="course.code" class="system-panel course-config-card">
            <header><span class="course-config-icon">{{ course.name.slice(0, 1) }}</span><div><small>{{ course.code }}</small><h3>{{ course.name }}</h3><p>面向：{{ course.target }}　·　开设周次：{{ course.weeks }}</p></div><i>本学期开设</i><button @click="openEditTaskDialog(course)">编辑课程</button><button @click="deleteTeachingTask(course)" style="color:red;margin-left:.25rem">删除</button><button @click="openAddProjectDialog(course)" style="margin-left:.25rem">＋ 添加项目</button></header>
            <div class="course-project-table">
              <div class="course-project-row course-project-head"><span>实验项目</span><span>预计人数</span><span>负责教师</span><span>所需实验器材</span><span>配置状态</span><span>操作</span></div>
              <div v-for="project in course.projects" :key="project.name" class="course-project-row"><span><b>{{ project.name }}</b><small>四节连堂 · {{ project.groupMode === 'INDIVIDUAL' ? '单人实验' : `${project.groupSize} 人/组` }}</small></span><span><strong>{{ project.expected }}</strong> 人次</span><span class="tag-cell"><i v-for="teacher in project.teachers" :key="teacher">{{ teacher }}</i></span><span class="tag-cell"><i v-for="item in project.equipment" :key="item">{{ item }}</i></span><span><em>已配置</em></span><span><button @click="openEditProjectDialog(project, course)">编辑</button><button @click="deleteProjectDemand(project, course)" style="color:red;margin-left:.25rem">删除</button></span></div>
            </div>
          </section>
        </template>

        <template v-else-if="activeView === 'labs'">
          <div style="display:flex;gap:.5rem;align-items:center;margin-bottom:.75rem">
            <label style="font-size:.85rem;cursor:pointer"><input type="checkbox" @change="toggleAllLabs" :checked="selectedLabIds.size === labs.length && labs.length > 0" /> 全选</label>
            <button v-if="selectedLabIds.size > 0" @click="batchDeleteLabs" style="background:#c0392b;color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:.85rem">删除选中 ({{ selectedLabIds.size }})</button>
          </div>
          <section class="lab-card-grid">
            <button v-for="lab in labs" :key="lab.name" :class="{ active: activeLab === lab.name }" @click="activeLab = lab.name"><span class="lab-card-icon">⌂</span><div><small>{{ lab.room_type || '实验室' }}</small><strong>{{ lab.name }}</strong></div><i>{{ lab.status }}</i><em><b>{{ lab.safety_capacity }}</b> 人 / 次</em><input type="checkbox" :checked="selectedLabIds.has(lab.id)" @click.stop="toggleLabSelect(lab.id)" style="position:absolute;bottom:8px;right:8px" /></button>
          </section>
          <section v-if="currentLab" class="system-panel equipment-panel">
            <div class="system-panel-title"><div><h3>{{ currentLab.name }} · 设备台账</h3><p>实验室单次最多容纳 {{ currentLab.safety_capacity }} 人开展实验</p></div><div><label class="system-search">⌕<input v-model="equipKeyword" placeholder="搜索器材名称或型号" /></label></div></div>
            <div class="equipment-table">
              <div class="equipment-row equipment-head"><span>器材名称</span><span>型号 / 规格</span><span>账面数量</span><span>可用数量</span><span>备注</span><span>操作</span></div>
              <div v-for="item in filteredEquipment" :key="item.id" class="equipment-row"><span><i>◇</i><b>{{ item.equipment_name }}</b></span><span>{{ item.model }}</span><span>{{ item.total_quantity }} 台 / 套</span><span><strong>{{ item.usable_quantity }}</strong> 台 / 套</span><span>{{ item.note }}</span><span style="white-space:nowrap"><button @click="scrapEquip(currentLab, item)" style="color:#e67e22">报废</button><button @click="addEquip(currentLab, item)" style="color:#27ae60;margin:0 2px">新增</button><button @click="deleteEquipment(currentLab, item)" style="color:red">删除</button></span></div>
            </div>
          </section>
          <section class="lab-capacity-note"><span>i</span><p>实验室容量应取场地安全容量、实验台位数及关键器材可用套数中的最小值。当前数据均为演示配置。</p></section>
        </template>

        <template v-else-if="activeView === 'schedule'">
          <section class="schedule-control-bar system-panel">
            <label>实验室<select v-model="scheduleLab"><option v-for="lab in labs" :key="lab.name">{{ lab.name }}</option></select></label>
            <label>教学周<select v-model="scheduleWeek"><option v-for="w in (termInfo?.total_weeks || 18)" :key="w" :value="w">第 {{ w }} 教学周</option></select></label>
            <div class="schedule-week-range" style="display:flex;align-items:center;gap:.5rem"><button @click="scheduleWeek = Math.max(1, scheduleWeek - 1)">‹</button><strong style="white-space:nowrap">{{ weekDates }}</strong><button @click="scheduleWeek = Math.min(termInfo?.total_weeks || 18, scheduleWeek + 1)">›</button></div>
            <div v-if="aiGenerated" class="schedule-publish-actions">
              <span class="ai-plan-tag">{{ selectedCandidate?.status === 'PUBLISHED' ? '课表已发布' : '候选课表' }}</span>
              <button
                class="schedule-publish-button"
                :disabled="!aiJob?.selected_candidate_version_id || publishingSchedule || selectedCandidate?.status === 'PUBLISHED'"
                @click="publishSelectedSchedule"
              >
                {{ selectedCandidate?.status === 'PUBLISHED' ? '已发布' : !aiJob?.selected_candidate_version_id ? '请先选择方案' : publishingSchedule ? '发布中…' : '发布课表' }}
              </button>
            </div>
          </section>
          <section v-if="aiGenerated && aiJob?.candidates.length" class="ai-candidate-grid">
            <article
              v-for="(candidate, index) in aiJob.candidates"
              :key="candidate.id"
              :class="{ active: previewCandidateId === candidate.id, selected: aiJob.selected_candidate_version_id === candidate.id }"
              @click="previewCandidateId = candidate.id"
            >
              <header><strong>方案 {{ index + 1 }}</strong><span v-if="aiJob.selected_candidate_version_id === candidate.id">已选择</span></header>
              <p><b>{{ candidate.soft_score.toFixed(2) }}</b> 综合分 · {{ candidate.session_count }} 个场次</p>
              <div class="ai-candidate-review">
                <section class="advantage">
                  <strong>优点</strong>
                  <ul>
                    <li v-for="item in candidate.score_details?.validation?.soft_constraint_review?.advantages || []" :key="item.rule_code">{{ item.text }}</li>
                    <li v-if="!candidate.score_details?.validation?.soft_constraint_review?.advantages?.length">暂无明显优势</li>
                  </ul>
                </section>
                <section class="tradeoff">
                  <strong>可改进</strong>
                  <ul>
                    <li v-for="item in candidate.score_details?.validation?.soft_constraint_review?.tradeoffs || []" :key="item.rule_code">{{ item.text }}</li>
                    <li v-if="!candidate.score_details?.validation?.soft_constraint_review?.tradeoffs?.length">暂无明显短板</li>
                  </ul>
                </section>
              </div>
              <button :disabled="selectingCandidate" @click.stop="chooseCandidate(candidate)">{{ aiJob.selected_candidate_version_id === candidate.id ? '已选择' : '选择此方案' }}</button>
            </article>
          </section>
          <section class="system-panel system-schedule-wrap">
            <div class="system-schedule">
              <div class="system-time-corner">节次</div>
              <div v-for="(day,index) in weekDayHeaders" :key="day" class="system-day-head" :style="{ gridColumn: index + 2 }"><strong>{{ day.split(' ')[0] }}</strong><span>{{ day.split(' ')[1] }}</span></div>
              <div v-for="period in 12" :key="period" class="system-period" :class="{ boundary: period === 4 || period === 8 }" :style="{ gridRow: period + 1 }">第 {{ period }} 节</div>
              <div v-for="day in 7" :key="day" class="system-day-column" :style="{ gridColumn: day + 1, gridRow: '2 / 14' }"></div>
              <article v-for="event in visibleScheduleEvents" :key="event.id" class="system-schedule-event" :class="event.tone" :style="{ gridColumn: event.day + 1, gridRow: `${event.start + 1} / span ${event.span}` }"><span>第 {{ event.start }}–{{ event.start + event.span - 1 }} 节</span><strong>{{ event.name }}</strong><small>{{ event.teacher }} · 容量 {{ event.capacity }} 人</small></article>
            </div>
          </section>
          <section class="ai-schedule-note ai-preference-panel">
            <span>✦</span>
            <div>
              <strong>告诉 AI 你更关注什么（可选）</strong>
              <p>可以直接输入，也可以选择示例：</p>
              <div class="ai-preference-examples">
                <button type="button" @click="applyPreferenceExample('尽量减少周末实验')">减少周末实验</button>
                <button type="button" @click="applyPreferenceExample('教师课时尽量紧凑')">教师课时更紧凑</button>
                <button type="button" @click="applyPreferenceExample('优先安排学生空闲人数多的时段')">优先学生空闲时段</button>
                <button
                  v-if="semesterCourses[0]"
                  type="button"
                  @click="applyPreferenceExample(`${semesterCourses[0].name}尽量排在前6周`)"
                >
                  课程尽量安排在前几周
                </button>
                <button
                  v-if="semesterCourses[0]?.projects[0]"
                  type="button"
                  @click="applyPreferenceExample(`${semesterCourses[0].projects[0].name}尽量排在前4周`)"
                >
                  项目尽量安排在前几周
                </button>
              </div>
              <textarea v-model="aiPreference" maxlength="1000" placeholder="例如：尽量减少周末实验，教师课时更紧凑"></textarea>
              <details v-if="aiJob?.warnings.length"><summary>{{ aiJob.warnings.length }} 条数据提示</summary><p v-for="warning in aiJob.warnings" :key="warning">{{ warning }}</p></details>
            </div>
            <button class="ai-submit-button" :disabled="aiGenerating" @click="generateSchedule">{{ aiGenerating ? '生成中…' : 'AI 生成候选课表' }}</button>
          </section>
        </template>

        <template v-else>
          <section class="approval-summary">
            <article><span>待审批</span><strong>{{ approvals.filter(a=>a.status==='待审批').length }}</strong><i class="pending"></i></article><article><span>已通过</span><strong>{{ approvals.filter(a=>a.status==='已通过').length }}</strong><i class="approved"></i></article><article><span>已驳回</span><strong>{{ approvals.filter(a=>a.status==='已驳回').length }}</strong><i class="rejected"></i></article><article><span>已取消</span><strong>{{ approvals.filter(a=>a.status==='已取消').length }}</strong><i class="cancelled"></i></article>
          </section>
          <section class="system-panel approval-panel">
            <div class="system-panel-title"><div><h3>申请审批列表</h3><p>审批结果需包含具体执行方案或明确驳回理由</p></div><div><label class="system-search">⌕<input placeholder="搜索申请人、编号或项目" /></label><select v-model="approvalFilter"><option>全部状态</option><option>待审批</option><option>已通过</option><option>已驳回</option><option>已取消</option></select></div></div>
            <div class="approval-table">
              <div class="approval-row approval-head"><span>申请编号 / 来源</span><span>申请人</span><span>申请类型</span><span>关联项目</span><span>提交时间</span><span>审批状态</span><span>操作</span></div>
              <div v-for="item in visibleApprovals" :key="item.id" class="approval-row"><span><b>{{ item.id }}</b><small>{{ item.source }}</small></span><span>{{ item.applicant }}</span><span><i class="approval-type" :class="item.type === '调课申请' ? 'type-reschedule' : item.type === '换组申请' ? 'type-change' : 'type-makeup'">{{ item.type }}</i></span><span>{{ item.subject }}</span><span>{{ item.submitted }}</span><span><i class="approval-status" :class="{ pending: item.status === '待审批', approved: item.status === '已通过', rejected: item.status === '已驳回', cancelled: item.status === '已取消' }">{{ item.status }}</i></span><span><button @click="selectedApprovalId = item.id">{{ item.status === '待审批' ? '去审批' : '查看结果' }}</button></span></div>
            </div>
          </section>
        </template>
      </main>
    </div>

    <div v-if="planEditorOpen" class="system-dialog-backdrop" @click.self="planEditorOpen = false">
      <form class="plan-editor" @submit.prevent="savePlanDraft">
        <header><div><span>▤</span><div><h2>编辑培养方案要求</h2><p>手动维护专业、年份与实验课程修读规则</p></div></div><button type="button" @click="planEditorOpen = false">×</button></header>
        <section>
          <h3>01　培养方案基础信息</h3>
          <div class="plan-form-grid">
            <label>专业<select v-model="planMajor" :disabled="!!currentPlanId"><option v-for="major in majors" :key="major.id">{{ major.name }}</option></select></label>
            <label>培养年份<select v-model="planYear" :disabled="!!currentPlanId"><option v-for="year in enrollmentYearOptions" :key="year">{{ year }}</option></select></label>
          </div>
        </section>
        <section>
          <div class="plan-section-heading"><div><h3>02　课程修读要求</h3><p>同一培养方案可以配置多门课程，并分别指定修读学年和学期</p></div><button type="button" @click="addPlanCourse">＋ 添加课程要求</button></div>
          <div class="plan-course-list">
            <article v-for="(course, index) in planCourses" :key="course.id" :class="{ active: activePlanCourseId === course.id }" @click="activePlanCourseId = course.id">
              <span>{{ String(index + 1).padStart(2, '0') }}</span>
              <div><label>实验课程名称<select v-model="course.courseId" @change="syncCourseSelection(course)"><option value="" disabled>请选择实验课程</option><option v-for="item in experimentCourseOptions(course)" :key="item.id" :value="item.id">{{ item.course_name }}</option></select></label><small class="course-requirement-summary">必选 {{ course.requiredProjects.length }} 项 · 选做 {{ course.optionalProjects.length }} 项</small></div>
              <label>建议修读学年<select v-model="course.studyYear"><option>第 1 学年</option><option>第 2 学年</option><option>第 3 学年</option><option>第 4 学年</option></select></label>
              <label>建议修读学期<select v-model="course.semester"><option>第一学期</option><option>第二学期</option><option>小学期</option></select></label>
              <label>先修课程要求
                <span class="prerequisite-inputs">
                  <span v-for="(_, prerequisiteIndex) in course.prerequisites" :key="`${course.id}-${prerequisiteIndex}`">
                    <select v-model="course.prerequisites[prerequisiteIndex]">
                      <option value="" disabled>请选择先修课程</option>
                      <option v-for="item in prerequisiteOptions(course, prerequisiteIndex)" :key="item.id" :value="item.id">{{ item.course_name }}</option>
                    </select>
                    <button type="button" @click.stop="removePrerequisite(course, prerequisiteIndex)">×</button>
                  </span>
                  <button type="button" @click.stop="addPrerequisite(course)">＋ 添加先修</button>
                </span>
              </label>
              <button type="button" aria-label="删除课程要求" @click.stop="removePlanCourse(course.id)">×</button>
            </article>
          </div>
        </section>
        <section v-if="activePlanCourse">
          <div class="plan-section-heading"><div><h3>03　实验项目要求</h3><p>当前配置：{{ activePlanCourse.name || '未命名课程' }} · {{ activePlanCourse.studyYear }} {{ activePlanCourse.semester }}</p></div><select v-model="activePlanCourseId"><option v-for="course in planCourses" :key="course.id" :value="course.id">{{ course.name || '未命名课程' }} · {{ course.studyYear }} {{ course.semester }}</option></select></div>
          <div class="requirement-columns">
            <div><label>本课程必选项目数量<input v-model.number="activePlanCourse.requiredCount" type="number" min="0" /></label><p>从"{{ activePlanCourse.name || '当前课程' }}"项目库选择必选项目</p><div class="project-select-row"><select v-model="selectedRequiredProject" :disabled="!activePlanCourse.courseId"><option value="">请选择本课程项目</option><option v-for="item in availableProjectOptions('required')" :key="item" :value="item">{{ item }}</option></select><button type="button" :disabled="!selectedRequiredProject" @click="addSelectedProject('required')">加入必选</button></div><div class="selected-project-list"><button v-for="item in activePlanCourse.requiredProjects" :key="item" type="button" class="selected" @click="toggleListItem('required',item)"><i>✓</i>{{ item }}<b>×</b></button></div><div class="custom-project-row"><input v-model="customRequiredProject" type="text" placeholder="项目库中没有？输入名称补充资料" @keyup.enter.prevent="addCustomProject('required')" /><button type="button" @click="addCustomProject('required')">新增项目</button></div></div>
            <div><label>本课程选做项目最低数量<input v-model.number="activePlanCourse.optionalCount" type="number" min="0" /></label><p>从"{{ activePlanCourse.name || '当前课程' }}"项目库选择选做项目</p><div class="project-select-row"><select v-model="selectedOptionalProject" :disabled="!activePlanCourse.courseId"><option value="">请选择本课程项目</option><option v-for="item in availableProjectOptions('optional')" :key="item" :value="item">{{ item }}</option></select><button type="button" :disabled="!selectedOptionalProject" @click="addSelectedProject('optional')">加入选做</button></div><div class="selected-project-list"><button v-for="item in activePlanCourse.optionalProjects" :key="item" type="button" class="selected" @click="toggleListItem('optional',item)"><i>✓</i>{{ item }}<b>×</b></button></div><div class="custom-project-row"><input v-model="customOptionalProject" type="text" placeholder="项目库中没有？输入名称补充资料" @keyup.enter.prevent="addCustomProject('optional')" /><button type="button" @click="addCustomProject('optional')">新增项目</button></div></div>
          </div>
        </section>
        <section v-if="activePlanCourse">
          <h3>04　当前课程的顺序与约束</h3>
          <label class="full-field">实验项目顺序要求<textarea v-model="activePlanCourse.orderRule" rows="3" placeholder="例如：必须先完成基础测量项目，再选择近代物理项目"></textarea></label>
          <div class="rule-options"><label><input type="checkbox" checked disabled />未完成先修课程时禁止选课</label><label><input type="checkbox" checked disabled />必做项目优先于选做项目</label><label><input v-model="activePlanCourse.allowOrderOverride" type="checkbox" />允许特殊情况跳过项目顺序</label></div>
        </section>
        <footer><p>保存后写入培养方案草稿，发布前仍可继续修改。</p><button type="button" @click="planEditorOpen = false">取消</button><button type="submit" :disabled="savingPlan">{{ savingPlan ? '保存中...' : '保存草稿' }}</button></footer>
      </form>
    </div>

    <div v-if="projectEditorOpen" class="system-dialog-backdrop" @click.self="projectEditorOpen = false">
      <form class="plan-editor" @submit.prevent="submitNewProject">
        <header><div><span>✦</span><div><h2>补充实验项目资料</h2><p>项目创建后将加入当前课程项目库</p></div></div><button type="button" @click="projectEditorOpen = false">×</button></header>
        <section>
          <div class="plan-form-grid">
            <label>项目编码<input v-model.trim="newProject.project_code" maxlength="32" required placeholder="例如 PHYS-EXP-001" /></label>
            <label>项目名称<input v-model.trim="newProject.project_name" maxlength="150" required /></label>
            <label>项目分类<select v-model="newProject.category" required><option disabled value="">请选择</option><option value="BASIC">基础</option><option value="MECHANICS">力学</option><option value="ELECTRICITY">电学</option><option value="OPTICS">光学</option><option value="MODERN">近代物理</option><option value="OTHER">其他</option></select></label>
            <label>所需节次<input v-model="newProject.required_slots" type="number" min="1" max="24" required /></label>
            <label>实验形式<select v-model="newProject.group_mode" @change="newProject.group_mode === 'GROUP' && Number(newProject.default_group_size) < 2 ? newProject.default_group_size = '2' : null"><option value="INDIVIDUAL">单人实验</option><option value="GROUP">多人分组实验</option></select></label>
            <label v-if="newProject.group_mode === 'GROUP'">每组人数<input v-model="newProject.default_group_size" type="number" min="2" max="100" required /></label>
            <label>历史选中比例<input v-model="newProject.historical_selection_ratio" type="number" min="0" max="1" step="0.0001" required placeholder="0 至 1" /></label>
          </div>
        </section>
        <footer><p>请确认资料准确，系统不会自动填充业务参数。</p><button type="button" @click="projectEditorOpen = false">取消</button><button type="submit">创建并加入</button></footer>
      </form>
    </div>

    <div v-if="selectedApproval" class="system-dialog-backdrop" @click.self="selectedApprovalId = null">
      <aside class="approval-detail">
        <header><div><span>✓</span><div><h2>审批详情</h2><p>{{ selectedApproval.id }}</p></div></div><button @click="selectedApprovalId = null">×</button></header>
        <dl><div><dt>申请人</dt><dd>{{ selectedApproval.applicant }}</dd></div><div><dt>申请类型</dt><dd><i class="approval-type" :class="selectedApproval.type === '调课申请' ? 'type-reschedule' : selectedApproval.type === '换组申请' ? 'type-change' : 'type-makeup'">{{ selectedApproval.type }}</i></dd></div><div><dt>原项目</dt><dd>{{ formatAdjustSession(selectedApproval.source_info?.session) || selectedApproval.source_info?.project_name || selectedApproval.subject }}</dd></div><div><dt>新项目</dt><dd>{{ formatAdjustSession(selectedApproval.target_info) || selectedApproval.subject }}</dd></div><div><dt>申请原因</dt><dd>{{ selectedApproval.reason_text || '—' }}</dd></div><div><dt>当前状态</dt><dd><i class="approval-status" :class="{ pending: selectedApproval.status === '待审批', approved: selectedApproval.status === '已通过', rejected: selectedApproval.status === '已驳回', cancelled: selectedApproval.status === '已取消' }">{{ selectedApproval.status }}</i></dd></div></dl>
        <section v-if="selectedApproval.status === '已驳回'" class="rejection"><span>驳回理由</span><p>{{ selectedApproval.result }}</p></section>
        <footer v-if="selectedApproval.status === '待审批'"><button @click="openReject(selectedApproval.rawId)">驳回</button><button @click="approveApplication(selectedApproval.rawId)">通过</button></footer><footer v-else><button @click="selectedApprovalId = null">关闭</button></footer>
      </aside>
    </div>

    <!-- 添加实验室弹窗 -->
    <Teleport to="body">
      <div v-if="addLabDialogOpen" class="system-dialog-backdrop" @click.self="addLabDialogOpen = false">
        <form class="approval-detail" style="width:660px;max-height:85vh;overflow-y:auto" @submit.prevent="saveNewLab">
          <header><div><span>⌂</span><div><h2>新建实验室</h2><p>填写实验室基本信息和设备台账</p></div></div><button type="button" @click="addLabDialogOpen = false">×</button></header>
          <div style="display:flex;gap:1rem;margin-bottom:1rem">
            <label style="flex:2">实验室名称<input v-model="newLab.name" placeholder="例：基础力学实验室 A204" style="width:100%" /></label>
            <label style="flex:1">容纳人数<input v-model.number="newLab.safety_capacity" type="number" min="1" max="100" style="width:100%" /></label>
          </div>
          <section style="background:#f7f8fa;border-radius:8px;padding:1rem">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.75rem">
              <strong>设备台账</strong><button type="button" @click="addEquipRow" style="background:#4769a8;color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:.85rem">＋ 添加器材</button>
            </div>
            <div v-if="newLabEquip.length" style="max-height:280px;overflow-y:auto">
              <div class="equipment-row equipment-head" style="display:flex;gap:6px;padding:6px 8px;font-weight:600;font-size:.85rem;border-bottom:2px solid #ddd">
                <span style="flex:2">器材名称</span><span style="flex:1.5">型号/规格</span><span style="flex:0.8">账面数</span><span style="flex:0.8">可用数</span><span style="flex:1.5">备注</span><span style="width:28px"></span>
              </div>
              <div v-for="(eq, i) in newLabEquip" :key="i" style="display:flex;gap:6px;padding:6px 8px;align-items:center;border-bottom:1px solid #eee">
                <span style="flex:2"><input v-model="eq.name" placeholder="器材名" style="width:100%" /></span>
                <span style="flex:1.5"><input v-model="eq.model" placeholder="型号" style="width:100%" /></span>
                <span style="flex:0.8"><input v-model.number="eq.total" type="number" min="1" style="width:100%" /></span>
                <span style="flex:0.8"><input v-model.number="eq.usable" type="number" min="0" :max="eq.total" style="width:100%" /></span>
                <span style="flex:1.5"><input v-model="eq.note" placeholder="如2人一台" style="width:100%" /></span>
                <span style="width:28px"><button type="button" @click="removeEquipRow(i)" style="color:red;background:none;border:none;cursor:pointer;font-size:1.2rem">×</button></span>
              </div>
            </div>
            <p v-else style="color:#888;font-size:.9rem">暂无设备，可点击上方按钮添加</p>
          </section>
          <footer><button type="button" @click="addLabDialogOpen = false">取消</button><button type="submit">创建实验室</button></footer>
        </form>
      </div>
    </Teleport>

    <!-- 驳回理由弹窗 -->
    <div v-if="rejectDialogOpen" class="system-dialog-backdrop" @click.self="rejectDialogOpen = false">
      <form class="approval-detail" style="width:400px" @submit.prevent="confirmReject">
        <header><div><span>✕</span><div><h2>驳回申请</h2></div></div><button type="button" @click="rejectDialogOpen = false">×</button></header>
        <label style="display:block;margin-bottom:1rem">驳回理由<textarea v-model="rejectReason" rows="4" style="width:100%" placeholder="请填写驳回的具体理由"></textarea></label>
        <footer><button type="button" @click="rejectDialogOpen = false">取消</button><button type="submit">确认驳回</button></footer>
      </form>
    </div>

    <!-- 学期设置弹窗 -->
    <Teleport to="body">
      <div v-if="termEditorOpen" class="system-dialog-backdrop" @click.self="termEditorOpen = false">
        <form class="approval-detail" style="width:440px" @submit.prevent="saveTermSettings">
          <header><div><span>⚙</span><div><h2>学期设置</h2></div></div><button type="button" @click="termEditorOpen = false">×</button></header>
          <div v-if="termInfo" style="display:flex;gap:8px;margin-bottom:1rem;padding:10px 14px;border-radius:8px;background:#e8f4f3;color:#287d82;font-size:.9rem">
            <strong>当前第 {{ termInfo.current_week }} 周</strong>
          </div>
          <label style="display:block;margin-bottom:.75rem">学年<input v-model="termEdit.academic_year" type="text" placeholder="如 2025-2026" style="width:100%" /></label>
          <label style="display:block;margin-bottom:.75rem">学期<select v-model.number="termEdit.semester_no" style="width:100%"><option :value="1">第一学期</option><option :value="2">第二学期</option></select></label>
          <label style="display:block;margin-bottom:.75rem">开学日期<input v-model="termEdit.start_date" type="date" style="width:100%" /></label>
          <label style="display:block;margin-bottom:.75rem">结束日期<input v-model="termEdit.end_date" type="date" style="width:100%" /></label>
          <label style="display:block;margin-bottom:.75rem">总教学周数<input v-model.number="termEdit.total_weeks" type="number" min="1" max="30" style="width:100%" /></label>
          <footer><button type="button" @click="termEditorOpen = false">取消</button><button type="submit">保存</button></footer>
        </form>
      </div>
    </Teleport>

    <Transition name="toast"><div v-if="toast" class="system-toast"><span>✓</span>{{ toast }}</div></Transition>

    <!-- 添加实验课程弹窗 -->
    <Teleport to="body">
      <div v-if="addCourseDialogOpen" class="system-dialog-backdrop" @click.self="addCourseDialogOpen = false">
        <form class="approval-detail" style="width:440px" @submit.prevent="addTeachingTask">
          <header><div><span>＋</span><div><h2>添加实验课程</h2><p>从课程库中选择并创建教学任务</p></div></div><button type="button" @click="addCourseDialogOpen = false">×</button></header>
          <label style="display:block;margin-bottom:1rem">实验课程<select v-model="selectedCourseId" style="width:100%"><option value="" disabled>请选择课程</option><option v-for="c in availableCourses" :key="c.id" :value="c.id">{{ c.course_code }} {{ c.course_name }}</option></select></label>
          <div style="display:flex;gap:1rem;margin-bottom:1rem">
            <label style="flex:1">起始周<input v-model.number="newWeekStart" type="number" min="1" max="20" style="width:100%" /></label>
            <label style="flex:1">结束周<input v-model.number="newWeekEnd" type="number" min="1" max="20" style="width:100%" /></label>
          </div>
          <p v-if="!availableCourses.length" style="color:#888">所有实验课程已创建教学任务。</p>
          <footer><button type="button" @click="addCourseDialogOpen = false">取消</button><button type="submit" :disabled="!selectedCourseId">创建</button></footer>
        </form>
      </div>
    </Teleport>

    <!-- 编辑课程弹窗 -->
    <Teleport to="body">
      <div v-if="editTaskDialog" class="system-dialog-backdrop" @click.self="editTaskDialog = null">
        <form class="approval-detail" style="width:400px" @submit.prevent="saveEditTask">
          <header><div><span>✎</span><div><h2>编辑教学任务</h2><p>{{ editTaskDialog.code }} {{ editTaskDialog.name }}</p></div></div><button type="button" @click="editTaskDialog = null">×</button></header>
          <div style="display:flex;gap:1rem;margin-bottom:1rem">
            <label style="flex:1">起始周<input v-model.number="editTaskDialog.weekStart" type="number" min="1" max="20" style="width:100%" /></label>
            <label style="flex:1">结束周<input v-model.number="editTaskDialog.weekEnd" type="number" min="1" max="20" style="width:100%" /></label>
          </div>
          <footer><button type="button" @click="editTaskDialog = null">取消</button><button type="submit">保存</button></footer>
        </form>
      </div>
    </Teleport>

    <!-- 添加项目弹窗 -->
    <Teleport to="body">
      <div v-if="addProjectDialog" class="system-dialog-backdrop" @click.self="addProjectDialog = null">
        <form class="approval-detail" style="width:500px;max-height:85vh;overflow-y:auto" @submit.prevent="saveAddProject">
          <header><div><span>＋</span><div><h2>创建实验项目</h2><p>课程：{{ addProjectDialog.course.code }}</p></div></div><button type="button" @click="addProjectDialog = null">×</button></header>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">
            <label>项目编号 *<input v-model="addProjectDialog.project_code" placeholder="例：PROJ-001" style="width:100%" /></label>
            <label>项目名称 *<input v-model="addProjectDialog.project_name" placeholder="例：用单摆测量重力加速度" style="width:100%" /></label>
            <label>类别<select v-model="addProjectDialog.category" style="width:100%"><option>BASIC</option><option>MECHANICS</option><option>ELECTRICITY</option><option>OPTICS</option><option>MODERN</option><option>OTHER</option></select></label>
            <label>所需学时<input v-model.number="addProjectDialog.required_slots" type="number" min="1" max="24" style="width:100%" /></label>
            <label>实验形式<select v-model="addProjectDialog.group_mode" @change="addProjectDialog.group_mode === 'GROUP' && Number(addProjectDialog.default_group_size) < 2 ? addProjectDialog.default_group_size = 2 : null" style="width:100%"><option value="INDIVIDUAL">单人实验</option><option value="GROUP">多人分组实验</option></select></label>
            <label v-if="addProjectDialog.group_mode === 'GROUP'">每组人数<input v-model.number="addProjectDialog.default_group_size" type="number" min="2" max="100" style="width:100%" /></label>
            <label>往届选择比<input v-model.number="addProjectDialog.historical_selection_ratio" type="number" min="0" max="1" step="0.01" style="width:100%" /></label>
          </div>
          <div style="margin:.75rem 0"><label><input v-model="addProjectDialog.reqType" value="REQUIRED" type="radio" /> 必做</label><label style="margin-left:1rem"><input v-model="addProjectDialog.reqType" value="OPTIONAL" type="radio" /> 选做</label></div>
          <footer><button type="button" @click="addProjectDialog = null">取消</button><button type="submit">创建并添加</button></footer>
        </form>
      </div>
    </Teleport>

    <!-- 编辑项目需求弹窗 -->
    <Teleport to="body">
      <div v-if="editProjectDialog" class="system-dialog-backdrop" @click.self="editProjectDialog = null">
        <form class="approval-detail" style="width:400px" @submit.prevent="saveProjectDemand">
          <header><div><span>✎</span><div><h2>编辑实验项目</h2><p>{{ editProjectDialog.name }}</p></div></div><button type="button" @click="editProjectDialog = null">×</button></header>
          <label style="display:block;margin-bottom:1rem">预计容量（人次）<input v-model.number="editProjectDialog.capacity" type="number" min="1" style="width:100%" /></label>
          <label style="display:block;margin-bottom:1rem">实验形式<select v-model="editProjectDialog.groupMode" @change="editProjectDialog.groupMode === 'GROUP' && editProjectDialog.groupSize < 2 ? editProjectDialog.groupSize = 2 : null" style="width:100%"><option value="INDIVIDUAL">单人实验</option><option value="GROUP">多人分组实验</option></select></label>
          <label v-if="editProjectDialog.groupMode === 'GROUP'" style="display:block;margin-bottom:1rem">每组人数<input v-model.number="editProjectDialog.groupSize" type="number" min="2" max="100" style="width:100%" /></label>
          <footer><button type="button" @click="editProjectDialog = null">取消</button><button type="submit">保存</button></footer>
        </form>
      </div>
    </Teleport>
  </div>
</template>
