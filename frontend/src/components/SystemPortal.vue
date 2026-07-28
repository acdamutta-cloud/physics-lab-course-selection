<script setup lang="ts">
import { computed, ref } from 'vue'

type SystemView = 'plans' | 'courses' | 'labs' | 'schedule' | 'approvals'
type ApprovalStatus = '待审批' | '已审批' | 'AI 自动审批' | '已驳回'
type PlanCourseRequirement = {
  id: number
  name: string
  studyYear: string
  semester: string
  prerequisite: string
  requiredCount: number
  optionalCount: number
  requiredProjects: string[]
  optionalProjects: string[]
  orderRule: string
}

const emit = defineEmits<{ logout: [] }>()
const activeView = ref<SystemView>('plans')
const sidebarOpen = ref(false)
const toast = ref('')
const planEditorOpen = ref(false)
const selectedPlan = ref('物理学（师范）')
const planMajor = ref('物理学（师范）')
const planYear = ref('2024')
const activePlanCourseId = ref(1)
const planCourseSeed = ref(3)
const customRequiredProject = ref('')
const customOptionalProject = ref('')
const planCourses = ref<PlanCourseRequirement[]>([
  {
    id: 1,
    name: '大学物理实验（上）',
    studyYear: '第 1 学年',
    semester: '第二学期',
    prerequisite: '大学物理 A（上）',
    requiredCount: 2,
    optionalCount: 1,
    requiredProjects: ['用单摆测量重力加速度', '示波器的原理与使用'],
    optionalProjects: ['霍尔效应及磁场测量'],
    orderRule: '先完成基础测量类项目，再进入电磁类实验项目',
  },
  {
    id: 2,
    name: '近代物理实验',
    studyYear: '第 2 学年',
    semester: '第一学期',
    prerequisite: '大学物理实验（上）',
    requiredCount: 2,
    optionalCount: 1,
    requiredProjects: ['光电效应与普朗克常量测定', '弗兰克—赫兹实验'],
    optionalProjects: ['密立根油滴实验'],
    orderRule: '完成大学物理实验（上）后修读；同一项目仅需完成一次',
  },
])
const activeLab = ref('实验楼 A203')
const scheduleLab = ref('实验楼 A203')
const aiGenerating = ref(false)
const aiGenerated = ref(false)
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

const plans = ref([
  { major: '物理学（师范）', year: '2024', courses: 2, required: 10, optional: 4, prerequisite: '大学物理 A（上）', updated: '2026-03-12', status: '已发布' },
  { major: '应用物理学', year: '2024', courses: 3, required: 12, optional: 6, prerequisite: '普通物理学（上）', updated: '2026-03-08', status: '草稿' },
  { major: '电子信息科学与技术', year: '2023', courses: 1, required: 6, optional: 2, prerequisite: '大学物理 B', updated: '2026-02-25', status: '已发布' },
  { major: '材料物理', year: '2023', courses: 2, required: 8, optional: 4, prerequisite: '力学与热学', updated: '2026-02-21', status: '已停用' },
])

const semesterCourses = [
  {
    name: '大学物理实验（上）',
    code: 'PHYS-LAB-101',
    target: '2024 级物理学、电子信息类',
    weeks: '第 2–10 周',
    projects: [
      { name: '用单摆测量重力加速度', expected: 120, teachers: ['李老师', '陈老师'], equipment: ['单摆实验仪', '光电计时器', '游标卡尺'] },
      { name: '示波器的原理与使用', expected: 108, teachers: ['王老师', '李老师'], equipment: ['数字示波器', '信号发生器', '万用表'] },
      { name: '霍尔效应及磁场测量', expected: 72, teachers: ['陈老师'], equipment: ['霍尔效应实验仪', '数字毫特计', '稳压电源'] },
    ],
  },
  {
    name: '近代物理实验',
    code: 'PHYS-LAB-203',
    target: '2023 级物理学、应用物理学',
    weeks: '第 5–16 周',
    projects: [
      { name: '光电效应与普朗克常量测定', expected: 64, teachers: ['周老师', '孙老师'], equipment: ['光电效应实验箱', '汞灯电源', '数字电流表'] },
      { name: '弗兰克—赫兹实验', expected: 48, teachers: ['孙老师'], equipment: ['弗兰克—赫兹实验仪', '示波器'] },
      { name: '密立根油滴实验', expected: 56, teachers: ['周老师'], equipment: ['密立根油滴仪', '显微测量系统'] },
    ],
  },
]

const labs = [
  { name: '实验楼 A203', type: '基础力学实验室', capacity: 24, manager: '赵老师', availability: '可排课', equipment: [
    { name: '单摆实验仪', model: 'DP-2024', quantity: 12, usable: 12, status: '正常' },
    { name: '光电计时器', model: 'GD-8A', quantity: 12, usable: 11, status: '部分检修' },
    { name: '游标卡尺', model: '0–150 mm', quantity: 30, usable: 30, status: '正常' },
    { name: '电子天平', model: 'FA2004', quantity: 6, usable: 6, status: '正常' },
  ] },
  { name: '实验楼 B105', type: '电学综合实验室', capacity: 20, manager: '钱老师', availability: '可排课', equipment: [
    { name: '数字示波器', model: 'TBS1102C', quantity: 10, usable: 9, status: '部分检修' },
    { name: '信号发生器', model: 'DG1022Z', quantity: 10, usable: 10, status: '正常' },
    { name: '数字万用表', model: 'UT61E+', quantity: 20, usable: 20, status: '正常' },
  ] },
  { name: '近代物理实验室 2', type: '近代物理实验室', capacity: 16, manager: '吴老师', availability: '限制排课', equipment: [
    { name: '光电效应实验箱', model: 'ZKY-GD-4', quantity: 8, usable: 7, status: '部分检修' },
    { name: '汞灯电源', model: 'GY-6', quantity: 8, usable: 8, status: '正常' },
    { name: '微电流测量仪', model: 'EM-5', quantity: 8, usable: 8, status: '正常' },
  ] },
]

const scheduleEvents = ref([
  { lab: '实验楼 A203', day: 3, start: 5, name: '用单摆测量重力加速度', teacher: '李老师', selected: 24, tone: 'teal' },
  { lab: '实验楼 A203', day: 5, start: 5, name: '用单摆测量重力加速度', teacher: '陈老师', selected: 22, tone: 'blue' },
  { lab: '实验楼 B105', day: 4, start: 5, name: '示波器的原理与使用', teacher: '王老师', selected: 20, tone: 'purple' },
  { lab: '近代物理实验室 2', day: 4, start: 5, name: '光电效应与普朗克常量测定', teacher: '周老师', selected: 16, tone: 'blue' },
])

const approvals = ref([
  { id: 'SP20260321008', source: '学生申请', applicant: '张同学 · 2024****18', type: '调课申请', subject: '用单摆测量重力加速度', submitted: '2026-03-21 09:32', status: '待审批' as ApprovalStatus, result: '等待实验中心审核，可用场次已完成冲突检查。' },
  { id: 'SP20260320006', source: '教师申请', applicant: '李老师 · T****026', type: '场地调整', subject: '示波器的原理与使用', submitted: '2026-03-20 16:18', status: 'AI 自动审批' as ApprovalStatus, result: '调整至第 7 周周四第 5–8 节，实验楼 A205；设备与容量满足要求，无时间冲突。' },
  { id: 'SP20260318004', source: '学生申请', applicant: '林同学 · 2024****05', type: '补做申请', subject: '光电效应与普朗克常量测定', submitted: '2026-03-18 11:05', status: '已审批' as ApprovalStatus, result: '安排至第 8 周周五第 5–8 节，近代物理实验室 2，由周老师指导。' },
  { id: 'SP20260315002', source: '学生申请', applicant: '周同学 · 2024****08', type: '换组申请', subject: '霍尔效应及磁场测量 → 密立根油滴实验', submitted: '2026-03-15 14:26', status: '已驳回' as ApprovalStatus, result: '驳回理由：目标项目已达到实验室安全容量上限，暂无可用名额。' },
])

const currentLab = computed(() => labs.find((lab) => lab.name === activeLab.value) ?? labs[0])
const activePlanCourse = computed(() => planCourses.value.find((course) => course.id === activePlanCourseId.value) ?? planCourses.value[0])
const activeProjectCatalog = computed(() => {
  const course = activePlanCourse.value
  if (!course) return []
  const configuredProjects = semesterCourses
    .find((item) => item.name === course.name)
    ?.projects.map((project) => project.name) ?? []
  return [...new Set([...configuredProjects, ...course.requiredProjects, ...course.optionalProjects])]
})
const visibleScheduleEvents = computed(() => scheduleEvents.value.filter((event) => event.lab === scheduleLab.value))
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

function courseDetailsForPlan(plan: { major: string; year: string }) {
  return plan.major === planMajor.value && plan.year === planYear.value ? planCourses.value : []
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
  const target = which === 'required' ? course.requiredProjects : course.optionalProjects
  if (!target.includes(name)) toggleListItem(which, name)
  input.value = ''
}

function addPlanCourse() {
  const id = planCourseSeed.value++
  planCourses.value.push({
    id,
    name: '',
    studyYear: '第 1 学年',
    semester: '第一学期',
    prerequisite: '无先修要求',
    requiredCount: 0,
    optionalCount: 0,
    requiredProjects: [],
    optionalProjects: [],
    orderRule: '',
  })
  activePlanCourseId.value = id
}

function removePlanCourse(id: number) {
  if (planCourses.value.length <= 1) {
    showToast('培养方案至少需要保留一门修读课程')
    return
  }
  planCourses.value = planCourses.value.filter((course) => course.id !== id)
  activePlanCourseId.value = planCourses.value[0].id
}

function savePlanDraft() {
  if (!planMajor.value || !planYear.value || planCourses.value.some((course) => !course.name || !course.studyYear || !course.semester)) {
    showToast('请完整填写专业、培养年份及每门课程的修读学年和学期')
    return
  }
  planEditorOpen.value = false
  showToast('培养方案已保存为前端演示草稿，未写入真实系统')
}

function generateSchedule() {
  if (aiGenerating.value) return
  aiGenerating.value = true
  window.setTimeout(() => {
    if (!aiGenerated.value) {
      scheduleEvents.value.push({
        lab: scheduleLab.value,
        day: 6,
        start: 9,
        name: scheduleLab.value === '实验楼 A203' ? '霍尔效应及磁场测量' : '实验项目候选场次',
        teacher: '陈老师',
        selected: 18,
        tone: 'ai',
      })
    }
    aiGenerated.value = true
    aiGenerating.value = false
    showToast('AI 已生成一版无冲突课表（仅演示，尚未发布）')
  }, 900)
}
</script>

<template>
  <div class="system-app">
    <aside class="system-sidebar" :class="{ open: sidebarOpen }">
      <div class="system-brand"><span class="system-logo"><i></i></span><div><strong>物理实验</strong><small>智能选课系统</small></div></div>
      <nav class="system-nav">
        <p>系统管理中心</p>
        <button v-for="item in navItems" :key="item.id" :class="{ active: activeView === item.id }" @click="navigate(item.id)"><span>{{ item.icon }}</span>{{ item.label }}<i v-if="item.id === 'approvals'" class="system-nav-count">4</i></button>
      </nav>
      <div class="system-status"><span><i></i>系统运行正常</span><small>当前为前端演示环境</small></div>
      <button class="system-logout" @click="emit('logout')">↪　退出演示</button>
    </aside>
    <button v-if="sidebarOpen" class="system-mask" @click="sidebarOpen = false"></button>

    <div class="system-main">
      <header class="system-topbar">
        <button class="system-menu" @click="sidebarOpen = true">☰</button>
        <div class="system-breadcrumb"><span>系统端</span><b>/</b>{{ viewMeta[activeView].title }}</div>
        <div class="system-top-actions"><span class="system-demo-badge">演示数据</span><button class="system-notice" @click="showToast('当前有 4 条申请需要关注')">♢<i>4</i></button><div class="system-profile"><span>管</span><div><strong>系统管理员</strong><small>ADMIN-001</small></div></div></div>
      </header>

      <main class="system-content">
        <div class="system-page-heading">
          <div><h1>{{ viewMeta[activeView].title }}</h1><p>{{ viewMeta[activeView].subtitle }}</p></div>
          <button v-if="activeView === 'plans'" @click="planEditorOpen = true">＋ 新建培养方案</button>
          <button v-else-if="activeView === 'courses'" @click="showToast('已创建一条空白课程配置草稿（演示）')">＋ 添加实验课程</button>
          <button v-else-if="activeView === 'labs'" @click="showToast('已创建一条空白实验室档案（演示）')">＋ 添加实验室</button>
          <button v-else-if="activeView === 'schedule'" class="ai-generate-button" :disabled="aiGenerating" @click="generateSchedule"><span>✦</span>{{ aiGenerating ? 'AI 正在生成...' : 'AI 一键生成课表' }}</button>
        </div>

        <template v-if="activeView === 'plans'">
          <section class="system-summary-grid">
            <article><span class="cyan">▤</span><div><small>培养方案</small><strong>4 <i>套</i></strong><p>覆盖 4 个专业方向</p></div></article>
            <article><span class="blue">◫</span><div><small>已发布</small><strong>2 <i>套</i></strong><p>学生选课规则已生效</p></div></article>
            <article><span class="purple">✦</span><div><small>实验课程</small><strong>3 <i>门</i></strong><p>共关联 16 个实验项目</p></div></article>
            <article><span class="amber">!</span><div><small>待完善</small><strong>1 <i>套</i></strong><p>缺少项目顺序要求</p></div></article>
          </section>
          <section class="system-panel plan-toolbar">
            <div class="plan-tabs"><button v-for="major in ['全部专业','物理学（师范）','应用物理学','电子信息科学与技术','材料物理']" :key="major" :class="{ active: selectedPlan === major }" @click="selectedPlan = major">{{ major }}</button></div>
            <div><label class="system-search">⌕<input placeholder="搜索专业或培养年份" /></label><select><option>全部年份</option><option>2024</option><option>2023</option></select></div>
          </section>
          <div class="plan-card-grid">
            <article v-for="plan in plans.filter(p => selectedPlan === '全部专业' || selectedPlan === p.major)" :key="`${plan.major}-${plan.year}`" class="system-panel plan-card">
              <div class="plan-card-head"><span>{{ plan.major.slice(0, 1) }}</span><div><h3>{{ plan.major }}</h3><p>{{ plan.year }} 级培养方案 · 最近更新 {{ plan.updated }}</p></div><i :class="plan.status">{{ plan.status }}</i></div>
              <div v-if="courseDetailsForPlan(plan).length" class="plan-course-detail-grid">
                <section v-for="(course, index) in courseDetailsForPlan(plan)" :key="course.id" class="plan-course-detail">
                  <header><span>{{ String(index + 1).padStart(2, '0') }}</span><div><small>实验课程</small><h4>{{ course.name || '未命名课程' }}</h4></div></header>
                  <dl>
                    <div><dt>建议修读时间</dt><dd>{{ course.studyYear }} · {{ course.semester }}</dd></div>
                    <div><dt>先修课程要求</dt><dd>{{ course.prerequisite || '无' }}</dd></div>
                  </dl>
                  <div class="plan-project-group required"><strong>必选项目 <i>要求 {{ course.requiredCount }} 项</i></strong><p><span v-for="item in course.requiredProjects" :key="item">{{ item }}</span><em v-if="!course.requiredProjects.length">尚未配置</em></p></div>
                  <div class="plan-project-group optional"><strong>选做项目 <i>最低 {{ course.optionalCount }} 项</i></strong><p><span v-for="item in course.optionalProjects" :key="item">{{ item }}</span><em v-if="!course.optionalProjects.length">尚未配置</em></p></div>
                  <div class="plan-order-rule"><small>项目顺序要求</small><p>{{ course.orderRule || '尚未设置项目顺序要求' }}</p></div>
                </section>
              </div>
              <div v-else class="plan-detail-empty"><span>＋</span><div><strong>尚未录入课程明细</strong><p>请进入“编辑要求”，逐门维护修读学期、必选项目、选做项目和顺序要求。</p></div></div>
              <div class="plan-card-actions"><button @click="planEditorOpen = true">编辑要求</button><button @click="showToast('培养方案详情预览已打开（演示）')">查看详情</button><button>•••</button></div>
            </article>
          </div>
        </template>

        <template v-else-if="activeView === 'courses'">
          <section class="semester-banner">
            <div><span>当前开课学期</span><strong>2025–2026 学年 第二学期</strong><p>选课开放时间：演示日期 · 当前课程配置仅为前端示例</p></div>
            <div><span>已设置课程</span><strong>2 <i>门</i></strong></div><div><span>实验项目</span><strong>6 <i>项</i></strong></div><div><span>预计选课</span><strong>568 <i>人次</i></strong></div>
          </section>
          <section v-for="course in semesterCourses" :key="course.code" class="system-panel course-config-card">
            <header><span class="course-config-icon">{{ course.name.slice(0, 1) }}</span><div><small>{{ course.code }}</small><h3>{{ course.name }}</h3><p>面向：{{ course.target }}　·　开设周次：{{ course.weeks }}</p></div><i>本学期开设</i><button @click="showToast('课程基础信息已进入编辑状态（演示）')">编辑课程</button></header>
            <div class="course-project-table">
              <div class="course-project-row course-project-head"><span>实验项目</span><span>预计人数</span><span>负责教师</span><span>所需实验器材</span><span>配置状态</span><span>操作</span></div>
              <div v-for="project in course.projects" :key="project.name" class="course-project-row"><span><b>{{ project.name }}</b><small>四节连堂 · 单次完成</small></span><span><strong>{{ project.expected }}</strong> 人次</span><span class="tag-cell"><i v-for="teacher in project.teachers" :key="teacher">{{ teacher }}</i></span><span class="tag-cell"><i v-for="item in project.equipment" :key="item">{{ item }}</i></span><span><em>已配置</em></span><span><button @click="showToast(`正在编辑“${project.name}”（演示）`)">编辑</button></span></div>
            </div>
          </section>
        </template>

        <template v-else-if="activeView === 'labs'">
          <section class="lab-card-grid">
            <button v-for="lab in labs" :key="lab.name" :class="{ active: activeLab === lab.name }" @click="activeLab = lab.name"><span class="lab-card-icon">⌂</span><div><small>{{ lab.type }}</small><strong>{{ lab.name }}</strong><p>负责人：{{ lab.manager }}</p></div><i :class="{ limited: lab.availability !== '可排课' }">{{ lab.availability }}</i><em><b>{{ lab.capacity }}</b> 人 / 次</em></button>
          </section>
          <section class="system-panel equipment-panel">
            <div class="system-panel-title"><div><h3>{{ currentLab.name }} · 设备台账</h3><p>实验室单次最多容纳 {{ currentLab.capacity }} 人开展实验</p></div><div><label class="system-search">⌕<input placeholder="搜索器材名称或型号" /></label><button @click="showToast('已创建一条空白设备记录（演示）')">＋ 添加器材</button></div></div>
            <div class="equipment-table">
              <div class="equipment-row equipment-head"><span>器材名称</span><span>型号 / 规格</span><span>账面数量</span><span>可用数量</span><span>使用状态</span><span>操作</span></div>
              <div v-for="item in currentLab.equipment" :key="item.name" class="equipment-row"><span><i>◇</i><b>{{ item.name }}</b></span><span>{{ item.model }}</span><span>{{ item.quantity }} 台 / 套</span><span><strong>{{ item.usable }}</strong> 台 / 套</span><span><em :class="{ warning: item.status !== '正常' }">{{ item.status }}</em></span><span><button @click="showToast(`已打开“${item.name}”设备档案（演示）`)">管理</button></span></div>
            </div>
          </section>
          <section class="lab-capacity-note"><span>i</span><p>实验室容量应取场地安全容量、实验台位数及关键器材可用套数中的最小值。当前数据均为演示配置。</p></section>
        </template>

        <template v-else-if="activeView === 'schedule'">
          <section class="schedule-control-bar system-panel">
            <label>实验室<select v-model="scheduleLab"><option v-for="lab in labs" :key="lab.name">{{ lab.name }}</option></select></label>
            <label>教学周<select><option>第 6 教学周</option><option>第 7 教学周</option></select></label>
            <div class="schedule-week-range"><button>‹</button><strong>2026.03.22 — 03.28</strong><button>›</button></div>
            <span v-if="aiGenerated" class="ai-plan-tag">✦ AI 方案 · 未发布</span>
          </section>
          <section class="system-panel system-schedule-wrap">
            <div class="system-schedule">
              <div class="system-time-corner">节次</div>
              <div v-for="(day,index) in ['周日 03/22','周一 03/23','周二 03/24','周三 03/25','周四 03/26','周五 03/27','周六 03/28']" :key="day" class="system-day-head" :style="{ gridColumn: index + 2 }"><strong>{{ day.split(' ')[0] }}</strong><span>{{ day.split(' ')[1] }}</span></div>
              <div v-for="period in 12" :key="period" class="system-period" :class="{ boundary: period === 4 || period === 8 }" :style="{ gridRow: period + 1 }">第 {{ period }} 节</div>
              <div v-for="day in 7" :key="day" class="system-day-column" :style="{ gridColumn: day + 1, gridRow: '2 / 14' }"></div>
              <article v-for="event in visibleScheduleEvents" :key="`${event.name}-${event.day}-${event.start}`" class="system-schedule-event" :class="event.tone" :style="{ gridColumn: event.day + 1, gridRow: `${event.start + 1} / span 4` }"><span>{{ event.start === 1 ? '第 1–4 节' : event.start === 5 ? '第 5–8 节' : '第 9–12 节' }}</span><strong>{{ event.name }}</strong><small>{{ event.teacher }} · 已选 {{ event.selected }} 人</small></article>
            </div>
          </section>
          <section class="ai-schedule-note"><span>✦</span><div><strong>AI 排课依据</strong><p>综合实验室容量、器材数量、教师可用时间、项目顺序、学生需求与时间冲突生成。当前为前端演示，生成结果不会自动发布。</p></div><button v-if="aiGenerated" @click="showToast('AI 方案已保存为演示草稿，尚未发布')">保存为草稿</button></section>
        </template>

        <template v-else>
          <section class="approval-summary">
            <article><span>待审批</span><strong>1</strong><i class="pending"></i></article><article><span>已审批</span><strong>1</strong><i class="approved"></i></article><article><span>AI 自动审批</span><strong>1</strong><i class="ai"></i></article><article><span>已驳回</span><strong>1</strong><i class="rejected"></i></article>
          </section>
          <section class="system-panel approval-panel">
            <div class="system-panel-title"><div><h3>申请审批列表</h3><p>审批结果需包含具体执行方案或明确驳回理由</p></div><div><label class="system-search">⌕<input placeholder="搜索申请人、编号或项目" /></label><select v-model="approvalFilter"><option>全部状态</option><option>待审批</option><option>已审批</option><option>AI 自动审批</option><option>已驳回</option></select></div></div>
            <div class="approval-table">
              <div class="approval-row approval-head"><span>申请编号 / 来源</span><span>申请人</span><span>申请类型</span><span>关联项目</span><span>提交时间</span><span>审批状态</span><span>操作</span></div>
              <div v-for="item in visibleApprovals" :key="item.id" class="approval-row"><span><b>{{ item.id }}</b><small>{{ item.source }}</small></span><span>{{ item.applicant }}</span><span>{{ item.type }}</span><span>{{ item.subject }}</span><span>{{ item.submitted }}</span><span><i class="approval-status" :class="{ pending: item.status === '待审批', approved: item.status === '已审批', ai: item.status === 'AI 自动审批', rejected: item.status === '已驳回' }">{{ item.status }}</i></span><span><button @click="selectedApprovalId = item.id">{{ item.status === '待审批' ? '去审批' : '查看结果' }}</button></span></div>
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
            <label>专业<select v-model="planMajor"><option>物理学（师范）</option><option>应用物理学</option><option>电子信息科学与技术</option><option>材料物理</option></select></label>
            <label>培养年份<select v-model="planYear"><option>2024</option><option>2023</option><option>2022</option></select></label>
          </div>
        </section>
        <section>
          <div class="plan-section-heading"><div><h3>02　课程修读要求</h3><p>同一培养方案可以配置多门课程，并分别指定修读学年和学期</p></div><button type="button" @click="addPlanCourse">＋ 添加课程要求</button></div>
          <div class="plan-course-list">
            <article v-for="(course, index) in planCourses" :key="course.id" :class="{ active: activePlanCourseId === course.id }" @click="activePlanCourseId = course.id">
              <span>{{ String(index + 1).padStart(2, '0') }}</span>
              <div><label>实验课程名称<input v-model="course.name" list="plan-course-options" placeholder="输入或选择课程名称" /></label><small class="course-requirement-summary">必选 {{ course.requiredProjects.length }} 项 · 选做 {{ course.optionalProjects.length }} 项</small></div>
              <label>建议修读学年<select v-model="course.studyYear"><option>第 1 学年</option><option>第 2 学年</option><option>第 3 学年</option><option>第 4 学年</option></select></label>
              <label>建议修读学期<select v-model="course.semester"><option>第一学期</option><option>第二学期</option><option>小学期</option></select></label>
              <label>先修课程要求<input v-model="course.prerequisite" list="prerequisite-options" placeholder="输入或选择先修课程" /></label>
              <button type="button" aria-label="删除课程要求" @click.stop="removePlanCourse(course.id)">×</button>
            </article>
            <datalist id="plan-course-options"><option>大学物理实验（上）</option><option>大学物理实验（下）</option><option>近代物理实验</option><option>专业物理实验</option></datalist>
            <datalist id="prerequisite-options"><option>大学物理 A（上）</option><option>大学物理实验（上）</option><option>普通物理学（上）</option><option>无先修要求</option></datalist>
          </div>
        </section>
        <section v-if="activePlanCourse">
          <div class="plan-section-heading"><div><h3>03　实验项目要求</h3><p>当前配置：{{ activePlanCourse.name || '未命名课程' }} · {{ activePlanCourse.studyYear }} {{ activePlanCourse.semester }}</p></div><select v-model="activePlanCourseId"><option v-for="course in planCourses" :key="course.id" :value="course.id">{{ course.name || '未命名课程' }} · {{ course.studyYear }} {{ course.semester }}</option></select></div>
          <div class="requirement-columns">
            <div><label>本课程必选项目数量<input v-model.number="activePlanCourse.requiredCount" type="number" min="0" /></label><p>选择“{{ activePlanCourse.name || '当前课程' }}”的必选项目</p><button v-for="item in activeProjectCatalog" :key="item" type="button" :class="{ selected: activePlanCourse.requiredProjects.includes(item) }" @click="toggleListItem('required',item)"><i>{{ activePlanCourse.requiredProjects.includes(item) ? '✓' : '＋' }}</i>{{ item }}</button><div class="custom-project-row"><input v-model="customRequiredProject" type="text" placeholder="手动输入本课程必选项目" @keyup.enter.prevent="addCustomProject('required')" /><button type="button" @click="addCustomProject('required')">添加</button></div></div>
            <div><label>本课程选做项目最低数量<input v-model.number="activePlanCourse.optionalCount" type="number" min="0" /></label><p>选择“{{ activePlanCourse.name || '当前课程' }}”的选做项目</p><button v-for="item in activeProjectCatalog" :key="item" type="button" :class="{ selected: activePlanCourse.optionalProjects.includes(item) }" @click="toggleListItem('optional',item)"><i>{{ activePlanCourse.optionalProjects.includes(item) ? '✓' : '＋' }}</i>{{ item }}</button><div class="custom-project-row"><input v-model="customOptionalProject" type="text" placeholder="手动输入本课程选做项目" @keyup.enter.prevent="addCustomProject('optional')" /><button type="button" @click="addCustomProject('optional')">添加</button></div></div>
          </div>
        </section>
        <section v-if="activePlanCourse">
          <h3>04　当前课程的顺序与约束</h3>
          <label class="full-field">实验项目顺序要求<textarea v-model="activePlanCourse.orderRule" rows="3" placeholder="例如：必须先完成基础测量项目，再选择近代物理项目"></textarea></label>
          <div class="rule-options"><label><input type="checkbox" checked />未完成先修课程时禁止选课</label><label><input type="checkbox" checked />必做项目优先于选做项目</label><label><input type="checkbox" />允许特殊情况跳过项目顺序</label></div>
        </section>
        <footer><p>当前为演示表单，保存不会修改真实培养方案。</p><button type="button" @click="planEditorOpen = false">取消</button><button type="submit">保存为演示草稿</button></footer>
      </form>
    </div>

    <div v-if="selectedApproval" class="system-dialog-backdrop" @click.self="selectedApprovalId = null">
      <aside class="approval-detail">
        <header><div><span>✓</span><div><h2>审批详情</h2><p>{{ selectedApproval.id }}</p></div></div><button @click="selectedApprovalId = null">×</button></header>
        <dl><div><dt>申请人</dt><dd>{{ selectedApproval.applicant }}</dd></div><div><dt>申请类型</dt><dd>{{ selectedApproval.type }}</dd></div><div><dt>关联项目</dt><dd>{{ selectedApproval.subject }}</dd></div><div><dt>当前状态</dt><dd><i class="approval-status" :class="{ pending: selectedApproval.status === '待审批', approved: selectedApproval.status === '已审批', ai: selectedApproval.status === 'AI 自动审批', rejected: selectedApproval.status === '已驳回' }">{{ selectedApproval.status }}</i></dd></div></dl>
        <section :class="{ rejection: selectedApproval.status === '已驳回' }"><span>{{ selectedApproval.status === '已驳回' ? '驳回理由' : selectedApproval.status === '待审批' ? 'AI 审批建议' : '审批通过方案' }}</span><p>{{ selectedApproval.result }}</p></section>
        <footer v-if="selectedApproval.status === '待审批'"><button @click="showToast('已生成驳回意见草稿，未提交'); selectedApprovalId = null">驳回（演示）</button><button @click="showToast('已生成审批通过方案，未提交'); selectedApprovalId = null">通过（演示）</button></footer><footer v-else><button @click="selectedApprovalId = null">关闭</button></footer>
      </aside>
    </div>

    <Transition name="toast"><div v-if="toast" class="system-toast"><span>✓</span>{{ toast }}</div></Transition>
  </div>
</template>
