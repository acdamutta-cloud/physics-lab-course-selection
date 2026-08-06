import base64
import json
from datetime import date as dt_date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import StreamingResponse

from app.agents.graphs.student_graph import (
    run_student_consultation,
    stream_student_consultation,
)
from app.api.dependencies import get_current_user
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
from app.models.identity import Campus, Major, Student
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.schemas.auth import UserProfile
from app.schemas.student_consultation import (
    ConsultationRequest,
    ConsultationResponse,
)
from app.services import selection_service

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
    from datetime import date as dt_date
    today = dt_date.today()
    if today < term.start_date:
        return 0
    return (today - term.start_date).days // 7 + 1


@router.get("/me/busy-bitmap")
async def get_my_bitmap(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
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


@router.get("/me/dashboard")
async def get_dashboard(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
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
                proj_sessions = (
                    (
                        await session.execute(
                            select(ExperimentSession)
                            .options(
                                selectinload(ExperimentSession.laboratory),
                                selectinload(ExperimentSession.teacher),
                            )
                            .join(
                                ScheduleVersion,
                                ScheduleVersion.id
                                == ExperimentSession.schedule_version_id,
                            )
                            .where(
                                ScheduleVersion.status.in_(["PUBLISHED", "DRAFT"]),
                                ExperimentSession.project_id == proj.id,
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
            prereqs = (
                (
                    await session.execute(
                        select(CoursePrerequisite).where(
                            CoursePrerequisite.plan_course_id == pc.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            prereq_passed = []
            prereq_failed = []
            for p in prereqs:
                prereq_course = await session.get(
                    ExperimentCourse, p.prerequisite_course_id
                )
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
    selected_count = (
        await session.scalar(
            select(StudentProjectRecord).where(
                StudentProjectRecord.student_id == student.id,
                StudentProjectRecord.term_id == term.id,
                StudentProjectRecord.status == "SELECTED",
            )
        )
    ) or 0
    # count query
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
    for rec in selected_records_all:
        if rec.session is None:
            continue
        s = rec.session
        selected_sessions.append(
            {
                "session_id": str(s.id),
                "project_id": str(s.project_id),
                "week_no": s.week_no,
                "day_of_week": s.day_of_week,
                "start_slot": s.start_slot,
                "end_slot": s.end_slot,
                "project_name": s.project.project_name if s.project else "",
                "lab_name": s.laboratory.name if s.laboratory else "",
                "teacher_name": s.teacher.name if s.teacher else "",
            }
        )

    # ── 4. 下一项实验（从已选场次中取最早的一个）──
    next_lab = None
    cw = min(_calc_week(term), term.total_weeks)
    selected_session_ids = [r.session_id for r in selected_records_all if r.session_id]
    next_session = None
    if selected_session_ids:
        next_session = (
            (
                await session.execute(
                    select(ExperimentSession)
                    .options(
                        selectinload(ExperimentSession.project),
                        selectinload(ExperimentSession.laboratory),
                        selectinload(ExperimentSession.teacher),
                    )
                    .where(
                        ExperimentSession.id.in_(selected_session_ids),
                        ExperimentSession.week_no >= cw,
                    )
                    .order_by(
                        ExperimentSession.week_no,
                        ExperimentSession.day_of_week,
                        ExperimentSession.start_slot,
                    )
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

    if next_session:
        s = next_session
        next_lab = {
            "week_no": s.week_no,
            "day_of_week": s.day_of_week,
            "start_slot": s.start_slot,
            "end_slot": s.end_slot,
            "project_name": s.project.project_name if s.project else "",
            "lab_name": s.laboratory.name if s.laboratory else "",
            "teacher_name": s.teacher.name if s.teacher else "",
        }

    # ── 5. 忙闲位图 ──
    bm = await student_crud.get_student_bitmap(session, student.id, term.id)
    bitmap_b64 = base64.b64encode(bm.bitmap).decode() if bm else None

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
        },
        "next_lab": next_lab,
        "selected_sessions": selected_sessions,
        "bitmap_data": bitmap_b64,
        "bitmap_weeks": 18,
        "bitmap_days": 7,
        "bitmap_slots": 12,
    }


@router.get("/me/notifications")
async def get_my_notifications(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403)
    student = await _get_student(session, current_user.login_name)
    from app.db.redis_client import get_redis_client
    items = await get_redis_client().lrange(f"student:{student.id}:notifications", 0, -1)
    from json import loads
    return [loads(i) for i in items]


@router.post("/me/notifications/read")
async def read_my_notification(
    body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403)
    student = await _get_student(session, current_user.login_name)
    from app.db.redis_client import get_redis_client
    await get_redis_client().lrem(f"student:{student.id}:notifications", 1, body.get("value", ""))
    return {"ok": True}


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
    student = await _get_student(session, current_user.login_name)
    term = await get_or_create_active_term(session)
    state = await run_student_consultation(
        {
            "session": session,
            "student_id": student.id,
            "term": term,
            "messages": body.messages,
            "page_context": body.page_context,
        }
    )
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
    student = await _get_student(session, current_user.login_name)
    term = await get_or_create_active_term(session)

    async def event_generator():
        try:
            async for item in stream_student_consultation(
                {
                    "session": session,
                    "student_id": student.id,
                    "term": term,
                    "messages": body.messages,
                    "page_context": body.page_context,
                }
            ):
                if await request.is_disconnected():
                    return
                yield _sse_event(str(item["event"]), item.get("data", {}))
        except Exception:  # noqa: BLE001 - stream boundary must emit SSE error
            yield _sse_event(
                "error",
                {
                    "code": "AI_STREAM_FAILED",
                    "message": "回答生成中断，请稍后重试。",
                },
            )

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
    """选课：Redis Lua 预扣库存。"""
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可选课")

    student = await _get_student(session, current_user.login_name)
    term = await get_or_create_active_term(session)

    redis = get_redis_client()
    result = await selection_service.select_session(
        redis,
        session,
        student_id=student.id,
        term_id=term.id,
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


@router.post("/me/deselect-session")
async def deselect_session_endpoint(
    body: SelectSessionBody,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """退选：Redis Lua 退回库存。"""
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可退选")

    student = await _get_student(session, current_user.login_name)
    term = await get_or_create_active_term(session)

    redis = get_redis_client()
    result = await selection_service.deselect_session(
        redis,
        session,
        student_id=student.id,
        term_id=term.id,
        session_id=body.session_id,
    )
    return {
        "result": result.result,
        "message": result.message,
        "details": result.details,
    }
