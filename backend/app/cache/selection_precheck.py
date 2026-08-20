"""Redis materialized context used by the selection admission Lua script."""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.db.redis_client import get_redis_client
from app.models.application import ApplicationRequest
from app.models.curriculum import (
    AcademicTerm,
    CoursePrerequisite,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingPlanProject,
)
from app.models.enrollment import StudentCourseCompletion, StudentProjectRecord
from app.models.identity import Student, StudentBusyBitmap
from app.models.scheduling import ExperimentSession

CONTEXT_VERSION = "v1"
CONTEXT_TTL_SECONDS = 7200
CONTEXT_TTL_JITTER_SECONDS = 900
ACTIVE_RECORD_STATUSES = {"SELECTED", "COMPLETED", "ABSENT", "MAKEUP_PENDING"}
TIME_RECORD_STATUSES = {"SELECTED", "MAKEUP_PENDING"}
ACTIVE_APPLICATION_STATUSES = {
    "SUBMITTED",
    "VALIDATING",
    "PENDING_REVIEW",
    "APPROVED",
}
logger = logging.getLogger(__name__)


def student_context_key(student_id: UUID, term_id: UUID) -> str:
    return f"selection:student-context:{student_id}:{term_id}:{CONTEXT_VERSION}"


def selected_projects_key(student_id: UUID, term_id: UUID) -> str:
    return f"selection:selected-projects:{student_id}:{term_id}"


def applications_key(student_id: UUID, term_id: UUID) -> str:
    return f"selection:applications:{student_id}:{term_id}"


def idempotency_key(student_id: UUID, session_id: UUID) -> str:
    return f"selection:idempotency:{student_id}:{session_id}"


def _period(student: Student, term: AcademicTerm) -> tuple[int, int]:
    base_year = int(term.academic_year.split("-", maxsplit=1)[0])
    return max(1, base_year - student.enrollment_year + 1), term.semester_no


def _ordinal(session: ExperimentSession, *, end: bool = False) -> int:
    slot = session.end_slot if end else session.start_slot
    return ((session.week_no - 1) * 7 + session.day_of_week - 1) * 12 + slot


def _add_session_slots(target: set[str], session: ExperimentSession) -> None:
    for slot in range(session.start_slot, session.end_slot + 1):
        target.add(f"{session.week_no}:{session.day_of_week}:{slot}")


def _bitmap_slots(bitmap: StudentBusyBitmap | None) -> set[str]:
    if bitmap is None:
        return set()
    result: set[str] = set()
    for week in range(bitmap.start_week, bitmap.end_week + 1):
        for day_no in range(1, bitmap.days_per_week + 1):
            for slot in range(1, bitmap.slots_per_day + 1):
                index = (
                    (week - bitmap.start_week)
                    * bitmap.days_per_week
                    * bitmap.slots_per_day
                    + (day_no - 1) * bitmap.slots_per_day
                    + slot
                    - 1
                )
                if index < len(bitmap.bitmap) * 8 and bitmap.bitmap[index // 8] & (
                    1 << (7 - index % 8)
                ):
                    result.add(f"{week}:{day_no}:{slot}")
    return result


async def build_selection_context(
    session: AsyncSession, *, student_id: UUID, term_id: UUID
) -> tuple[dict[str, object], set[UUID], set[UUID]] | None:
    student = await session.get(Student, student_id)
    term = await session.get(AcademicTerm, term_id)
    if student is None or term is None:
        return None

    plan = (
        await session.execute(
            select(TrainingPlan)
            .options(
                selectinload(TrainingPlan.courses)
                .selectinload(TrainingPlanCourse.projects)
                .selectinload(TrainingPlanProject.project),
                selectinload(TrainingPlan.courses)
                .selectinload(TrainingPlanCourse.prerequisites)
                .selectinload(CoursePrerequisite.prerequisite_course),
                selectinload(TrainingPlan.courses).selectinload(
                    TrainingPlanCourse.order_constraints
                ),
            )
            .where(
                TrainingPlan.major_id == student.major_id,
                TrainingPlan.enrollment_year == student.enrollment_year,
                TrainingPlan.status == "PUBLISHED",
            )
            .order_by(TrainingPlan.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    completions = list(
        await session.scalars(
            select(StudentCourseCompletion).where(
                StudentCourseCompletion.student_id == student_id
            )
        )
    )
    completion_map = {item.course_id: item.status for item in completions}
    bitmap = (
        await session.execute(
            select(StudentBusyBitmap)
            .where(
                StudentBusyBitmap.student_id == student_id,
                StudentBusyBitmap.term_id == term_id,
            )
            .order_by(StudentBusyBitmap.mapping_version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    records = list(
        (
            await session.execute(
                select(StudentProjectRecord)
                .options(joinedload(StudentProjectRecord.session))
                .where(
                    StudentProjectRecord.student_id == student_id,
                    StudentProjectRecord.term_id == term_id,
                    StudentProjectRecord.status.in_(ACTIVE_RECORD_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    applications = list(
        (
            await session.execute(
                select(ApplicationRequest).where(
                    ApplicationRequest.student_id == student_id,
                    ApplicationRequest.status.in_(ACTIVE_APPLICATION_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    application_session_ids = {
        value
        for item in applications
        for value in (item.original_session_id, item.target_session_id)
        if value is not None
    }
    application_sessions = (
        list(
            await session.scalars(
                select(ExperimentSession).where(
                    ExperimentSession.id.in_(application_session_ids)
                )
            )
        )
        if application_session_ids
        else []
    )

    projects: dict[str, dict[str, object]] = {}
    order_constraints: list[dict[str, str]] = []
    current_period = _period(student, term)
    if plan is not None:
        for course in plan.courses:
            violations: list[str] = []
            if student.academic_status != "ACTIVE":
                violations.append("STUDENT_INACTIVE")
            if current_period < (course.study_year, course.semester_no):
                violations.append("STUDY_PERIOD_NOT_REACHED")
            if completion_map.get(course.course_id) == "PASSED":
                violations.append("COURSE_ALREADY_PASSED")
            if any(
                prerequisite.requirement_type == "MUST_COMPLETE"
                and completion_map.get(prerequisite.prerequisite_course_id) != "PASSED"
                for prerequisite in course.prerequisites
            ):
                violations.append("PREREQUISITE_COURSE_NOT_PASSED")
            for item in course.projects:
                projects[str(item.project_id)] = {
                    "course_id": str(course.course_id),
                    "requirement_type": item.requirement_type,
                    "violations": violations,
                }
            order_constraints.extend(
                {
                    "before": str(item.before_project_id),
                    "after": str(item.after_project_id),
                }
                for item in course.order_constraints
            )

    selected_projects = {item.project_id for item in records}
    application_projects = {
        value
        for item in applications
        for value in (item.project_id, item.target_project_id)
        if value is not None
    }
    busy_slots = _bitmap_slots(bitmap)
    selected_times: dict[str, dict[str, int]] = {}
    for record in records:
        if record.session is None:
            continue
        selected_times[str(record.project_id)] = {
            "start": _ordinal(record.session),
            "end": _ordinal(record.session, end=True),
        }
        if record.status in TIME_RECORD_STATUSES:
            _add_session_slots(busy_slots, record.session)
    for application_session in application_sessions:
        _add_session_slots(busy_slots, application_session)

    context: dict[str, object] = {
        "academic_active": student.academic_status == "ACTIVE",
        "bitmap_valid": bitmap is not None,
        "term_id": str(term_id),
        "projects": projects,
        "busy_slots": {value: True for value in busy_slots},
        "selected_times": selected_times,
        "order_constraints": order_constraints,
        "generated_on": datetime.now(UTC).date().isoformat(),
    }
    return context, selected_projects, application_projects


async def write_selection_context(
    student_id: UUID,
    term_id: UUID,
    context: dict[str, object],
    selected_projects: set[UUID],
    application_projects: set[UUID],
) -> None:
    redis = get_redis_client()
    context_key = student_context_key(student_id, term_id)
    selected_key = selected_projects_key(student_id, term_id)
    application_key = applications_key(student_id, term_id)
    pipe = redis.pipeline(transaction=True)
    ttl_seconds = CONTEXT_TTL_SECONDS + secrets.randbelow(
        CONTEXT_TTL_JITTER_SECONDS + 1
    )
    pipe.set(
        context_key,
        json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        ex=ttl_seconds,
    )
    pipe.delete(selected_key, application_key)
    if selected_projects:
        pipe.sadd(selected_key, *(str(value) for value in selected_projects))
        pipe.expire(selected_key, ttl_seconds)
    if application_projects:
        pipe.sadd(application_key, *(str(value) for value in application_projects))
        pipe.expire(application_key, ttl_seconds)
    await pipe.execute()


async def refresh_selection_context(
    session: AsyncSession, *, student_id: UUID, term_id: UUID
) -> bool:
    built = await build_selection_context(
        session, student_id=student_id, term_id=term_id
    )
    if built is None:
        return False
    await write_selection_context(student_id, term_id, *built)
    return True


async def ensure_selection_context(
    session: AsyncSession,
    *,
    student_id: UUID,
    term_id: UUID,
    redis: Redis | None = None,
) -> None:
    """选课准入前确保 context 缓存存在,缺失时同步构建(幂等)。

    课表发布批量清理或缓存过期后,学生的准入上下文可能缺失,直接进 Lua
    会命中 SELECTION_CACHE_MISSING。这里在准入前补建,避免学生刷页面时
    被"选课状态发生变化"挡住。Redis 不可用时静默返回,由调用方降级。
    """

    try:
        client = redis or get_redis_client()
        if await client.exists(student_context_key(student_id, term_id)):
            return
    except Exception:  # noqa: BLE001 - admission falls back on its own
        return
    await refresh_selection_context(session, student_id=student_id, term_id=term_id)


async def invalidate_selection_context(student_id: UUID, term_id: UUID) -> None:
    try:
        await get_redis_client().delete(
            student_context_key(student_id, term_id),
            selected_projects_key(student_id, term_id),
            applications_key(student_id, term_id),
        )
    except Exception:  # cache invalidation must not reverse a committed transaction
        logger.warning(
            "Selection context invalidation failed student=%s term=%s",
            student_id,
            term_id,
            exc_info=True,
        )
