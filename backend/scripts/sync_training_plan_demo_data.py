import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from pwdlib import PasswordHash
from sqlalchemy import delete, func, select, update

from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models import (
    CoursePrerequisite,
    ExperimentCourse,
    ExperimentProject,
    Major,
    ProjectOrderConstraint,
    Student,
    StudentBusyBitmap,
    StudentClass,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingPlanProject,
    UserAccount,
)
from scripts.seed_demo_data import (
    CLASSES_PER_MAJOR,
    DEMO_ENROLLMENT_YEAR,
    MAJOR_SPECS,
    STUDENTS_PER_CLASS,
    STUDENTS_PER_MAJOR,
    build_busy_bitmap,
    demo_id,
)

TARGET_YEARS = (2023, DEMO_ENROLLMENT_YEAR, 2025)
PHYSICS_HEAVY = {"DEMO-AP", "DEMO-OPTO"}
ENGINEERING_TWO_COURSES = {
    "DEMO-ME",
    "DEMO-EEA",
    "DEMO-EIE",
    "DEMO-AU",
    "DEMO-EPE",
    "DEMO-VE",
    "DEMO-MSE",
}
COURSE_CODES_BY_GROUP = {
    "basic": ["DEMO-PHY101"],
    "engineering": ["DEMO-PHY101", "DEMO-PHY201"],
    "physics": ["DEMO-PHY101", "DEMO-PHY201", "DEMO-PHY301"],
}
BASE_PREREQUISITES = {
    "DEMO-PHY101": ["DEMO-TH-MATH101", "DEMO-TH-PHYS101"],
    "DEMO-PHY201": ["DEMO-TH-MATH102", "DEMO-TH-PHYS102", "DEMO-PHY101"],
    "DEMO-PHY301": ["DEMO-TH-PHYS102", "DEMO-TH-PHYS301", "DEMO-PHY101"],
}
ENGINEERING_PREREQUISITES = {
    "DEMO-PHY201": ["DEMO-TH-ENG101"],
}
PHYSICS_PREREQUISITES = {
    "DEMO-PHY101": ["DEMO-TH-MATH201"],
    "DEMO-PHY201": ["DEMO-TH-PHYS201", "DEMO-TH-PHYS202"],
    "DEMO-PHY301": ["DEMO-TH-PHYS202", "DEMO-TH-PHYS302", "DEMO-PHY201"],
}
YEAR_PREREQUISITES = {
    2025: {
        "DEMO-PHY101": ["DEMO-TH-MATH202"],
        "DEMO-PHY201": ["DEMO-TH-MATH202"],
    }
}
PROJECT_ORDER_RULES = {
    "DEMO-PHY101": [
        (
            "DEMO-PHY101-P01",
            "DEMO-PHY101-P02",
            "完成“长度与密度测量”后再进入“单摆测重力加速度”。",
        ),
    ],
    "DEMO-PHY201": [
        (
            "DEMO-PHY201-P01",
            "DEMO-PHY201-P02",
            "完成“霍尔效应与磁场测量”后再进入“RLC暂态过程”。",
        ),
    ],
    "DEMO-PHY301": [
        (
            "DEMO-PHY301-P01",
            "DEMO-PHY301-P02",
            "完成“光电效应与普朗克常量”后再进入“弗兰克—赫兹实验”。",
        ),
    ],
}
MAJOR_PROJECT_ORDER_RULES = {
    "DEMO-AP": {
        "DEMO-PHY301": [
            (
                "DEMO-PHY301-P02",
                "DEMO-PHY301-P03",
                "完成“弗兰克—赫兹实验”后再进入“密立根油滴实验”。",
            ),
        ],
    },
    "DEMO-OPTO": {
        "DEMO-PHY101": [
            (
                "DEMO-PHY101-P09",
                "DEMO-PHY101-P10",
                "完成“薄透镜焦距测量”后再进入“光的干涉与衍射”。",
            ),
        ],
    },
}
for _major_code in ("DEMO-EEA", "DEMO-EIE", "DEMO-AU", "DEMO-EPE"):
    MAJOR_PROJECT_ORDER_RULES[_major_code] = {
        "DEMO-PHY201": [
            (
                "DEMO-PHY201-P02",
                "DEMO-PHY201-P03",
                "完成“RLC暂态过程”后再进入“交流电桥”。",
            ),
        ],
    }
THEORY_COURSE_SPECS = [
    ("DEMO-TH-MATH101", "高等数学（上）", "数学基础：极限、微分与积分"),
    ("DEMO-TH-MATH102", "高等数学（下）", "数学基础：多元微积分与常微分方程"),
    ("DEMO-TH-MATH201", "线性代数", "数学基础：矩阵、向量与线性方程组"),
    ("DEMO-TH-MATH202", "概率论与数理统计", "数学基础：随机变量与统计推断"),
    ("DEMO-TH-PHYS101", "大学物理Ⅰ（力学与热学）", "物理理论基础：力学、振动与热学"),
    ("DEMO-TH-PHYS102", "大学物理Ⅱ（电磁学与光学）", "物理理论基础：电磁学、波动与光学"),
    ("DEMO-TH-PHYS201", "理论力学", "物理理论进阶：质点、刚体与分析力学"),
    ("DEMO-TH-PHYS202", "光学", "物理理论进阶：几何光学、物理光学与激光基础"),
    ("DEMO-TH-PHYS301", "现代物理导论", "物理理论进阶：相对论、量子与原子物理"),
    ("DEMO-TH-PHYS302", "量子力学导论", "物理理论进阶：波函数、算符与量子态"),
    ("DEMO-TH-ENG101", "电路分析基础", "工程理论基础：直流、交流与暂态电路"),
    ("DEMO-TH-ENG102", "模拟电子技术", "工程理论基础：半导体器件与模拟电路"),
]


def major_course_codes(major_code: str) -> list[str]:
    if major_code in PHYSICS_HEAVY:
        return COURSE_CODES_BY_GROUP["physics"]
    if major_code in ENGINEERING_TWO_COURSES:
        return COURSE_CODES_BY_GROUP["engineering"]
    return COURSE_CODES_BY_GROUP["basic"]


def prerequisite_codes_for(
    major_code: str, enrollment_year: int, course_code: str
) -> list[str]:
    codes = list(BASE_PREREQUISITES.get(course_code, []))
    if major_code in ENGINEERING_TWO_COURSES:
        codes.extend(ENGINEERING_PREREQUISITES.get(course_code, []))
    if major_code in PHYSICS_HEAVY:
        codes.extend(PHYSICS_PREREQUISITES.get(course_code, []))
    codes.extend(YEAR_PREREQUISITES.get(enrollment_year, {}).get(course_code, []))
    return list(dict.fromkeys(codes))


def project_order_rules_for(
    major_code: str, course_code: str
) -> list[tuple[str, str, str]]:
    rules = list(PROJECT_ORDER_RULES.get(course_code, []))
    rules.extend(
        MAJOR_PROJECT_ORDER_RULES.get(major_code, {}).get(course_code, [])
    )
    return rules


async def ensure_theory_courses(session) -> None:
    specs_by_code = {item[0]: item for item in THEORY_COURSE_SPECS}
    existing = {
        item.course_code: item
        for item in (
            await session.execute(
                select(ExperimentCourse).where(
                    ExperimentCourse.course_code.in_(specs_by_code)
                )
            )
        ).scalars()
    }
    for code, (course_code, course_name, description) in specs_by_code.items():
        course = existing.get(code)
        if course is None:
            session.add(
                ExperimentCourse(
                    id=demo_id("course", course_code),
                    course_code=course_code,
                    course_name=course_name,
                    course_type="THEORY",
                    credits=Decimal("3.0"),
                    default_slots=2,
                    description=description,
                    status="ACTIVE",
                )
            )
        else:
            course.course_name = course_name
            course.course_type = "THEORY"
            course.description = description
            course.status = "ACTIVE"
    await session.flush()


async def ensure_students(session, majors, term_id, password_hash: str) -> None:
    for year in TARGET_YEARS:
        for major_index, major in enumerate(majors, start=1):
            class_ids = []
            for class_index in range(1, CLASSES_PER_MAJOR + 1):
                class_code = f"DEMO-{year}-{major_index:02d}-{class_index:02d}"
                class_id = demo_id("class", class_code)
                if await session.get(StudentClass, class_id) is None:
                    session.add(
                        StudentClass(
                            id=class_id,
                            code=class_code,
                            name=f"{major.name}{class_index:02d}班",
                            major_id=major.id,
                            enrollment_year=year,
                            campus_id=demo_id(
                                "campus", "MAIN" if major_index <= 7 else "EAST"
                            ),
                            status="ACTIVE",
                        )
                    )
                class_ids.append(class_id)
            await session.flush()

            for local_index in range(1, STUDENTS_PER_MAJOR + 1):
                student_no = f"D{year}{major_index:02d}{local_index:04d}"
                user_id = demo_id("user", student_no)
                if await session.get(UserAccount, user_id) is None:
                    session.add(
                        UserAccount(
                            id=user_id,
                            login_name=student_no.lower(),
                            password_hash=password_hash,
                            user_type="STUDENT",
                            status="ACTIVE",
                        )
                    )
                    await session.flush()
                student_id = demo_id("student", student_no)
                if await session.get(Student, student_id) is None:
                    class_id = class_ids[(local_index - 1) // STUDENTS_PER_CLASS]
                    session.add(
                        Student(
                            id=student_id,
                            user_id=user_id,
                            student_no=student_no,
                            name=f"模拟学生{year}{major_index:02d}{local_index:02d}",
                            gender="MALE" if local_index % 2 else "FEMALE",
                            enrollment_year=year,
                            major_id=major.id,
                            class_id=class_id,
                            campus_id=demo_id(
                                "campus", "MAIN" if major_index <= 7 else "EAST"
                            ),
                            academic_status="ACTIVE",
                        )
                    )
                bitmap_id = demo_id("busy_bitmap", student_no)
                if await session.get(StudentBusyBitmap, bitmap_id) is None:
                    session.add(
                        StudentBusyBitmap(
                            id=bitmap_id,
                            student_id=student_id,
                            term_id=term_id,
                            start_week=1,
                            end_week=17,
                            days_per_week=7,
                            slots_per_day=12,
                            bitmap=build_busy_bitmap(local_index),
                            mapping_version=1,
                            source_version="DEMO-TRAINING-PLAN-SYNC",
                        )
                    )
    await session.flush()


async def replace_plan_courses(
    session,
    plan: TrainingPlan,
    major_code: str,
    enrollment_year: int,
    course_codes: list[str],
    courses_by_code: dict[str, ExperimentCourse],
    projects_by_course: dict[str, list[ExperimentProject]],
) -> None:
    plan_course_ids = select(TrainingPlanCourse.id).where(
        TrainingPlanCourse.plan_id == plan.id
    )
    await session.execute(
        delete(CoursePrerequisite).where(
            CoursePrerequisite.plan_course_id.in_(plan_course_ids)
        )
    )
    await session.execute(
        delete(ProjectOrderConstraint).where(
            ProjectOrderConstraint.plan_course_id.in_(plan_course_ids)
        )
    )
    await session.execute(
        delete(TrainingPlanProject).where(
            TrainingPlanProject.plan_course_id.in_(plan_course_ids)
        )
    )
    await session.execute(
        delete(TrainingPlanCourse).where(TrainingPlanCourse.plan_id == plan.id)
    )
    await session.flush()

    for course_code in course_codes:
        course = courses_by_code[course_code]
        required_count = 6 if len(course_codes) >= 3 else 5 if len(course_codes) == 2 else 4
        optional_min = 2 if len(course_codes) >= 2 else 1
        project_rules = project_order_rules_for(major_code, course_code)
        plan_course = TrainingPlanCourse(
            id=demo_id("plan_course", f"{plan.plan_code}:{course_code}"),
            plan_id=plan.id,
            course_id=course.id,
            course_nature="REQUIRED",
            study_year=2,
            semester_no=1,
            required_project_count=required_count,
            optional_project_min_count=optional_min,
            order_rule_text="; ".join(
                description for _, _, description in project_rules
            ),
            allow_order_override=False,
        )
        session.add(plan_course)
        await session.flush()

        for prerequisite_code in prerequisite_codes_for(
            major_code, enrollment_year, course_code
        ):
            prerequisite_course = courses_by_code.get(prerequisite_code)
            if prerequisite_course is None:
                continue
            session.add(
                CoursePrerequisite(
                    id=demo_id(
                        "course_prerequisite",
                        f"{plan.plan_code}:{course_code}:{prerequisite_code}",
                    ),
                    plan_course_id=plan_course.id,
                    prerequisite_course_id=prerequisite_course.id,
                    requirement_type="MUST_COMPLETE",
                )
            )

        projects_by_code = {
            project.project_code: project for project in projects_by_course[course_code]
        }
        for display_order, project in enumerate(
            projects_by_course[course_code], start=1
        ):
            session.add(
                TrainingPlanProject(
                    id=demo_id(
                        "plan_project",
                        f"{plan.plan_code}:{project.project_code}",
                    ),
                    plan_course_id=plan_course.id,
                    project_id=project.id,
                    requirement_type=(
                        "REQUIRED" if display_order <= required_count else "OPTIONAL"
                    ),
                    display_order=display_order,
                )
            )

        for before_code, after_code, description in project_rules:
            before_project = projects_by_code.get(before_code)
            after_project = projects_by_code.get(after_code)
            if before_project is None or after_project is None:
                continue
            session.add(
                ProjectOrderConstraint(
                    id=demo_id(
                        "project_order",
                        f"{plan.plan_code}:{before_code}:{after_code}",
                    ),
                    plan_course_id=plan_course.id,
                    before_project_id=before_project.id,
                    after_project_id=after_project.id,
                    allow_override=False,
                    description=description,
                )
            )
    await session.flush()


async def ensure_training_plans(session, majors) -> None:
    now = datetime.now(UTC)
    experiment_course_codes = {
        course_code
        for codes in COURSE_CODES_BY_GROUP.values()
        for course_code in codes
    }
    all_prerequisite_codes = set()
    for year in TARGET_YEARS:
        for major_code, _ in MAJOR_SPECS:
            for course_code in major_course_codes(major_code):
                all_prerequisite_codes.update(
                    prerequisite_codes_for(major_code, year, course_code)
                )
    all_course_codes = experiment_course_codes | all_prerequisite_codes
    courses = list(
        (
            await session.execute(
                select(ExperimentCourse).where(
                    ExperimentCourse.course_code.in_(all_course_codes)
                )
            )
        ).scalars()
    )
    courses_by_code = {course.course_code: course for course in courses}
    missing_codes = all_course_codes - courses_by_code.keys()
    if missing_codes:
        raise RuntimeError(
            f"缺少先修或实验课程基础资料：{', '.join(sorted(missing_codes))}"
        )
    projects = list(
        (
            await session.execute(
                select(ExperimentProject)
                .where(
                    ExperimentProject.course_id.in_(
                        [courses_by_code[code].id for code in experiment_course_codes]
                    )
                )
                .order_by(ExperimentProject.project_code)
            )
        ).scalars()
    )
    projects_by_course = {
        course_code: [
            project
            for project in projects
            if project.course_id == courses_by_code[course_code].id
        ]
        for course_code in experiment_course_codes
    }

    for year in TARGET_YEARS:
        for major in majors:
            plan_id = demo_id("plan", f"{major.code}:{year}:CURRENT")
            plan_code = f"DEMO-PLAN-{major.code}-{year}-V1"
            plan = (
                await session.execute(
                    select(TrainingPlan).where(
                        TrainingPlan.major_id == major.id,
                        TrainingPlan.enrollment_year == year,
                        TrainingPlan.version_no == 1,
                    )
                )
            ).scalar_one_or_none()
            if plan is None:
                plan = TrainingPlan(
                    id=plan_id,
                    plan_code=plan_code,
                    major_id=major.id,
                    enrollment_year=year,
                    version_no=1,
                    status="PUBLISHED",
                    effective_from=date(year, 9, 1),
                    published_at=now,
                    published_by=demo_id("user", "ADMIN"),
                )
                session.add(plan)
                await session.flush()
            else:
                plan.plan_code = plan_code
                plan.status = "PUBLISHED"
                plan.version_no = 1
                plan.effective_from = date(year, 9, 1)
                plan.published_at = plan.published_at or now

            await session.execute(
                update(TrainingPlan)
                .where(
                    TrainingPlan.major_id == major.id,
                    TrainingPlan.enrollment_year == year,
                    TrainingPlan.id != plan.id,
                    TrainingPlan.status != "ARCHIVED",
                )
                .values(status="ARCHIVED")
            )
            await replace_plan_courses(
                session,
                plan,
                major.code,
                year,
                major_course_codes(major.code),
                courses_by_code,
                projects_by_course,
            )
    await session.flush()


async def main() -> None:
    password_hash = PasswordHash.recommended().hash("Demo@123456")
    async with AsyncSessionFactory.begin() as session:
        majors = list(
            (
                await session.execute(
                    select(Major)
                    .where(Major.code.in_([code for code, _ in MAJOR_SPECS]))
                    .order_by(Major.code)
                )
            ).scalars()
        )
        if len(majors) != len(MAJOR_SPECS):
            raise RuntimeError("请先运行 seed_demo_data.py 初始化基础 DEMO 专业")
        term_id = demo_id("term", "2025-2026-2")
        await ensure_students(session, majors, term_id, password_hash)
        await ensure_theory_courses(session)
        await ensure_training_plans(session, majors)

        plan_count = await session.scalar(
            select(func.count())
            .select_from(TrainingPlan)
            .where(
                TrainingPlan.enrollment_year.in_(TARGET_YEARS),
                TrainingPlan.status != "ARCHIVED",
            )
        )
        student_count = await session.scalar(
            select(func.count())
            .select_from(Student)
            .where(Student.enrollment_year.in_(TARGET_YEARS))
        )
    await dispose_database_engine()
    print(f"DEMO 培养方案同步完成：{plan_count} 个当前方案，{student_count} 名学生。")


if __name__ == "__main__":
    asyncio.run(main())
