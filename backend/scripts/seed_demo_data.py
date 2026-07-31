import asyncio
import math
import os
import random
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid5

from pwdlib import PasswordHash
from sqlalchemy import func, select

from app.data.physics_resources import (
    EQUIPMENT_SPECS as PHYSICS_EQUIPMENT_SPECS,
)
from app.data.physics_resources import (
    LABORATORY_SPECS,
    PROJECT_LAB_CAPACITIES,
    PROJECT_RESOURCE_SPECS,
    build_reserved_inventory_specs,
)
from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models import (
    AcademicTerm,
    Campus,
    EquipmentType,
    ExperimentCourse,
    ExperimentProject,
    ExperimentSession,
    LabEquipmentInventory,
    Laboratory,
    LabProjectCapability,
    Major,
    OperationLog,
    ProjectDemand,
    ProjectEquipmentRequirement,
    RuleConfig,
    RuleSet,
    ScheduleVersion,
    SelectionWindow,
    Student,
    StudentBusyBitmap,
    StudentClass,
    Teacher,
    TeacherAvailability,
    TeacherProjectQualification,
    TeachingTask,
    TeachingTaskCohort,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingPlanProject,
    UserAccount,
)

DEMO_NAMESPACE = UUID("a869341b-7477-4d3a-81b9-57e212579ec9")
SOFT_RULE_NAMESPACE = UUID("72bb4302-7d4a-49b9-b3c6-969fe2846cb2")
DEMO_ENROLLMENT_YEAR = 2024
STUDENTS_PER_MAJOR = 80
CLASSES_PER_MAJOR = 2
STUDENTS_PER_CLASS = STUDENTS_PER_MAJOR // CLASSES_PER_MAJOR

MAJOR_SPECS = [
    ("DEMO-ME", "机械工程"),
    ("DEMO-EEA", "电气工程及其自动化"),
    ("DEMO-EIE", "电子信息工程"),
    ("DEMO-CS", "计算机科学与技术"),
    ("DEMO-SE", "软件工程"),
    ("DEMO-CE", "土木工程"),
    ("DEMO-MSE", "材料科学与工程"),
    ("DEMO-CH", "化学工程与工艺"),
    ("DEMO-AU", "自动化"),
    ("DEMO-EPE", "能源与动力工程"),
    ("DEMO-VE", "车辆工程"),
    ("DEMO-IOT", "物联网工程"),
    ("DEMO-AI", "人工智能"),
    ("DEMO-OPTO", "光电信息科学与工程"),
    ("DEMO-AP", "应用物理学"),
]

COURSE_SPECS = [
    ("DEMO-PHY101", "大学物理实验", "基础测量、力热电光综合训练"),
    ("DEMO-PHY201", "工程物理实验", "面向工程应用的综合物理实验"),
    ("DEMO-PHY301", "近代物理实验", "近代物理与前沿测量实验"),
]

PROJECT_NAMES = {
    "DEMO-PHY101": [
        ("长度与密度测量", "BASIC"), ("单摆测重力加速度", "MECHANICS"),
        ("气垫导轨碰撞", "MECHANICS"), ("杨氏模量测量", "MECHANICS"),
        ("液体表面张力", "BASIC"), ("金属比热容测量", "BASIC"),
        ("示波器原理与使用", "ELECTRICITY"), ("伏安法测电阻", "ELECTRICITY"),
        ("薄透镜焦距测量", "OPTICS"), ("光的干涉与衍射", "OPTICS"),
    ],
    "DEMO-PHY201": [
        ("霍尔效应与磁场测量", "ELECTRICITY"), ("RLC暂态过程", "ELECTRICITY"),
        ("交流电桥", "ELECTRICITY"), ("传感器特性研究", "ELECTRICITY"),
        ("超声波声速测量", "BASIC"), ("热电偶定标", "BASIC"),
        ("太阳能电池特性", "MODERN"), ("光纤传输特性", "OPTICS"),
        ("振动系统频率响应", "MECHANICS"), ("真空获得与测量", "MODERN"),
    ],
    "DEMO-PHY301": [
        ("光电效应与普朗克常量", "MODERN"), ("弗兰克—赫兹实验", "MODERN"),
        ("密立根油滴实验", "MODERN"), ("电子衍射", "MODERN"),
        ("塞曼效应", "MODERN"), ("核磁共振", "MODERN"),
        ("微波布拉格衍射", "MODERN"), ("激光全息照相", "OPTICS"),
        ("单光子计数", "MODERN"), ("X射线特征谱", "MODERN"),
    ],
}

PROJECT_SPECS = [
    (f"{course_code}-P{index:02d}", course_code, name, category, "0.6000")
    for course_code, items in PROJECT_NAMES.items()
    for index, (name, category) in enumerate(items, start=1)
]

RULE_SET_SPECS = {
    "SCHEDULING": {
        "code": "DEMO-PHYSICS-LAB-SCHEDULING",
        "name": "物理实验模拟排课规则集 V1",
        "status": "PUBLISHED",
    },
    "SELECTION": {
        "code": "DEMO-PHYSICS-LAB-SELECTION",
        "name": "物理实验模拟选课规则集 V1",
        "status": "PUBLISHED",
    },
    "ADJUSTMENT": {
        "code": "DEMO-PHYSICS-LAB-ADJUSTMENT",
        "name": "物理实验模拟调整规则集 V1",
        "status": "DRAFT",
    },
    "APPROVAL": {
        "code": "DEMO-PHYSICS-LAB-APPROVAL",
        "name": "物理实验模拟审批规则集 V1",
        "status": "DRAFT",
    },
}

RULE_SPECS = {
    "SCHEDULING": [
        ("TEACHER_TIME_CONFLICT", "教师时间不得冲突", "BLOCK", 100, {}),
        ("LAB_TIME_CONFLICT", "实验室时间不得冲突", "BLOCK", 100, {}),
        ("SESSION_CAPACITY", "场次不得超过容量", "BLOCK", 100, {}),
        ("TEACHER_QUALIFICATION", "教师须具备项目资格", "BLOCK", 100, {}),
        ("LAB_CAPABILITY", "实验室须支持对应项目", "BLOCK", 100, {}),
        ("EQUIPMENT_AVAILABLE", "设备数量须满足要求", "BLOCK", 100, {}),
        ("TEACHER_BALANCE", "教师工作量尽量均衡", "SCORE", 50, {}),
        ("EVENING_PENALTY", "尽量减少晚间场次", "SCORE", 40, {}),
    ],
    "SELECTION": [
        ("STUDENT_TIME_CONFLICT", "学生时间不得冲突", "BLOCK", 100, {}),
        ("PROJECT_DUPLICATE", "项目不得重复修读", "BLOCK", 100, {}),
    ],
    "ADJUSTMENT": [
        ("RESCHEDULE_SAME_PROJECT", "调课须保持实验项目不变", "BLOCK", 100, {}),
        ("RESCHEDULE_TARGET_CAPACITY", "调课目标场次须有余量", "BLOCK", 100, {}),
        ("RESCHEDULE_STUDENT_TIME", "调课不得与学生课表冲突", "BLOCK", 100, {}),
        ("GROUP_CHANGE_CAPACITY", "换组目标组须有容量", "BLOCK", 100, {}),
        (
            "GROUP_CHANGE_FEATURE_ENABLED",
            "未启用场次内分组时不得换组",
            "BLOCK",
            100,
            {},
        ),
        ("MAKEUP_ABSENCE_REQUIRED", "补做须存在缺做记录", "BLOCK", 100, {}),
        ("MAKEUP_WITHIN_DEADLINE", "补做须在规定期限内申请", "BLOCK", 100, {}),
        (
            "REPLACEMENT_TEACHER_QUALIFIED",
            "替换教师须具备项目资格",
            "BLOCK",
            100,
            {},
        ),
        ("RESOURCE_ISSUE_SUSPEND", "资源异常须暂停受影响安排", "WARN", 100, {}),
        ("LAB_UNAVAILABLE_BLOCK", "停用实验室不得继续使用", "BLOCK", 100, {}),
        (
            "MINIMIZE_PUBLISHED_SCHEDULE_CHANGE",
            "调整应尽量减少对已发布课表的影响",
            "SCORE",
            50,
            {},
        ),
    ],
    "APPROVAL": [
        (
            "SINGLE_STUDENT_NO_SESSION_CHANGE_AUTO_APPROVE",
            "单人且不改变正式场次时自动批准",
            "ROUTE",
            100,
            {"route": "AUTO_APPROVE"},
        ),
        (
            "HARD_CONFLICT_AUTO_REJECT",
            "存在阻断级硬冲突时自动驳回",
            "ROUTE",
            100,
            {"route": "AUTO_REJECT"},
        ),
        (
            "OFFICIAL_SESSION_CHANGE_MANUAL_REVIEW",
            "改变正式场次时转管理员审批",
            "ROUTE",
            100,
            {"route": "MANUAL_REVIEW"},
        ),
        (
            "MULTI_STUDENT_IMPACT_MANUAL_REVIEW",
            "影响多名学生时转管理员审批",
            "ROUTE",
            100,
            {"route": "MANUAL_REVIEW"},
        ),
        (
            "INITIAL_SCHEDULE_PUBLISH_ADMIN_CONFIRM",
            "初始课表发布须管理员确认",
            "ROUTE",
            100,
            {"route": "ADMIN_CONFIRM"},
        ),
        (
            "RULE_SET_PUBLISH_ADMIN_CONFIRM",
            "规则启用须管理员确认",
            "ROUTE",
            100,
            {"route": "ADMIN_CONFIRM"},
        ),
        (
            "SCHEDULE_ROLLBACK_ADMIN_CONFIRM",
            "课表回滚须管理员确认",
            "ROUTE",
            100,
            {"route": "ADMIN_CONFIRM"},
        ),
    ],
}

NEW_SCHEDULING_SOFT_RULES = [
    (
        "TEACHER_COMPACTNESS",
        "减少教师课时过于分散",
        "SCORE",
        0,
        {"metric": "teacher_schedule_compactness"},
    ),
    (
        "TEACHER_CONSECUTIVE_LOAD",
        "避免教师连续承担过多实验",
        "SCORE",
        0,
        {"metric": "teacher_consecutive_load"},
    ),
    (
        "LAB_UTILIZATION_BALANCE",
        "平衡实验室利用率",
        "SCORE",
        0,
        {"metric": "laboratory_utilization_balance"},
    ),
    (
        "STUDENT_AVAILABILITY_COVERAGE",
        "提高学生可选时间覆盖率",
        "SCORE",
        0,
        {"metric": "student_availability_coverage"},
    ),
    (
        "WEEKEND_PENALTY",
        "尽量减少周末实验",
        "SCORE",
        0,
        {"metric": "weekend_session_count"},
    ),
    (
        "TEACHER_PREFERRED_TIME",
        "尽量满足教师偏好时间",
        "SCORE",
        0,
        {"metric": "teacher_preferred_time_match"},
    ),
    (
        "TEACHER_TARGET_LOAD_SCORE",
        "指定教师课时负荷评分",
        "SCORE",
        0,
        {"metric": "target_teacher_assigned_session_count"},
    ),
    (
        "COURSE_EARLY_WEEK_PREFERENCE",
        "指定课程前置周安排评分",
        "SCORE",
        0,
        {"metric": "target_course_late_session_ratio"},
    ),
    (
        "PROJECT_EARLY_WEEK_PREFERENCE",
        "指定项目前置周安排评分",
        "SCORE",
        0,
        {"metric": "target_project_late_session_ratio"},
    ),
]

NEW_SCHEDULING_SOFT_CONDITIONS = {
    "WEEKEND_PENALTY": {"weekend_days": [1, 7]},
    "TEACHER_PREFERRED_TIME": {
        "availability_type": "PREFERRED",
    },
    "TEACHER_TARGET_LOAD_SCORE": {
        "configuration_status": "RUNTIME",
        "target_source": "SCHEDULE_JOB_INPUT",
    },
    "COURSE_EARLY_WEEK_PREFERENCE": {
        "configuration_status": "RUNTIME",
        "target_source": "SCHEDULE_JOB_INPUT",
        "parameter": "preferred_end_week",
    },
    "PROJECT_EARLY_WEEK_PREFERENCE": {
        "configuration_status": "RUNTIME",
        "target_source": "SCHEDULE_JOB_INPUT",
        "parameter": "preferred_end_week",
    },
}

SCHEDULING_SOFT_RULE_INITIALS = {
    "STUDENT_AVAILABILITY_COVERAGE": {
        "weight": Decimal(25),
        "priority": 90,
    },
    "TEACHER_BALANCE": {
        "weight": Decimal(15),
        "priority": 80,
    },
    "EVENING_PENALTY": {
        "weight": Decimal(12),
        "priority": 70,
    },
    "WEEKEND_PENALTY": {
        "weight": Decimal(10),
        "priority": 70,
    },
    "TEACHER_COMPACTNESS": {
        "weight": Decimal(10),
        "priority": 60,
    },
    "TEACHER_CONSECUTIVE_LOAD": {
        "weight": Decimal(10),
        "priority": 60,
    },
    "TEACHER_PREFERRED_TIME": {
        "weight": Decimal(10),
        "priority": 60,
    },
    "LAB_UTILIZATION_BALANCE": {
        "weight": Decimal(8),
        "priority": 50,
    },
    "TEACHER_TARGET_LOAD_SCORE": {
        "weight": Decimal(0),
        "priority": 40,
    },
    "COURSE_EARLY_WEEK_PREFERENCE": {
        "weight": Decimal(0),
        "priority": 40,
    },
    "PROJECT_EARLY_WEEK_PREFERENCE": {
        "weight": Decimal(0),
        "priority": 40,
    },
}

SURNAMES = "张王李赵陈刘杨黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤尹黎易常武乔贺赖龚文"
GIVEN_NAMES = ["小明","小红","子涵","雨桐","浩然","欣怡","宇轩","思琪","嘉豪","梦瑶","梓涵","俊杰","诗涵","晨曦","文博","佳宁","明轩","若彤","天宇","雅婷"]


def demo_id(entity: str, code: str) -> UUID:
    return uuid5(DEMO_NAMESPACE, f"{entity}:{code}")


def build_busy_bitmap(student_index: int) -> bytes:
    weeks = 17
    days_per_week = 7
    slots_per_day = 12
    bitmap = bytearray(math.ceil(weeks * days_per_week * slots_per_day / 8))

    weekly_blocks = [
        (2 + student_index % 5, 1, 4),
        (4 + student_index % 3, 5, 8),
    ]
    for week_no in range(1, weeks + 1):
        for day_of_week, start_slot, end_slot in weekly_blocks:
            for slot_no in range(start_slot, end_slot + 1):
                index = (
                    (week_no - 1) * days_per_week * slots_per_day
                    + (day_of_week - 1) * slots_per_day
                    + (slot_no - 1)
                )
                bitmap[index // 8] |= 1 << (index % 8)
    return bytes(bitmap)


async def assert_seed_is_safe(session) -> bool:
    major_count = await session.scalar(
        select(func.count())
        .select_from(Major)
        .where(Major.code.like("DEMO-%"))
    )
    if major_count == 0:
        return True

    student_count = await session.scalar(
        select(func.count())
        .select_from(Student)
        .where(Student.student_no.like("D2024%"))
    )
    if major_count == len(MAJOR_SPECS) and student_count == (
        len(MAJOR_SPECS) * STUDENTS_PER_MAJOR
    ):
        print(
            "模拟数据已完整存在："
            f"{major_count} 个专业，{student_count} 名学生；跳过重复写入。"
        )
        return False

    raise RuntimeError(
        "检测到不完整的 DEMO 数据。为避免覆盖现有数据，Seed 已停止；"
        "请先人工检查数据库。"
    )


async def seed_demo_data() -> None:
    now = datetime.now(UTC)
    demo_password = os.getenv("DEMO_ACCOUNT_PASSWORD", "Demo@123456")
    password_hash = PasswordHash.recommended().hash(demo_password)

    async with AsyncSessionFactory.begin() as session:
        if not await assert_seed_is_safe(session):
            return

        main_campus = Campus(
            id=demo_id("campus", "MAIN"),
            code="DEMO-MAIN",
            name="模拟主校区",
            address="仅用于开发测试",
            status="ACTIVE",
        )
        east_campus = Campus(
            id=demo_id("campus", "EAST"),
            code="DEMO-EAST",
            name="模拟东校区",
            address="仅用于开发测试",
            status="ACTIVE",
        )
        session.add_all([main_campus, east_campus])
        await session.flush()

        majors = [
            Major(
                id=demo_id("major", code),
                code=code,
                name=f"{name}（模拟）",
                degree_type="ENGINEERING",
                status="ACTIVE",
            )
            for code, name in MAJOR_SPECS
        ]
        session.add_all(majors)
        await session.flush()

        term = AcademicTerm(
            id=demo_id("term", "2025-2026-2"),
            code="DEMO-2025-2026-2",
            academic_year="2025-2026",
            semester_no=2,
            start_date=date(2026, 2, 23),
            end_date=date(2026, 6, 28),
            total_weeks=17,
            days_per_week=7,
            slots_per_day=12,
            status="CLOSED",
        )
        session.add(term)
        await session.flush()

        admin_account = UserAccount(
            id=demo_id("user", "ADMIN"),
            login_name="demo_admin",
            password_hash=password_hash,
            user_type="ADMIN",
            status="ACTIVE",
        )
        session.add(admin_account)
        await session.flush()

        teachers: list[Teacher] = []
        teacher_accounts: list[UserAccount] = []
        teacher_names = ["张伟", "王芳", "李强", "赵敏", "陈杰", "刘洋", "杨静", "黄磊", "周颖", "吴昊"]
        for index, teacher_name in enumerate(teacher_names, start=1):
            employee_no = f"DEMO-T{index:03d}"
            account = UserAccount(
                id=demo_id("user", employee_no),
                login_name=employee_no.lower(),
                password_hash=password_hash,
                user_type="TEACHER",
                status="ACTIVE",
            )
            teacher = Teacher(
                id=demo_id("teacher", employee_no),
                user_id=account.id,
                employee_no=employee_no,
                name=teacher_name,
                campus_id=main_campus.id if index <= 7 else east_campus.id,
                department="物理实验中心（模拟）",
                title="实验教师",
                status="ACTIVE",
            )
            teacher_accounts.append(account)
            teachers.append(teacher)
        session.add_all(teacher_accounts)
        await session.flush()
        session.add_all(teachers)
        await session.flush()

        classes: list[StudentClass] = []
        student_accounts: list[UserAccount] = []
        students: list[Student] = []
        busy_bitmaps: list[StudentBusyBitmap] = []
        global_student_index = 0
        name_rng = random.Random(20260728)

        for major_index, major in enumerate(majors, start=1):
            major_classes: list[StudentClass] = []
            for class_index in range(1, CLASSES_PER_MAJOR + 1):
                class_code = (
                    f"DEMO-{DEMO_ENROLLMENT_YEAR}-"
                    f"{major_index:02d}-{class_index:02d}"
                )
                student_class = StudentClass(
                    id=demo_id("class", class_code),
                    code=class_code,
                    name=(
                        f"{MAJOR_SPECS[major_index - 1][1]}"
                        f"{class_index:02d}班（模拟）"
                    ),
                    major_id=major.id,
                    enrollment_year=DEMO_ENROLLMENT_YEAR,
                    campus_id=(
                        main_campus.id if major_index <= 7 else east_campus.id
                    ),
                    status="ACTIVE",
                )
                classes.append(student_class)
                major_classes.append(student_class)

            for local_index in range(1, STUDENTS_PER_MAJOR + 1):
                global_student_index += 1
                student_no = (
                    f"D{DEMO_ENROLLMENT_YEAR}"
                    f"{major_index:02d}{local_index:04d}"
                )
                student_class = major_classes[
                    (local_index - 1) // STUDENTS_PER_CLASS
                ]
                account = UserAccount(
                    id=demo_id("user", student_no),
                    login_name=student_no.lower(),
                    password_hash=password_hash,
                    user_type="STUDENT",
                    status="ACTIVE",
                )
                student = Student(
                    id=demo_id("student", student_no),
                    user_id=account.id,
                    student_no=student_no,
                    name=f"{name_rng.choice(SURNAMES)}{name_rng.choice(GIVEN_NAMES)}",
                    gender="MALE" if global_student_index % 2 else "FEMALE",
                    enrollment_year=DEMO_ENROLLMENT_YEAR,
                    major_id=major.id,
                    class_id=student_class.id,
                    campus_id=student_class.campus_id,
                    academic_status="ACTIVE",
                )
                student_accounts.append(account)
                students.append(student)
                busy_bitmaps.append(
                    StudentBusyBitmap(
                        id=demo_id("busy_bitmap", student_no),
                        student_id=student.id,
                        term_id=term.id,
                        start_week=1,
                        end_week=17,
                        days_per_week=7,
                        slots_per_day=12,
                        bitmap=build_busy_bitmap(global_student_index),
                        mapping_version=1,
                        source_version="DEMO-V1",
                    )
                )

        session.add_all(classes)
        await session.flush()
        session.add_all(student_accounts)
        await session.flush()
        session.add_all(students)
        await session.flush()
        session.add_all(busy_bitmaps)
        await session.flush()

        courses = [
            ExperimentCourse(
                id=demo_id("course", course_code),
                course_code=course_code,
                course_name=course_name,
                credits=Decimal("1.0"),
                default_slots=4,
                description=description,
                status="ACTIVE",
            )
            for course_code, course_name, description in COURSE_SPECS
        ]
        course_by_code = {course.course_code: course for course in courses}
        session.add_all(courses)
        await session.flush()

        projects: list[ExperimentProject] = []
        for (
            project_code,
            course_code,
            project_name,
            category,
            historical_ratio,
        ) in PROJECT_SPECS:
            project = ExperimentProject(
                id=demo_id("project", project_code),
                project_code=project_code,
                course_id=course_by_code[course_code].id,
                project_name=f"{project_name}（模拟）",
                category=category,
                required_slots=4,
                group_mode="INDIVIDUAL",
                default_group_size=1,
                historical_selection_ratio=Decimal(historical_ratio),
                status="ACTIVE",
            )
            projects.append(project)
        project_by_code = {
            project.project_code: project for project in projects
        }
        session.add_all(projects)
        await session.flush()

        training_plans: list[TrainingPlan] = []
        plan_courses: list[TrainingPlanCourse] = []
        plan_projects: list[TrainingPlanProject] = []
        for major in majors:
            plan = TrainingPlan(
                id=demo_id("plan", major.code),
                plan_code=f"DEMO-PLAN-{major.code}-2024-V1",
                major_id=major.id,
                enrollment_year=DEMO_ENROLLMENT_YEAR,
                version_no=1,
                status="PUBLISHED",
                effective_from=date(2024, 9, 1),
                published_at=now,
                published_by=admin_account.id,
            )
            training_plans.append(plan)

            major_index = majors.index(major)
            for course_index, course in enumerate(courses, start=1):
                required_count = 6 if major_index in {0, 1, 2, 8, 12, 13, 14} else 5 if major_index in {5, 6, 7, 9, 10} else 4
                optional_min = 2 if required_count >= 5 else 3
                plan_course = TrainingPlanCourse(
                    id=demo_id(
                        "plan_course", f"{major.code}:{course.course_code}"
                    ),
                    plan_id=plan.id,
                    course_id=course.id,
                    course_nature="REQUIRED",
                    study_year=course_index,
                    semester_no=2 if course_index == 1 else 1,
                    required_project_count=required_count,
                    optional_project_min_count=optional_min,
                    order_rule_text=f"本专业本课程前{required_count}项必做，其余选做且至少完成{optional_min}项。",
                )
                plan_courses.append(plan_course)

                course_projects = [
                    project
                    for project in projects
                    if project.course_id == course.id
                ]
                for display_order, project in enumerate(
                    course_projects, start=1
                ):
                    plan_projects.append(
                        TrainingPlanProject(
                            id=demo_id(
                                "plan_project",
                                f"{major.code}:{project.project_code}",
                            ),
                            plan_course_id=plan_course.id,
                            project_id=project.id,
                            requirement_type=(
                                "REQUIRED"
                                if display_order <= required_count
                                else "OPTIONAL"
                            ),
                            display_order=display_order,
                        )
                    )
        session.add_all(training_plans)
        await session.flush()
        session.add_all(plan_courses)
        await session.flush()
        session.add_all(plan_projects)
        await session.flush()

        laboratories = [
            Laboratory(
                id=demo_id("lab", spec.code),
                lab_code=spec.code,
                name=spec.name,
                campus_id=(
                    east_campus.id
                    if spec.code.startswith("C")
                    else main_campus.id
                ),
                room_type=spec.room_type,
                safety_capacity=spec.safety_capacity,
                manager_teacher_id=teachers[index % len(teachers)].id,
                status="ACTIVE",
                description="通用高校物理实验演示配置",
            )
            for index, spec in enumerate(LABORATORY_SPECS)
        ]
        laboratory_by_code = {
            laboratory.lab_code: laboratory
            for laboratory in laboratories
        }
        session.add_all(laboratories)
        await session.flush()

        equipment_types = [
            EquipmentType(
                id=demo_id("equipment", spec.code),
                equipment_code=spec.code,
                name=spec.name,
                model=spec.model,
                unit=spec.unit,
                status="ACTIVE",
            )
            for spec in PHYSICS_EQUIPMENT_SPECS
        ]
        equipment_by_code = {
            equipment.equipment_code: equipment
            for equipment in equipment_types
        }
        session.add_all(equipment_types)
        await session.flush()

        lab_capabilities: list[LabProjectCapability] = []
        inventories: list[LabEquipmentInventory] = []
        equipment_requirements: list[ProjectEquipmentRequirement] = []

        project_lab_map = {
            project.project_code: laboratory_by_code[
                PROJECT_RESOURCE_SPECS[
                    project.project_code
                ].lab_codes[0]
            ]
            for project in projects
        }
        project_capacity_map: dict[str, int] = {}

        reserved_inventory_specs = build_reserved_inventory_specs()
        for project_code, resource_spec in PROJECT_RESOURCE_SPECS.items():
            project = project_by_code[project_code]
            project.material_note = resource_spec.material_note
            for lab_code in resource_spec.lab_codes:
                laboratory = laboratory_by_code[lab_code]
                effective_capacity = PROJECT_LAB_CAPACITIES[
                    project_code
                ][lab_code]
                if lab_code == resource_spec.lab_codes[0]:
                    project_capacity_map[project_code] = effective_capacity
                lab_capabilities.append(
                    LabProjectCapability(
                        id=demo_id(
                            "lab_capability",
                            f"{project_code}:{lab_code}",
                        ),
                        laboratory_id=laboratory.id,
                        project_id=project.id,
                        effective_capacity=effective_capacity,
                        status="ACTIVE",
                        note=resource_spec.capability_note,
                    )
                )
            for item in resource_spec.requirements:
                equipment_requirements.append(
                    ProjectEquipmentRequirement(
                        id=demo_id(
                            "project_equipment",
                            f"{project_code}:{item.equipment_code}",
                        ),
                        project_id=project.id,
                        equipment_type_id=equipment_by_code[
                            item.equipment_code
                        ].id,
                        units_per_group=item.units_per_group,
                        required=item.required,
                    )
                )

        for lab_code, inventory_spec in reserved_inventory_specs.items():
            laboratory = laboratory_by_code[lab_code]
            for equipment_code, quantities in inventory_spec.items():
                total, usable = quantities
                equipment = equipment_by_code[equipment_code]
                inventories.append(
                    LabEquipmentInventory(
                        id=demo_id(
                            "inventory",
                            f"{laboratory.lab_code}:{equipment_code}",
                        ),
                        laboratory_id=laboratory.id,
                        equipment_type_id=equipment.id,
                        total_quantity=total,
                        usable_quantity=usable,
                        disabled_quantity=total - usable,
                        checked_at=now,
                    )
                )
        session.add_all(
            lab_capabilities + inventories + equipment_requirements
        )
        await session.flush()

        qualifications: list[TeacherProjectQualification] = []
        availabilities: list[TeacherAvailability] = []
        for teacher_index, teacher in enumerate(teachers):
            assigned_projects = [
                projects[teacher_index],
                projects[teacher_index + 10],
                projects[teacher_index + 20],
            ]
            for project in assigned_projects:
                qualifications.append(
                    TeacherProjectQualification(
                        id=demo_id(
                            "qualification",
                            f"{teacher.employee_no}:{project.project_code}",
                        ),
                        teacher_id=teacher.id,
                        project_id=project.id,
                        valid_from=date(2026, 1, 1),
                        status="ACTIVE",
                    )
                )
            for day_of_week in range(2, 7):
                availabilities.append(
                    TeacherAvailability(
                        id=demo_id(
                            "availability",
                            f"{teacher.employee_no}:{day_of_week}",
                        ),
                        teacher_id=teacher.id,
                        term_id=term.id,
                        week_start=1,
                        week_end=17,
                        day_of_week=day_of_week,
                        start_slot=1,
                        end_slot=8,
                        availability_type="AVAILABLE",
                        reason="模拟可用时间",
                    )
                )
        session.add_all(qualifications + availabilities)
        await session.flush()

        rule_sets = {
            domain: RuleSet(
                id=demo_id("rule_set", f"{domain}-V1"),
                rule_domain=domain,
                rule_set_code=spec["code"],
                version_no=1,
                name=spec["name"],
                status=spec["status"],
                published_at=(
                    now if spec["status"] == "PUBLISHED" else None
                ),
                published_by=(
                    admin_account.id
                    if spec["status"] == "PUBLISHED"
                    else None
                ),
            )
            for domain, spec in RULE_SET_SPECS.items()
        }
        session.add_all(rule_sets.values())
        await session.flush()

        session.add_all(
            [
                RuleConfig(
                    id=demo_id("rule", f"{domain}:{rule_code}"),
                    rule_set_id=rule_sets[domain].id,
                    rule_code=rule_code,
                    rule_name=rule_name,
                    enforcement_type=enforcement_type,
                    scope_config={"demo": True, "domain": domain},
                    condition_config={
                        "source": "DEMO"
                        if domain in {"SCHEDULING", "SELECTION"}
                        else "PRD_V1",
                        "requires_runtime_context": domain
                        in {"ADJUSTMENT", "APPROVAL"},
                    },
                    action_config={
                        "action": enforcement_type,
                        **action_options,
                    },
                    weight=(
                        Decimal(1)
                        if domain == "SCHEDULING"
                        and enforcement_type == "SCORE"
                        else Decimal(0)
                    ),
                    priority=priority,
                    description=(
                        f"{rule_name}（模拟规则）"
                        if domain in {"SCHEDULING", "SELECTION"}
                        else f"{rule_name}（PRD 初始草稿）"
                    ),
                    enabled=True,
                )
                for domain, domain_rules in RULE_SPECS.items()
                for (
                    rule_code,
                    rule_name,
                    enforcement_type,
                    priority,
                    action_options,
                ) in domain_rules
            ]
        )
        await session.flush()

        scheduling_v2_id = uuid5(
            SOFT_RULE_NAMESPACE,
            f"{rule_sets['SCHEDULING'].id}:SCHEDULING:V2",
        )
        scheduling_v2 = RuleSet(
            id=scheduling_v2_id,
            rule_domain="SCHEDULING",
            rule_set_code=rule_sets["SCHEDULING"].rule_set_code,
            version_no=2,
            name="排课规则集 V2（软约束扩展草稿）",
            status="DRAFT",
        )
        session.add(scheduling_v2)
        await session.flush()
        new_soft_rule_codes = {
            item[0] for item in NEW_SCHEDULING_SOFT_RULES
        }
        session.add_all(
            [
                RuleConfig(
                    id=uuid5(
                        SOFT_RULE_NAMESPACE,
                        f"{scheduling_v2_id}:{rule_code}",
                    ),
                    rule_set_id=scheduling_v2_id,
                    rule_code=rule_code,
                    rule_name=rule_name,
                    enforcement_type=enforcement_type,
                    scope_config={
                        "demo": True,
                        "domain": "SCHEDULING",
                    },
                    condition_config=(
                        {
                            "configuration_status": "PENDING",
                            **NEW_SCHEDULING_SOFT_CONDITIONS.get(
                                rule_code, {}
                            ),
                        }
                        if rule_code in new_soft_rule_codes
                        else {"source": "DEMO"}
                    ),
                    action_config={
                        "action": enforcement_type,
                        **action_options,
                    },
                    weight=(
                        SCHEDULING_SOFT_RULE_INITIALS[rule_code][
                            "weight"
                        ]
                        if enforcement_type == "SCORE"
                        else Decimal(0)
                    ),
                    priority=(
                        SCHEDULING_SOFT_RULE_INITIALS[rule_code][
                            "priority"
                        ]
                        if enforcement_type == "SCORE"
                        else priority
                    ),
                    description=(
                        f"{rule_name}；采用综合均衡初始化，可按管理员偏好调整"
                        if rule_code in new_soft_rule_codes
                        else f"{rule_name}（模拟规则）"
                    ),
                    enabled=True,
                )
                for (
                    rule_code,
                    rule_name,
                    enforcement_type,
                    priority,
                    action_options,
                ) in (
                    RULE_SPECS["SCHEDULING"]
                    + NEW_SCHEDULING_SOFT_RULES
                )
            ]
        )
        await session.flush()

        tasks: list[TeachingTask] = []
        cohorts: list[TeachingTaskCohort] = []
        demands: list[ProjectDemand] = []
        for course in courses:
            task = TeachingTask(
                id=demo_id("task", course.course_code),
                task_code=f"DEMO-TASK-{course.course_code}",
                term_id=term.id,
                course_id=course.id,
                planned_student_count=len(students),
                week_start=2,
                week_end=16,
                capacity_buffer_ratio=Decimal("1.20"),
                status="READY",
            )
            tasks.append(task)
            for major in majors:
                cohorts.append(
                    TeachingTaskCohort(
                        id=demo_id(
                            "cohort", f"{course.course_code}:{major.code}"
                        ),
                        task_id=task.id,
                        major_id=major.id,
                        enrollment_year=DEMO_ENROLLMENT_YEAR,
                        student_count=STUDENTS_PER_MAJOR,
                    )
                )
            for project in [
                item for item in projects if item.course_id == course.id
            ]:
                required_major_count = sum(
                    1
                    for item in plan_projects
                    if item.project_id == project.id
                    and item.requirement_type == "REQUIRED"
                )
                requirement_type = (
                    "REQUIRED"
                    if required_major_count >= math.ceil(len(majors) / 2)
                    else "OPTIONAL"
                )
                prediction_ratio = Decimal(
                    str(round(0.6 + 0.4 * required_major_count / len(majors), 4))
                )
                base_demand = len(students)
                required_capacity = math.ceil(
                    base_demand * float(prediction_ratio) * 1.2
                )
                demands.append(
                    ProjectDemand(
                        id=demo_id("demand", project.project_code),
                        task_id=task.id,
                        project_id=project.id,
                        requirement_type=requirement_type,
                        base_demand=base_demand,
                        prediction_ratio=prediction_ratio,
                        buffer_ratio=Decimal("1.20"),
                        required_capacity=required_capacity,
                        required_session_count=math.ceil(
                            required_capacity
                            / project_capacity_map[project.project_code]
                        ),
                        calculation_snapshot={
                            "demo": True,
                            "students": base_demand,
                            "buffer_ratio": 1.2,
                        },
                    )
                )
        session.add_all(tasks)
        await session.flush()
        session.add_all(cohorts)
        await session.flush()
        session.add_all(demands)
        await session.flush()

        schedule_version = ScheduleVersion(
            id=demo_id("schedule_version", "DEMO-DRAFT-V1"),
            term_id=term.id,
            version_no=1,
            status="DRAFT",
            hard_constraint_passed=False,
            score_details={"demo": True},
            optimization_params={"demo": True},
            scheduling_rule_set_id=rule_sets["SCHEDULING"].id,
        )
        session.add(schedule_version)
        await session.flush()

        sessions: list[ExperimentSession] = []
        for project_index, project in enumerate(projects):
            task = next(item for item in tasks if item.course_id == project.course_id)
            laboratory = project_lab_map[project.project_code]
            qualified_teachers = [teachers[project_index % len(teachers)]]
            for session_index in range(1, 4):
                session_code = (
                    f"DEMO-S-{project_index + 1:02d}-{session_index:02d}"
                )
                sessions.append(
                    ExperimentSession(
                        id=demo_id("session", session_code),
                        schedule_version_id=schedule_version.id,
                        session_code=session_code,
                        task_id=task.id,
                        project_id=project.id,
                        week_no=2 + project_index % 15,
                        day_of_week=session_index + 1,
                        start_slot=1 if session_index == 1 else 5,
                        end_slot=4 if session_index == 1 else 8,
                        teacher_id=qualified_teachers[
                            (session_index - 1) % len(qualified_teachers)
                        ].id,
                        laboratory_id=laboratory.id,
                        capacity=project_capacity_map[project.project_code],
                        selected_count=0,
                        status="DRAFT",
                        locked=False,
                    )
                )
        session.add_all(sessions)
        await session.flush()

        session.add(
            SelectionWindow(
                id=demo_id("selection_window", "DEMO-2026"),
                term_id=term.id,
                selection_rule_set_id=rule_sets["SELECTION"].id,
                start_at=datetime(2026, 2, 25, 0, 0, tzinfo=UTC),
                end_at=datetime(2026, 3, 8, 23, 59, tzinfo=UTC),
                withdraw_end_at=datetime(2026, 3, 15, 23, 59, tzinfo=UTC),
                status="CLOSED",
            )
        )
        await session.flush()

        session.add(
            OperationLog(
                id=demo_id("operation_log", "DEMO-SEED-V1"),
                operator_user_id=admin_account.id,
                operation_type="DEMO_SEED",
                object_type="DATABASE",
                request_id="DEMO-SEED-V1",
                before_snapshot={},
                after_snapshot={
                    "demo": True,
                    "majors": len(majors),
                    "students_per_major": STUDENTS_PER_MAJOR,
                    "students": len(students),
                },
                rule_set_id=rule_sets["SCHEDULING"].id,
                result="SUCCEEDED",
            )
        )
        await session.flush()

    print("模拟数据写入完成：")
    print(f"- 工科专业：{len(MAJOR_SPECS)}")
    print(f"- 每专业学生：{STUDENTS_PER_MAJOR}")
    print(f"- 学生总数：{len(MAJOR_SPECS) * STUDENTS_PER_MAJOR}")
    print(f"- 模拟班级：{len(MAJOR_SPECS) * CLASSES_PER_MAJOR}")
    print(f"- 模拟教师：{len(teachers)}")
    print(f"- 实验课程：{len(courses)}")
    print(f"- 实验项目：{len(projects)}")
    print(f"- 草稿实验场次：{len(sessions)}")
    print("提示：以上均为开发测试用模拟数据。")


async def main() -> None:
    try:
        await seed_demo_data()
    finally:
        await dispose_database_engine()


if __name__ == "__main__":
    asyncio.run(main())
