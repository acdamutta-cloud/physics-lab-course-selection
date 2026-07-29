from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import String, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import OperationLog
from app.models.curriculum import (
    CoursePrerequisite,
    ExperimentCourse,
    ExperimentProject,
    ProjectOrderConstraint,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingPlanProject,
)
from app.models.identity import Major

PLAN_LOAD_OPTIONS = (
    selectinload(TrainingPlan.major),
    selectinload(TrainingPlan.courses).selectinload(TrainingPlanCourse.course),
    selectinload(TrainingPlan.courses)
    .selectinload(TrainingPlanCourse.projects)
    .selectinload(TrainingPlanProject.project),
    selectinload(TrainingPlan.courses)
    .selectinload(TrainingPlanCourse.prerequisites)
    .selectinload(CoursePrerequisite.prerequisite_course),
    selectinload(TrainingPlan.courses)
    .selectinload(TrainingPlanCourse.order_constraints)
    .selectinload(ProjectOrderConstraint.before_project),
    selectinload(TrainingPlan.courses)
    .selectinload(TrainingPlanCourse.order_constraints)
    .selectinload(ProjectOrderConstraint.after_project),
)


async def get_plans_list(
    session: AsyncSession,
    major_id: UUID | None = None,
    enrollment_year: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[TrainingPlan], int]:
    filters = []
    if major_id:
        filters.append(TrainingPlan.major_id == major_id)
    if enrollment_year:
        filters.append(TrainingPlan.enrollment_year == enrollment_year)
    if status:
        filters.append(TrainingPlan.status == status)
    else:
        filters.append(TrainingPlan.status != "ARCHIVED")
    if keyword:
        normalized = keyword.strip()
        filters.append(
            or_(
                Major.name.ilike(f"%{normalized}%"),
                Major.code.ilike(f"%{normalized}%"),
                TrainingPlan.plan_code.ilike(f"%{normalized}%"),
                func.cast(TrainingPlan.enrollment_year, String).ilike(
                    f"%{normalized}%"
                ),
            )
        )

    stmt = (
        select(TrainingPlan)
        .join(Major, Major.id == TrainingPlan.major_id)
        .options(*PLAN_LOAD_OPTIONS)
        .where(*filters)
        .order_by(
            TrainingPlan.enrollment_year.desc(),
            Major.code,
            TrainingPlan.version_no.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    count_stmt = (
        select(func.count(TrainingPlan.id))
        .join(Major, Major.id == TrainingPlan.major_id)
        .where(*filters)
    )
    items = list((await session.execute(stmt)).scalars().unique())
    total = int((await session.scalar(count_stmt)) or 0)
    return items, total


async def get_plan_detail(
    session: AsyncSession, plan_id: UUID, *, for_update: bool = False
) -> TrainingPlan | None:
    stmt = (
        select(TrainingPlan)
        .options(*PLAN_LOAD_OPTIONS)
        .where(TrainingPlan.id == plan_id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalars().unique().one_or_none()


async def get_latest_plan_for_update(
    session: AsyncSession, major_id: UUID, enrollment_year: int
) -> TrainingPlan | None:
    stmt = (
        select(TrainingPlan)
        .where(
            TrainingPlan.major_id == major_id,
            TrainingPlan.enrollment_year == enrollment_year,
        )
        .order_by(TrainingPlan.version_no.desc())
        .limit(1)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_current_plan_for_update(
    session: AsyncSession, major_id: UUID, enrollment_year: int
) -> TrainingPlan | None:
    stmt = (
        select(TrainingPlan)
        .where(
            TrainingPlan.major_id == major_id,
            TrainingPlan.enrollment_year == enrollment_year,
            TrainingPlan.status != "ARCHIVED",
        )
        .order_by(TrainingPlan.version_no.desc())
        .limit(1)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_active_major(session: AsyncSession, major_id: UUID) -> Major | None:
    return (
        await session.execute(
            select(Major).where(Major.id == major_id, Major.status == "ACTIVE")
        )
    ).scalar_one_or_none()


async def get_active_courses_by_ids(
    session: AsyncSession, course_ids: set[UUID]
) -> dict[UUID, ExperimentCourse]:
    if not course_ids:
        return {}
    items = (
        await session.execute(
            select(ExperimentCourse).where(
                ExperimentCourse.id.in_(course_ids),
                ExperimentCourse.status == "ACTIVE",
            )
        )
    ).scalars()
    return {item.id: item for item in items}


async def get_active_projects_by_ids(
    session: AsyncSession, project_ids: set[UUID]
) -> dict[UUID, ExperimentProject]:
    if not project_ids:
        return {}
    items = (
        await session.execute(
            select(ExperimentProject).where(
                ExperimentProject.id.in_(project_ids),
                ExperimentProject.status == "ACTIVE",
            )
        )
    ).scalars()
    return {item.id: item for item in items}


async def _insert_courses(
    session: AsyncSession,
    plan: TrainingPlan,
    courses_data: list[dict],
    actor_id: UUID,
) -> None:
    for course_data in courses_data:
        course = TrainingPlanCourse(
            plan_id=plan.id,
            course_id=course_data["course_id"],
            course_nature=course_data.get("course_nature", "REQUIRED"),
            study_year=course_data["study_year"],
            semester_no=course_data["semester_no"],
            required_project_count=course_data.get("required_project_count", 0),
            optional_project_min_count=course_data.get("optional_project_min_count", 0),
            order_rule_text=course_data.get("order_rule_text"),
            allow_order_override=course_data.get("allow_order_override", False),
            created_by=actor_id,
            updated_by=actor_id,
        )
        session.add(course)
        await session.flush()

        prerequisite_ids = course_data.get("prerequisite_course_ids") or []
        if not prerequisite_ids and course_data.get("prerequisite_course_id"):
            prerequisite_ids = [course_data["prerequisite_course_id"]]
        for prerequisite_id in prerequisite_ids:
            session.add(
                CoursePrerequisite(
                    plan_course_id=course.id,
                    prerequisite_course_id=prerequisite_id,
                    requirement_type="MUST_COMPLETE",
                )
            )

        for project_data in course_data.get("projects", []):
            session.add(
                TrainingPlanProject(
                    plan_course_id=course.id,
                    project_id=project_data["project_id"],
                    requirement_type=project_data.get(
                        "requirement_type", "REQUIRED"
                    ),
                    display_order=project_data.get("display_order", 1),
                )
            )
    await session.flush()


async def create_plan(
    session: AsyncSession,
    *,
    plan_code: str,
    major_id: UUID,
    enrollment_year: int,
    version_no: int,
    effective_from: date | None,
    actor_id: UUID,
    courses_data: list[dict],
) -> TrainingPlan:
    plan = TrainingPlan(
        plan_code=plan_code,
        major_id=major_id,
        enrollment_year=enrollment_year,
        version_no=version_no,
        effective_from=effective_from,
        status="DRAFT",
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(plan)
    await session.flush()
    await _insert_courses(session, plan, courses_data, actor_id)
    return plan


async def replace_plan_courses(
    session: AsyncSession,
    plan: TrainingPlan,
    *,
    major_id: UUID,
    enrollment_year: int,
    effective_from: date | None,
    actor_id: UUID,
    courses_data: list[dict],
) -> None:
    course_ids = select(TrainingPlanCourse.id).where(
        TrainingPlanCourse.plan_id == plan.id
    )
    await session.execute(
        delete(CoursePrerequisite).where(
            CoursePrerequisite.plan_course_id.in_(course_ids)
        )
    )
    await session.execute(
        delete(TrainingPlanProject).where(
            TrainingPlanProject.plan_course_id.in_(course_ids)
        )
    )
    await session.execute(
        delete(ProjectOrderConstraint).where(
            ProjectOrderConstraint.plan_course_id.in_(course_ids)
        )
    )
    await session.execute(
        delete(TrainingPlanCourse).where(TrainingPlanCourse.plan_id == plan.id)
    )
    plan.major_id = major_id
    plan.enrollment_year = enrollment_year
    plan.effective_from = effective_from
    plan.updated_by = actor_id
    await session.flush()
    await _insert_courses(session, plan, courses_data, actor_id)


async def archive_other_published(
    session: AsyncSession, plan: TrainingPlan, actor_id: UUID
) -> list[UUID]:
    ids = list(
        (
            await session.execute(
                select(TrainingPlan.id).where(
                    TrainingPlan.major_id == plan.major_id,
                    TrainingPlan.enrollment_year == plan.enrollment_year,
                    TrainingPlan.status == "PUBLISHED",
                    TrainingPlan.id != plan.id,
                )
            )
        ).scalars()
    )
    if ids:
        await session.execute(
            update(TrainingPlan)
            .where(TrainingPlan.id.in_(ids))
            .values(status="ARCHIVED", updated_by=actor_id)
        )
        await session.flush()
    return ids


async def publish_plan(
    session: AsyncSession, plan: TrainingPlan, publisher_id: UUID
) -> None:
    plan.status = "PUBLISHED"
    plan.published_at = datetime.now(timezone.utc)
    plan.published_by = publisher_id
    plan.updated_by = publisher_id
    await session.flush()


async def archive_plan(
    session: AsyncSession, plan: TrainingPlan, actor_id: UUID
) -> None:
    plan.status = "ARCHIVED"
    plan.updated_by = actor_id
    await session.flush()


async def add_operation_log(
    session: AsyncSession,
    *,
    actor_id: UUID,
    operation_type: str,
    object_type: str = "TRAINING_PLAN",
    object_id: UUID | None,
    before_snapshot: dict,
    after_snapshot: dict,
    result: str = "SUCCEEDED",
) -> None:
    session.add(
        OperationLog(
            operator_user_id=actor_id,
            operation_type=operation_type,
            object_type=object_type,
            object_id=object_id,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            result=result,
        )
    )
    await session.flush()


async def get_active_majors(session: AsyncSession) -> list[Major]:
    result = await session.execute(
        select(Major).where(Major.status == "ACTIVE").order_by(Major.code)
    )
    return list(result.scalars())


async def get_active_courses(session: AsyncSession) -> list[ExperimentCourse]:
    result = await session.execute(
        select(ExperimentCourse)
        .where(ExperimentCourse.status == "ACTIVE")
        .order_by(ExperimentCourse.course_code)
    )
    return list(result.scalars())


async def get_course_projects(
    session: AsyncSession, course_id: UUID
) -> list[ExperimentProject]:
    result = await session.execute(
        select(ExperimentProject)
        .where(
            ExperimentProject.course_id == course_id,
            ExperimentProject.status == "ACTIVE",
        )
        .order_by(ExperimentProject.project_code)
    )
    return list(result.scalars())


async def create_course_project(
    session: AsyncSession,
    *,
    course_id: UUID,
    project_code: str,
    project_name: str,
    category: str,
    required_slots: int,
    default_group_size: int,
    historical_selection_ratio: Decimal,
    actor_id: UUID,
) -> ExperimentProject:
    project = ExperimentProject(
        course_id=course_id,
        project_code=project_code.strip(),
        project_name=project_name.strip(),
        category=category,
        required_slots=required_slots,
        default_group_size=default_group_size,
        historical_selection_ratio=historical_selection_ratio,
        status="ACTIVE",
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(project)
    await session.flush()
    return project
