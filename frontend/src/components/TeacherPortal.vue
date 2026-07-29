<script setup lang="ts">
import { computed, ref } from 'vue'
import type { UserProfile } from '../api/auth'

type TeacherView = 'home' | 'schedule' | 'classes' | 'adjustments' | 'resources'
type AdjustmentType = '调课申请' | '场地调整申请' | '停课申请'

const props = defineProps<{ user: UserProfile | null }>()
const emit = defineEmits<{ logout: [] }>()
const activeView = ref<TeacherView>('home')
const sidebarOpen = ref(false)
const toast = ref('')
const selectedProjectId = ref(1)
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

const viewMeta: Record<TeacherView, { title: string; subtitle: string }> = {
  home: { title: teacherGreeting.value, subtitle: '第 6 教学周 · 今日有 2 个实验教学任务' },
  schedule: { title: '教师课表', subtitle: '按教学周查看个人实验教学安排' },
  classes: { title: '项目学生管理', subtitle: '按实验项目场次查看自主选课学生的基本信息' },
  adjustments: { title: '教学调整申请', subtitle: '提交并跟踪调课、场地调整及停课申请' },
  resources: { title: '资源异常上报', subtitle: '上报实验室、仪器与耗材异常并跟踪处理进度' },
}

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

const scheduleEvents = [
  { day: 3, startPeriod: 5, title: '用单摆测量重力加速度', info: '大学物理实验（上） · 24 人已选', room: '实验楼 A203', tone: 'teal' },
  { day: 4, startPeriod: 5, title: '光电效应与普朗克常量测定', info: '近代物理实验 · 16 人已选', room: '近代物理实验室 2', tone: 'blue' },
  { day: 4, startPeriod: 9, title: '实验室开放答疑', info: '大学物理实验（上）', room: '实验楼 A210', tone: 'amber' },
  { day: 5, startPeriod: 5, title: '示波器的原理与使用', info: '大学物理实验（上） · 22 人已选', room: '实验楼 B105', tone: 'purple' },
]

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

const adjustmentRecords = ref([
  { id: 'TJ20260318001', type: '调课申请', project: '用单摆测量重力加速度', original: '第 4 周 周二 5–8 节', target: '第 4 周 周五 5–8 节', date: '2026-03-18', status: '审核中' },
  { id: 'TJ20260302003', type: '场地调整申请', project: '示波器的原理与使用', original: '第 7 周 周四 5–8 节 · 实验楼 B105', target: '原时间 · 实验楼 A205', date: '2026-03-02', status: '已通过' },
  { id: 'TJ20260224002', type: '停课申请', project: '光电效应与普朗克常量测定', original: '第 6 周 周三 5–8 节', target: '待重新安排', date: '2026-02-24', status: '已驳回' },
])

const resourceRecords = ref([
  { id: 'YC20260320004', type: '仪器故障', location: '近代物理实验室 2', detail: '光电效应实验箱电流示数异常', level: '紧急', date: '2026-03-20', status: '处理中' },
  { id: 'YC20260312002', type: '耗材不足', location: '实验楼 A203', detail: '单摆实验备用细线库存不足', level: '一般', date: '2026-03-12', status: '已解决' },
  { id: 'YC20260306001', type: '环境异常', location: '实验楼 B105', detail: '实验台局部照明闪烁', level: '一般', date: '2026-03-06', status: '已解决' },
])

const selectedTask = computed(() => teachingTasks.find((task) => task.id === selectedProjectId.value) ?? teachingTasks[0])
const filteredStudents = computed(() => students.filter((student) => {
  const keyword = studentKeyword.value.trim().toLowerCase()
  return student.projectId === selectedProjectId.value
    && (!keyword || `${student.name}${student.no}${student.major}`.toLowerCase().includes(keyword))
}))

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
      <div class="teacher-semester"><span>当前学期</span><strong>2025–2026 学年</strong><small>第二学期 · 第 6 教学周</small></div>
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
          <div><h1>{{ viewMeta[activeView].title }}</h1><p>{{ viewMeta[activeView].subtitle }}</p></div>
          <button v-if="activeView === 'home'" type="button" @click="navigate('schedule')">查看本周课表 <span>→</span></button>
          <button v-if="activeView === 'schedule'" class="teacher-outline-btn" type="button" @click="showToast('课表导出将在接口接入后启用')">↓ 导出课表</button>
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
            <article><span class="teacher-summary-icon teal">▤</span><div><small>承担课程</small><strong>2 <i>门</i></strong><p>大学物理实验、近代物理实验</p></div></article>
            <article><span class="teacher-summary-icon blue">✦</span><div><small>负责实验项目</small><strong>4 <i>项</i></strong><p>开设 4 个选课场次</p></div></article>
            <article><span class="teacher-summary-icon purple">♙</span><div><small>本学期选课学生</small><strong>76 <i>人次</i></strong><p>学生自主选择实验场次</p></div></article>
            <article><span class="teacher-summary-icon amber">◷</span><div><small>待处理事项</small><strong>5 <i>项</i></strong><p>申请 3 项 · 异常 2 项</p></div></article>
          </section>

          <div class="teacher-home-grid">
            <section class="teacher-panel teaching-task-panel">
              <div class="teacher-panel-title"><div><h3>当前学期教学任务</h3><p>具体实验项目、所属课程与执行信息</p></div><span>共 {{ teachingTasks.length }} 项</span></div>
              <article v-for="task in teachingTasks" :key="task.id" class="teaching-task-row">
                <div class="task-color" :style="{ background: task.color }">{{ task.project.slice(0, 1) }}</div>
                <div class="task-primary"><h4>{{ task.project }}</h4><p><span>{{ task.course }}</span><i>{{ task.courseCode }}</i></p></div>
                <div class="task-detail"><span>学生选课情况</span><strong>{{ task.students }} / {{ task.capacity }} 人已选</strong><small>学生自主选择此场次</small></div>
                <div class="task-detail"><span>实验场次</span><strong>{{ task.week }} {{ task.time }}</strong><small>{{ task.room }} · 四节连堂</small></div>
                <div class="task-progress"><span>{{ task.completed }} / {{ task.total }} 次</span><div><i :style="{ width: `${(task.completed / task.total) * 100}%`, background: task.color }"></i></div><small>{{ task.completed ? '本场次已完成' : '待开课' }}</small></div>
                <button type="button" @click="selectedProjectId = task.id; navigate('classes')">查看学生 →</button>
              </article>
            </section>

            <aside class="teacher-side-column">
              <section class="teacher-panel today-teaching">
                <div class="teacher-panel-title"><div><h3>今日教学</h3><p>3 月 25 日 · 星期三</p></div><span class="today-count">2 项</span></div>
                <article><span class="time-block">5–8 节</span><div><strong>光电效应与普朗克常量测定</strong><p>近代物理实验 · 16 人已选</p><small>近代物理实验室 2</small></div></article>
                <article><span class="time-block evening">9–12 节</span><div><strong>实验室开放答疑</strong><p>大学物理实验（上）</p><small>实验楼 A210</small></div></article>
              </section>
              <section class="teacher-panel pending-matters">
                <div class="teacher-panel-title"><div><h3>待处理事项</h3><p>建议及时完成处理</p></div></div>
                <button type="button" @click="navigate('adjustments')"><span class="teal">⇄</span><div><strong>教学调整申请</strong><small>3 项待关注</small></div><i>→</i></button>
                <button type="button" @click="navigate('resources')"><span class="amber">△</span><div><strong>资源异常</strong><small>2 项处理中</small></div><i>→</i></button>
              </section>
            </aside>
          </div>
        </template>

        <template v-else-if="activeView === 'schedule'">
          <section class="teacher-filter-bar">
            <label>教学周<select><option>第 6 教学周</option><option>第 7 教学周</option></select></label>
            <label>课程<select><option>全部课程</option><option>大学物理实验（上）</option><option>近代物理实验</option></select></label>
            <div class="teacher-week-nav"><button>‹</button><strong>2026.03.22 — 03.28</strong><button>›</button></div>
          </section>
          <section class="teacher-panel teacher-timetable-wrap">
            <div class="teacher-timetable">
              <div class="teacher-time-corner">节次</div>
              <div v-for="(day, index) in ['周日 03/22','周一 03/23','周二 03/24','周三 03/25','周四 03/26','周五 03/27','周六 03/28']" :key="day" class="teacher-day-head" :style="{ gridColumn: index + 2 }"><strong>{{ day.split(' ')[0] }}</strong><span>{{ day.split(' ')[1] }}</span></div>
              <div v-for="slot in 12" :key="slot" class="teacher-time-label" :class="{ 'teacher-period-boundary': slot === 4 || slot === 8 }" :style="{ gridRow: slot + 1 }"><strong>第 {{ slot }} 节</strong></div>
              <div v-for="day in 7" :key="day" class="teacher-day-column" :style="{ gridColumn: day + 1, gridRow: '2 / 14' }"></div>
              <article v-for="event in scheduleEvents" :key="event.title" class="teacher-schedule-event" :class="event.tone" :style="{ gridColumn: event.day + 1, gridRow: `${event.startPeriod + 1} / span 4` }">
                <span>{{ event.info }}</span><strong>{{ event.title }}</strong><small>⌖ {{ event.room }}</small>
              </article>
            </div>
          </section>
          <section class="teacher-schedule-list">
            <div><span>下一节课</span><strong>第 6 周 周三 5–8 节</strong></div><h3>光电效应与普朗克常量测定</h3><p>近代物理实验 · 16 人已选</p><p>近代物理实验室 2</p><button type="button" @click="selectedProjectId = 3; navigate('classes')">查看学生名单 →</button>
          </section>
        </template>

        <template v-else-if="activeView === 'classes'">
          <section class="class-project-picker">
            <label>选择实验项目
              <select v-model="selectedProjectId">
                <option v-for="task in teachingTasks" :key="task.id" :value="task.id">{{ task.project }} · {{ task.week }} {{ task.time }}</option>
              </select>
            </label>
            <div class="selected-project-info">
              <span class="selected-project-mark" :style="{ background: selectedTask.color }">{{ selectedTask.project.slice(0, 1) }}</span>
              <div><small>{{ selectedTask.course }} · {{ selectedTask.courseCode }}</small><strong>{{ selectedTask.project }}</strong><p>{{ selectedTask.week }} {{ selectedTask.time }}　|　{{ selectedTask.room }}　|　{{ selectedTask.students }} 人已选</p></div>
            </div>
            <div class="class-numbers"><span><strong>{{ selectedTask.students }}</strong>已选</span><span><strong>{{ selectedTask.capacity }}</strong>容量</span><span><strong>{{ selectedTask.capacity - selectedTask.students }}</strong>余量</span></div>
          </section>

          <section class="teacher-panel student-list-panel">
            <div class="student-list-tools">
              <div><h3>已选学生基本信息</h3><p>学生自主选择该实验项目场次，联系方式已脱敏</p></div>
              <div><label class="teacher-search">⌕<input v-model="studentKeyword" placeholder="搜索姓名、学号或专业" /></label><button type="button" @click="showToast('名单导出将在后端接口接入后启用')">↓ 导出名单</button></div>
            </div>
            <div class="teacher-table">
              <div class="teacher-table-row teacher-table-head"><span>姓名</span><span>学号</span><span>专业</span><span>联系方式</span></div>
              <div v-for="student in filteredStudents" :key="student.no" class="teacher-table-row">
                <span class="student-name-cell"><i>{{ student.name.slice(0, 1) }}</i><b>{{ student.name }}</b></span><span>{{ student.no }}</span><span><b>{{ student.major }}</b></span><span>{{ student.phone }}</span>
              </div>
              <div v-if="!filteredStudents.length" class="empty-students">当前筛选条件下没有学生记录</div>
            </div>
          </section>
          <section class="class-management-tip"><span>i</span><p>名单按学生自主选择的具体实验项目场次生成，可包含不同专业的学生；本原型暂不写入真实学生数据。</p></section>
        </template>

        <template v-else-if="activeView === 'adjustments'">
          <section class="adjustment-types">
            <button type="button" @click="openAdjustment('调课申请')"><span class="teal">⇄</span><div><strong>调课申请</strong><small>调整教学周、日期或时段</small></div><i>→</i></button>
            <button type="button" @click="openAdjustment('场地调整申请')"><span class="blue">⌖</span><div><strong>场地调整申请</strong><small>调整实验场次使用的实验室</small></div><i>→</i></button>
            <button type="button" @click="openAdjustment('停课申请')"><span class="purple">Ⅱ</span><div><strong>停课申请</strong><small>因特殊情况暂停实验教学</small></div><i>→</i></button>
          </section>
          <section class="teacher-panel adjustment-records">
            <div class="teacher-panel-title"><div><h3>教学调整记录</h3><p>查看申请进度与实验中心审核意见</p></div><select><option>全部状态</option><option>审核中</option><option>已通过</option><option>已驳回</option></select></div>
            <div class="adjustment-table">
              <div class="adjustment-row adjustment-head"><span>申请编号 / 类型</span><span>实验项目</span><span>原安排</span><span>目标安排</span><span>申请日期</span><span>状态</span></div>
              <div v-for="record in adjustmentRecords" :key="record.id" class="adjustment-row">
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
        <label>实验项目<select v-model="adjustmentProject"><option value="" disabled>请选择实验项目</option><option v-for="task in teachingTasks" :key="task.id">{{ task.project }}</option></select></label>
        <label>目标安排<input v-model="adjustmentTarget" placeholder="例如：第 8 周 周五 5–8 节" /></label>
        <label>申请原因<textarea v-model="adjustmentReason" rows="4" placeholder="请说明调整原因及对学生、场地的影响"></textarea></label>
        <div class="teacher-dialog-warning">当前为演示原型，确认后只加入页面记录，不会真实提交。</div>
        <div class="teacher-dialog-actions"><button type="button" @click="adjustmentDialog = null">取消</button><button type="submit">加入演示列表</button></div>
      </form>
    </div>

    <Transition name="toast"><div v-if="toast" class="teacher-toast" role="status"><span>✓</span>{{ toast }}</div></Transition>
  </div>
</template>
