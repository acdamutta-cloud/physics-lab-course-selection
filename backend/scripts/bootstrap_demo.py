"""在空业务库中写入一组最小、明确标记的虚构演示资料。"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import math
import sys
from datetime import date, datetime, timezone
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
    LabEquipmentInventory,
    Laboratory,
    LabProjectCapability,
    Major,
    ProjectDemand,
    ProjectEquipmentRequirement,
    Student,
    StudentBusyBitmap,
    StudentClass,
    Teacher,
    TeacherBusyBitmap,
    TeacherProjectQualification,
    TeachingTask,
    TeachingTaskCohort,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingPlanProject,
    UserAccount,
)

DEMO_NAMESPACE = UUID("2f1dc498-f91c-4e26-b06d-c64e5563cb65")
MIN_DEMO_PASSWORD_LENGTH = 12
EXPECTED_DEMO_ENTITY_COUNT = 24
password_hash = PasswordHash.recommended()


def demo_id(entity: str) -> UUID:
    return uuid5(DEMO_NAMESPACE, entity)


def validate_demo_password(password: str) -> str:
    if len(password) < MIN_DEMO_PASSWORD_LENGTH:
        raise ValueError(
            f"演示账号密码至少需要 {MIN_DEMO_PASSWORD_LENGTH} 个字符"
        )
    if len(password) > 128:
        raise ValueError("演示账号密码不能超过 128 个字符")
    return password


def read_demo_password(*, password_stdin: bool) -> str:
    if password_stdin:
        return validate_demo_password(sys.stdin.readline().rstrip("\r\n"))
    first = getpass.getpass("演示教师和学生共用密码：")
    second = getpass.getpass("再次输入演示账号密码：")
    if first != second:
        raise ValueError("两次输入的演示账号密码不一致")
    return validate_demo_password(first)


async def _existing_demo_entity_count(session) -> int:
    model_ids = (
        (Campus, demo_id("campus")),
        (Major, demo_id("major")),
        (StudentClass, demo_id("class")),
        (AcademicTerm, demo_id("term")),
        (UserAccount, demo_id("teacher-user")),
        (Teacher, demo_id("teacher")),
        (UserAccount, demo_id("student-user")),
        (Student, demo_id("student")),
        (StudentBusyBitmap, demo_id("student-bitmap")),
        (TeacherBusyBitmap, demo_id("teacher-bitmap")),
        (ExperimentCourse, demo_id("course")),
        (ExperimentProject, demo_id("project")),
        (TrainingPlan, demo_id("plan")),
        (TrainingPlanCourse, demo_id("plan-course")),
        (TrainingPlanProject, demo_id("plan-project")),
        (Laboratory, demo_id("laboratory")),
        (LabProjectCapability, demo_id("lab-capability")),
        (EquipmentType, demo_id("equipment-type")),
        (LabEquipmentInventory, demo_id("inventory")),
        (ProjectEquipmentRequirement, demo_id("equipment-requirement")),
        (TeacherProjectQualification, demo_id("qualification")),
        (TeachingTask, demo_id("teaching-task")),
        (TeachingTaskCohort, demo_id("task-cohort")),
        (ProjectDemand, demo_id("project-demand")),
    )
    existing_count = 0
    for model, entity_id in model_ids:
        if await session.get(model, entity_id):
            existing_count += 1
    return existing_count


async def _assert_demo_target_is_safe(session) -> bool:
    existing_demo_count = await _existing_demo_entity_count(session)
    if existing_demo_count == EXPECTED_DEMO_ENTITY_COUNT:
        print("最小演示资料已完整存在，跳过重复写入。")
        return False
    if existing_demo_count:
        raise RuntimeError(
            "检测到不完整的 DEMO 资料。为避免覆盖或混合数据，初始化已停止。"
        )

    for model in (Campus, Major, AcademicTerm, ExperimentCourse):
        count = await session.scalar(select(func.count()).select_from(model))
        if count:
            raise RuntimeError(
                "数据库已经包含机构或教学基础资料，拒绝写入演示数据。"
            )
    return True


def _empty_bitmap(*, weeks: int, days: int, slots: int) -> bytes:
    return bytes(math.ceil(weeks * days * slots / 8))


async def bootstrap_demo(password: str) -> bool:
    validated_password = validate_demo_password(password)
    shared_password_hash = password_hash.hash(validated_password)
    now = datetime.now(timezone.utc)

    async with AsyncSessionFactory.begin() as session:
        if not await _assert_demo_target_is_safe(session):
            return False

        admin_id = await session.scalar(
            select(UserAccount.id)
            .where(
                UserAccount.user_type == "ADMIN",
                UserAccount.status == "ACTIVE",
            )
            .order_by(UserAccount.created_at, UserAccount.id)
            .limit(1)
        )
        if admin_id is None:
            raise RuntimeError(
                "未找到活动管理员。请先运行 scripts.create_admin。"
            )

        campus = Campus(
            id=demo_id("campus"),
            code="DEMO-CAMPUS",
            name="演示校区",
            address="虚构演示资料",
            status="ACTIVE",
            created_by=admin_id,
        )
        major = Major(
            id=demo_id("major"),
            code="DEMO-MAJOR",
            name="演示专业",
            degree_type="ENGINEERING",
            status="ACTIVE",
            created_by=admin_id,
        )
        student_class = StudentClass(
            id=demo_id("class"),
            code="DEMO-CLASS-2026",
            name="演示班级",
            major_id=major.id,
            enrollment_year=2026,
            campus_id=campus.id,
            status="ACTIVE",
            created_by=admin_id,
        )
        term = AcademicTerm(
            id=demo_id("term"),
            code="DEMO-2026-2027-1",
            academic_year="2026-2027",
            semester_no=1,
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 17),
            total_weeks=18,
            days_per_week=7,
            slots_per_day=12,
            status="ACTIVE",
            created_by=admin_id,
        )
        session.add_all([campus, major, student_class, term])
        await session.flush()

        teacher_user = UserAccount(
            id=demo_id("teacher-user"),
            login_name="demo_teacher",
            password_hash=shared_password_hash,
            user_type="TEACHER",
            status="ACTIVE",
            password_changed_at=now,
            created_by=admin_id,
        )
        teacher = Teacher(
            id=demo_id("teacher"),
            user_id=teacher_user.id,
            employee_no="DEMO-T001",
            name="演示教师",
            campus_id=campus.id,
            department="物理实验中心（演示）",
            title="实验教师",
            status="ACTIVE",
            created_by=admin_id,
        )
        student_user = UserAccount(
            id=demo_id("student-user"),
            login_name="demo_student",
            password_hash=shared_password_hash,
            user_type="STUDENT",
            status="ACTIVE",
            password_changed_at=now,
            created_by=admin_id,
        )
        student = Student(
            id=demo_id("student"),
            user_id=student_user.id,
            student_no="DEMO-S001",
            name="演示学生",
            enrollment_year=2026,
            major_id=major.id,
            class_id=student_class.id,
            campus_id=campus.id,
            academic_status="ACTIVE",
            created_by=admin_id,
        )
        session.add_all([teacher_user, teacher, student_user, student])
        await session.flush()

        bitmap = _empty_bitmap(weeks=18, days=7, slots=12)
        session.add_all(
            [
                StudentBusyBitmap(
                    id=demo_id("student-bitmap"),
                    student_id=student.id,
                    term_id=term.id,
                    start_week=1,
                    end_week=18,
                    days_per_week=7,
                    slots_per_day=12,
                    bitmap=bitmap,
                    mapping_version=1,
                    source_version="DEMO-V1",
                ),
                TeacherBusyBitmap(
                    id=demo_id("teacher-bitmap"),
                    teacher_id=teacher.id,
                    term_id=term.id,
                    start_week=1,
                    end_week=18,
                    days_per_week=7,
                    slots_per_day=12,
                    bitmap=bitmap,
                    mapping_version=1,
                    source_version="DEMO-V1",
                ),
            ]
        )

        course = ExperimentCourse(
            id=demo_id("course"),
            course_code="DEMO-PHY001",
            course_name="物理实验演示课程",
            course_type="EXPERIMENT",
            credits=Decimal("1.0"),
            default_slots=4,
            description="仅用于验证系统部署的虚构课程",
            status="ACTIVE",
            created_by=admin_id,
        )
        project = ExperimentProject(
            id=demo_id("project"),
            project_code="DEMO-PHY001-P01",
            course_id=course.id,
            project_name="基础测量演示实验",
            category="BASIC",
            required_slots=4,
            default_group_size=1,
            group_mode="INDIVIDUAL",
            historical_selection_ratio=Decimal("1.0000"),
            status="ACTIVE",
            created_by=admin_id,
        )
        session.add_all([course, project])
        await session.flush()

        plan = TrainingPlan(
            id=demo_id("plan"),
            plan_code="DEMO-PLAN-2026-V1",
            major_id=major.id,
            enrollment_year=2026,
            version_no=1,
            status="PUBLISHED",
            effective_from=date(2026, 9, 1),
            published_at=now,
            published_by=admin_id,
            created_by=admin_id,
        )
        plan_course = TrainingPlanCourse(
            id=demo_id("plan-course"),
            plan_id=plan.id,
            course_id=course.id,
            course_nature="REQUIRED",
            study_year=1,
            semester_no=1,
            required_project_count=1,
            optional_project_min_count=0,
            order_rule_text="演示课程包含一个必做实验项目。",
            created_by=admin_id,
        )
        plan_project = TrainingPlanProject(
            id=demo_id("plan-project"),
            plan_course_id=plan_course.id,
            project_id=project.id,
            requirement_type="REQUIRED",
            display_order=1,
        )
        session.add_all([plan, plan_course, plan_project])
        await session.flush()

        laboratory = Laboratory(
            id=demo_id("laboratory"),
            lab_code="DEMO-LAB-001",
            name="演示实验室",
            campus_id=campus.id,
            room_type="基础物理实验室",
            safety_capacity=1,
            manager_teacher_id=teacher.id,
            status="ACTIVE",
            description="仅用于验证系统部署的虚构实验室",
            created_by=admin_id,
        )
        capability = LabProjectCapability(
            id=demo_id("lab-capability"),
            laboratory_id=laboratory.id,
            project_id=project.id,
            effective_capacity=1,
            status="ACTIVE",
            note="演示能力配置",
        )
        equipment_type = EquipmentType(
            id=demo_id("equipment-type"),
            equipment_code="DEMO-EQ-001",
            name="演示测量仪器",
            model="DEMO",
            unit="台",
            status="ACTIVE",
            created_by=admin_id,
        )
        inventory = LabEquipmentInventory(
            id=demo_id("inventory"),
            laboratory_id=laboratory.id,
            equipment_type_id=equipment_type.id,
            total_quantity=1,
            usable_quantity=1,
            disabled_quantity=0,
            usage_note="一人一台（演示）",
            students_per_unit=1,
            sharing_rule_status="CONFIRMED",
            sharing_rule_source="MANUAL",
            checked_at=now,
            created_by=admin_id,
        )
        requirement = ProjectEquipmentRequirement(
            id=demo_id("equipment-requirement"),
            project_id=project.id,
            equipment_type_id=equipment_type.id,
            units_per_group=1,
            required=True,
            description="演示实验所需仪器",
        )
        qualification = TeacherProjectQualification(
            id=demo_id("qualification"),
            teacher_id=teacher.id,
            project_id=project.id,
            valid_from=date(2026, 9, 1),
            status="ACTIVE",
            created_by=admin_id,
        )
        session.add_all(
            [
                laboratory,
                capability,
                equipment_type,
                inventory,
                requirement,
                qualification,
            ]
        )
        await session.flush()

        task = TeachingTask(
            id=demo_id("teaching-task"),
            task_code="DEMO-TASK-PHY001",
            term_id=term.id,
            course_id=course.id,
            planned_student_count=1,
            week_start=1,
            week_end=18,
            capacity_buffer_ratio=Decimal("1.00"),
            status="READY",
            created_by=admin_id,
        )
        cohort = TeachingTaskCohort(
            id=demo_id("task-cohort"),
            task_id=task.id,
            major_id=major.id,
            enrollment_year=2026,
            class_id=student_class.id,
            student_count=1,
        )
        demand = ProjectDemand(
            id=demo_id("project-demand"),
            task_id=task.id,
            project_id=project.id,
            requirement_type="REQUIRED",
            base_demand=1,
            prediction_ratio=Decimal("1.0000"),
            buffer_ratio=Decimal("1.00"),
            required_capacity=1,
            required_session_count=1,
            calculation_snapshot={"source": "DEMO_BOOTSTRAP"},
        )
        session.add_all([task, cohort, demand])

    print("最小演示资料初始化成功：")
    print("- 教师账号：demo_teacher")
    print("- 学生账号：demo_student")
    print("- 密码：使用本次初始化时输入的密码")
    print("所有 DEMO 资料均为虚构数据，不用于生产环境。")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="向空业务库写入最小虚构演示资料。"
    )
    parser.add_argument(
        "--confirm-demo-data",
        action="store_true",
        help="确认目标是演示环境，并允许写入 DEMO 标记资料",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="从标准输入读取教师和学生的共用演示密码",
    )
    return parser


async def async_main() -> int:
    args = build_parser().parse_args()
    if not args.confirm_demo_data:
        print(
            "未写入数据：必须显式传入 --confirm-demo-data。",
            file=sys.stderr,
        )
        return 2
    try:
        password = read_demo_password(password_stdin=args.password_stdin)
        await bootstrap_demo(password)
        return 0
    except (ValueError, RuntimeError, EOFError) as error:
        print(f"演示资料初始化失败：{error}", file=sys.stderr)
        return 2
    finally:
        await dispose_database_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
