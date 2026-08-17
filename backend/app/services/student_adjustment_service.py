from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.application import (
    AdjustmentExecutionAudit,
    ApplicationRequest,
    ApprovalRecord,
)
from app.models.curriculum import (
    AcademicTerm,
    ExperimentCourse,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingPlanProject,
)
from app.models.enrollment import StudentProjectRecord
from app.models.identity import Campus, Student, Teacher
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.schemas.student_adjustment import (
    AdjustmentApplicationOut,
    AdjustmentCreateRequest,
    AdjustmentRecommendationOption,
    AdjustmentRequestType,
    AdjustmentSessionSummary,
    AdjustmentSourceRecord,
    AdjustmentValidationResult,
    AdjustmentViolation,
)
from app.schemas.student_consultation import RecommendationSession, SelectionPreferences
from app.services.student_consultation_service import (
    _preference_explanations,
    _preference_score,
    check_selection_eligibility,
    weekday_name,
)
from app.services.student_cache_service import refresh_experiment_views_after_commit

SHANGHAI = ZoneInfo("Asia/Shanghai")
ACTIVE_APPLICATION_STATUSES = {
    "SUBMITTED",
    "VALIDATING",
    "PENDING_REVIEW",
    "APPROVED",
}


def session_calendar_date(term: AcademicTerm, session: ExperimentSession) -> date:
    """Resolve a Sunday-first teaching-week slot to its calendar date."""

    days_since_sunday = (term.start_date.weekday() + 1) % 7
    first_sunday = term.start_date - timedelta(days=days_since_sunday)
    return first_sunday + timedelta(
        days=(session.week_no - 1) * 7 + (session.day_of_week - 1)
    )


def session_has_started(
    term: AcademicTerm,
    session: ExperimentSession,
    *,
    today: date | None = None,
) -> bool:
    return session_calendar_date(term, session) <= (
        today or datetime.now(SHANGHAI).date()
    )


async def _load_session(session: AsyncSession, session_id: UUID) -> ExperimentSession | None:
    return (
        await session.execute(
            select(ExperimentSession)
            .options(
                selectinload(ExperimentSession.project),
                selectinload(ExperimentSession.teacher),
                selectinload(ExperimentSession.laboratory),
            )
            .where(ExperimentSession.id == session_id)
        )
    ).scalar_one_or_none()


async def _load_source(
    session: AsyncSession,
    *,
    student_id: UUID,
    term_id: UUID,
    record_id: UUID,
) -> StudentProjectRecord | None:
    return (
        await session.execute(
            select(StudentProjectRecord)
            .options(
                selectinload(StudentProjectRecord.session).selectinload(
                    ExperimentSession.project
                ),
                selectinload(StudentProjectRecord.session).selectinload(
                    ExperimentSession.teacher
                ),
                selectinload(StudentProjectRecord.session).selectinload(
                    ExperimentSession.laboratory
                ),
            )
            .where(
                StudentProjectRecord.id == record_id,
                StudentProjectRecord.student_id == student_id,
                StudentProjectRecord.term_id == term_id,
            )
        )
    ).scalar_one_or_none()


async def _course_name(session: AsyncSession, course_id: UUID) -> str:
    course = await session.get(ExperimentCourse, course_id)
    return course.course_name if course else ""


async def _requirement_type(
    session: AsyncSession, *, student: Student, project_id: UUID
) -> str | None:
    return await session.scalar(
        select(TrainingPlanProject.requirement_type)
        .join(
            TrainingPlanCourse,
            TrainingPlanCourse.id == TrainingPlanProject.plan_course_id,
        )
        .join(TrainingPlan, TrainingPlan.id == TrainingPlanCourse.plan_id)
        .where(
            TrainingPlan.major_id == student.major_id,
            TrainingPlan.enrollment_year == student.enrollment_year,
            TrainingPlan.status == "PUBLISHED",
            TrainingPlanProject.project_id == project_id,
        )
        .limit(1)
    )


async def _active_reservation_count(
    session: AsyncSession,
    *,
    target_session_id: UUID,
    exclude_application_id: UUID | None = None,
) -> int:
    conditions = [
        ApplicationRequest.target_session_id == target_session_id,
        ApplicationRequest.status.in_(ACTIVE_APPLICATION_STATUSES),
        ApplicationRequest.reservation_status == "HELD",
        or_(
            ApplicationRequest.reservation_expires_at.is_(None),
            ApplicationRequest.reservation_expires_at > datetime.now(UTC),
        ),
    ]
    if exclude_application_id is not None:
        conditions.append(ApplicationRequest.id != exclude_application_id)
    return int(
        await session.scalar(select(func.count()).where(*conditions)) or 0
    )


async def _session_summary(
    session: AsyncSession,
    *,
    term: AcademicTerm,
    item: ExperimentSession,
    requirement_type: str,
    exclude_application_id: UUID | None = None,
) -> AdjustmentSessionSummary:
    project = item.project
    assert project is not None
    reservations = await _active_reservation_count(
        session,
        target_session_id=item.id,
        exclude_application_id=exclude_application_id,
    )
    item_date = session_calendar_date(term, item)
    return AdjustmentSessionSummary(
        session_id=item.id,
        project_id=item.project_id,
        project_name=project.project_name,
        course_id=project.course_id,
        course_name=await _course_name(session, project.course_id),
        requirement_type=requirement_type,
        week_no=item.week_no,
        day_of_week=item.day_of_week,
        day_name=weekday_name(item.day_of_week),
        start_slot=item.start_slot,
        end_slot=item.end_slot,
        session_date=item_date,
        started=session_has_started(term, item),
        teacher_name=item.teacher.name if item.teacher else "",
        laboratory_name=item.laboratory.name if item.laboratory else "",
        remaining=max(0, item.capacity - item.selected_count - reservations),
    )


async def _source_summary(
    session: AsyncSession,
    *,
    term: AcademicTerm,
    record: StudentProjectRecord,
) -> AdjustmentSourceRecord:
    assert record.session is not None
    summary = await _session_summary(
        session,
        term=term,
        item=record.session,
        requirement_type=record.requirement_type,
    )
    available: list[AdjustmentRequestType] = []
    if record.status == "SELECTED" and not summary.started:
        available.append("RESCHEDULE")
        if record.requirement_type == "OPTIONAL":
            available.append("PROJECT_CHANGE")
    if record.status != "COMPLETED" and summary.started:
        available.append("MAKEUP")
    return AdjustmentSourceRecord(
        record_id=record.id,
        status=record.status,
        session=summary,
        available_for=available,
    )


def _approval_route(request_type: AdjustmentRequestType) -> str:
    return {"RESCHEDULE": "AUTO", "PROJECT_CHANGE": "ADMIN", "MAKEUP": "TEACHER_THEN_ADMIN"}[
        request_type
    ]


async def validate_student_adjustment(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    request_type: AdjustmentRequestType,
    source_record_id: UUID,
    target_session_id: UUID,
    exclude_application_id: UUID | None = None,
    lock_rows: bool = False,
) -> AdjustmentValidationResult:
    student = await session.get(Student, student_id)
    source = await _load_source(
        session,
        student_id=student_id,
        term_id=term.id,
        record_id=source_record_id,
    )
    target = await _load_session(session, target_session_id)
    route = _approval_route(request_type)
    violations: list[AdjustmentViolation] = []
    warnings: list[AdjustmentViolation] = []

    def block(code: str, message: str, **details: object) -> None:
        violations.append(
            AdjustmentViolation(code=code, message=message, details=details)
        )

    if student is None or source is None or source.session is None:
        block("ADJUSTMENT_SOURCE_NOT_FOUND", "未找到属于你的原实验记录。")
        return AdjustmentValidationResult(
            decision="BLOCK",
            request_type=request_type,
            approval_route=route,  # type: ignore[arg-type]
            violations=violations,
        )
    source_summary = await _source_summary(session, term=term, record=source)
    if target is None or target.project is None:
        block("ADJUSTMENT_TARGET_NOT_FOUND", "目标实验场次不存在。")
        return AdjustmentValidationResult(
            decision="BLOCK",
            request_type=request_type,
            approval_route=route,  # type: ignore[arg-type]
            source=source_summary,
            violations=violations,
        )

    if lock_rows:
        await session.execute(
            select(ExperimentSession)
            .where(ExperimentSession.id.in_([source.session_id, target.id]))
            .with_for_update()
        )

    target_requirement = await _requirement_type(
        session, student=student, project_id=target.project_id
    )
    target_summary = await _session_summary(
        session,
        term=term,
        item=target,
        requirement_type=target_requirement or "",
        exclude_application_id=exclude_application_id,
    )
    source_started = source_summary.session.started
    target_started = target_summary.started

    if source.session_id == target.id:
        block("SAME_SESSION", "目标场次不能与原场次相同。")
    if target_started:
        block("TARGET_SESSION_STARTED", "目标场次已经开课，不能作为调整目标。")
    if request_type == "RESCHEDULE":
        if source_started:
            block("SOURCE_SESSION_STARTED", "原场次已经开课，不能申请换时间。")
        if source.status != "SELECTED":
            block("SOURCE_STATUS_INVALID", "只有当前已选的实验可以申请换时间。")
        if source.project_id != target.project_id:
            block("PROJECT_MISMATCH", "换时间只能选择同一实验项目的其他场次。")
    elif request_type == "PROJECT_CHANGE":
        if source_started:
            block("SOURCE_SESSION_STARTED", "原场次已经开课，不能申请换项目。")
        if source.status != "SELECTED":
            block("SOURCE_STATUS_INVALID", "只有当前已选的实验可以申请换项目。")
        if source.requirement_type != "OPTIONAL" or target_requirement != "OPTIONAL":
            block("OPTIONAL_PROJECTS_ONLY", "换项目只允许同一课程的选做项目互换。")
        if source.session.project is None or (
            source.session.project.course_id != target.project.course_id
        ):
            block("COURSE_MISMATCH", "换项目只能在同一实验课程内进行。")
        duplicate = await session.scalar(
            select(func.count()).where(
                StudentProjectRecord.student_id == student_id,
                StudentProjectRecord.term_id == term.id,
                StudentProjectRecord.project_id == target.project_id,
                StudentProjectRecord.status.in_(
                    {"SELECTED", "COMPLETED", "ABSENT", "MAKEUP_PENDING"}
                ),
                StudentProjectRecord.id != source.id,
            )
        )
        if duplicate:
            block("TARGET_PROJECT_ALREADY_ACTIVE", "目标项目已经选择或完成。")
    else:
        if not source_started:
            block("SOURCE_SESSION_NOT_STARTED", "原场次尚未开课，不能申请补做。")
        if source.status == "COMPLETED":
            block("PROJECT_ALREADY_COMPLETED", "该实验已经完成，不能申请补做。")
        if source.project_id != target.project_id:
            block("PROJECT_MISMATCH", "补做只能选择同一实验项目的其他场次。")

    schedule = await session.get(ScheduleVersion, target.schedule_version_id)
    if schedule is None or schedule.term_id != term.id or schedule.status != "PUBLISHED":
        block("TARGET_NOT_PUBLISHED", "目标场次不属于当前学期已发布课表。")

    base = await check_selection_eligibility(
        session,
        student_id=student_id,
        session_id=target.id,
        lock_target=lock_rows,
    )
    ignored_codes = {"PROJECT_OCCUPIED_BY_APPLICATION"}
    if source.project_id == target.project_id:
        ignored_codes.add("PROJECT_ALREADY_SELECTED")
    for item in base.violations:
        if item.code in ignored_codes:
            continue
        if item.code == "EXPERIMENT_SESSION_CONFLICT" and item.details.get(
            "conflicting_session_id"
        ) == str(source.session_id):
            continue
        block(item.code, item.message, **item.details)
    warnings.extend(
        AdjustmentViolation(
            code=item.code,
            message=item.message,
            details=dict(item.details),
        )
        for item in base.warnings
    )

    reservations = await _active_reservation_count(
        session,
        target_session_id=target.id,
        exclude_application_id=exclude_application_id,
    )
    if target.selected_count + reservations >= target.capacity:
        block("TARGET_RESERVED_FULL", "目标场次名额已被选课或处理中申请占满。")
    duplicate_application = await session.scalar(
        select(func.count()).where(
            ApplicationRequest.student_id == student_id,
            ApplicationRequest.status.in_(ACTIVE_APPLICATION_STATUSES),
            ApplicationRequest.id != exclude_application_id
            if exclude_application_id is not None
            else ApplicationRequest.id.is_not(None),
            or_(
                ApplicationRequest.original_session_id == source.session_id,
                ApplicationRequest.target_session_id == target.id,
            ),
        )
    )
    if duplicate_application:
        block("DUPLICATE_ACTIVE_APPLICATION", "原实验或目标场次已有处理中申请。")

    decision = "BLOCK" if violations else ("ALLOW" if route == "AUTO" else "REVIEW")
    return AdjustmentValidationResult(
        decision=decision,  # type: ignore[arg-type]
        request_type=request_type,
        approval_route=route,  # type: ignore[arg-type]
        source=source_summary,
        target=target_summary,
        violations=list({item.code: item for item in violations}.values()),
        warnings=list({item.code: item for item in warnings}.values()),
    )


async def _candidate_sessions(
    session: AsyncSession,
    *,
    student: Student,
    term: AcademicTerm,
    source: StudentProjectRecord,
    request_type: AdjustmentRequestType,
) -> list[ExperimentSession]:
    assert source.session is not None and source.session.project is not None
    query = (
        select(ExperimentSession)
        .options(
            selectinload(ExperimentSession.project),
            selectinload(ExperimentSession.teacher),
            selectinload(ExperimentSession.laboratory),
        )
        .join(ScheduleVersion, ScheduleVersion.id == ExperimentSession.schedule_version_id)
        .where(
            ScheduleVersion.term_id == term.id,
            ScheduleVersion.status == "PUBLISHED",
            ExperimentSession.id != source.session_id,
            ExperimentSession.status.in_({"DRAFT", "OPEN", "FULL"}),
        )
    )
    if request_type in {"RESCHEDULE", "MAKEUP"}:
        query = query.where(ExperimentSession.project_id == source.project_id)
    else:
        optional_project_ids = select(TrainingPlanProject.project_id).join(
            TrainingPlanCourse,
            TrainingPlanCourse.id == TrainingPlanProject.plan_course_id,
        ).join(TrainingPlan, TrainingPlan.id == TrainingPlanCourse.plan_id).where(
            TrainingPlan.major_id == student.major_id,
            TrainingPlan.enrollment_year == student.enrollment_year,
            TrainingPlan.status == "PUBLISHED",
            TrainingPlanCourse.course_id == source.course_id,
            TrainingPlanProject.requirement_type == "OPTIONAL",
            TrainingPlanProject.project_id != source.project_id,
        )
        query = query.where(ExperimentSession.project_id.in_(optional_project_ids))
    return list(
        (
            await session.execute(
                query.order_by(
                    ExperimentSession.week_no,
                    ExperimentSession.day_of_week,
                    ExperimentSession.start_slot,
                )
            )
        )
        .scalars()
        .all()
    )


async def recommend_adjustment_options(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    request_type: AdjustmentRequestType,
    source_record_id: UUID,
    preferences: SelectionPreferences,
    max_options: int = 3,
) -> list[AdjustmentRecommendationOption]:
    student = await session.get(Student, student_id)
    source = await _load_source(
        session,
        student_id=student_id,
        term_id=term.id,
        record_id=source_record_id,
    )
    if student is None or source is None or source.session is None:
        return []
    campus = await session.get(Campus, student.campus_id)
    candidates = await _candidate_sessions(
        session,
        student=student,
        term=term,
        source=source,
        request_type=request_type,
    )
    scored: list[tuple[int, AdjustmentRecommendationOption]] = []
    for candidate in candidates:
        if session_has_started(term, candidate):
            continue
        result = await validate_student_adjustment(
            session,
            student_id=student_id,
            term=term,
            request_type=request_type,
            source_record_id=source_record_id,
            target_session_id=candidate.id,
        )
        if not result.allowed or result.target is None or candidate.project is None:
            continue
        recommendation = RecommendationSession(
            session_id=candidate.id,
            project_id=candidate.project_id,
            project_name=candidate.project.project_name,
            course_name=result.target.course_name,
            requirement_type=result.target.requirement_type,  # type: ignore[arg-type]
            category=candidate.project.category,
            week_no=candidate.week_no,
            day_of_week=candidate.day_of_week,
            start_slot=candidate.start_slot,
            end_slot=candidate.end_slot,
            laboratory_name=result.target.laboratory_name,
            campus_name=(
                (await session.get(Campus, candidate.laboratory.campus_id)).name
                if candidate.laboratory
                and await session.get(Campus, candidate.laboratory.campus_id)
                else ""
            ),
            remaining=result.target.remaining,
        )
        score, reasons, item_warnings = _preference_score(
            recommendation,
            preferences,
            student_campus=campus.name if campus else "",
        )
        preference_reasons, preference_warnings = _preference_explanations(
            [recommendation], preferences
        )
        scored.append(
            (
                score,
                AdjustmentRecommendationOption(
                    target=result.target,
                    score=score,
                    reasons=list(dict.fromkeys([*reasons, *preference_reasons])),
                    warnings=list(
                        dict.fromkeys([*item_warnings, *preference_warnings])
                    ),
                    approval_route=result.approval_route,
                ),
            )
        )
    scored.sort(
        key=lambda pair: (
            -pair[0],
            pair[1].target.week_no,
            pair[1].target.day_of_week,
            pair[1].target.start_slot,
        )
    )
    return [item for _, item in scored[:max_options]]


async def get_adjustment_context(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    request_type: AdjustmentRequestType | None = None,
    source_record_id: UUID | None = None,
) -> dict[str, object]:
    records = list(
        (
            await session.execute(
                select(StudentProjectRecord)
                .options(
                    selectinload(StudentProjectRecord.session).selectinload(
                        ExperimentSession.project
                    ),
                    selectinload(StudentProjectRecord.session).selectinload(
                        ExperimentSession.teacher
                    ),
                    selectinload(StudentProjectRecord.session).selectinload(
                        ExperimentSession.laboratory
                    ),
                )
                .where(
                    StudentProjectRecord.student_id == student_id,
                    StudentProjectRecord.term_id == term.id,
                    StudentProjectRecord.status.in_(
                        {"SELECTED", "ABSENT", "MAKEUP_PENDING", "COMPLETED"}
                    ),
                    StudentProjectRecord.session_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    sources = [await _source_summary(session, term=term, record=item) for item in records]
    if request_type:
        sources = [item for item in sources if request_type in item.available_for]
    candidates: list[dict[str, object]] = []
    if request_type and source_record_id:
        source = next((item for item in records if item.id == source_record_id), None)
        student = await session.get(Student, student_id)
        if source and student:
            for candidate in await _candidate_sessions(
                session,
                student=student,
                term=term,
                source=source,
                request_type=request_type,
            ):
                result = await validate_student_adjustment(
                    session,
                    student_id=student_id,
                    term=term,
                    request_type=request_type,
                    source_record_id=source_record_id,
                    target_session_id=candidate.id,
                )
                candidates.append(result.model_dump(mode="json"))
    return {
        "request_type": request_type,
        "sources": [item.model_dump(mode="json") for item in sources],
        "candidates": candidates,
    }


def application_out(item: ApplicationRequest) -> AdjustmentApplicationOut:
    return AdjustmentApplicationOut.model_validate(
        {
            column: getattr(item, column)
            for column in AdjustmentApplicationOut.model_fields
        }
    )


def _snapshot(record: StudentProjectRecord) -> dict[str, object]:
    return {
        "record_id": str(record.id),
        "course_id": str(record.course_id),
        "project_id": str(record.project_id),
        "session_id": str(record.session_id) if record.session_id else None,
        "requirement_type": record.requirement_type,
        "status": record.status,
        "version_no": record.version_no,
    }


async def _execute_application(
    session: AsyncSession,
    *,
    application: ApplicationRequest,
    actor_id: UUID,
) -> None:
    assert application.student_id and application.original_session_id
    assert application.target_session_id
    record = (
        await session.execute(
            select(StudentProjectRecord)
            .where(
                StudentProjectRecord.student_id == application.student_id,
                StudentProjectRecord.session_id == application.original_session_id,
                StudentProjectRecord.status == "SELECTED",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if record is None:
        raise LookupError("未找到该场次的选课记录")
    sessions = list(
        (
            await session.execute(
                select(ExperimentSession)
                .where(
                    ExperimentSession.id.in_(
                        [application.original_session_id, application.target_session_id]
                    )
                )
                .with_for_update()
            )
        ).scalars()
    )
    by_id = {item.id: item for item in sessions}
    original = by_id[application.original_session_id]
    target = by_id[application.target_session_id]
    before = _snapshot(record)
    if application.request_type != "MAKEUP":
        original.selected_count = max(0, original.selected_count - 1)
    target.selected_count += 1
    record.session_id = target.id
    if application.request_type == "PROJECT_CHANGE":
        assert application.target_project_id is not None
        record.project_id = application.target_project_id
        # Project changes are validated as same-course swaps, so course_id is stable.
        record.requirement_type = "OPTIONAL"
    if application.request_type == "MAKEUP":
        record.status = "SELECTED"
    record.version_no += 1
    record.updated_by = actor_id
    application.status = "EXECUTED"
    application.executed_at = datetime.now(UTC)
    application.reservation_status = "CONSUMED"
    application.updated_by = actor_id
    session.add(
        AdjustmentExecutionAudit(
            application_id=application.id,
            session_id=original.id,
            change_type=application.request_type,
            before_snapshot=before,
            after_snapshot=_snapshot(record),
            execution_status="EXECUTED",
            executed_by=actor_id,
            executed_at=application.executed_at,
            idempotency_key=application.idempotency_key or str(application.id),
        )
    )


async def create_adjustment_application(
    session: AsyncSession,
    *,
    student_id: UUID,
    actor_id: UUID,
    term: AcademicTerm,
    body: AdjustmentCreateRequest,
) -> AdjustmentApplicationOut:
    # Serialize adjustment submissions for the same student and term. This also
    # makes an idempotent retry wait for the first transaction to finish.
    locked_student_id = await session.scalar(
        select(Student.id).where(Student.id == student_id).with_for_update()
    )
    if locked_student_id is None:
        raise LookupError("学生信息不存在。")
    existing = await session.scalar(
        select(ApplicationRequest).where(
            ApplicationRequest.student_id == student_id,
            ApplicationRequest.idempotency_key == body.idempotency_key,
        )
    )
    if existing:
        return application_out(existing)
    result = await validate_student_adjustment(
        session,
        student_id=student_id,
        term=term,
        request_type=body.request_type,
        source_record_id=body.source_record_id,
        target_session_id=body.target_session_id,
        lock_rows=True,
    )
    if not result.allowed or result.source is None or result.target is None:
        raise ValueError("；".join(item.message for item in result.violations))
    now = datetime.now(UTC)
    target_midnight = datetime.combine(
        result.target.session_date, time.min, tzinfo=SHANGHAI
    ).astimezone(UTC)
    application = ApplicationRequest(
        request_no=f"SA-{now:%Y%m%d}-{uuid4().hex[:8].upper()}",
        request_type=body.request_type,
        applicant_user_id=actor_id,
        student_id=student_id,
        project_id=result.source.session.project_id,
        target_project_id=result.target.project_id,
        original_session_id=result.source.session.session_id,
        target_session_id=result.target.session_id,
        reason=body.reason,
        payload={
            "source_record_id": str(body.source_record_id),
            "source": result.source.model_dump(mode="json"),
            "target": result.target.model_dump(mode="json"),
        },
        validation_result=result.model_dump(mode="json"),
        approval_route=result.approval_route,
        reservation_status=("NONE" if result.approval_route == "AUTO" else "HELD"),
        reservation_expires_at=(
            None if result.approval_route == "AUTO" else target_midnight
        ),
        idempotency_key=body.idempotency_key,
        status="VALIDATING",
        submitted_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(application)
    await session.flush()
    if result.approval_route == "AUTO":
        application.status = "APPROVED"
        session.add(
            ApprovalRecord(
                application_id=application.id,
                approval_type="AUTO",
                approver_user_id=None,
                decision="APPROVED",
                matched_rules=result.model_dump(mode="json"),
                comment="确定性规则校验通过，系统自动审批。",
                decided_at=now,
            )
        )
        await _execute_application(session, application=application, actor_id=actor_id)
    else:
        application.status = "PENDING_REVIEW"
    await session.commit()
    await session.refresh(application)
    await refresh_experiment_views_after_commit(
        student_id,
        term.id,
        dashboard=application.status == "EXECUTED",
    )

    # 推送管理员通知
    try:
        from app.db.redis_client import get_redis_client
        import json as _json
        name = ""
        if student_id:
            st = await session.get(Student, student_id)
            if st:
                name = st.name
        labels = {"RESCHEDULE": "调课申请", "PROJECT_CHANGE": "换组申请", "MAKEUP": "补做申请"}
        redis = get_redis_client()
        await redis.lpush("admin:notifications", _json.dumps({
            "request_no": application.request_no,
            "student_name": name,
            "type": labels.get(body.request_type, body.request_type),
            "status": application.status,
            "time": now.strftime("%m-%d %H:%M"),
        }, ensure_ascii=False))
    except Exception:
        pass

    # 推送待审批教师通知（补做申请需原场次教师审批）
    if application.approval_route in ("TEACHER", "TEACHER_THEN_ADMIN"):
        try:
            from app.db.redis_client import get_redis_client
            import json as _json

            original = await session.get(
                ExperimentSession, application.original_session_id
            )
            if original is not None and original.teacher_id is not None:
                teacher = await session.get(Teacher, original.teacher_id)
                if teacher is not None and teacher.user_id is not None:
                    student = await session.get(Student, student_id)
                    student_name = student.name if student else ""
                    msg = f"学生{student_name}提交了补做申请，请审批"
                    await get_redis_client().lpush(
                        f"teacher:{teacher.user_id}:notifications",
                        _json.dumps({
                            "request_no": application.request_no,
                            "title": "补做申请待审批",
                            "msg": msg,
                            "type": "补做",
                            "time": now.strftime("%m-%d %H:%M"),
                        }, ensure_ascii=False),
                    )
        except Exception:
            pass

    return application_out(application)


async def list_adjustment_applications(
    session: AsyncSession, *, student_id: UUID
) -> list[AdjustmentApplicationOut]:
    items = list(
        (
            await session.execute(
                select(ApplicationRequest)
                .where(ApplicationRequest.student_id == student_id)
                .order_by(ApplicationRequest.created_at.desc())
            )
        ).scalars()
    )
    return [application_out(item) for item in items]


async def cancel_adjustment_application(
    session: AsyncSession,
    *,
    student_id: UUID,
    application_id: UUID,
    actor_id: UUID,
) -> AdjustmentApplicationOut:
    item = await session.scalar(
        select(ApplicationRequest)
        .where(
            ApplicationRequest.id == application_id,
            ApplicationRequest.student_id == student_id,
        )
        .with_for_update()
    )
    if item is None:
        raise LookupError("申请不存在。")
    if item.status not in {"SUBMITTED", "VALIDATING", "PENDING_REVIEW"}:
        raise ValueError("当前状态不能取消申请。")
    item.status = "CANCELLED"
    item.reservation_status = "RELEASED"
    item.updated_by = actor_id
    await session.commit()
    await session.refresh(item)
    target = await session.get(ExperimentSession, item.target_session_id)
    if target is not None:
        schedule = await session.get(ScheduleVersion, target.schedule_version_id)
        if schedule is not None:
            await refresh_experiment_views_after_commit(
                student_id, schedule.term_id, dashboard=False
            )
    return application_out(item)


async def review_adjustment_application(
    session: AsyncSession,
    *,
    application_id: UUID,
    actor_id: UUID,
    actor_type: str,
    actor_login_name: str,
    decision: str,
    comment: str | None,
    term: AcademicTerm,
) -> AdjustmentApplicationOut:
    application = await session.scalar(
        select(ApplicationRequest)
        .where(ApplicationRequest.id == application_id)
        .with_for_update()
    )
    if application is None:
        raise LookupError("申请不存在。")
    if application.status != "PENDING_REVIEW":
        raise ValueError("该申请当前不在待审核状态。")
    if application.approval_route == "ADMIN" and actor_type != "ADMIN":
        raise PermissionError("该申请需要管理员审批。")
    if application.approval_route in ("TEACHER", "TEACHER_THEN_ADMIN"):
        if actor_type == "TEACHER":
            teacher = await session.scalar(
                select(Teacher).where(Teacher.employee_no == actor_login_name.upper())
            )
            original = await session.get(ExperimentSession, application.original_session_id)
            if teacher is None or original is None or original.teacher_id != teacher.id:
                raise PermissionError("只有原场次任课教师可以审批该补做申请。")
        elif application.approval_route == "TEACHER_THEN_ADMIN" and actor_type == "ADMIN":
            raise PermissionError("需要任课教师先审批，通过后管理员方可二审。")
        else:
            raise PermissionError("该申请当前不允许此角色审批。")
    now = datetime.now(UTC)
    session.add(
        ApprovalRecord(
            application_id=application.id,
            approval_type="MANUAL",
            approver_user_id=actor_id,
            decision=decision,
            matched_rules=application.validation_result,
            comment=comment,
            decided_at=now,
        )
    )
    if decision == "REJECTED":
        application.status = "REJECTED"
        application.reservation_status = "RELEASED"
    elif application.approval_route == "TEACHER_THEN_ADMIN" and actor_type == "TEACHER":
        # 教师通过 → 转管理员二审
        application.approval_route = "ADMIN"
        application.status = "PENDING_REVIEW"
    else:
        assert application.student_id and application.target_session_id
        source_record_id = UUID(str(application.payload["source_record_id"]))
        result = await validate_student_adjustment(
            session,
            student_id=application.student_id,
            term=term,
            request_type=application.request_type,  # type: ignore[arg-type]
            source_record_id=source_record_id,
            target_session_id=application.target_session_id,
            exclude_application_id=application.id,
            lock_rows=True,
        )
        if not result.allowed:
            raise ValueError("审批时复核未通过：" + "；".join(
                item.message for item in result.violations
            ))
        application.status = "APPROVED"
        await _execute_application(session, application=application, actor_id=actor_id)
    application.updated_by = actor_id
    await session.commit()
    await session.refresh(application)
    if application.student_id is not None:
        await refresh_experiment_views_after_commit(
            application.student_id,
            term.id,
            dashboard=application.status == "EXECUTED",
        )

    # 推送学生通知（双重审批时教师初审通过不单独发送，等终审结果统一通知）
    if not (decision == 'APPROVED' and actor_type == "TEACHER"):
        try:
            from app.db.redis_client import get_redis_client
            labels = {"RESCHEDULE": "调课", "PROJECT_CHANGE": "换组", "MAKEUP": "补做"}
            msg = f"{labels.get(application.request_type, application.request_type)}{'通过' if decision == 'APPROVED' else '被驳回'}"
            if comment and decision == 'REJECTED':
                msg += f"（{comment[:30]}）"
            import json as _json
            redis = get_redis_client()
            await redis.lpush(f"student:{application.student_id}:notifications", _json.dumps({
                "request_no": application.request_no,
                "msg": msg,
                "decision": decision,
                "time": datetime.now(UTC).strftime("%m-%d %H:%M"),
            }, ensure_ascii=False))
        except Exception:
            pass

    return application_out(application)
