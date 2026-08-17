import base64
import json
import logging
from datetime import date as dt_date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import StreamingResponse

from app.agents.registry import (
    invoke_registered_graph,
    stream_registered_graph,
)
from app.api.dependencies import get_current_user
from app.cache.academic_term import get_active_term_id
from app.cache.student_views import (
    bitmap_key,
    dashboard_summary,
    dashboard_summary_key,
    dashboard_timetable,
    get_or_build,
    get_or_build_dashboard,
    timetable_key,
)
from app.core.config.settings import get_settings
from app.crud import students as student_crud
from app.crud.teaching_tasks import get_or_create_active_term
from app.db.redis_client import get_redis_client
from app.db.session import get_db_session
from app.models.curriculum import (
    CoursePrerequisite,
    ExperimentCourse,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingPlanProject,
)
from app.models.enrollment import StudentCourseCompletion, StudentProjectRecord
from app.models.identity import Campus, Major, Student, Teacher
from app.models.resources import Laboratory
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.schemas.auth import UserProfile
from app.schemas.student_consultation import (
    ConsultationRequest,
    ConsultationResponse,
)
from app.services import selection_service
from app.services import selection_window_service as window_svc
from app.services.effective_session_service import effective_session_values
from app.services.student_ai_concurrency import (
    StudentAIConcurrencyError,
    StudentAILease,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/students", tags=["学生"])


async def _get_student(session, login_name: str) -> Student:
    student = (
        await session.execute(
            select(Student).where(Student.student_no == login_name.upper())
        )
    ).scalar_one_or_none()
    if student is None:
        raise HTTPException(status_code=404, detail="学生信息不存在")
    return student


def _calc_week(term) -> int:
    """计算当前教学周。学期结束后仍按日期累计。"""
    today = dt_date.today()
    if today < term.start_date:
        return 0
    return (today - term.start_date).days // 7 + 1


async def _get_my_bitmap_uncached(
    session: AsyncSession,
    current_user: UserProfile,
):
    """获取当前学生的忙闲位图。"""
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可查询")

    term = await get_or_create_active_term(session)
    student = await _get_student(session, current_user.login_name)
    bitmap = await student_crud.get_student_bitmap(session, student.id, term.id)
    if bitmap is None:
        return {"weeks": 18, "days": 7, "slots": 12, "data": None}

    return {
        "weeks": bitmap.end_week - bitmap.start_week + 1,
        "days": bitmap.days_per_week,
        "slots": bitmap.slots_per_day,
        "data": base64.b64encode(bitmap.bitmap).decode(),
        "start_week": bitmap.start_week,
        "end_week": bitmap.end_week,
    }


@router.get("/me/busy-bitmap")
async def get_my_bitmap(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可查询")
    student_id = current_user.student_id
    if student_id is None:
        student_id = (await _get_student(session, current_user.login_name)).id
    term_id = await get_active_term_id(session)
    settings = get_settings()
    return await get_or_build(
        bitmap_key(student_id, term_id),
        ttl=settings.student_bitmap_cache_ttl_seconds,
        builder=lambda: _get_my_bitmap_uncached(session, current_user),
    )


async def _get_dashboard_uncached(
    session: AsyncSession,
    current_user: UserProfile,
):
    """学生首页聚合数据。"""
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可查询")

    student = await _get_student(session, current_user.login_name)
    term = await get_or_create_active_term(session)
    major = await session.get(Major, student.major_id)
    campus = await session.get(Campus, student.campus_id)

    # ── 1. 课程完成情况 ──
    completions = (
        (
            await session.execute(
                select(StudentCourseCompletion).where(
                    StudentCourseCompletion.student_id == student.id,
                )
            )
        )
        .scalars()
        .all()
    )
    comp_map = {c.course_id: c.status for c in completions}

    # ── 2. 培养方案 → 课程 + 项目 ──
    plan = (
        await session.execute(
            select(TrainingPlan)
            .where(
                TrainingPlan.major_id == student.major_id,
                TrainingPlan.enrollment_year == student.enrollment_year,
                TrainingPlan.status == "PUBLISHED",
            )
            .order_by(TrainingPlan.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    courses_data = []
    total_required = 0
    total_optional_pool = 0
    total_optional_min = 0
    all_prereq_passed: list[str] = []
    all_prereq_failed: list[str] = []

    if plan:
        plan_courses = (
            (
                await session.execute(
                    select(TrainingPlanCourse)
                    .options(
                        selectinload(TrainingPlanCourse.course),
                        selectinload(TrainingPlanCourse.projects).selectinload(
                            TrainingPlanProject.project
                        ),
                    )
                    .where(TrainingPlanCourse.plan_id == plan.id)
                )
            )
            .scalars()
            .all()
        )
        plan_course_ids = [item.id for item in plan_courses]
        plan_project_ids = [
            item.project_id for course in plan_courses for item in course.projects
        ]
        project_sessions_by_id: dict[UUID, list[ExperimentSession]] = {}
        if plan_project_ids:
            all_project_sessions = (
                (
                    await session.execute(
                        select(ExperimentSession)
                        .options(
                            selectinload(ExperimentSession.laboratory),
                            selectinload(ExperimentSession.teacher),
                        )
                        .join(
                            ScheduleVersion,
                            ScheduleVersion.id == ExperimentSession.schedule_version_id,
                        )
                        .where(
                            ScheduleVersion.status.in_(["PUBLISHED", "DRAFT"]),
                            ExperimentSession.project_id.in_(plan_project_ids),
                        )
                        .order_by(
                            ExperimentSession.week_no,
                            ExperimentSession.day_of_week,
                            ExperimentSession.start_slot,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for item in all_project_sessions:
                project_sessions_by_id.setdefault(item.project_id, []).append(item)

        prerequisites_by_plan_course: dict[UUID, list[CoursePrerequisite]] = {}
        prerequisite_courses: dict[UUID, ExperimentCourse] = {}
        if plan_course_ids:
            all_prerequisites = (
                (
                    await session.execute(
                        select(CoursePrerequisite).where(
                            CoursePrerequisite.plan_course_id.in_(plan_course_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for item in all_prerequisites:
                prerequisites_by_plan_course.setdefault(item.plan_course_id, []).append(
                    item
                )
            prerequisite_ids = {
                item.prerequisite_course_id for item in all_prerequisites
            }
            if prerequisite_ids:
                prerequisite_courses = {
                    item.id: item
                    for item in (
                        (
                            await session.execute(
                                select(ExperimentCourse).where(
                                    ExperimentCourse.id.in_(prerequisite_ids)
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                }

        for pc in plan_courses:
            req_count = len(
                [p for p in pc.projects if p.requirement_type == "REQUIRED"]
            )
            opt_count = len(
                [p for p in pc.projects if p.requirement_type == "OPTIONAL"]
            )
            total_required += req_count
            total_optional_pool += opt_count
            total_optional_min += pc.optional_project_min_count or 0

            # 项目列表（含可用场次）
            projects_data = []
            for tp in sorted(pc.projects, key=lambda p: p.display_order or 1):
                proj = tp.project
                if proj is None:
                    continue
                proj_sessions = project_sessions_by_id.get(proj.id, [])
                projects_data.append(
                    {
                        "project_id": str(proj.id),
                        "project_name": proj.project_name,
                        "requirement_type": tp.requirement_type,
                        "category": proj.category or "",
                        "group_size": proj.default_group_size or 2,
                        "available_sessions": [
                            {
                                "id": str(s.id),
                                "week_no": s.week_no,
                                "day_of_week": s.day_of_week,
                                "start_slot": s.start_slot,
                                "end_slot": s.end_slot,
                                "lab_name": s.laboratory.name if s.laboratory else "",
                                "capacity": s.capacity,
                                "selected_count": s.selected_count,
                                "teacher_name": s.teacher.name if s.teacher else "",
                            }
                            for s in proj_sessions
                        ],
                    }
                )

            # 先修课程状态
            prereqs = prerequisites_by_plan_course.get(pc.id, [])
            prereq_passed = []
            prereq_failed = []
            for p in prereqs:
                prereq_course = prerequisite_courses.get(p.prerequisite_course_id)
                if not prereq_course:
                    continue
                status = comp_map.get(p.prerequisite_course_id, "NOT_TAKEN")
                if status == "PASSED":
                    prereq_passed.append(prereq_course.course_name)
                else:
                    prereq_failed.append(prereq_course.course_name)
            all_prereq_passed.extend(prereq_passed)
            all_prereq_failed.extend(prereq_failed)

            courses_data.append(
                {
                    "course_name": pc.course.course_name,
                    "course_code": pc.course.course_code,
                    "required_count": pc.required_project_count or 0,
                    "optional_min": pc.optional_project_min_count or 0,
                    "study_year": pc.study_year,
                    "semester_no": pc.semester_no,
                    "order_rule": pc.order_rule_text or "",
                    "completion_status": comp_map.get(pc.course_id, "NOT_TAKEN"),
                    "prerequisites_passed": prereq_passed,
                    "prerequisites_failed": prereq_failed,
                    "projects": projects_data,
                }
            )

    # ── 3. 选课进度 ──
    selected_count = len(
        (
            await session.execute(
                select(StudentProjectRecord).where(
                    StudentProjectRecord.student_id == student.id,
                    StudentProjectRecord.term_id == term.id,
                    StudentProjectRecord.status == "SELECTED",
                )
            )
        )
        .scalars()
        .all()
    )

    # 已选场次（供课表绿框）
    selected_records_all = (
        (
            await session.execute(
                select(StudentProjectRecord)
                .options(
                    selectinload(StudentProjectRecord.session).selectinload(
                        ExperimentSession.laboratory
                    ),
                    selectinload(StudentProjectRecord.session).selectinload(
                        ExperimentSession.project
                    ),
                    selectinload(StudentProjectRecord.session).selectinload(
                        ExperimentSession.teacher
                    ),
                )
                .where(
                    StudentProjectRecord.student_id == student.id,
                    StudentProjectRecord.term_id == term.id,
                    StudentProjectRecord.status == "SELECTED",
                )
            )
        )
        .scalars()
        .all()
    )
    selected_sessions = []
    selected_session_models = [
        rec.session for rec in selected_records_all if rec.session is not None
    ]
    effective = await effective_session_values(session, selected_session_models)
    effective_lab_ids = {item["laboratory_id"] for item in effective.values()}
    effective_teacher_ids = {item["teacher_id"] for item in effective.values()}
    effective_labs = (
        {
            item.id: item
            for item in (
                (
                    await session.execute(
                        select(Laboratory).where(Laboratory.id.in_(effective_lab_ids))
                    )
                )
                .scalars()
                .all()
            )
        }
        if effective_lab_ids
        else {}
    )
    effective_teachers = (
        {
            item.id: item
            for item in (
                (
                    await session.execute(
                        select(Teacher).where(Teacher.id.in_(effective_teacher_ids))
                    )
                )
                .scalars()
                .all()
            )
        }
        if effective_teacher_ids
        else {}
    )
    for rec in selected_records_all:
        if rec.session is None:
            continue
        s = rec.session
        actual = effective[s.id]
        actual_lab = effective_labs.get(actual["laboratory_id"])
        actual_teacher = effective_teachers.get(actual["teacher_id"])
        selected_sessions.append(
            {
                "session_id": str(s.id),
                "project_id": str(s.project_id),
                "course_id": str(rec.course_id),
                "course_name": next(
                    (
                        item["course_name"]
                        for item in courses_data
                        if any(
                            project["project_id"] == str(s.project_id)
                            for project in item["projects"]
                        )
                    ),
                    "",
                ),
                "course_code": next(
                    (
                        item["course_code"]
                        for item in courses_data
                        if any(
                            project["project_id"] == str(s.project_id)
                            for project in item["projects"]
                        )
                    ),
                    "",
                ),
                "week_no": actual["week_no"],
                "day_of_week": actual["day_of_week"],
                "start_slot": actual["start_slot"],
                "end_slot": actual["end_slot"],
                "project_name": s.project.project_name if s.project else "",
                "lab_name": actual_lab.name if actual_lab else "",
                "teacher_name": actual_teacher.name if actual_teacher else "",
            }
        )

    # ── 4. 下一项实验（从已选场次中取最早的一个）──
    next_lab = None
    cw = min(_calc_week(term), term.total_weeks)
    future_sessions = [item for item in selected_sessions if item["week_no"] >= cw]
    if future_sessions:
        next_lab = min(
            future_sessions,
            key=lambda item: (item["week_no"], item["day_of_week"], item["start_slot"]),
        )

    # ── 5. 忙闲位图 ──
    bm = await student_crud.get_student_bitmap(session, student.id, term.id)
    bitmap_b64 = base64.b64encode(bm.bitmap).decode() if bm else None

    # ── 6. 选课时间窗口（仅展示用途，门控判定在后端准入）──
    window = await window_svc.get_term_window(session, term.id)
    selection_window = (
        {
            "start_at": window.start_at.isoformat(),
            "end_at": window.end_at.isoformat(),
            "withdraw_end_at": (
                window.withdraw_end_at.isoformat()
                if window.withdraw_end_at
                else None
            ),
            "status": window.status,
        }
        if window is not None
        else None
    )

    return {
        "profile": {
            "name": student.name,
            "student_no": student.student_no,
            "major_name": major.name if major else "未知",
            "enrollment_year": student.enrollment_year,
            "campus_name": campus.name if campus else "未知",
        },
        "term": {
            "academic_year": term.academic_year,
            "semester_no": term.semester_no,
            "start_date": str(term.start_date),
            "current_week": _calc_week(term),
            "total_weeks": term.total_weeks,
        },
        "courses": courses_data,
        "prerequisites": {
            "passed": all_prereq_passed,
            "failed": all_prereq_failed,
        },
        "selection": {
            "selected_count": selected_count,
            "total_required": total_required,
            "total_optional_pool": total_optional_pool,
            "total_optional_min": total_optional_min,
            "selection_window": selection_window,
        },
        "next_lab": next_lab,
        "selected_sessions": selected_sessions,
        "bitmap_data": bitmap_b64,
        "bitmap_weeks": 18,
        "bitmap_days": 7,
        "bitmap_slots": 12,
    }


@router.get("/me/dashboard")
async def get_dashboard(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可查询")
    student_id = current_user.student_id
    if student_id is None:
        student_id = (await _get_student(session, current_user.login_name)).id
    term_id = await get_active_term_id(session)
    value = await get_or_build_dashboard(
        student_id,
        term_id,
        builder=lambda: _get_dashboard_uncached(session, current_user),
    )
    # Capacity is shared, highly dynamic data. Merge Redis stock into a copy of
    # the cached response so one enrolment does not invalidate 4,800 students.
    session_items = [
        item
        for course in value.get("courses", [])
        for project in course.get("projects", [])
        for item in project.get("available_sessions", [])
        if item.get("id")
    ]
    if session_items:
        keys = [f"session:stock:{item['id']}" for item in session_items]
        try:
            stocks = await get_redis_client().mget(keys)
            if any(stock is not None for stock in stocks):
                value = json.loads(json.dumps(value))
                copied_items = [
                    item
                    for course in value.get("courses", [])
                    for project in course.get("projects", [])
                    for item in project.get("available_sessions", [])
                    if item.get("id")
                ]
                for item, stock in zip(copied_items, stocks, strict=True):
                    if stock is not None:
                        remaining = max(0, int(stock))
                        item["selected_count"] = max(0, item["capacity"] - remaining)
        except Exception:  # noqa: BLE001 - preserve the cached DB-equivalent value
            logger.warning("Dashboard stock overlay failed", exc_info=True)
    # 选课窗口是全局低频配置：实时读库覆盖缓存值（dashboard 缓存 30 分钟，
    # 否则管理员刚改的窗口要等缓存过期学生端才看到）。
    window = await window_svc.get_term_window(session, term_id)
    value.setdefault("selection", {})["selection_window"] = (
        {
            "start_at": window.start_at.isoformat(),
            "end_at": window.end_at.isoformat(),
            "withdraw_end_at": (
                window.withdraw_end_at.isoformat() if window.withdraw_end_at else None
            ),
            "status": window.status,
        }
        if window is not None
        else None
    )
    return value


async def _get_cached_dashboard_for_student(
    session: AsyncSession,
    current_user: UserProfile,
    student_id: UUID,
    term_id: UUID,
) -> dict:
    return await get_or_build_dashboard(
        student_id,
        term_id,
        builder=lambda: _get_dashboard_uncached(session, current_user),
    )


@router.get("/me/dashboard-summary")
async def get_dashboard_summary(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """Lightweight home payload derived from the existing Dashboard cache."""

    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可查询")
    student_id = current_user.student_id
    if student_id is None:
        student_id = (await _get_student(session, current_user.login_name)).id
    term_id = await get_active_term_id(session)
    settings = get_settings()
    return await get_or_build(
        dashboard_summary_key(student_id, term_id),
        ttl=settings.student_dashboard_cache_ttl_seconds,
        builder=lambda: _build_dashboard_summary(
            session, current_user, student_id, term_id
        ),
    )


async def _build_dashboard_summary(
    session: AsyncSession,
    current_user: UserProfile,
    student_id: UUID,
    term_id: UUID,
) -> dict:
    value = await _get_cached_dashboard_for_student(
        session, current_user, student_id, term_id
    )
    return dashboard_summary(value)


@router.get("/me/timetable")
async def get_student_timetable(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """Return only the fields used by the current experiment timetable page."""

    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可查询")
    student_id = current_user.student_id
    if student_id is None:
        student_id = (await _get_student(session, current_user.login_name)).id
    term_id = await get_active_term_id(session)
    settings = get_settings()
    return await get_or_build(
        timetable_key(student_id, term_id),
        ttl=settings.student_dashboard_cache_ttl_seconds,
        builder=lambda: _build_student_timetable(
            session, current_user, student_id, term_id
        ),
    )


async def _build_student_timetable(
    session: AsyncSession,
    current_user: UserProfile,
    student_id: UUID,
    term_id: UUID,
) -> dict:
    value = await _get_cached_dashboard_for_student(
        session, current_user, student_id, term_id
    )
    return dashboard_timetable(value)


@router.get("/me/notifications")
async def get_my_notifications(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403)
    student = await _get_student(session, current_user.login_name)
    try:
        items = await get_redis_client().lrange(
            f"student:{student.id}:notifications", 0, -1
        )
    except RedisError:
        logger.warning(
            "Student notifications unavailable; returning an empty list student=%s",
            student.id,
        )
        return []

    notifications = []
    for item in items:
        try:
            notifications.append(json.loads(item))
        except (TypeError, ValueError):
            logger.warning("Ignored malformed student notification student=%s", student.id)
    return notifications


@router.post("/me/notifications/read")
async def read_my_notification(
    body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403)
    student = await _get_student(session, current_user.login_name)
    try:
        from app.services.notification_service import remove_notification_by_value

        await remove_notification_by_value(
            f"student:{student.id}:notifications", body.get("value", "")
        )
        return {"ok": True, "cache_sync": "ok"}
    except RedisError:
        logger.warning(
            "Student notification acknowledgement deferred student=%s",
            student.id,
        )
        return {"ok": True, "cache_sync": "deferred"}


class SelectSessionBody(BaseModel):
    session_id: UUID


@router.post("/me/ai-consult", response_model=ConsultationResponse)
async def consult_student_agent(
    body: ConsultationRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """Run the isolated, read-only student consultation graph."""

    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可使用智能咨询")
    student_id = current_user.student_id
    if student_id is None:
        student_id = (await _get_student(session, current_user.login_name)).id
    lease = StudentAILease(student_id)
    try:
        await lease.acquire()
    except StudentAIConcurrencyError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    try:
        term = await get_or_create_active_term(session)
        state = await invoke_registered_graph(
            business_type="STUDENT_CONSULTATION",
            actor_type=current_user.user_type,
            payload={
                "session": session,
                "student_id": student_id,
                "term": term,
                "messages": body.messages,
                "page_context": body.page_context,
            },
        )
    finally:
        await lease.release()
    if state.get("model_error"):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AI_PLANNER_UNAVAILABLE",
                "message": state["model_error"],
                "trace_id": state.get("trace_id"),
            },
        )
    return ConsultationResponse(
        intent=state.get("intent", "UNKNOWN"),
        answer=state.get("answer", "当前数据不足，暂时无法回答。"),
        cards=state.get("cards", []),
        warnings=state.get("warnings", []),
        unknowns=state.get("unknowns", []),
    )


def _sse_event(event: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/me/ai-consult/stream")
async def stream_consult_student_agent(
    body: ConsultationRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """Stream the isolated student consultation graph as SSE over POST."""

    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可使用智能咨询")
    student_id = current_user.student_id
    if student_id is None:
        student_id = (await _get_student(session, current_user.login_name)).id
    lease = StudentAILease(student_id)
    try:
        await lease.acquire()
    except StudentAIConcurrencyError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    try:
        term = await get_or_create_active_term(session)
    except Exception:
        await lease.release()
        raise

    async def event_generator():
        try:
            async for item in stream_registered_graph(
                business_type="STUDENT_CONSULTATION",
                actor_type=current_user.user_type,
                payload={
                    "session": session,
                    "student_id": student_id,
                    "term": term,
                    "messages": body.messages,
                    "page_context": body.page_context,
                },
            ):
                if await request.is_disconnected():
                    return
                yield _sse_event(str(item["event"]), item.get("data", {}))
        except Exception:  # noqa: BLE001 - stream boundary must emit SSE error
            logger.exception(
                "Student AI consultation stream failed",
                extra={
                    "student_id": str(student_id),
                    "login_name": current_user.login_name,
                },
            )
            yield _sse_event(
                "error",
                {
                    "code": "AI_STREAM_FAILED",
                    "message": "回答生成中断，请稍后重试。",
                },
            )
        finally:
            await lease.release()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/me/select-session")
async def select_session_endpoint(
    body: SelectSessionBody,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """选课：优先由 Redis 队列受理，不可用时退化为数据库同步事务。"""
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可选课")

    student_id = current_user.student_id
    if student_id is None:
        student_id = (await _get_student(session, current_user.login_name)).id
    term_id = await get_active_term_id(session)

    redis = get_redis_client()
    result = await selection_service.enqueue_select_session_with_fallback(
        redis,
        session,
        student_id=student_id,
        term_id=term_id,
        session_id=body.session_id,
    )
    return {
        "result": result.result,
        "message": result.message,
        "eligibility": (
            result.eligibility.model_dump(mode="json") if result.eligibility else None
        ),
        "details": result.details,
    }


@router.get("/me/selection-requests/{request_id}")
async def get_selection_request_endpoint(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """查询当前学生异步选课任务的最终结果。"""
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可查询选课结果")
    if len(request_id) != 32 or any(char not in "0123456789abcdef" for char in request_id):
        raise HTTPException(status_code=404, detail="选课请求不存在或已过期")

    student_id = current_user.student_id
    if student_id is None:
        student_id = (await _get_student(session, current_user.login_name)).id
    result = await selection_service.get_selection_request_status(
        get_redis_client(), student_id=student_id, request_id=request_id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="选课请求不存在或已过期")
    return {
        "result": result.result,
        "message": result.message,
        "eligibility": (
            result.eligibility.model_dump(mode="json") if result.eligibility else None
        ),
        "details": result.details,
    }


@router.post("/me/deselect-session")
async def deselect_session_endpoint(
    body: SelectSessionBody,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """退选：Redis Lua 退回库存。"""
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可退选")

    student_id = current_user.student_id
    if student_id is None:
        student_id = (await _get_student(session, current_user.login_name)).id
    term_id = await get_active_term_id(session)

    redis = get_redis_client()
    result = await selection_service.deselect_session(
        redis,
        session,
        student_id=student_id,
        term_id=term_id,
        session_id=body.session_id,
    )
    return {
        "result": result.result,
        "message": result.message,
        "details": result.details,
    }
