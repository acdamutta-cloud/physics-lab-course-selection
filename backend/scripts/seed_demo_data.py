import asyncio
import math
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid5

from pwdlib import PasswordHash
from sqlalchemy import func, select

from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models import (
    AcademicTerm,
    Campus,
    EquipmentType,
    ExperimentCourse,
    ExperimentProject,
    ExperimentSession,
    LabEquipmentInventory,
    LabProjectCapability,
    Laboratory,
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
DEMO_ENROLLMENT_YEAR = 2024
STUDENTS_PER_MAJOR = 200
CLASSES_PER_MAJOR = 5
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
]

PROJECT_SPECS = [
    (
        "DEMO-PHY101-P01",
        "DEMO-PHY101",
        "用单摆测量重力加速度",
        "MECHANICS",
        "REQUIRED",
        "0.9000",
    ),
    (
        "DEMO-PHY101-P02",
        "DEMO-PHY101",
        "示波器的原理与使用",
        "ELECTRICITY",
        "REQUIRED",
        "0.9000",
    ),
    (
        "DEMO-PHY101-P03",
        "DEMO-PHY101",
        "霍尔效应及磁场测量",
        "ELECTRICITY",
        "OPTIONAL",
        "0.6000",
    ),
    (
        "DEMO-PHY203-P01",
        "DEMO-PHY203",
        "光电效应与普朗克常量测定",
        "OPTICS",
        "REQUIRED",
        "0.9000",
    ),
    (
        "DEMO-PHY203-P02",
        "DEMO-PHY203",
        "弗兰克—赫兹实验",
        "MODERN",
        "REQUIRED",
        "0.9000",
    ),
    (
        "DEMO-PHY203-P03",
        "DEMO-PHY203",
        "密立根油滴实验",
        "MODERN",
        "OPTIONAL",
        "0.6000",
    ),
]

EQUIPMENT_SPECS = [
    ("DEMO-EQ-PENDULUM", "单摆实验仪", "DP-DEMO"),
    ("DEMO-EQ-TIMER", "光电计时器", "GD-DEMO"),
    ("DEMO-EQ-OSC", "数字示波器", "OSC-DEMO"),
    ("DEMO-EQ-SIGNAL", "信号发生器", "SG-DEMO"),
    ("DEMO-EQ-HALL", "霍尔效应实验仪", "HALL-DEMO"),
    ("DEMO-EQ-PHOTO", "光电效应实验箱", "PHOTO-DEMO"),
    ("DEMO-EQ-FH", "弗兰克—赫兹实验仪", "FH-DEMO"),
    ("DEMO-EQ-OILDROP", "密立根油滴仪", "OIL-DEMO"),
]


def demo_id(entity: str, code: str) -> UUID:
    return uuid5(DEMO_NAMESPACE, f"{entity}:{code}")


def build_busy_bitmap(student_index: int) -> bytes:
    weeks = 17
    days_per_week = 7
    slots_per_day = 12
    bitmap = bytearray(math.ceil(weeks * days_per_week * slots_per_day / 8))

    weekly_blocks = [
        (1 + student_index % 5, 1, 4),
        (3 + student_index % 3, 5, 8),
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
        for index in range(1, 21):
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
                name=f"模拟教师{index:02d}",
                campus_id=main_campus.id if index <= 14 else east_campus.id,
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
                    name=(
                        f"{MAJOR_SPECS[major_index - 1][1]}"
                        f"模拟学生{local_index:03d}"
                    ),
                    gender="UNSPECIFIED",
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
                id=demo_id("course", "DEMO-PHY101"),
                course_code="DEMO-PHY101",
                course_name="大学物理实验（上）（模拟）",
                credits=Decimal("1.0"),
                default_slots=4,
                description="模拟基础实验课程",
                status="ACTIVE",
            ),
            ExperimentCourse(
                id=demo_id("course", "DEMO-PHY203"),
                course_code="DEMO-PHY203",
                course_name="近代物理实验（模拟）",
                credits=Decimal("1.0"),
                default_slots=4,
                description="模拟近代物理实验课程",
                status="ACTIVE",
            ),
        ]
        course_by_code = {course.course_code: course for course in courses}
        session.add_all(courses)
        await session.flush()

        projects: list[ExperimentProject] = []
        requirement_by_project: dict[str, str] = {}
        for (
            project_code,
            course_code,
            project_name,
            category,
            requirement_type,
            historical_ratio,
        ) in PROJECT_SPECS:
            project = ExperimentProject(
                id=demo_id("project", project_code),
                project_code=project_code,
                course_id=course_by_code[course_code].id,
                project_name=f"{project_name}（模拟）",
                category=category,
                required_slots=4,
                default_group_size=2,
                historical_selection_ratio=Decimal(historical_ratio),
                status="ACTIVE",
            )
            projects.append(project)
            requirement_by_project[project_code] = requirement_type
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

            for course_index, course in enumerate(courses, start=1):
                plan_course = TrainingPlanCourse(
                    id=demo_id(
                        "plan_course", f"{major.code}:{course.course_code}"
                    ),
                    plan_id=plan.id,
                    course_id=course.id,
                    course_nature="REQUIRED",
                    study_year=course_index,
                    semester_no=2 if course_index == 1 else 1,
                    required_project_count=2,
                    optional_project_min_count=1,
                    order_rule_text="模拟规则：必做项目优先，项目不得重复修读。",
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
                            requirement_type=requirement_by_project[
                                project.project_code
                            ],
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
                id=demo_id("lab", "A203"),
                lab_code="DEMO-A203",
                name="模拟基础力学实验室",
                campus_id=main_campus.id,
                room_type="基础力学",
                safety_capacity=24,
                manager_teacher_id=teachers[0].id,
                status="ACTIVE",
            ),
            Laboratory(
                id=demo_id("lab", "B105"),
                lab_code="DEMO-B105",
                name="模拟电学综合实验室",
                campus_id=main_campus.id,
                room_type="电学综合",
                safety_capacity=20,
                manager_teacher_id=teachers[1].id,
                status="ACTIVE",
            ),
            Laboratory(
                id=demo_id("lab", "M201"),
                lab_code="DEMO-M201",
                name="模拟近代物理实验室",
                campus_id=east_campus.id,
                room_type="近代物理",
                safety_capacity=16,
                manager_teacher_id=teachers[2].id,
                status="ACTIVE",
            ),
        ]
        session.add_all(laboratories)
        await session.flush()

        equipment_types = [
            EquipmentType(
                id=demo_id("equipment", code),
                equipment_code=code,
                name=f"{name}（模拟）",
                model=model,
                unit="台",
                status="ACTIVE",
            )
            for code, name, model in EQUIPMENT_SPECS
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
            "DEMO-PHY101-P01": laboratories[0],
            "DEMO-PHY101-P02": laboratories[1],
            "DEMO-PHY101-P03": laboratories[1],
            "DEMO-PHY203-P01": laboratories[2],
            "DEMO-PHY203-P02": laboratories[2],
            "DEMO-PHY203-P03": laboratories[2],
        }
        project_equipment_map = {
            "DEMO-PHY101-P01": ["DEMO-EQ-PENDULUM", "DEMO-EQ-TIMER"],
            "DEMO-PHY101-P02": ["DEMO-EQ-OSC", "DEMO-EQ-SIGNAL"],
            "DEMO-PHY101-P03": ["DEMO-EQ-HALL"],
            "DEMO-PHY203-P01": ["DEMO-EQ-PHOTO"],
            "DEMO-PHY203-P02": ["DEMO-EQ-FH", "DEMO-EQ-OSC"],
            "DEMO-PHY203-P03": ["DEMO-EQ-OILDROP"],
        }

        for project_code, laboratory in project_lab_map.items():
            project = project_by_code[project_code]
            lab_capabilities.append(
                LabProjectCapability(
                    id=demo_id("lab_capability", project_code),
                    laboratory_id=laboratory.id,
                    project_id=project.id,
                    effective_capacity=laboratory.safety_capacity,
                    status="ACTIVE",
                    note="模拟能力配置",
                )
            )
            for equipment_code in project_equipment_map[project_code]:
                equipment_requirements.append(
                    ProjectEquipmentRequirement(
                        id=demo_id(
                            "project_equipment",
                            f"{project_code}:{equipment_code}",
                        ),
                        project_id=project.id,
                        equipment_type_id=equipment_by_code[equipment_code].id,
                        units_per_group=1,
                        required=True,
                    )
                )

        inventory_pairs: set[tuple[UUID, UUID]] = set()
        for project_code, laboratory in project_lab_map.items():
            for equipment_code in project_equipment_map[project_code]:
                equipment = equipment_by_code[equipment_code]
                pair = (laboratory.id, equipment.id)
                if pair in inventory_pairs:
                    continue
                inventory_pairs.add(pair)
                total = max(8, laboratory.safety_capacity // 2)
                inventories.append(
                    LabEquipmentInventory(
                        id=demo_id(
                            "inventory",
                            f"{laboratory.lab_code}:{equipment_code}",
                        ),
                        laboratory_id=laboratory.id,
                        equipment_type_id=equipment.id,
                        total_quantity=total,
                        usable_quantity=total,
                        disabled_quantity=0,
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
                projects[teacher_index % len(projects)],
                projects[(teacher_index + 2) % len(projects)],
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
            for day_of_week in range(1, 6):
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

        rule_set = RuleSet(
            id=demo_id("rule_set", "DEMO-V1"),
            rule_set_code="DEMO-PHYSICS-LAB",
            version_no=1,
            name="物理实验模拟规则集 V1",
            status="PUBLISHED",
            published_at=now,
            published_by=admin_account.id,
        )
        session.add(rule_set)
        await session.flush()

        rule_specs = [
            (
                "TEACHER_TIME_CONFLICT",
                "教师时间不得冲突",
                "HARD",
                100,
            ),
            (
                "LAB_TIME_CONFLICT",
                "实验室时间不得冲突",
                "HARD",
                100,
            ),
            ("SESSION_CAPACITY", "场次不得超过容量", "HARD", 100),
            ("TEACHER_QUALIFICATION", "教师须具备项目资格", "HARD", 100),
            ("LAB_CAPABILITY", "实验室须支持对应项目", "HARD", 100),
            ("EQUIPMENT_AVAILABLE", "设备数量须满足要求", "HARD", 100),
            ("STUDENT_TIME_CONFLICT", "学生时间不得冲突", "HARD", 100),
            ("PROJECT_DUPLICATE", "项目不得重复修读", "HARD", 100),
            ("TEACHER_BALANCE", "教师工作量尽量均衡", "SOFT", 50),
            ("EVENING_PENALTY", "尽量减少晚间场次", "SOFT", 40),
        ]
        session.add_all(
            [
                RuleConfig(
                    id=demo_id("rule", rule_code),
                    rule_set_id=rule_set.id,
                    rule_code=rule_code,
                    rule_name=rule_name,
                    rule_type=rule_type,
                    scope_config={"demo": True},
                    condition_config={"source": "DEMO"},
                    action_config={
                        "action": "BLOCK"
                        if rule_type == "HARD"
                        else "SCORE"
                    },
                    weight=(
                        Decimal("0")
                        if rule_type == "HARD"
                        else Decimal("1")
                    ),
                    priority=priority,
                    description=f"{rule_name}（模拟规则）",
                    enabled=True,
                )
                for rule_code, rule_name, rule_type, priority in rule_specs
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
                requirement_type = requirement_by_project[
                    project.project_code
                ]
                prediction_ratio = (
                    Decimal("1.0")
                    if requirement_type == "REQUIRED"
                    else Decimal("0.6")
                )
                base_demand = len(students)
                required_capacity = math.ceil(
                    base_demand * float(prediction_ratio) * 1.2
                )
                lab = project_lab_map[project.project_code]
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
                            required_capacity / lab.safety_capacity
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
            rule_set_id=rule_set.id,
        )
        session.add(schedule_version)
        await session.flush()

        sessions: list[ExperimentSession] = []
        for project_index, project in enumerate(projects):
            task = next(item for item in tasks if item.course_id == project.course_id)
            laboratory = project_lab_map[project.project_code]
            qualified_teachers = [
                teacher
                for teacher_index, teacher in enumerate(teachers)
                if (
                    project
                    in [
                        projects[teacher_index % len(projects)],
                        projects[(teacher_index + 2) % len(projects)],
                    ]
                )
            ]
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
                        week_no=2 + project_index * 2,
                        day_of_week=session_index + 1,
                        start_slot=1 if session_index == 1 else 5,
                        end_slot=4 if session_index == 1 else 8,
                        teacher_id=qualified_teachers[
                            (session_index - 1) % len(qualified_teachers)
                        ].id,
                        laboratory_id=laboratory.id,
                        capacity=laboratory.safety_capacity,
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
                rule_set_id=rule_set.id,
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
