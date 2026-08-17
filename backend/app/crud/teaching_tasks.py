from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.curriculum import (
    AcademicTerm,
    ExperimentCourse,
    ExperimentProject,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingPlanProject,
)
from app.models.scheduling import ProjectDemand, TeachingTask, TeachingTaskCohort


async def get_or_create_active_term(session: AsyncSession) -> AcademicTerm:
    """获取活跃学期。优先返回有教学任务数据的学期，否则创建当前学期。"""
    # 1. 查找有教学任务的学期（含演示数据）
    task_term_stmt = (
        select(AcademicTerm)
        .join(TeachingTask, TeachingTask.term_id == AcademicTerm.id)
        .order_by(AcademicTerm.start_date.desc())
        .limit(1)
    )
    result = await session.execute(task_term_stmt)
    term = result.scalar_one_or_none()
    if term is not None:
        return term

    # 2. 查找当前学期
    stmt = select(AcademicTerm).where(
        AcademicTerm.academic_year == "2026-2027",
        AcademicTerm.semester_no == 1,
    )
    result = await session.execute(stmt)
    term = result.scalar_one_or_none()
    if term is not None:
        return term

    # 3. 创建新学期
    term = AcademicTerm(
        id=uuid4(),
        code="2026-2027-1",
        academic_year="2026-2027",
        semester_no=1,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 15),
        total_weeks=18,
        days_per_week=7,
        slots_per_day=12,
        status="ACTIVE",
    )
    session.add(term)
    await session.flush()
    return term


async def get_teaching_tasks(
    session: AsyncSession, term_id: UUID
) -> list[TeachingTask]:
    """获取指定学期的所有教学任务（含课程、队列、需求）。"""
    stmt = (
        select(TeachingTask)
        .options(
            selectinload(TeachingTask.course),
            selectinload(TeachingTask.term),
            selectinload(TeachingTask.cohorts).selectinload(TeachingTaskCohort.major),
            selectinload(TeachingTask.demands).selectinload(ProjectDemand.project),
        )
        .where(TeachingTask.term_id == term_id)
        .order_by(TeachingTask.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().unique())


async def get_teaching_task_by_id(
    session: AsyncSession, task_id: UUID
) -> TeachingTask | None:
    stmt = (
        select(TeachingTask)
        .options(
            selectinload(TeachingTask.course),
            selectinload(TeachingTask.term),
            selectinload(TeachingTask.cohorts).selectinload(TeachingTaskCohort.major),
            selectinload(TeachingTask.demands).selectinload(ProjectDemand.project),
        )
        .where(TeachingTask.id == task_id)
    )
    result = await session.execute(stmt)
    return result.scalars().unique().one_or_none()


async def get_task_by_course_term(
    session: AsyncSession, course_id: UUID, term_id: UUID
) -> TeachingTask | None:
    stmt = select(TeachingTask).where(
        TeachingTask.course_id == course_id,
        TeachingTask.term_id == term_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_target_cohorts(
    session: AsyncSession, course_id: UUID
) -> list[dict]:
    """从已发布培养方案获取该课程的目标学生群体。返回 [{major_id, enrollment_year}]。"""
    stmt = (
        select(
            TrainingPlan.major_id,
            TrainingPlan.enrollment_year,
        )
        .join(TrainingPlanCourse, TrainingPlanCourse.plan_id == TrainingPlan.id)
        .where(
            TrainingPlan.status == "PUBLISHED",
            TrainingPlanCourse.course_id == course_id,
            TrainingPlanCourse.course_nature == "REQUIRED",
        )
        .group_by(TrainingPlan.major_id, TrainingPlan.enrollment_year)
    )
    result = await session.execute(stmt)
    return [{"major_id": r[0], "enrollment_year": r[1]} for r in result.all()]


async def create_teaching_task(
    session: AsyncSession,
    course_id: UUID,
    term_id: UUID,
    week_start: int,
    week_end: int,
    planned_student_count: int,
    created_by: UUID,
) -> TeachingTask:
    course = await session.get(ExperimentCourse, course_id)
    task_code = f"TT-{course.course_code}-{term_id.hex[:8]}"
    task = TeachingTask(
        task_code=task_code,
        term_id=term_id,
        course_id=course_id,
        planned_student_count=planned_student_count,
        week_start=week_start,
        week_end=week_end,
        status="DRAFT",
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(task)
    await session.flush()
    return task


async def add_task_cohort(
    session: AsyncSession,
    task_id: UUID,
    major_id: UUID,
    enrollment_year: int,
    student_count: int,
) -> TeachingTaskCohort:
    cohort = TeachingTaskCohort(
        task_id=task_id,
        major_id=major_id,
        enrollment_year=enrollment_year,
        student_count=student_count,
    )
    session.add(cohort)
    return cohort


async def add_project_demand(
    session: AsyncSession,
    task_id: UUID,
    project_id: UUID,
    requirement_type: str,
    base_demand: int,
    prediction_ratio: Decimal,
    buffer_ratio: Decimal,
    required_capacity: int,
    group_size: int,
) -> ProjectDemand:
    required_sessions = max(1, (required_capacity + group_size - 1) // group_size)
    demand = ProjectDemand(
        task_id=task_id,
        project_id=project_id,
        requirement_type=requirement_type,
        base_demand=base_demand,
        prediction_ratio=prediction_ratio,
        buffer_ratio=buffer_ratio,
        required_capacity=required_capacity,
        required_session_count=required_sessions,
        calculation_snapshot={
            "base": base_demand,
            "prediction": float(prediction_ratio),
            "buffer": float(buffer_ratio),
            "capacity": required_capacity,
            "sessions": required_sessions,
            "group_size": group_size,
        },
    )
    session.add(demand)
    return demand


async def clear_task_children(session: AsyncSession, task_id: UUID) -> None:
    """清除教学任务的队列和需求数据。"""
    from sqlalchemy import delete as sql_delete
    await session.execute(
        sql_delete(ProjectDemand).where(ProjectDemand.task_id == task_id)
    )
    await session.execute(
        sql_delete(TeachingTaskCohort).where(TeachingTaskCohort.task_id == task_id)
    )
    await session.flush()


async def get_training_plan_courses_for_course(
    session: AsyncSession, course_id: UUID
) -> list[TrainingPlanCourse]:
    """获取该课程在所有已发布培养方案中的配置（每专业取最新一条）。"""
    # 子查询：每专业取一个 plan_id
    dedup_plan_subq = (
        select(TrainingPlan.id)
        .where(TrainingPlan.status == "PUBLISHED")
        .distinct(TrainingPlan.major_id)
        .order_by(TrainingPlan.major_id, TrainingPlan.version_no.desc(), TrainingPlan.created_at.desc())
        .subquery()
    )
    stmt = (
        select(TrainingPlanCourse)
        .options(
            selectinload(TrainingPlanCourse.plan),
            selectinload(TrainingPlanCourse.projects).selectinload(
                TrainingPlanProject.project
            ),
        )
        .where(
            TrainingPlanCourse.plan_id.in_(select(dedup_plan_subq)),
            TrainingPlanCourse.course_id == course_id,
        )
    )
    result = await session.execute(stmt)
    return list(result.scalars().unique())


async def get_teachers_by_project_ids(
    session: AsyncSession, project_ids: list[UUID]
) -> dict[UUID, list[tuple[UUID, str]]]:
    if not project_ids:
        return {}
    from app.models.identity import Teacher
    from app.models.resources import TeacherProjectQualification
    stmt = (
        select(TeacherProjectQualification.project_id, Teacher.id, Teacher.name)
        .join(Teacher, Teacher.id == TeacherProjectQualification.teacher_id)
        .where(
            TeacherProjectQualification.project_id.in_(project_ids),
            TeacherProjectQualification.status == "ACTIVE",
            Teacher.status == "ACTIVE",
        )
    )
    result = await session.execute(stmt)
    mapping: dict[UUID, list[tuple[UUID, str]]] = {pid: [] for pid in project_ids}
    for pid, teacher_id, name in result.all():
        mapping.setdefault(pid, []).append((teacher_id, name))
    return mapping


async def get_equipment_by_project_ids(
    session: AsyncSession, project_ids: list[UUID]
) -> dict[UUID, list[tuple[UUID, str]]]:
    if not project_ids:
        return {}
    from app.models.resources import EquipmentType, ProjectEquipmentRequirement
    stmt = (
        select(ProjectEquipmentRequirement.project_id, EquipmentType.id, EquipmentType.name)
        .join(EquipmentType, EquipmentType.id == ProjectEquipmentRequirement.equipment_type_id)
        .where(ProjectEquipmentRequirement.project_id.in_(project_ids))
    )
    result = await session.execute(stmt)
    mapping: dict[UUID, list[tuple[UUID, str]]] = {pid: [] for pid in project_ids}
    for pid, equipment_id, name in result.all():
        mapping.setdefault(pid, []).append((equipment_id, name))
    return mapping
