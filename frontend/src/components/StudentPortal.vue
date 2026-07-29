<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import type { UserProfile } from '../api/auth'

type View = 'home' | 'schedule' | 'selection' | 'applications' | 'ai'
type ApplicationType = '调课申请' | '换组申请' | '补做申请'

const props = defineProps<{ user: UserProfile | null }>()
const emit = defineEmits<{ logout: [] }>()
const activeView = ref<View>('home')
const sidebarOpen = ref(false)
const toast = ref('')
const courseFilter = ref('全部课程')
const projectKeyword = ref('')
const projectType = ref('全部')
const selectedProjectIds = ref<number[]>([1, 4, 6])
const applicationDialog = ref<ApplicationType | null>(null)
const applicationReason = ref('')
const applicationTarget = ref('')
const applicationSourceProject = ref('')
const applicationDestinationProject = ref('')
const aiInput = ref('')
const aiThread = ref<HTMLDivElement | null>(null)

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

const viewMeta: Record<View, { title: string; subtitle: string }> = {
  home: { title: greetingText.value, subtitle: '今天是第 6 教学周，查看你的实验学习进度' },
  schedule: { title: '实验课表查询', subtitle: '查看本学期已选实验的时间与地点安排' },
  selection: { title: '在线选课', subtitle: '根据培养方案选择必做与选做实验项目' },
  applications: { title: '个人申请', subtitle: '提交并跟踪调课、换组与补做申请' },
  ai: { title: 'AI 智能咨询', subtitle: '面向实验选课与教学安排的智能问答助手' },
}

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

const schedule = [
  { day: 3, startPeriod: 5, title: '用单摆测量重力加速度', room: '实验楼 A203', week: '第 4 周 · 第 5–8 节', tone: 'teal' },
  { day: 4, startPeriod: 5, title: '光电效应与普朗克常量测定', room: '近代物理实验室 2', week: '第 6 周 · 第 5–8 节', tone: 'blue' },
  { day: 3, startPeriod: 9, title: '密立根油滴实验', room: '近代物理实验室 3', week: '第 12 周 · 第 9–12 节', tone: 'purple' },
]

const applications = ref([
  { id: 'SQ20260318001', type: '调课申请', project: '用单摆测量重力加速度', date: '2026-03-18', status: '审核中', note: '预计 2 个工作日内反馈' },
  { id: 'SQ20260305003', type: '补做申请', project: '示波器的原理与使用', date: '2026-03-05', status: '已通过', note: '已安排至第 7 周周四' },
  { id: 'SQ20260226002', type: '换组申请', project: '霍尔效应及磁场测量 → 密立根油滴实验', date: '2026-02-26', status: '已驳回', note: '目标实验项目名额不足' },
])

const messages = ref([
  { role: 'assistant', text: '你好，张同学！我是物理实验 AI 助手。我可以帮你查询选课规则、实验时间、申请流程，也能解答实验预习中的常见问题。' },
])

const filteredProjects = computed(() => projects.filter((project) => {
  const matchesCourse = courseFilter.value === '全部课程' || project.course === courseFilter.value
  const matchesType = projectType.value === '全部' || project.type === projectType.value
  const keyword = projectKeyword.value.trim().toLowerCase()
  const matchesKeyword = !keyword || `${project.name}${project.teacher}${project.room}`.toLowerCase().includes(keyword)
  return matchesCourse && matchesType && matchesKeyword
}))

const selectedProjects = computed(() => projects.filter((project) => selectedProjectIds.value.includes(project.id)))
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
const selectedRequiredProjects = computed(() => selectedProjects.value.filter((project) => project.type === '必做').length)
const selectedOptionalProjects = computed(() => selectedProjects.value.filter((project) => project.type === '选做').length)
const requiredSelectionTarget = computed(() => totalRequiredProjects.value + totalOptionalRequired.value)
const satisfiedSelectionCount = computed(() => courseSelectionDetails.value.reduce((total, course) => total + course.satisfiedSelectionCount, 0))
const completionRate = computed(() => Math.round((satisfiedSelectionCount.value / requiredSelectionTarget.value) * 100))
const swapTargetProjects = computed(() => projects.filter((project) =>
  project.name !== applicationSourceProject.value
  && !selectedProjectIds.value.includes(project.id)
  && project.remaining > 0
))

function navigate(view: View) {
  activeView.value = view
  sidebarOpen.value = false
}

function showToast(text: string) {
  toast.value = text
  window.setTimeout(() => {
    if (toast.value === text) toast.value = ''
  }, 2600)
}

function toggleProject(id: number) {
  const project = projects.find((item) => item.id === id)
  if (!project) return
  if (selectedProjectIds.value.includes(id)) {
    selectedProjectIds.value = selectedProjectIds.value.filter((item) => item !== id)
    showToast(`已退选“${project.name}”（仅当前演示会话）`)
    return
  }
  if (project.remaining === 0) {
    showToast('该小组当前已满，可关注后续余量变化')
    return
  }
  selectedProjectIds.value = [...selectedProjectIds.value, id]
  showToast(`已选择“${project.name}”（仅当前演示会话）`)
}

function openApplication(type: ApplicationType) {
  applicationDialog.value = type
  applicationReason.value = ''
  applicationTarget.value = ''
  applicationSourceProject.value = ''
  applicationDestinationProject.value = ''
}

function submitApplication() {
  if (applicationDialog.value !== '换组申请' && !applicationTarget.value) {
    showToast('请选择关联实验项目')
    return
  }
  if (!applicationReason.value.trim()) {
    showToast('请填写申请项目和申请原因')
    return
  }
  if (applicationDialog.value === '换组申请') {
    if (!applicationSourceProject.value || !applicationDestinationProject.value) {
      showToast('请选择原实验项目和目标实验项目')
      return
    }
    if (applicationSourceProject.value === applicationDestinationProject.value) {
      showToast('目标实验项目不能与原实验项目相同')
      return
    }
  }
  const projectDescription = applicationDialog.value === '换组申请'
    ? `${applicationSourceProject.value} → ${applicationDestinationProject.value}`
    : applicationTarget.value
  applications.value.unshift({
    id: `SQ-DEMO-${String(applications.value.length + 1).padStart(3, '0')}`,
    type: applicationDialog.value ?? '调课申请',
    project: projectDescription,
    date: '演示日期',
    status: '草稿演示',
    note: '未提交至真实系统',
  })
  applicationDialog.value = null
  showToast('申请已加入演示列表，未提交至真实系统')
}

async function askAi(preset?: string) {
  const question = (preset ?? aiInput.value).trim()
  if (!question) return
  messages.value.push({ role: 'user', text: question })
  aiInput.value = ''
  const answer = question.includes('选课')
    ? '本学期选课时，请先完成培养方案中的必做项目，再从选做项目中补足课程要求。页面中的“在线选课”会同步展示名额、周次与时间冲突提示。'
    : question.includes('调课') || question.includes('申请')
      ? '进入“个人申请”，选择调课、换组或补做类型，填写目标安排和原因。正式系统中提交后会由任课教师或实验中心审核。'
      : question.includes('冲突') || question.includes('课表')
        ? '你可以在“实验课表”中按周次查看安排。选课时系统应校验同一时间段是否已有课程，当前原型已预留冲突提示位置。'
        : '我建议先确认课程名称、实验项目和目标周次。当前是交互原型，正式接入教务数据后，我可以结合你的真实培养方案给出更准确的答案。'
  window.setTimeout(async () => {
    messages.value.push({ role: 'assistant', text: answer })
    await nextTick()
    aiThread.value?.scrollTo({ top: aiThread.value.scrollHeight, behavior: 'smooth' })
  }, 450)
}
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
          <span class="demo-badge">演示数据</span>
          <button class="notice-button" type="button" aria-label="通知" @click="showToast('你有 2 条实验安排提醒')">♢<i>2</i></button>
          <div class="student-profile"><span>{{ userInitial }}</span><div><strong>{{ studentName }}</strong><small>{{ studentNo }}</small></div></div>
        </div>
      </header>

      <main class="student-content">
        <div class="page-heading">
          <div><h1>{{ viewMeta[activeView].title }}</h1><p>{{ viewMeta[activeView].subtitle }}</p></div>
          <div v-if="activeView === 'home'" class="term-selector">2025–2026 学年 第二学期⌄</div>
          <button v-if="activeView === 'schedule'" class="outline-action" type="button" @click="showToast('课表导出将在后端接口接入后启用')">↓ 导出课表</button>
        </div>

        <template v-if="activeView === 'home'">
          <section class="student-hero">
            <div class="hero-copy">
              <span class="hero-kicker">MY PHYSICS LAB</span>
              <h2>探索，从一次严谨的实验开始。</h2>
              <p>本学期第 6 教学周 · 下一项实验安排在周三第 5–8 节</p>
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
            <span class="sample-label">示例信息</span>
          </section>

          <section class="summary-grid">
            <article><div class="summary-icon teal">▤</div><div><span>应修实验课程</span><strong>2 <small>门</small></strong><p>均已完成课程确认</p></div></article>
            <article><div class="summary-icon blue">✓</div><div><span>必做实验项目</span><strong>{{ totalRequiredProjects }} <small>项</small></strong><p>已选 {{ selectedRequiredProjects }} 项 · 待选 {{ totalRequiredProjects - selectedRequiredProjects }} 项</p></div></article>
            <article><div class="summary-icon purple">✦</div><div><span>选做项目池</span><strong>{{ totalOptionalProjects }} <small>项</small></strong><p>至少选 {{ totalOptionalRequired }} 项 · 当前已选 {{ selectedOptionalProjects }} 项</p></div></article>
            <article><div class="summary-icon amber">◷</div><div><span>当前已选项目</span><strong>{{ selectedProjectIds.length }} <small>项</small></strong><p>本周有 1 项实验</p></div></article>
          </section>

          <div class="home-columns">
            <section class="panel-card course-progress">
              <div class="panel-title"><div><h3>本学期实验课程</h3><p>培养方案要求与当前选课进度</p></div><button type="button" @click="navigate('selection')">去选课 →</button></div>
              <article v-for="course in courseSelectionDetails" :key="course.code" class="student-course-detail">
                <header>
                  <div class="course-letter" :style="{ background: course.color }">{{ course.name.slice(0, 1) }}</div>
                  <div><h4>{{ course.name }}</h4><span>{{ course.code }} · 课程负责人：{{ course.teacher }}</span></div>
                  <div class="course-ratio"><strong>{{ course.satisfiedSelectionCount }} / {{ course.requiredSelectionCount }}</strong><span>已满足 / 应选</span></div>
                </header>
                <div class="student-course-requirements">
                  <section>
                    <div class="requirement-title"><strong>必做项目</strong><span>应选 {{ course.required }} 项 · 已选 {{ course.selectedRequired }} 项</span></div>
                    <ul><li v-for="project in course.requiredProjects" :key="project.id" :class="{ selected: selectedProjectIds.includes(project.id) }"><i>{{ selectedProjectIds.includes(project.id) ? '✓' : '○' }}</i><span>{{ project.name }}</span><em>{{ selectedProjectIds.includes(project.id) ? '已选' : '待选' }}</em></li></ul>
                  </section>
                  <section>
                    <div class="requirement-title optional"><strong>选做项目池</strong><span>共 {{ course.optionalProjects.length }} 项 · 至少选 {{ course.optionalRequired }} 项 · 已选 {{ course.selectedOptional }} 项</span></div>
                    <ul><li v-for="project in course.optionalProjects" :key="project.id" :class="{ selected: selectedProjectIds.includes(project.id) }"><i>{{ selectedProjectIds.includes(project.id) ? '✓' : '○' }}</i><span>{{ project.name }}</span><em>{{ selectedProjectIds.includes(project.id) ? '已选' : '未选' }}</em></li></ul>
                  </section>
                </div>
                <footer><span>选课要求完成度（已满足 / 应选）</span><div class="progress-track"><i :style="{ width: `${course.completionPercent}%`, background: course.color }"></i></div><strong>{{ course.satisfiedSelectionCount }} / {{ course.requiredSelectionCount }} · {{ course.completionPercent }}%</strong></footer>
              </article>
            </section>

            <section class="panel-card next-lab">
              <div class="panel-title"><div><h3>下一项实验</h3><p>请提前完成实验预习</p></div><span class="week-badge">第 6 周</span></div>
              <div class="next-date"><strong>26</strong><span>三月<br />星期三</span></div>
              <h4>光电效应与普朗克常量测定</h4>
              <ul><li><span>◷</span>周三 第 5–8 节</li><li><span>⌖</span>近代物理实验室 2</li><li><span>◎</span>指导教师：周老师</li></ul>
              <button type="button" @click="navigate('ai')">让 AI 帮我预习 <span>✦</span></button>
            </section>
          </div>

          <section class="panel-card selected-overview">
            <div class="panel-title"><div><h3>个人当前已选实验项目</h3><p>展示最近的项目安排，全部数据为示例</p></div><button type="button" @click="navigate('schedule')">查看完整课表 →</button></div>
            <div class="compact-table">
              <div class="table-row table-head"><span>实验项目</span><span>所属课程</span><span>时间安排</span><span>地点</span><span>状态</span></div>
              <div v-for="project in selectedProjects.slice(0, 3)" :key="project.id" class="table-row">
                <span><b>{{ project.name }}</b><small>{{ project.type }}项目</small></span><span>{{ project.course }}</span><span>{{ project.week }} · {{ project.time }}</span><span>{{ project.room }}</span><span><i class="status confirmed">已确认</i></span>
              </div>
            </div>
          </section>
        </template>

        <template v-else-if="activeView === 'schedule'">
          <section class="filter-bar">
            <label>教学周<select><option>第 1–18 周</option><option>第 6 周</option></select></label>
            <label>课程<select><option>全部课程</option><option v-for="course in semesterCourses" :key="course.code">{{ course.name }}</option></select></label>
            <div class="schedule-legend"><span><i class="teal"></i>大学物理实验</span><span><i class="blue"></i>近代物理实验</span></div>
          </section>
          <section class="panel-card timetable-card">
            <div class="week-switch"><button type="button">‹</button><div><strong>第 6 教学周</strong><span>2026.03.22 — 2026.03.28</span></div><button type="button">›</button></div>
            <div class="timetable">
              <div class="time-corner">节次</div>
              <div v-for="(day, index) in ['周日 03/22','周一 03/23','周二 03/24','周三 03/25','周四 03/26','周五 03/27','周六 03/28']" :key="day" class="day-head" :style="{ gridColumn: index + 2 }"><strong>{{ day.split(' ')[0] }}</strong><span>{{ day.split(' ')[1] }}</span></div>
              <div v-for="slot in 12" :key="slot" class="time-label" :class="{ 'period-boundary': slot === 4 || slot === 8 }" :style="{ gridRow: slot + 1 }"><strong>第 {{ slot }} 节</strong></div>
              <div v-for="day in 7" :key="`col-${day}`" class="day-column" :style="{ gridColumn: day + 1, gridRow: '2 / 14' }"></div>
              <article v-for="item in schedule" :key="item.title" class="schedule-event" :class="item.tone" :style="{ gridColumn: item.day + 1, gridRow: `${item.startPeriod + 1} / span 4` }">
                <span>{{ item.week }}</span><strong>{{ item.title }}</strong><small>{{ item.room }}</small>
              </article>
            </div>
          </section>
          <section class="schedule-notice"><span>i</span><div><strong>课表说明</strong><p>实验项目可能按指定教学周开设，请以项目卡片标注的周次为准。正式系统接入后，临时调课会通过站内消息同步。</p></div></section>
        </template>

        <template v-else-if="activeView === 'selection'">
          <section class="selection-summary">
            <div><span>本学期选课要求完成度（已满足 / 应选）</span><strong>{{ satisfiedSelectionCount }} / {{ requiredSelectionTarget }}</strong><div class="progress-track"><i :style="{ width: `${completionRate}%` }"></i></div></div>
            <p><i>!</i> 必做项目还需选择 3 项，建议优先完成必做项目安排。</p>
            <button type="button" @click="showToast('当前选择已暂存于演示会话')">暂存选择</button>
          </section>
          <section class="selection-tools">
            <div class="course-tabs"><button v-for="name in ['全部课程','大学物理实验（上）','近代物理实验']" :key="name" :class="{ active: courseFilter === name }" @click="courseFilter = name">{{ name }}</button></div>
            <div class="project-filters">
              <label class="search-box">⌕<input v-model="projectKeyword" placeholder="搜索实验名称、教师或实验室" /></label>
              <select v-model="projectType"><option>全部</option><option>必做</option><option>选做</option></select>
            </div>
          </section>
          <div class="project-grid">
            <article v-for="project in filteredProjects" :key="project.id" class="project-card" :class="{ selected: selectedProjectIds.includes(project.id) }">
              <div class="project-card-top"><span :class="project.type === '必做' ? 'required' : 'optional'">{{ project.type }}</span><i>{{ project.course }}</i></div>
              <h3>{{ project.name }}</h3>
              <ul><li><span>▣</span>{{ project.week }} · {{ project.time }}</li><li><span>⌖</span>{{ project.room }}</li><li><span>◎</span>{{ project.teacher }}</li></ul>
              <div class="capacity"><span>名额</span><div><i :style="{ width: `${((project.capacity - project.remaining) / project.capacity) * 100}%` }"></i></div><b :class="{ danger: project.remaining <= 2 }">{{ project.remaining ? `余 ${project.remaining}` : '已满' }}</b></div>
              <button type="button" :class="{ remove: selectedProjectIds.includes(project.id) }" @click="toggleProject(project.id)">
                {{ selectedProjectIds.includes(project.id) ? '已选 · 点击退选' : project.remaining === 0 ? '关注名额' : '选择该项目' }}
              </button>
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
              <div class="table-row application-head"><span>申请编号 / 类型</span><span>关联实验项目</span><span>申请日期</span><span>处理状态</span><span>审核说明</span></div>
              <div v-for="item in applications" :key="item.id" class="table-row application-row">
                <span><b>{{ item.type }}</b><small>{{ item.id }}</small></span><span>{{ item.project }}</span><span>{{ item.date }}</span><span><i class="status" :class="{ pending: item.status === '审核中', confirmed: item.status === '已通过', rejected: item.status === '已驳回', draft: item.status === '草稿演示' }">{{ item.status }}</i></span><span>{{ item.note }}</span>
              </div>
            </div>
          </section>
          <section class="application-tip"><span>!</span><p>正式申请提交后将进入教师或实验中心审核流程；当前页面仅演示交互，不会提交真实申请。</p></section>
        </template>

        <template v-else>
          <div class="ai-layout">
            <aside class="ai-guide">
              <div class="ai-intro"><span>✦</span><h3>实验智能助手</h3><p>基于课程规则与实验安排提供咨询</p><i>原型演示</i></div>
              <div class="quick-question"><span>你可以这样问</span>
                <button v-for="question in ['如何完成本学期选课？','实验时间冲突怎么办？','调课申请需要什么条件？','如何准备光电效应实验？']" :key="question" @click="askAi(question)">{{ question }} <i>→</i></button>
              </div>
              <p class="ai-notice">AI 回答仅供参考，正式业务规则以实验中心发布为准。</p>
            </aside>
            <section class="ai-chat panel-card">
              <header><div><span class="ai-avatar">✦</span><div><strong>物理实验 AI 助手</strong><small><i></i> 在线 · 原型回答</small></div></div><button type="button" @click="messages = messages.slice(0, 1)">清空对话</button></header>
              <div ref="aiThread" class="ai-thread">
                <div v-for="(item, index) in messages" :key="index" class="ai-message" :class="item.role">
                  <span>{{ item.role === 'assistant' ? '✦' : userInitial }}</span><div><p>{{ item.text }}</p><small>{{ item.role === 'assistant' ? 'AI 助手' : '刚刚' }}</small></div>
                </div>
              </div>
              <form class="ai-input" @submit.prevent="askAi()">
                <textarea v-model="aiInput" rows="2" placeholder="请输入关于实验选课、课表或申请的问题…" @keydown.enter.exact.prevent="askAi()"></textarea>
                <div><span>Enter 发送 · Shift + Enter 换行</span><button type="submit" :disabled="!aiInput.trim()">发送 <i>↑</i></button></div>
              </form>
            </section>
          </div>
        </template>
      </main>
    </div>

    <div v-if="applicationDialog" class="dialog-backdrop" @click.self="applicationDialog = null">
      <form class="application-dialog" @submit.prevent="submitApplication">
        <div class="dialog-title"><div><span>◇</span><div><h3>{{ applicationDialog }}</h3><p>填写以下信息创建演示申请</p></div></div><button type="button" @click="applicationDialog = null">×</button></div>
        <label v-if="applicationDialog !== '换组申请'">实验项目<select v-model="applicationTarget"><option value="" disabled>请选择关联实验项目</option><option v-for="project in selectedProjects" :key="project.id">{{ project.name }}</option></select></label>
        <div v-else class="project-transfer-fields">
          <label>原实验项目
            <select v-model="applicationSourceProject" @change="applicationDestinationProject = ''">
              <option value="" disabled>请选择当前已选实验项目</option>
              <option v-for="project in selectedProjects" :key="project.id">{{ project.name }}</option>
            </select>
          </label>
          <span class="project-transfer-arrow" aria-hidden="true">→</span>
          <label>目标实验项目
            <select v-model="applicationDestinationProject" :disabled="!applicationSourceProject">
              <option value="" disabled>{{ applicationSourceProject ? '请选择希望调入的实验项目' : '请先选择原实验项目' }}</option>
              <option v-for="project in swapTargetProjects" :key="project.id" :value="project.name">{{ project.name }} · 余 {{ project.remaining }} 个名额</option>
            </select>
          </label>
        </div>
        <label>申请原因<textarea v-model="applicationReason" rows="4" placeholder="请简要说明申请原因（仅用于本次演示）"></textarea></label>
        <div class="dialog-warning">当前为演示原型，确认后只会加入页面列表，不会提交到真实系统。</div>
        <div class="dialog-actions"><button type="button" @click="applicationDialog = null">取消</button><button type="submit">加入演示列表</button></div>
      </form>
    </div>

    <Transition name="toast"><div v-if="toast" class="portal-toast" role="status"><span>✓</span>{{ toast }}</div></Transition>
  </div>
</template>
