from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, product
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.application import ApplicationRequest
from app.models.curriculum import (
    AcademicTerm,
    CoursePrerequisite,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingPlanProject,
)
from app.models.enrollment import StudentCourseCompletion, StudentProjectRecord
from app.models.identity import Campus, Student, StudentBusyBitmap
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.schemas.student_consultation import (
    CourseCoverage,
    ExcludedCourse,
    RecommendationPlan,
    RecommendationScope,
    RecommendationSession,
    SelectionEligibilityResult,
    SelectionPreferences,
    SelectionViolation,
    TimePeriod,
    UnmetRequirement,
    WeekRangePreference,
    weekday_full_name,
    weekday_name,
    weekday_number,
)

ACTIVE_PROJECT_STATUSES = {"SELECTED", "COMPLETED", "ABSENT", "MAKEUP_PENDING"}
TIME_OCCUPYING_STATUSES = {"SELECTED", "MAKEUP_PENDING"}
ACTIVE_APPLICATION_STATUSES = {
    "SUBMITTED",
    "VALIDATING",
    "PENDING_REVIEW",
    "APPROVED",
}


@dataclass(frozen=True)
class _EligibilityContext:
    student: Student
    term: AcademicTerm
    schedule_status: str
    target: ExperimentSession
    plan_course: TrainingPlanCourse | None
    bitmap: StudentBusyBitmap | None
    records: list[StudentProjectRecord]
    application_sessions: list[ExperimentSession]
    application_project_ids: set[UUID]


def session_start_ordinal(session: ExperimentSession) -> int:
    return (
        (session.week_no - 1) * 7 + (session.day_of_week - 1)
    ) * 12 + session.start_slot


def session_end_ordinal(session: ExperimentSession) -> int:
    return (
        (session.week_no - 1) * 7 + (session.day_of_week - 1)
    ) * 12 + session.end_slot


def sessions_overlap(left: ExperimentSession, right: ExperimentSession) -> bool:
    return not (
        session_end_ordinal(left) < session_start_ordinal(right)
        or session_end_ordinal(right) < session_start_ordinal(left)
    )


def _base_schedule_conflict_message(
    target: ExperimentSession, conflicting_slots: list[int]
) -> str:
    return (
        f"第{target.week_no}周{weekday_full_name(target.day_of_week)}第"
        f"{min(conflicting_slots)}—{max(conflicting_slots)}节与已有课程冲突。"
    )


def _experiment_session_conflict_message(occupied: ExperimentSession) -> str:
    return (
        "该场次与已选或处理中的实验安排冲突（"
        f"第{occupied.week_no}周，{weekday_full_name(occupied.day_of_week)}第"
        f"{occupied.start_slot}—{occupied.end_slot}节）。"
    )


def _bitmap_is_compatible(bitmap: StudentBusyBitmap, term: AcademicTerm) -> bool:
    required_bits = term.total_weeks * term.days_per_week * term.slots_per_day
    return (
        bitmap.start_week == 1
        and bitmap.end_week >= term.total_weeks
        and bitmap.days_per_week == term.days_per_week
        and bitmap.slots_per_day == term.slots_per_day
        and len(bitmap.bitmap) * 8 >= required_bits
    )


def _bitmap_busy(bitmap: StudentBusyBitmap, *, week: int, day: int, slot: int) -> bool:
    index = (
        (week - bitmap.start_week) * bitmap.days_per_week * bitmap.slots_per_day
        + (day - 1) * bitmap.slots_per_day
        + slot
        - 1
    )
    return bool(bitmap.bitmap[index // 8] & (1 << (7 - (index % 8))))


def _current_study_period(student: Student, term: AcademicTerm) -> tuple[int, int]:
    base_year = int(term.academic_year.split("-", maxsplit=1)[0])
    return max(1, base_year - student.enrollment_year + 1), term.semester_no


async def _load_context(
    session: AsyncSession,
    *,
    student_id: UUID,
    session_id: UUID,
    lock_target: bool = False,
) -> _EligibilityContext | None:
    student = await session.get(Student, student_id)
    target_stmt = (
        select(ExperimentSession)
        .options(
            selectinload(ExperimentSession.project),
            selectinload(ExperimentSession.laboratory),
        )
        .where(ExperimentSession.id == session_id)
    )
    if lock_target:
        target_stmt = target_stmt.with_for_update()
    target = (await session.execute(target_stmt)).scalar_one_or_none()
    if student is None or target is None:
        return None

    schedule = await session.get(ScheduleVersion, target.schedule_version_id)
    if schedule is None:
        return None
    term = await session.get(AcademicTerm, schedule.term_id)
    if term is None:
        return None

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
    plan_course = None
    if plan is not None and target.project is not None:
        plan_course = (
            await session.execute(
                select(TrainingPlanCourse)
                .options(
                    selectinload(TrainingPlanCourse.course),
                    selectinload(TrainingPlanCourse.projects).selectinload(
                        TrainingPlanProject.project
                    ),
                    selectinload(TrainingPlanCourse.prerequisites).selectinload(
                        CoursePrerequisite.prerequisite_course
                    ),
                    selectinload(TrainingPlanCourse.order_constraints),
                )
                .where(
                    TrainingPlanCourse.plan_id == plan.id,
                    TrainingPlanCourse.course_id == target.project.course_id,
                )
            )
        ).scalar_one_or_none()

    bitmap = (
        await session.execute(
            select(StudentBusyBitmap)
            .where(
                StudentBusyBitmap.student_id == student.id,
                StudentBusyBitmap.term_id == term.id,
            )
            .order_by(StudentBusyBitmap.mapping_version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    records = list(
        (
            await session.execute(
                select(StudentProjectRecord)
                .options(selectinload(StudentProjectRecord.session))
                .where(
                    StudentProjectRecord.student_id == student.id,
                    StudentProjectRecord.term_id == term.id,
                    StudentProjectRecord.status.in_(ACTIVE_PROJECT_STATUSES),
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
                    ApplicationRequest.student_id == student.id,
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
    application_sessions: list[ExperimentSession] = []
    if application_session_ids:
        application_sessions = list(
            (
                await session.execute(
                    select(ExperimentSession).where(
                        ExperimentSession.id.in_(application_session_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
    application_project_ids = {
        value
        for item in applications
        for value in (item.project_id, item.target_project_id)
        if value is not None
    }
    return _EligibilityContext(
        student=student,
        term=term,
        schedule_status=schedule.status,
        target=target,
        plan_course=plan_course,
        bitmap=bitmap,
        records=records,
        application_sessions=application_sessions,
        application_project_ids=application_project_ids,
    )


async def check_selection_eligibility(
    session: AsyncSession,
    *,
    student_id: UUID,
    session_id: UUID,
    lock_target: bool = False,
) -> SelectionEligibilityResult:
    context = await _load_context(
        session,
        student_id=student_id,
        session_id=session_id,
        lock_target=lock_target,
    )
    if context is None:
        return SelectionEligibilityResult(
            decision="UNKNOWN",
            student_id=student_id,
            session_id=session_id,
            violations=[
                SelectionViolation(
                    code="SELECTION_CONTEXT_MISSING",
                    scope="DATA",
                    message="当前规则库中未找到完整的学生、学期或场次信息。",
                )
            ],
        )

    target = context.target
    project = target.project
    result = SelectionEligibilityResult(
        decision="ALLOW",
        student_id=student_id,
        session_id=session_id,
        term_id=context.term.id,
        project_id=target.project_id,
        course_id=project.course_id if project else None,
    )

    def block(code: str, scope: str, message: str, **details: object) -> None:
        result.violations.append(
            SelectionViolation(
                code=code,
                scope=scope,  # type: ignore[arg-type]
                message=message,
                details=details,
            )
        )

    def warn(code: str, scope: str, message: str, **details: object) -> None:
        result.warnings.append(
            SelectionViolation(
                code=code,
                scope=scope,  # type: ignore[arg-type]
                message=message,
                details=details,
            )
        )

    if context.student.academic_status != "ACTIVE":
        block("STUDENT_INACTIVE", "COURSE", "当前学籍状态不允许选课。")
    if context.plan_course is None or project is None:
        block(
            "TRAINING_PLAN_RULE_MISSING",
            "DATA",
            "当前培养方案中未找到该实验课程的修读规则。",
        )
    else:
        required_period = (
            context.plan_course.study_year,
            context.plan_course.semester_no,
        )
        current_period = _current_study_period(context.student, context.term)
        if current_period < required_period:
            block(
                "STUDY_PERIOD_NOT_REACHED",
                "COURSE",
                f"该课程要求到第{required_period[0]}学年第"
                f"{required_period[1]}学期后方可修读。",
                current_study_year=current_period[0],
                current_semester=current_period[1],
            )

        completion_rows = list(
            (
                await session.execute(
                    select(StudentCourseCompletion).where(
                        StudentCourseCompletion.student_id == student_id,
                        StudentCourseCompletion.course_id.in_(
                            [
                                context.plan_course.course_id,
                                *[
                                    item.prerequisite_course_id
                                    for item in context.plan_course.prerequisites
                                ],
                            ]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        completion_by_course = {item.course_id: item.status for item in completion_rows}
        if completion_by_course.get(context.plan_course.course_id) == "PASSED":
            block(
                "COURSE_ALREADY_PASSED", "COURSE", "该实验课程已经通过，不能重复修读。"
            )
        for prerequisite in context.plan_course.prerequisites:
            if prerequisite.requirement_type != "MUST_COMPLETE":
                continue
            if (
                completion_by_course.get(prerequisite.prerequisite_course_id)
                != "PASSED"
            ):
                name = (
                    prerequisite.prerequisite_course.course_name
                    if prerequisite.prerequisite_course
                    else str(prerequisite.prerequisite_course_id)
                )
                block(
                    "PREREQUISITE_COURSE_NOT_PASSED",
                    "COURSE",
                    f"先修课程“{name}”尚未通过。",
                    prerequisite_course_id=str(prerequisite.prerequisite_course_id),
                )

    if context.schedule_status != "PUBLISHED":
        block("SCHEDULE_NOT_PUBLISHED", "SESSION", "该场次所属课表尚未发布。")
    # Historical/demo data keeps sessions as DRAFT after the containing schedule
    # is published.  The published schedule is the authoritative visibility
    # boundary, so those rows are treated as open without mutating the database.
    if target.status not in {"DRAFT", "OPEN", "FULL"}:
        block("SESSION_NOT_OPEN", "SESSION", "该实验场次当前未开放选课。")
    if target.selected_count >= target.capacity or target.status == "FULL":
        block("SESSION_FULL", "SESSION", "该实验场次名额已满。")

    for record in context.records:
        if record.project_id != target.project_id:
            continue
        if record.session_id == target.id:
            warn("SESSION_ALREADY_SELECTED", "PROJECT", "你已经选择了该实验场次。")
        else:
            block(
                "PROJECT_ALREADY_SELECTED",
                "PROJECT",
                "同一实验项目只能选择一个场次。",
                existing_session_id=str(record.session_id)
                if record.session_id
                else None,
            )
    if target.project_id in context.application_project_ids:
        block(
            "PROJECT_OCCUPIED_BY_APPLICATION",
            "PROJECT",
            "该项目存在待审核或处理中的安排，暂时不能重复选择。",
        )

    if context.bitmap is None or not _bitmap_is_compatible(
        context.bitmap, context.term
    ):
        block(
            "BUSY_BITMAP_MISSING",
            "DATA",
            "当前学期忙闲数据缺失或格式不兼容，暂时无法确认该场次是否可选。",
        )
    else:
        conflicting_slots = [
            slot
            for slot in range(target.start_slot, target.end_slot + 1)
            if _bitmap_busy(
                context.bitmap,
                week=target.week_no,
                day=target.day_of_week,
                slot=slot,
            )
        ]
        if conflicting_slots:
            block(
                "BASE_SCHEDULE_CONFLICT",
                "SESSION",
                _base_schedule_conflict_message(target, conflicting_slots),
                week_no=target.week_no,
                day_of_week=target.day_of_week,
                slots=conflicting_slots,
            )

    occupied_sessions = [
        record.session
        for record in context.records
        if record.status in TIME_OCCUPYING_STATUSES and record.session is not None
    ]
    occupied_sessions.extend(context.application_sessions)
    for occupied in occupied_sessions:
        if occupied.id == target.id:
            continue
        if sessions_overlap(target, occupied):
            block(
                "EXPERIMENT_SESSION_CONFLICT",
                "SESSION",
                _experiment_session_conflict_message(occupied),
                conflicting_session_id=str(occupied.id),
            )

    if context.plan_course is not None:
        selected_by_project = {
            item.project_id: item.session
            for item in context.records
            if item.session is not None
        }
        for constraint in context.plan_course.order_constraints:
            if target.project_id == constraint.before_project_id:
                after_session = selected_by_project.get(constraint.after_project_id)
                if after_session and session_end_ordinal(
                    target
                ) >= session_start_ordinal(after_session):
                    block(
                        "PROJECT_ORDER_VIOLATION",
                        "PROJECT",
                        constraint.description or "该项目必须安排在已选后续项目之前。",
                    )
            elif target.project_id == constraint.after_project_id:
                before_session = selected_by_project.get(constraint.before_project_id)
                if before_session is None:
                    warn(
                        "PROJECT_ORDER_PENDING",
                        "PROJECT",
                        constraint.description or "后续仍需选择时间更早的前置项目。",
                        before_project_id=str(constraint.before_project_id),
                    )
                elif session_end_ordinal(before_session) >= session_start_ordinal(
                    target
                ):
                    block(
                        "PROJECT_ORDER_VIOLATION",
                        "PROJECT",
                        constraint.description or "当前场次违反项目修读顺序。",
                    )

    if result.violations:
        result.decision = (
            "UNKNOWN"
            if any(item.scope == "DATA" for item in result.violations)
            else "BLOCK"
        )
    return result


async def get_training_plan_context(
    session: AsyncSession, *, student_id: UUID, term: AcademicTerm
) -> dict[str, object]:
    student = await session.get(Student, student_id)
    if student is None:
        return {"unknown": "学生信息不存在"}
    plan = (
        await session.execute(
            select(TrainingPlan)
            .options(
                selectinload(TrainingPlan.courses).selectinload(
                    TrainingPlanCourse.course
                ),
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
    if plan is None:
        return {"unknown": "当前规则库中未找到该学生的已发布培养方案。"}
    completions = list(
        (
            await session.execute(
                select(StudentCourseCompletion).where(
                    StudentCourseCompletion.student_id == student.id
                )
            )
        )
        .scalars()
        .all()
    )
    completion_map = {item.course_id: item.status for item in completions}
    records = list(
        (
            await session.execute(
                select(StudentProjectRecord).where(
                    StudentProjectRecord.student_id == student.id,
                    or_(
                        StudentProjectRecord.status == "COMPLETED",
                        and_(
                            StudentProjectRecord.term_id == term.id,
                            StudentProjectRecord.status.in_(ACTIVE_PROJECT_STATUSES),
                        ),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    record_map = {item.project_id: item.status for item in records}
    courses = []
    current_period = _current_study_period(student, term)
    for plan_course in sorted(
        plan.courses, key=lambda item: (item.study_year, item.semester_no)
    ):
        completion_status = completion_map.get(plan_course.course_id, "NOT_TAKEN")
        prerequisite_facts = [
            {
                "course_name": item.prerequisite_course.course_name,
                "requirement_type": item.requirement_type,
                "status": completion_map.get(item.prerequisite_course_id, "NOT_TAKEN"),
            }
            for item in plan_course.prerequisites
        ]
        eligibility_violations: list[dict[str, str]] = []
        if student.academic_status != "ACTIVE":
            eligibility_violations.append(
                {"code": "STUDENT_INACTIVE", "message": "当前学籍状态不允许选课"}
            )
        required_period = (plan_course.study_year, plan_course.semester_no)
        if current_period < required_period:
            eligibility_violations.append(
                {
                    "code": "STUDY_PERIOD_NOT_REACHED",
                    "message": (
                        f"尚未达到第{plan_course.study_year}学年第"
                        f"{plan_course.semester_no}学期的修读要求"
                    ),
                }
            )
        if completion_status == "PASSED":
            eligibility_violations.append(
                {
                    "code": "COURSE_ALREADY_PASSED",
                    "message": "该课程已经通过，不能重复修读",
                }
            )
        for prerequisite in prerequisite_facts:
            if (
                prerequisite["requirement_type"] == "MUST_COMPLETE"
                and prerequisite["status"] != "PASSED"
            ):
                eligibility_violations.append(
                    {
                        "code": "PREREQUISITE_COURSE_NOT_PASSED",
                        "message": f"先修课程尚未通过：{prerequisite['course_name']}",
                    }
                )
        courses.append(
            {
                "course_id": str(plan_course.course_id),
                "course_name": plan_course.course.course_name,
                "study_year": plan_course.study_year,
                "semester_no": plan_course.semester_no,
                "completion_status": completion_status,
                "eligibility": {
                    "decision": "BLOCK" if eligibility_violations else "ALLOW",
                    "violations": eligibility_violations,
                },
                "required_project_count": plan_course.required_project_count,
                "optional_project_min_count": plan_course.optional_project_min_count,
                "projects": [
                    {
                        "project_id": str(item.project_id),
                        "project_name": item.project.project_name,
                        "requirement_type": item.requirement_type,
                        "category": item.project.category,
                        "student_status": record_map.get(
                            item.project_id, "NOT_SELECTED"
                        ),
                    }
                    for item in sorted(
                        plan_course.projects, key=lambda project: project.display_order
                    )
                ],
                "prerequisites": prerequisite_facts,
                "order_rules": [
                    {
                        "before_project_id": str(item.before_project_id),
                        "after_project_id": str(item.after_project_id),
                        "description": item.description,
                    }
                    for item in plan_course.order_constraints
                ],
            }
        )
    return {
        "plan_code": plan.plan_code,
        "version": plan.version_no,
        "current_study_year": _current_study_period(student, term)[0],
        "current_semester": term.semester_no,
        "courses": courses,
    }


async def get_remaining_projects(
    session: AsyncSession, *, student_id: UUID, term: AcademicTerm
) -> dict[str, object]:
    context = await get_training_plan_context(session, student_id=student_id, term=term)
    if "unknown" in context:
        return context
    available_sessions = list(
        (
            await session.execute(
                select(ExperimentSession)
                .join(
                    ScheduleVersion,
                    ScheduleVersion.id == ExperimentSession.schedule_version_id,
                )
                .where(
                    ScheduleVersion.term_id == term.id,
                    ScheduleVersion.status == "PUBLISHED",
                    ExperimentSession.status.in_({"DRAFT", "OPEN", "FULL"}),
                )
            )
        )
        .scalars()
        .all()
    )
    sessions_by_project: dict[UUID, list[ExperimentSession]] = {}
    for candidate in available_sessions:
        sessions_by_project.setdefault(candidate.project_id, []).append(candidate)

    remaining: list[dict[str, object]] = []
    current_period = (
        int(context.get("current_study_year", 0)),
        int(context.get("current_semester", 0)),
    )
    for course in context.get("courses", []):
        assert isinstance(course, dict)
        course_status = course.get("completion_status")
        course_eligible = (
            course_status != "PASSED"
            and current_period
            >= (int(course.get("study_year", 0)), int(course.get("semester_no", 0)))
            and all(
                item.get("requirement_type") != "MUST_COMPLETE"
                or item.get("status") == "PASSED"
                for item in course.get("prerequisites", [])
            )
        )
        for project in course.get("projects", []):
            if project.get("student_status") in ACTIVE_PROJECT_STATUSES:
                category = (
                    "已完成"
                    if project.get("student_status") == "COMPLETED"
                    else "已选未完成"
                )
            elif not course_eligible:
                category = "尚缺但课程层面无资格"
            else:
                eligibility_results = [
                    await check_selection_eligibility(
                        session,
                        student_id=student_id,
                        session_id=candidate.id,
                    )
                    for candidate in sessions_by_project.get(
                        UUID(str(project["project_id"])), []
                    )
                ]
                if any(item.eligible for item in eligibility_results):
                    category = "尚缺且当前可选"
                elif any(
                    violation.code == "PROJECT_ORDER_VIOLATION"
                    for item in eligibility_results
                    for violation in item.violations
                ):
                    category = "尚缺但违反顺序"
                else:
                    category = "尚缺但当前不可选"
            remaining.append(
                {
                    **project,
                    "course_name": course.get("course_name"),
                    "category_label": category,
                }
            )
    return {"projects": remaining}


_PERIOD_BOUNDS: dict[TimePeriod, tuple[int, int]] = {
    "MORNING": (1, 4),
    "AFTERNOON": (5, 8),
    "EVENING": (9, 12),
}
_PERIOD_LABELS: dict[TimePeriod, str] = {
    "MORNING": "早上",
    "AFTERNOON": "下午",
    "EVENING": "晚上",
}
_PERIOD_ORDER: tuple[TimePeriod, ...] = ("MORNING", "AFTERNOON", "EVENING")


def _session_within_period(item: RecommendationSession, period: TimePeriod) -> bool:
    start, end = _PERIOD_BOUNDS[period]
    return item.start_slot >= start and item.end_slot <= end


def _session_overlaps_period(item: RecommendationSession, period: TimePeriod) -> bool:
    start, end = _PERIOD_BOUNDS[period]
    return item.start_slot <= end and item.end_slot >= start


def _week_matches_range(week_no: int, week_range: WeekRangePreference | None) -> bool:
    if week_range is None:
        return True
    if week_range.start_week is not None and (
        week_no < week_range.start_week
        or (week_no == week_range.start_week and not week_range.start_inclusive)
    ):
        return False
    return not (
        week_range.end_week is not None
        and (
            week_no > week_range.end_week
            or (week_no == week_range.end_week and not week_range.end_inclusive)
        )
    )


def _week_range_description(week_range: WeekRangePreference) -> str:
    if week_range.start_week is not None and week_range.end_week is not None:
        effective_start = week_range.start_week + (
            0 if week_range.start_inclusive else 1
        )
        effective_end = week_range.end_week - (0 if week_range.end_inclusive else 1)
        return f"第{effective_start}—{effective_end}周"
    if week_range.start_week is not None:
        connector = "及以后" if week_range.start_inclusive else "以后"
        return f"第{week_range.start_week}周{connector}"
    connector = "及以前" if week_range.end_inclusive else "以前"
    return f"第{week_range.end_week}周{connector}"


def _normalized_avoided_periods(
    preferences: SelectionPreferences,
) -> set[TimePeriod]:
    periods = set(preferences.avoided_periods)
    if preferences.avoid_evening:
        periods.add("EVENING")
    return periods


def _preference_score(
    item: RecommendationSession,
    preferences: SelectionPreferences,
    *,
    student_campus: str,
) -> tuple[int, list[str], list[str]]:
    score = 0
    reasons: list[str] = []
    warnings: list[str] = []
    if item.requirement_type == "REQUIRED":
        score += 100
        reasons.append("优先满足必做项目要求")
    if item.category in preferences.preferred_categories:
        score += 60
        reasons.append("符合项目模块偏好")
    preferred_day_numbers = {
        weekday_number(day_name) for day_name in preferences.preferred_days
    }
    avoided_day_numbers = {
        weekday_number(day_name) for day_name in preferences.avoided_days
    }
    if any(
        _session_within_period(item, period) for period in preferences.preferred_periods
    ):
        score += 40
        reasons.append("符合偏好时间段")
    if item.day_of_week in preferred_day_numbers:
        score += 30
        reasons.append("符合偏好日期")
    if item.day_of_week in avoided_day_numbers:
        score -= 80
    for period in _normalized_avoided_periods(preferences):
        if _session_overlaps_period(item, period):
            score -= 100
    if item.week_no in preferences.avoided_weeks:
        score -= 90
    if preferences.avoid_weekend and item.day_of_week in {1, 7}:
        score -= 100
    if item.start_slot <= 8:
        score += 15
    if item.day_of_week not in {1, 7}:
        score += 10
    if item.campus_name == student_campus:
        score += 20
        reasons.append("与就读校区一致")
    elif item.start_slot >= 9:
        score -= 120
        warnings.append("该场次为跨校区晚间安排，通常不优先推荐")
    score += min(item.remaining, 20)
    if not reasons:
        reasons.append("当前无时间冲突且仍有名额")
    return score, reasons, warnings


def _preference_explanations(
    sessions: list[RecommendationSession], preferences: SelectionPreferences
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    if not sessions:
        return reasons, warnings

    preferred_periods = list(dict.fromkeys(preferences.preferred_periods))
    if preferred_periods:
        matched = [
            item
            for item in sessions
            if any(_session_within_period(item, period) for period in preferred_periods)
        ]
        labels = "、".join(_PERIOD_LABELS[item] for item in preferred_periods)
        if matched:
            reasons.append(f"{len(matched)}个新增场次符合{labels}偏好")
        for item in sessions:
            if item not in matched:
                warnings.append(f"{item.project_name}未能安排在偏好的{labels}时段")

    preferred_day_numbers = {
        weekday_number(day_name) for day_name in preferences.preferred_days
    }
    if preferred_day_numbers:
        matched = [
            item for item in sessions if item.day_of_week in preferred_day_numbers
        ]
        labels = "、".join(preferences.preferred_days)
        if matched:
            reasons.append(f"{len(matched)}个新增场次符合{labels}偏好")
        for item in sessions:
            if item not in matched:
                warnings.append(f"{item.project_name}未能安排在偏好的{labels}")

    avoided_periods = [
        period
        for period in _PERIOD_ORDER
        if period in _normalized_avoided_periods(preferences)
    ]
    for item in sessions:
        matched_periods = [
            period
            for period in avoided_periods
            if _session_overlaps_period(item, period)
        ]
        if matched_periods:
            labels = "、".join(_PERIOD_LABELS[period] for period in matched_periods)
            warnings.append(f"{item.project_name}未能避开{labels}时段")

    avoided_day_numbers = {
        weekday_number(day_name) for day_name in preferences.avoided_days
    }
    for item in sessions:
        if item.day_of_week in avoided_day_numbers:
            warnings.append(
                f"{item.project_name}未能避开{weekday_name(item.day_of_week)}"
            )
        if preferences.avoid_weekend and item.day_of_week in {1, 7}:
            warnings.append(f"{item.project_name}未能避开周末")
        if item.week_no in preferences.avoided_weeks:
            warnings.append(f"{item.project_name}未能避开第{item.week_no}周")

    return list(dict.fromkeys(reasons)), list(dict.fromkeys(warnings))


def _fixed_selection_preference_warnings(
    sessions: list[RecommendationSession], preferences: SelectionPreferences
) -> list[str]:
    warnings: list[str] = []
    avoided_periods = [
        period
        for period in _PERIOD_ORDER
        if period in _normalized_avoided_periods(preferences)
    ]
    preferred_day_numbers = {
        weekday_number(day_name) for day_name in preferences.preferred_days
    }
    avoided_day_numbers = {
        weekday_number(day_name) for day_name in preferences.avoided_days
    }
    for item in sessions:
        misses: list[str] = []
        if preferences.preferred_periods and not any(
            _session_within_period(item, period)
            for period in preferences.preferred_periods
        ):
            misses.append("不在偏好时间段")
        if preferred_day_numbers and item.day_of_week not in preferred_day_numbers:
            misses.append("不在偏好星期")
        if any(_session_overlaps_period(item, period) for period in avoided_periods):
            misses.append("处于希望避开的时间段")
        if item.day_of_week in avoided_day_numbers:
            misses.append(f"处于希望避开的{weekday_name(item.day_of_week)}")
        if preferences.avoid_weekend and item.day_of_week in {1, 7}:
            misses.append("处于周末")
        if item.week_no in preferences.avoided_weeks:
            misses.append(f"处于希望避开的第{item.week_no}周")
        if not _week_matches_range(item.week_no, preferences.week_range):
            misses.append("不在本轮指定周次范围内")
        if misses:
            warnings.append(
                f"已选固定场次“{item.project_name}”{'、'.join(misses)}，本轮保持不变"
            )
    return warnings


async def recommend_selection_plans(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    preferences: SelectionPreferences,
    scope: RecommendationScope | None = None,
    course_ids: set[UUID] | None = None,
    project_ids: set[UUID] | None = None,
) -> list[RecommendationPlan]:
    """Build up to three requirement-complete combinations, then partial fallbacks."""

    scope = scope or RecommendationScope()
    course_ids = course_ids or set()
    project_ids = project_ids or set()
    student = await session.get(Student, student_id)
    if student is None:
        return []
    campus = await session.get(Campus, student.campus_id)
    student_campus = campus.name if campus else ""
    sessions = list(
        (
            await session.execute(
                select(ExperimentSession)
                .options(
                    selectinload(ExperimentSession.project),
                    selectinload(ExperimentSession.laboratory),
                )
                .join(
                    ScheduleVersion,
                    ScheduleVersion.id == ExperimentSession.schedule_version_id,
                )
                .where(
                    ScheduleVersion.term_id == term.id,
                    ScheduleVersion.status == "PUBLISHED",
                    ExperimentSession.status.in_({"DRAFT", "OPEN"}),
                    ExperimentSession.selected_count < ExperimentSession.capacity,
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
    # Project/course/plan metadata is loaded separately to avoid coupling the
    # recommendation path to dashboard serialization.
    plan_context = await get_training_plan_context(
        session, student_id=student_id, term=term
    )
    sessions_by_project: dict[UUID, list[ExperimentSession]] = {}
    for item in sessions:
        sessions_by_project.setdefault(item.project_id, []).append(item)

    records = list(
        (
            await session.execute(
                select(StudentProjectRecord)
                .options(
                    selectinload(StudentProjectRecord.session).selectinload(
                        ExperimentSession.project
                    ),
                    selectinload(StudentProjectRecord.session).selectinload(
                        ExperimentSession.laboratory
                    ),
                )
                .where(
                    StudentProjectRecord.student_id == student_id,
                    or_(
                        StudentProjectRecord.status == "COMPLETED",
                        and_(
                            StudentProjectRecord.term_id == term.id,
                            StudentProjectRecord.status.in_(ACTIVE_PROJECT_STATUSES),
                        ),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    active_project_ids = {item.project_id for item in records}
    satisfied_project_ids = {
        item.project_id for item in records if item.status in {"SELECTED", "COMPLETED"}
    }
    locked_unsatisfied_ids = active_project_ids - satisfied_project_ids

    project_meta: dict[UUID, dict[str, object]] = {}
    course_meta: dict[UUID, dict[str, object]] = {}
    constraints_by_after: dict[UUID, list[UUID]] = {}
    constraints: list[tuple[UUID, UUID]] = []
    for course in plan_context.get("courses", []):
        if not isinstance(course, dict):
            continue
        course_id = UUID(str(course["course_id"]))
        course_meta[course_id] = course
        for project_item in course.get("projects", []):
            if not isinstance(project_item, dict):
                continue
            project_id = UUID(str(project_item["project_id"]))
            project_meta[project_id] = {
                **project_item,
                "course_id": course_id,
                "course_name": course["course_name"],
            }
        for rule in course.get("order_rules", []):
            if not isinstance(rule, dict):
                continue
            after_id = UUID(str(rule["after_project_id"]))
            before_id = UUID(str(rule["before_project_id"]))
            constraints_by_after.setdefault(after_id, []).append(before_id)
            constraints.append((before_id, after_id))

    def course_block_reasons(course: dict[str, object]) -> list[str]:
        reasons: list[str] = []
        if student.academic_status != "ACTIVE":
            reasons.append("当前学籍状态不允许选课")
        required_period = (int(course["study_year"]), int(course["semester_no"]))
        if _current_study_period(student, term) < required_period:
            reasons.append(
                f"尚未达到第{required_period[0]}学年第{required_period[1]}学期的修读要求"
            )
        if course.get("completion_status") == "PASSED":
            reasons.append("该实验课程已经通过，不能重复修读")
        failed_prerequisites = [
            str(item.get("course_name"))
            for item in course.get("prerequisites", [])
            if isinstance(item, dict)
            and item.get("requirement_type") == "MUST_COMPLETE"
            and item.get("status") != "PASSED"
        ]
        if failed_prerequisites:
            reasons.append("先修课程尚未通过：" + "、".join(failed_prerequisites))
        return reasons

    scoped_courses: list[dict[str, object]] = []
    excluded_courses: list[ExcludedCourse] = []
    for course_id, course in course_meta.items():
        course_project_ids = {
            UUID(str(item["project_id"]))
            for item in course.get("projects", [])
            if isinstance(item, dict)
        }
        if scope.mode == "COURSES" and course_id not in course_ids:
            continue
        if scope.mode == "PROJECTS" and not course_project_ids.intersection(
            project_ids
        ):
            continue
        reasons = course_block_reasons(course)
        if reasons:
            excluded_courses.append(
                ExcludedCourse(
                    course_id=course_id,
                    course_name=str(course["course_name"]),
                    reasons=reasons,
                )
            )
            continue
        scoped_courses.append(course)

    relevant_project_ids = {
        UUID(str(item["project_id"]))
        for course in scoped_courses
        for item in course.get("projects", [])
        if isinstance(item, dict)
        and (scope.mode != "PROJECTS" or UUID(str(item["project_id"])) in project_ids)
    }

    async def has_feasible_predecessors(candidate: ExperimentSession) -> bool:
        """A recommended successor must leave every missing predecessor feasible."""
        for before_project_id in constraints_by_after.get(candidate.project_id, []):
            if before_project_id in satisfied_project_ids:
                continue
            feasible = False
            for before_session in sessions_by_project.get(before_project_id, []):
                if not _week_matches_range(
                    before_session.week_no, preferences.week_range
                ):
                    continue
                if session_end_ordinal(before_session) >= session_start_ordinal(
                    candidate
                ):
                    continue
                before_result = await check_selection_eligibility(
                    session,
                    student_id=student_id,
                    session_id=before_session.id,
                )
                if before_result.eligible:
                    feasible = True
                    break
            if not feasible:
                return False
        return True

    scored_by_project: dict[UUID, list[tuple[int, RecommendationSession]]] = {}
    raw_session_by_id: dict[UUID, ExperimentSession] = {}
    for candidate in sessions:
        if candidate.project_id not in relevant_project_ids:
            continue
        if not _week_matches_range(candidate.week_no, preferences.week_range):
            continue
        meta = project_meta.get(candidate.project_id)
        if meta is None:
            continue
        eligibility = await check_selection_eligibility(
            session, student_id=student_id, session_id=candidate.id
        )
        if not eligibility.eligible:
            continue
        if not await has_feasible_predecessors(candidate):
            continue
        laboratory = candidate.laboratory
        candidate_campus = (
            await session.get(Campus, laboratory.campus_id) if laboratory else None
        )
        item = RecommendationSession(
            session_id=candidate.id,
            project_id=candidate.project_id,
            project_name=str(meta["project_name"]),
            course_name=str(meta["course_name"]),
            requirement_type=str(meta["requirement_type"]),  # type: ignore[arg-type]
            category=str(meta.get("category") or ""),
            week_no=candidate.week_no,
            day_of_week=candidate.day_of_week,
            start_slot=candidate.start_slot,
            end_slot=candidate.end_slot,
            laboratory_name=laboratory.name if laboratory else "",
            campus_name=candidate_campus.name if candidate_campus else "",
            remaining=candidate.capacity - candidate.selected_count,
        )
        score, reasons, warnings = _preference_score(
            item, preferences, student_campus=student_campus
        )
        item.reasons = reasons
        item.warnings = warnings
        raw_session_by_id[candidate.id] = candidate
        scored_by_project.setdefault(candidate.project_id, []).append((score, item))
    for project_candidates in scored_by_project.values():
        project_candidates.sort(
            key=lambda pair: (
                -pair[0],
                pair[1].week_no,
                pair[1].day_of_week,
                pair[1].start_slot,
            )
        )
        del project_candidates[5:]

    retained_selections: list[RecommendationSession] = []
    fixed_sessions: dict[UUID, ExperimentSession] = {}
    campus_cache: dict[UUID, Campus | None] = {}
    for record in records:
        if record.status != "SELECTED" or record.session is None:
            continue
        if record.project_id not in relevant_project_ids:
            continue
        raw = record.session
        fixed_sessions[record.project_id] = raw
        meta = project_meta.get(record.project_id)
        if meta is None:
            continue
        laboratory = raw.laboratory
        candidate_campus = None
        if laboratory:
            if laboratory.campus_id not in campus_cache:
                campus_cache[laboratory.campus_id] = await session.get(
                    Campus, laboratory.campus_id
                )
            candidate_campus = campus_cache[laboratory.campus_id]
        retained_selections.append(
            RecommendationSession(
                session_id=raw.id,
                project_id=record.project_id,
                project_name=str(meta["project_name"]),
                course_name=str(meta["course_name"]),
                requirement_type=str(meta["requirement_type"]),  # type: ignore[arg-type]
                category=str(meta.get("category") or ""),
                week_no=raw.week_no,
                day_of_week=raw.day_of_week,
                start_slot=raw.start_slot,
                end_slot=raw.end_slot,
                laboratory_name=laboratory.name if laboratory else "",
                campus_name=candidate_campus.name if candidate_campus else "",
                remaining=max(raw.capacity - raw.selected_count, 0),
                reasons=["该项目已经选择，作为方案中的固定安排"],
            )
        )

    base_unmet: list[UnmetRequirement] = []
    target_options_by_course: list[list[set[UUID]]] = []
    for course in scoped_courses:
        projects = [
            item
            for item in course.get("projects", [])
            if isinstance(item, dict)
            and (
                scope.mode != "PROJECTS" or UUID(str(item["project_id"])) in project_ids
            )
        ]
        if scope.mode == "PROJECTS":
            targets = {
                UUID(str(item["project_id"]))
                for item in projects
                if UUID(str(item["project_id"])) not in satisfied_project_ids
            }
            target_options_by_course.append([targets])
            continue

        required_ids = {
            UUID(str(item["project_id"]))
            for item in projects
            if item.get("requirement_type") == "REQUIRED"
        }
        optional_ids = {
            UUID(str(item["project_id"]))
            for item in projects
            if item.get("requirement_type") == "OPTIONAL"
        }
        missing_required = required_ids - satisfied_project_ids
        selected_optional_count = len(optional_ids.intersection(satisfied_project_ids))
        optional_needed = max(
            0,
            int(course.get("optional_project_min_count") or 0)
            - selected_optional_count,
        )
        available_optional = sorted(
            optional_ids - satisfied_project_ids - locked_unsatisfied_ids,
            key=str,
        )
        choose_count = min(optional_needed, len(available_optional))
        optional_choices = list(combinations(available_optional, choose_count)) or [()]
        target_options_by_course.append(
            [
                (missing_required - locked_unsatisfied_ids).union(choice)
                for choice in optional_choices
            ]
        )
        for locked_id in missing_required.intersection(locked_unsatisfied_ids):
            meta = project_meta[locked_id]
            base_unmet.append(
                UnmetRequirement(
                    course_name=str(course["course_name"]),
                    project_name=str(meta["project_name"]),
                    reason="该项目存在处理中或缺做记录，暂时不能重新推荐场次",
                )
            )
        if choose_count < optional_needed:
            base_unmet.append(
                UnmetRequirement(
                    course_name=str(course["course_name"]),
                    reason=f"选做项目仍缺{optional_needed - choose_count}项可安排项目",
                )
            )

    target_sets = [
        set().union(*items)
        for items in product(*(target_options_by_course or [[set()]]))
    ]
    target_sets.sort(
        key=lambda ids: (
            -sum(
                scored_by_project.get(project_id, [(0, None)])[0][0]
                for project_id in ids
            )
        )
    )
    target_sets = target_sets[:100]

    def ordered_projects(target_ids: set[UUID]) -> list[UUID]:
        remaining = set(target_ids)
        ordered: list[UUID] = []
        while remaining:
            ready = sorted(
                (
                    project_id
                    for project_id in remaining
                    if not set(constraints_by_after.get(project_id, [])).intersection(
                        remaining
                    )
                ),
                key=str,
            )
            if not ready:
                ready = [min(remaining, key=str)]
            ordered.extend(ready)
            remaining.difference_update(ready)
        return ordered

    def can_add(
        project_id: UUID,
        candidate: RecommendationSession,
        chosen: dict[UUID, RecommendationSession],
        target_ids: set[UUID],
    ) -> bool:
        raw = raw_session_by_id[candidate.session_id]
        if any(
            sessions_overlap(raw, raw_session_by_id[item.session_id])
            for item in chosen.values()
        ):
            return False
        combined_raw = {**fixed_sessions}
        combined_raw.update(
            {
                selected_project_id: raw_session_by_id[item.session_id]
                for selected_project_id, item in chosen.items()
            }
        )
        combined_raw[project_id] = raw
        for before_id, after_id in constraints:
            before = combined_raw.get(before_id)
            after = combined_raw.get(after_id)
            if (
                before is not None
                and after is not None
                and session_end_ordinal(before) >= session_start_ordinal(after)
            ):
                return False
            if (
                after_id == project_id
                and before_id in target_ids
                and before_id not in combined_raw
                and before_id not in satisfied_project_ids
            ):
                return False
        return True

    generated: list[tuple[bool, int, int, RecommendationPlan]] = []
    for target_ids in target_sets:
        beam: list[tuple[int, dict[UUID, RecommendationSession], set[UUID]]] = [
            (0, {}, set())
        ]
        for project_id in ordered_projects(target_ids):
            next_beam: list[
                tuple[int, dict[UUID, RecommendationSession], set[UUID]]
            ] = []
            candidates = scored_by_project.get(project_id, [])
            for score, chosen, missing in beam:
                expanded = False
                for candidate_score, candidate in candidates:
                    if can_add(project_id, candidate, chosen, target_ids):
                        next_beam.append(
                            (
                                score + candidate_score,
                                {**chosen, project_id: candidate},
                                set(missing),
                            )
                        )
                        expanded = True
                if not expanded:
                    next_beam.append((score, dict(chosen), {*missing, project_id}))
            next_beam.sort(key=lambda item: (-len(item[1]), -item[0]))
            beam = next_beam[:100]

        for score, chosen, missing_ids in beam[:10]:
            unmet = list(base_unmet)
            if not scoped_courses:
                unmet.append(
                    UnmetRequirement(
                        course_name="当前推荐范围",
                        reason="当前没有具备修读资格的实验课程或项目",
                    )
                )
            for missing_id in sorted(missing_ids, key=str):
                meta = project_meta[missing_id]
                missing_reason = (
                    f"{_week_range_description(preferences.week_range)}内没有同时满足"
                    "资格、容量、时间和项目顺序的可用场次"
                    if preferences.week_range is not None
                    else "当前没有同时满足资格、容量、时间和项目顺序的可用场次"
                )
                unmet.append(
                    UnmetRequirement(
                        course_name=str(meta["course_name"]),
                        project_name=str(meta["project_name"]),
                        reason=missing_reason,
                    )
                )

            covered_ids = satisfied_project_ids.union(chosen)
            course_requirements: list[CourseCoverage] = []
            for course in scoped_courses:
                course_id = UUID(str(course["course_id"]))
                projects = [
                    item
                    for item in course.get("projects", [])
                    if isinstance(item, dict)
                    and (
                        scope.mode != "PROJECTS"
                        or UUID(str(item["project_id"])) in project_ids
                    )
                ]
                required_ids = {
                    UUID(str(item["project_id"]))
                    for item in projects
                    if item.get("requirement_type") == "REQUIRED"
                }
                optional_ids = {
                    UUID(str(item["project_id"]))
                    for item in projects
                    if item.get("requirement_type") == "OPTIONAL"
                }
                optional_min = (
                    len(optional_ids)
                    if scope.mode == "PROJECTS"
                    else int(course.get("optional_project_min_count") or 0)
                )
                required_satisfied = len(required_ids.intersection(covered_ids))
                optional_satisfied = len(optional_ids.intersection(covered_ids))
                course_requirements.append(
                    CourseCoverage(
                        course_id=course_id,
                        course_name=str(course["course_name"]),
                        required_total=len(required_ids),
                        required_satisfied=required_satisfied,
                        optional_min=optional_min,
                        optional_satisfied=optional_satisfied,
                    )
                )
                if required_satisfied < len(required_ids) and not any(
                    item.course_name == str(course["course_name"]) and item.project_name
                    for item in unmet
                ):
                    unmet.append(
                        UnmetRequirement(
                            course_name=str(course["course_name"]),
                            reason="仍有必做项目未覆盖",
                        )
                    )
                if optional_satisfied < optional_min:
                    unmet.append(
                        UnmetRequirement(
                            course_name=str(course["course_name"]),
                            reason=f"选做项目仍缺{optional_min - optional_satisfied}项",
                        )
                    )

            proposed_sessions = list(chosen.values())
            preference_reasons, preference_warnings = _preference_explanations(
                proposed_sessions, preferences
            )
            warnings = list(
                dict.fromkeys(
                    warning for item in proposed_sessions for warning in item.warnings
                )
            )
            warnings.extend(
                warning for warning in preference_warnings if warning not in warnings
            )
            warnings.extend(
                warning
                for warning in _fixed_selection_preference_warnings(
                    retained_selections, preferences
                )
                if warning not in warnings
            )
            complete = not unmet and bool(scoped_courses)
            plan = RecommendationPlan(
                name="",
                coverage_status="COMPLETE" if complete else "PARTIAL",
                scope=scope,
                sessions=proposed_sessions,
                retained_selections=retained_selections,
                course_requirements=course_requirements,
                excluded_courses=excluded_courses,
                unmet_requirements=unmet,
                reasons=[
                    (
                        "完整覆盖当前具备修读资格范围内的项目要求"
                        if complete
                        else "这是当前可行场次中覆盖要求最多的部分方案"
                    ),
                    *preference_reasons,
                ],
                warnings=warnings,
            )
            generated.append((complete, len(chosen), score, plan))

    generated.sort(key=lambda item: (-int(item[0]), -item[1], -item[2]))
    plans: list[RecommendationPlan] = []
    seen: set[tuple[str, ...]] = set()
    for _, _, _, plan in generated:
        key = tuple(sorted(str(item.session_id) for item in plan.sessions))
        if key in seen:
            continue
        seen.add(key)
        plan.name = f"推荐方案{len(plans) + 1}"
        plans.append(plan)
        if len(plans) == 3:
            break
    return plans
