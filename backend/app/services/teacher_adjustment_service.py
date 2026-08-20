from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.db.session import AsyncSessionFactory
from app.models.application import ApplicationRequest, ApprovalRecord
from app.models.curriculum import AcademicTerm, ExperimentCourse, ExperimentProject
from app.models.enrollment import StudentProjectRecord
from app.models.identity import Student, Teacher
from app.models.resources import (
    EquipmentType,
    LabEquipmentInventory,
    Laboratory,
    LabProjectCapability,
    ProjectEquipmentRequirement,
    ResourceIssueAsset,
    ResourceIssueReport,
    TeacherProjectQualification,
)
from app.models.scheduling import (
    ExperimentSession,
    ScheduleVersion,
    TeacherTimetableEntry,
)
from app.models.teaching_adjustment import (
    AdjustmentRemediationItem,
    AdjustmentRemediationPlan,
    ApplicationApprovalTask,
    EquipmentInventoryMovement,
    ResourceRelocationPlan,
    ResourceRepairUpdate,
    SessionExecutionOverride,
)
from app.schemas.student_consultation import (
    SelectionPreferences,
    weekday_name,
)
from app.schemas.teacher_adjustment import (
    LabChangePreviewRequest,
    RepairUpdateCreateRequest,
    ResourceIssueCreateRequest,
    SubstitutionPreviewRequest,
    TeacherAdjustmentCreateRequest,
    TeacherAdjustmentValidation,
    TeacherRescheduleOption,
    TimeTarget,
)
from app.services import equipment_asset_service as asset_svc
from app.services.resource_capacity_service import (
    cache_resource_impact,
    equipment_student_capacity,
    get_cached_resource_impact,
    invalidate_resource_impact_cache,
    minimum_resource_capacity,
    relocation_requirement,
)
from app.services.schedule_constraint_validation_service import (
    affected_students_for_time,
    published_session,
    validate_teacher_and_lab_time,
)
from app.services.student_adjustment_service import (
    recommend_adjustment_options,
    session_calendar_date,
    session_has_started,
    validate_student_adjustment,
)
from app.services.student_cache_service import refresh_experiment_views_after_commit

ACTIVE_APPLICATIONS = {"SUBMITTED", "VALIDATING", "PENDING_REVIEW", "APPROVED"}

logger = logging.getLogger(__name__)


async def _teacher_session(
    session: AsyncSession, *, teacher_id: UUID, session_id: UUID
) -> ExperimentSession:
    item = await published_session(session, session_id)
    if item is None:
        raise LookupError("未找到已发布的原实验场次。")
    if item.teacher_id != teacher_id:
        raise PermissionError("只能调整当前教师负责的实验场次。")
    return item


def _preference_score(
    preferences: SelectionPreferences, target: TimeTarget
) -> tuple[int, list[str], list[str]]:
    score = 0
    reasons: list[str] = []
    warnings: list[str] = []
    day_name = weekday_name(target.day_of_week)
    periods = set()
    if target.start_slot <= 4 and target.end_slot <= 4:
        periods.add("MORNING")
    if target.start_slot >= 5 and target.end_slot <= 8:
        periods.add("AFTERNOON")
    if target.start_slot >= 9:
        periods.add("EVENING")
    if periods.intersection(preferences.preferred_periods):
        score += 40
        reasons.append("符合偏好的上课时段")
    if periods.intersection(preferences.avoided_periods) or (
        preferences.avoid_evening and "EVENING" in periods
    ):
        score -= 100
        warnings.append("未能避开不喜欢的上课时段")
    if day_name in preferences.preferred_days:
        score += 30
        reasons.append(f"符合{day_name}偏好")
    if day_name in preferences.avoided_days:
        score -= 80
        warnings.append(f"未能避开{day_name}")
    if target.week_no in preferences.avoided_weeks:
        score -= 90
        warnings.append(f"未能避开第{target.week_no}周")
    if preferences.avoid_weekend and target.day_of_week in {1, 7}:
        score -= 80
        warnings.append("该方案安排在周末")
    return score, reasons, warnings


def _inside_week_range(preferences: SelectionPreferences, week_no: int) -> bool:
    value = preferences.week_range
    if value is None:
        return True
    if value.start_week is not None and (
        week_no < value.start_week
        or (week_no == value.start_week and not value.start_inclusive)
    ):
        return False
    return not (
        value.end_week is not None
        and (
            week_no > value.end_week
            or (week_no == value.end_week and not value.end_inclusive)
        )
    )


async def validate_reschedule(
    session: AsyncSession,
    *,
    teacher_id: UUID,
    term: AcademicTerm,
    original_session_id: UUID,
    target: TimeTarget,
) -> TeacherAdjustmentValidation:
    original = await _teacher_session(
        session, teacher_id=teacher_id, session_id=original_session_id
    )
    if session_has_started(term, original):
        return TeacherAdjustmentValidation(
            allowed=False, conflicts=["已经开课的场次不能申请调课。"]
        )
    if target.week_no > term.total_weeks:
        return TeacherAdjustmentValidation(
            allowed=False, conflicts=["目标教学周超出当前学期范围。"]
        )
    if (
        original.week_no,
        original.day_of_week,
        original.start_slot,
        original.end_slot,
    ) == (target.week_no, target.day_of_week, target.start_slot, target.end_slot):
        return TeacherAdjustmentValidation(
            allowed=False, conflicts=["目标时间与原场次相同。"]
        )
    hard = await validate_teacher_and_lab_time(
        session,
        schedule_version_id=original.schedule_version_id,
        original_session_id=original.id,
        teacher_id=original.teacher_id,
        laboratory_id=original.laboratory_id,
        target=target,
    )
    affected = await affected_students_for_time(
        session, term_id=term.id, original_session_id=original.id, target=target
    )
    allowed = not hard and not affected
    return TeacherAdjustmentValidation(
        allowed=allowed,
        can_submit_for_review=not hard,
        conflicts=hard,
        warnings=(
            []
            if not affected
            else ["部分学生在目标时间存在冲突，管理员批准前必须完成学生安置。"]
        ),
        affected_students=affected,
        impact_summary={
            "selected_students": original.selected_count,
            "affected_student_count": len(affected),
            "requires_remediation": bool(affected),
        },
    )


async def recommend_reschedules(
    session: AsyncSession,
    *,
    teacher_id: UUID,
    term: AcademicTerm,
    original_session_id: UUID,
    preferences: SelectionPreferences,
    max_options: int = 3,
) -> list[TeacherRescheduleOption]:
    original = await _teacher_session(
        session, teacher_id=teacher_id, session_id=original_session_id
    )
    if session_has_started(term, original):
        return []
    candidates: list[TeacherRescheduleOption] = []
    current_week = max(1, (datetime.now(UTC).date() - term.start_date).days // 7 + 1)
    for week in range(current_week, term.total_weeks + 1):
        if not _inside_week_range(preferences, week):
            continue
        for day in range(1, 8):
            for start in (1, 5, 9):
                target = TimeTarget(
                    week_no=week, day_of_week=day, start_slot=start, end_slot=start + 3
                )
                if (week, day, start) == (
                    original.week_no,
                    original.day_of_week,
                    original.start_slot,
                ):
                    continue
                result = await validate_reschedule(
                    session,
                    teacher_id=teacher_id,
                    term=term,
                    original_session_id=original.id,
                    target=target,
                )
                if result.conflicts or not result.can_submit_for_review:
                    continue
                pref_score, reasons, warnings = _preference_score(preferences, target)
                score = 1000 - len(result.affected_students) * 250 + pref_score
                score -= abs(week - original.week_no) * 3
                if not result.affected_students:
                    reasons.insert(0, "教师、实验室和学生均无时间冲突")
                else:
                    warnings.extend(result.warnings)
                candidates.append(
                    TeacherRescheduleOption(
                        target=target,
                        score=score,
                        reasons=reasons or ["综合冲突和时间距离后较优"],
                        warnings=warnings,
                        affected_student_count=len(result.affected_students),
                        affected_students=result.affected_students,
                    )
                )
    candidates.sort(
        key=lambda item: (
            -item.score,
            item.target.week_no,
            item.target.day_of_week,
            item.target.start_slot,
        )
    )
    result: list[TeacherRescheduleOption] = []
    used_days: set[tuple[int, int, int]] = set()
    for item in candidates:
        key = (item.target.week_no, item.target.day_of_week, item.target.start_slot)
        if key in used_days:
            continue
        result.append(item)
        used_days.add(key)
        if len(result) >= max_options:
            break
    return result


async def validate_lab_change(
    session: AsyncSession, *, teacher_id: UUID, body: LabChangePreviewRequest
) -> TeacherAdjustmentValidation:
    original = await _teacher_session(
        session, teacher_id=teacher_id, session_id=body.original_session_id
    )
    lab = await session.get(Laboratory, body.target_laboratory_id)
    if lab is None or lab.status not in {"ACTIVE", "LIMITED"}:
        return TeacherAdjustmentValidation(
            allowed=False, conflicts=["目标实验室不存在或当前不可用。"]
        )
    if lab.id == original.laboratory_id:
        return TeacherAdjustmentValidation(
            allowed=False, conflicts=["目标实验室与原实验室相同。"]
        )
    capability = await session.scalar(
        select(LabProjectCapability.id).where(
            LabProjectCapability.laboratory_id == lab.id,
            LabProjectCapability.project_id == original.project_id,
            LabProjectCapability.status == "ACTIVE",
            LabProjectCapability.effective_capacity >= original.selected_count,
        )
    )
    conflicts: list[str] = []
    if capability is None:
        conflicts.append("目标实验室不支持该项目或有效容量不足。")
    occupied = await session.scalar(
        select(ExperimentSession.id)
        .where(
            ExperimentSession.schedule_version_id == original.schedule_version_id,
            ExperimentSession.id != original.id,
            ExperimentSession.laboratory_id == lab.id,
            ExperimentSession.week_no == original.week_no,
            ExperimentSession.day_of_week == original.day_of_week,
            ExperimentSession.start_slot <= original.end_slot,
            ExperimentSession.end_slot >= original.start_slot,
            ExperimentSession.status.notin_(["CANCELLED", "COMPLETED"]),
        )
        .limit(1)
    )
    if occupied:
        conflicts.append("目标实验室在该时间已被占用。")
    return TeacherAdjustmentValidation(
        allowed=not conflicts, can_submit_for_review=not conflicts, conflicts=conflicts
    )


async def validate_substitution(
    session: AsyncSession, *, teacher_id: UUID, body: SubstitutionPreviewRequest
) -> TeacherAdjustmentValidation:
    original = await _teacher_session(
        session, teacher_id=teacher_id, session_id=body.original_session_id
    )
    target = await session.get(Teacher, body.substitute_teacher_id)
    conflicts: list[str] = []
    if target is None or target.status != "ACTIVE":
        conflicts.append("代课教师不存在或当前不可授课。")
    if target and target.id == original.teacher_id:
        conflicts.append("代课教师不能是原任课教师。")
    qualified = await session.scalar(
        select(TeacherProjectQualification.id)
        .where(
            TeacherProjectQualification.teacher_id == body.substitute_teacher_id,
            TeacherProjectQualification.project_id == original.project_id,
            TeacherProjectQualification.status == "ACTIVE",
        )
        .limit(1)
    )
    if qualified is None:
        conflicts.append("代课教师不具备该实验项目授课资格。")
    occupied = await session.scalar(
        select(ExperimentSession.id)
        .where(
            ExperimentSession.schedule_version_id == original.schedule_version_id,
            ExperimentSession.teacher_id == body.substitute_teacher_id,
            ExperimentSession.week_no == original.week_no,
            ExperimentSession.day_of_week == original.day_of_week,
            ExperimentSession.start_slot <= original.end_slot,
            ExperimentSession.end_slot >= original.start_slot,
            ExperimentSession.status.notin_(["CANCELLED", "COMPLETED"]),
        )
        .limit(1)
    )
    if occupied:
        conflicts.append("代课教师在该时间已有实验场次。")
    return TeacherAdjustmentValidation(
        allowed=not conflicts, can_submit_for_review=not conflicts, conflicts=conflicts
    )


async def _apply_session_change(
    session: AsyncSession, *, item: ApplicationRequest, actor_id: UUID
) -> ExperimentSession:
    """执行教师调整的正式变更：修改场次并写入覆盖审计记录。"""
    original = await session.get(ExperimentSession, item.original_session_id)
    if original is None:
        raise LookupError("原实验场次不存在。")
    if item.request_type == "TEACHER_ADJUSTMENT":
        override_type = "TIME"
        before = {
            "week_no": original.week_no,
            "day_of_week": original.day_of_week,
            "start_slot": original.start_slot,
            "end_slot": original.end_slot,
        }
        after = dict(item.payload["target_time"])
        # 直接修改原表，课表实时生效
        original.week_no = after["week_no"]
        original.day_of_week = after["day_of_week"]
        original.start_slot = after["start_slot"]
        original.end_slot = after["end_slot"]
    elif item.request_type == "LAB_CHANGE":
        override_type = "LAB"
        before = {"laboratory_id": str(original.laboratory_id)}
        after = {"laboratory_id": item.payload["target_laboratory_id"]}
        original.laboratory_id = UUID(after["laboratory_id"])
    else:
        override_type = "TEACHER"
        before = {"teacher_id": str(original.teacher_id)}
        after = {"teacher_id": item.payload["substitute_teacher_id"]}
    # 保留覆盖记录作为审计日志
    active = list(
        (
            await session.execute(
                select(SessionExecutionOverride).where(
                    SessionExecutionOverride.session_id == original.id,
                    SessionExecutionOverride.override_type == override_type,
                    SessionExecutionOverride.status == "ACTIVE",
                )
            )
        ).scalars()
    )
    for old in active:
        old.status = "SUPERSEDED"
    session.add(
        SessionExecutionOverride(
            session_id=original.id,
            application_id=item.id,
            override_type=override_type,
            before_snapshot=before,
            after_snapshot=after,
            effective_from=datetime.now(UTC),
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    if item.request_type == "TEACHER_SUBSTITUTION":
        timetable_entry = await session.scalar(
            select(TeacherTimetableEntry).where(
                TeacherTimetableEntry.experiment_session_id == original.id
            )
        )
        if timetable_entry is not None:
            new_tid = UUID(str(item.payload["substitute_teacher_id"]))
            timetable_entry.teacher_id = new_tid
        # 同步更新 ExperimentSession.teacher_id
        original.teacher_id = UUID(str(item.payload["substitute_teacher_id"]))
    return original


async def _notify_teacher_adjustment_result(
    session: AsyncSession, *, item: ApplicationRequest, approved: bool
) -> None:
    """通知申请教师审批/执行结果（代课场景同时通知代课教师）；Redis 故障不影响主流程。"""
    try:
        import json as _json

        from app.db.redis_client import get_redis_client

        redis = get_redis_client()
        label_map = {
            "TEACHER_ADJUSTMENT": "调课",
            "LAB_CHANGE": "场地调整",
            "TEACHER_SUBSTITUTION": "代课",
        }
        label = label_map.get(item.request_type, item.request_type)
        decision_text = "已通过" if approved else "已驳回"
        applicant_msg = f"你的{label}申请{decision_text}"
        if approved and item.request_type == "TEACHER_SUBSTITUTION":
            sub_tid = item.payload.get("substitute_teacher_id")
            if sub_tid:
                substitute = await session.get(Teacher, UUID(str(sub_tid)))
                if substitute is not None:
                    applicant_msg += f"，由{substitute.name}老师代课"
        await redis.lpush(
            f"teacher:{item.applicant_user_id}:notifications",
            _json.dumps(
                {
                    "request_no": item.request_no,
                    "title": f"{label}申请{decision_text}",
                    "msg": applicant_msg,
                    "type": label,
                    "time": datetime.now(UTC).strftime("%m-%d %H:%M"),
                },
                ensure_ascii=False,
            ),
        )
        # 代课申请通过/驳回后通知代课老师
        if item.request_type == "TEACHER_SUBSTITUTION":
            sub_tid = item.payload.get("substitute_teacher_id")
            if sub_tid:
                substitute = await session.get(Teacher, UUID(str(sub_tid)))
                if substitute is not None and substitute.user_id is not None:
                    await redis.lpush(
                        f"teacher:{substitute.user_id}:notifications",
                        _json.dumps(
                            {
                                "request_no": item.request_no,
                                "title": f"代课申请{decision_text}",
                                "msg": f"代课申请{decision_text} · {item.request_no}",
                                "type": "代课",
                                "time": datetime.now(UTC).strftime("%m-%d %H:%M"),
                            },
                            ensure_ascii=False,
                        ),
                    )
    except Exception:
        pass


async def create_teacher_adjustment(
    session: AsyncSession,
    *,
    teacher_id: UUID,
    actor_id: UUID,
    term: AcademicTerm,
    body: TeacherAdjustmentCreateRequest,
) -> ApplicationRequest:
    existing = await session.scalar(
        select(ApplicationRequest).where(
            ApplicationRequest.applicant_user_id == actor_id,
            ApplicationRequest.idempotency_key == body.idempotency_key,
        )
    )
    if existing:
        return existing
    payload: dict[str, object] = {}
    if body.request_type == "TEACHER_ADJUSTMENT":
        if body.target_time is None:
            raise ValueError("调课申请必须提供目标时间。")
        validation = await validate_reschedule(
            session,
            teacher_id=teacher_id,
            term=term,
            original_session_id=body.original_session_id,
            target=body.target_time,
        )
        if not validation.can_submit_for_review:
            raise ValueError("；".join(validation.conflicts))
        payload["target_time"] = body.target_time.model_dump()
    elif body.request_type == "LAB_CHANGE":
        if body.target_laboratory_id is None:
            raise ValueError("场地调整必须选择目标实验室。")
        validation = await validate_lab_change(
            session,
            teacher_id=teacher_id,
            body=LabChangePreviewRequest(
                original_session_id=body.original_session_id,
                target_laboratory_id=body.target_laboratory_id,
            ),
        )
        if not validation.allowed:
            raise ValueError("；".join(validation.conflicts))
        payload["target_laboratory_id"] = str(body.target_laboratory_id)
    else:
        if body.substitute_teacher_id is None:
            raise ValueError("代课申请必须选择代课教师。")
        validation = await validate_substitution(
            session,
            teacher_id=teacher_id,
            body=SubstitutionPreviewRequest(
                original_session_id=body.original_session_id,
                substitute_teacher_id=body.substitute_teacher_id,
            ),
        )
        if not validation.allowed:
            raise ValueError("；".join(validation.conflicts))
        payload["substitute_teacher_id"] = str(body.substitute_teacher_id)
    original = await _teacher_session(
        session, teacher_id=teacher_id, session_id=body.original_session_id
    )
    # 自动批准：调课/场地调整校验通过且不影响他人（无硬冲突、无受影响学生）时直接执行；
    # 代课涉及代课教师本人，一律走审批（代课教师确认 -> 管理员批准）。
    auto_approve = (
        body.request_type != "TEACHER_SUBSTITUTION" and validation.allowed
    )
    item = ApplicationRequest(
        request_no=f"TA-{datetime.now(UTC):%Y%m%d}-{uuid4().hex[:8].upper()}",
        request_type=body.request_type,
        applicant_user_id=actor_id,
        teacher_id=teacher_id,
        project_id=original.project_id,
        original_session_id=original.id,
        reason=body.reason,
        payload=payload,
        validation_result=validation.model_dump(mode="json"),
        approval_route="AUTO"
        if auto_approve
        else (
            "TEACHER_ADMIN"
            if body.request_type == "TEACHER_SUBSTITUTION"
            else "ADMIN"
        ),
        reservation_status="NONE",
        idempotency_key=body.idempotency_key,
        status="APPROVED" if auto_approve else "PENDING_REVIEW",
        submitted_at=datetime.now(UTC),
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(item)
    await session.flush()
    if auto_approve:
        await _apply_session_change(session, item=item, actor_id=actor_id)
        session.add(
            ApprovalRecord(
                application_id=item.id,
                approval_type="AUTO",
                approver_user_id=None,
                decision="APPROVED",
                matched_rules=validation.model_dump(mode="json"),
                comment="确定性规则校验通过，系统自动审批。",
                decided_at=datetime.now(UTC),
            )
        )
        item.status = "EXECUTED"
        item.executed_at = datetime.now(UTC)
    if body.request_type == "TEACHER_SUBSTITUTION":
        substitute = await session.get(Teacher, body.substitute_teacher_id)
        session.add_all(
            [
                ApplicationApprovalTask(
                    application_id=item.id,
                    approver_type="SUBSTITUTE_TEACHER",
                    approver_user_id=substitute.user_id if substitute else None,
                    sequence_no=1,
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
                ApplicationApprovalTask(
                    application_id=item.id,
                    approver_type="ADMIN",
                    sequence_no=2,
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
            ]
        )
    await session.commit()
    await session.refresh(item)

    if auto_approve:
        # 自动执行结果通知申请教师本人
        await _notify_teacher_adjustment_result(
            session, item=item, approved=True
        )
        # 调课改时间影响该场次全部已选学生，刷新其课表视图缓存
        if body.request_type == "TEACHER_ADJUSTMENT":
            try:
                schedule = await session.get(
                    ScheduleVersion, original.schedule_version_id
                )
                if schedule is not None:
                    student_ids = list(
                        (
                            await session.execute(
                                select(StudentProjectRecord.student_id).where(
                                    StudentProjectRecord.session_id == original.id,
                                    StudentProjectRecord.status.in_(
                                        ["SELECTED", "MAKEUP_PENDING"]
                                    ),
                                )
                            )
                        ).scalars()
                    )
                    for student_id in student_ids:
                        await refresh_experiment_views_after_commit(
                            student_id, schedule.term_id
                        )
            except Exception:
                pass
        return item

    # 推送管理员通知（教师发起的调课/场地调整/代课申请）
    try:
        import json as _json

        from app.db.redis_client import get_redis_client

        teacher = await session.get(Teacher, teacher_id)
        teacher_name = teacher.name if teacher else ""
        project_name = (
            original.project.project_name if original.project is not None else ""
        )
        title_map = {
            "TEACHER_ADJUSTMENT": "教师调课申请",
            "LAB_CHANGE": "场地调整申请",
            "TEACHER_SUBSTITUTION": "代课申请",
        }
        type_map = {
            "TEACHER_ADJUSTMENT": "教师调课",
            "LAB_CHANGE": "场地调整",
            "TEACHER_SUBSTITUTION": "代课",
        }
        await get_redis_client().lpush(
            "admin:notifications",
            _json.dumps(
                {
                    "request_no": item.request_no,
                    "title": title_map.get(item.request_type, item.request_type),
                    "msg": f"{teacher_name} · {project_name}",
                    "type": type_map.get(item.request_type, item.request_type),
                    "status": "PENDING_REVIEW",
                    "time": datetime.now(UTC).strftime("%m-%d %H:%M"),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        pass

    return item


async def list_teacher_adjustments(
    session: AsyncSession, *, teacher_id: UUID | None = None
) -> list[ApplicationRequest]:
    stmt = (
        select(ApplicationRequest)
        .where(
            ApplicationRequest.request_type.in_(
                ["TEACHER_ADJUSTMENT", "LAB_CHANGE", "TEACHER_SUBSTITUTION"]
            )
        )
        .order_by(ApplicationRequest.created_at.desc())
    )
    if teacher_id is not None:
        stmt = stmt.where(ApplicationRequest.teacher_id == teacher_id)
    return list((await session.execute(stmt)).scalars())


async def generate_remediation_plans(
    session: AsyncSession,
    *,
    application_id: UUID,
    actor_id: UUID,
    max_plans: int = 3,
) -> list[AdjustmentRemediationPlan]:
    """Generate complete, persisted student relocation plans for a teacher reschedule."""
    application = await session.get(ApplicationRequest, application_id)
    if application is None or application.request_type != "TEACHER_ADJUSTMENT":
        raise LookupError("教师调课申请不存在。")
    if application.status != "PENDING_REVIEW":
        raise ValueError("只有待审批的调课申请可以生成学生安置方案。")
    affected = application.validation_result.get("affected_students") or []
    if not affected:
        return []
    original = await session.get(ExperimentSession, application.original_session_id)
    if original is None:
        raise LookupError("原实验场次不存在。")
    version = await session.get(ScheduleVersion, original.schedule_version_id)
    term = await session.get(AcademicTerm, version.term_id) if version else None
    if term is None:
        raise LookupError("无法确定调课所属学期。")

    choices: list[tuple[UUID, StudentProjectRecord, list[object]]] = []
    for affected_student in affected:
        student_id = UUID(str(affected_student["student_id"]))
        record = await session.scalar(
            select(StudentProjectRecord).where(
                StudentProjectRecord.student_id == student_id,
                StudentProjectRecord.term_id == term.id,
                StudentProjectRecord.session_id == original.id,
                StudentProjectRecord.status.in_(["SELECTED", "MAKEUP_PENDING"]),
            )
        )
        if record is None:
            raise ValueError(
                f"学生 {affected_student.get('name') or affected_student.get('student_no')} 的原实验记录不存在。"
            )
        options = await recommend_adjustment_options(
            session,
            student_id=student_id,
            term=term,
            request_type="RESCHEDULE",
            source_record_id=record.id,
            preferences=SelectionPreferences(),
            max_options=3,
        )
        if not options:
            raise ValueError(
                f"学生 {affected_student.get('name') or affected_student.get('student_no')} 暂无可行安置场次，不能批准该调课。"
            )
        choices.append((student_id, record, list(options)))

    # 删除旧方案，确保 plan_no 从 1 开始
    old_plans = list(
        (
            await session.execute(
                select(AdjustmentRemediationPlan).where(
                    AdjustmentRemediationPlan.application_id == application.id,
                )
            )
        ).scalars()
    )
    for old in old_plans:
        old_items = (
            await session.execute(
                select(AdjustmentRemediationItem).where(
                    AdjustmentRemediationItem.plan_id == old.id,
                )
            )
        ).scalars()
        for item in old_items:
            await session.delete(item)
        await session.delete(old)
    await session.flush()

    generated: list[AdjustmentRemediationPlan] = []
    signatures: set[tuple[str, ...]] = set()
    for variant in range(max_plans * 3):
        selected = [options[variant % len(options)] for _, _, options in choices]
        signature = tuple(str(option.target.session_id) for option in selected)
        if signature in signatures:
            continue
        target_counts: dict[UUID, int] = {}
        target_remaining: dict[UUID, int] = {}
        for option in selected:
            target_id = option.target.session_id
            target_counts[target_id] = target_counts.get(target_id, 0) + 1
            target_remaining[target_id] = option.target.remaining
        if any(
            target_counts[target_id] > target_remaining[target_id]
            for target_id in target_counts
        ):
            continue
        signatures.add(signature)
        plan = AdjustmentRemediationPlan(
            application_id=application.id,
            plan_no=len(generated) + 1,
            status="VALIDATED",
            summary={
                "student_count": len(choices),
                "description": f"覆盖全部 {len(choices)} 名受影响学生",
            },
            validation_result={"complete": True, "student_count": len(choices)},
            created_by=actor_id,
            updated_by=actor_id,
        )
        session.add(plan)
        await session.flush()
        for (student_id, record, _), option in zip(choices, selected, strict=True):
            session.add(
                AdjustmentRemediationItem(
                    plan_id=plan.id,
                    student_id=student_id,
                    original_session_id=original.id,
                    target_session_id=option.target.session_id,
                    reason="避开教师调课后的个人课程或实验冲突",
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
        generated.append(plan)
        if len(generated) >= max_plans:
            break
    if not generated:
        raise ValueError("现有场次容量不足，无法形成覆盖全部受影响学生的安置方案。")
    await session.commit()
    return generated


async def approve_teacher_adjustment(
    session: AsyncSession,
    *,
    application_id: UUID,
    actor_id: UUID,
    approved: bool,
    remediation_plan_id: UUID | None = None,
    comment: str | None = None,
) -> ApplicationRequest:
    item = await session.scalar(
        select(ApplicationRequest)
        .where(ApplicationRequest.id == application_id)
        .with_for_update()
    )
    if item is None or item.request_type not in {
        "TEACHER_ADJUSTMENT",
        "LAB_CHANGE",
        "TEACHER_SUBSTITUTION",
    }:
        raise LookupError("教师调整申请不存在。")
    if item.status in {"EXECUTED", "REJECTED"}:
        return item
    if not approved:
        item.status = "REJECTED"
    else:
        if item.request_type == "TEACHER_SUBSTITUTION":
            teacher_task = await session.scalar(
                select(ApplicationApprovalTask).where(
                    ApplicationApprovalTask.application_id == item.id,
                    ApplicationApprovalTask.approver_type == "SUBSTITUTE_TEACHER",
                )
            )
            if teacher_task is None or teacher_task.status != "APPROVED":
                raise ValueError("代课教师尚未确认，管理员暂时不能批准。")
        if (
            item.request_type == "TEACHER_ADJUSTMENT"
            and item.validation_result.get("affected_students")
            and remediation_plan_id is None
        ):
            raise ValueError("该调课存在受影响学生，必须先选择完整的学生安置方案。")
        original = await _apply_session_change(
            session, item=item, actor_id=actor_id
        )
        remediation_plan: AdjustmentRemediationPlan | None = None
        remediation_items: list[AdjustmentRemediationItem] = []
        remediation_records: list[tuple[StudentProjectRecord, ExperimentSession]] = []
        if item.request_type == "TEACHER_ADJUSTMENT" and item.validation_result.get(
            "affected_students"
        ):
            remediation_plan = await session.scalar(
                select(AdjustmentRemediationPlan)
                .where(
                    AdjustmentRemediationPlan.id == remediation_plan_id,
                    AdjustmentRemediationPlan.application_id == item.id,
                    AdjustmentRemediationPlan.status == "VALIDATED",
                )
                .with_for_update()
            )
            if remediation_plan is None:
                raise ValueError("所选学生安置方案不存在、已失效或不属于当前申请。")
            remediation_items = list(
                (
                    await session.execute(
                        select(AdjustmentRemediationItem).where(
                            AdjustmentRemediationItem.plan_id == remediation_plan.id
                        )
                    )
                ).scalars()
            )
            expected_student_ids = {
                UUID(str(value["student_id"]))
                for value in item.validation_result["affected_students"]
            }
            if {
                value.student_id for value in remediation_items
            } != expected_student_ids:
                raise ValueError("学生安置方案未覆盖全部受影响学生。")
            version = await session.get(ScheduleVersion, original.schedule_version_id)
            term = await session.get(AcademicTerm, version.term_id) if version else None
            if term is None:
                raise LookupError("无法确定调课所属学期。")
            target_ids = {value.target_session_id for value in remediation_items}
            targets = {
                value.id: value
                for value in (
                    await session.execute(
                        select(ExperimentSession)
                        .where(ExperimentSession.id.in_(target_ids))
                        .with_for_update()
                    )
                ).scalars()
            }
            target_counts: dict[UUID, int] = {}
            for remediation_item in remediation_items:
                record = await session.scalar(
                    select(StudentProjectRecord)
                    .where(
                        StudentProjectRecord.student_id == remediation_item.student_id,
                        StudentProjectRecord.term_id == term.id,
                        StudentProjectRecord.session_id == original.id,
                        StudentProjectRecord.status.in_(["SELECTED", "MAKEUP_PENDING"]),
                    )
                    .with_for_update()
                )
                target = targets.get(remediation_item.target_session_id)
                if record is None or target is None:
                    raise ValueError("学生安置方案中的原记录或目标场次已发生变化。")
                validation = await validate_student_adjustment(
                    session,
                    student_id=remediation_item.student_id,
                    term=term,
                    request_type="RESCHEDULE",
                    source_record_id=record.id,
                    target_session_id=target.id,
                    lock_rows=True,
                )
                if not validation.allowed:
                    reasons = "；".join(
                        value.message for value in validation.violations
                    )
                    raise ValueError(f"学生安置方案实时复核未通过：{reasons}")
                target_counts[target.id] = target_counts.get(target.id, 0) + 1
                remediation_records.append((record, target))
            if any(
                targets[target_id].selected_count + count > targets[target_id].capacity
                for target_id, count in target_counts.items()
            ):
                raise ValueError("学生安置目标场次的剩余容量已不足，请重新生成方案。")
        # 审批结果通知：申请教师本人（代课场景即被代的原教师）+ 代课教师
        await _notify_teacher_adjustment_result(
            session, item=item, approved=approved
        )

        if remediation_plan is not None:
            for record, target in remediation_records:
                original.selected_count = max(0, original.selected_count - 1)
                target.selected_count += 1
                record.session_id = target.id
                record.version_no += 1
                record.updated_by = actor_id
                # 通知被迁移的学生
                from json import dumps as _json_dumps

                from app.db.redis_client import get_redis_client
                from app.models.identity import Student as Stu

                stu = await session.get(Stu, record.student_id)
                if stu is not None:
                    proj_name = target.project.project_name if target.project else ""
                    day_names = ["", "周日", "周一", "周二", "周三", "周四", "周五", "周六"]
                    msg = (
                        f"你的实验「{proj_name}」"
                        f"已调整至第{target.week_no}周 {day_names[target.day_of_week]} "
                        f"第{target.start_slot}—{target.end_slot}节"
                    )
                    redis_client = get_redis_client()
                    await redis_client.lpush(
                        f"student:{stu.id}:notifications",
                        _json_dumps(
                            {
                                "request_no": item.request_no,
                                "msg": msg,
                                "time": datetime.now(UTC).strftime("%m-%d %H:%M"),
                            },
                            ensure_ascii=False,
                        ),
                    )
                    pass
            remediation_plan.status = "EXECUTED"
            remediation_plan.updated_by = actor_id
        item.status = "EXECUTED"
        item.executed_at = datetime.now(UTC)
    session.add(
        ApprovalRecord(
            application_id=item.id,
            approval_type="MANUAL",
            approver_user_id=actor_id,
            decision="APPROVED" if approved else "REJECTED",
            matched_rules={},
            comment=comment,
            decided_at=datetime.now(UTC),
        )
    )
    item.updated_by = actor_id
    await session.commit()
    await session.refresh(item)
    if approved and remediation_records:
        schedule = await session.get(ScheduleVersion, original.schedule_version_id)
        if schedule is not None:
            affected_ids = {record.student_id for record, _ in remediation_records}
            for affected_student_id in affected_ids:
                await refresh_experiment_views_after_commit(
                    affected_student_id, schedule.term_id
                )
    return item


async def confirm_substitution(
    session: AsyncSession,
    *,
    application_id: UUID,
    actor_id: UUID,
    approved: bool,
) -> ApplicationRequest:
    task = await session.scalar(
        select(ApplicationApprovalTask)
        .where(
            ApplicationApprovalTask.application_id == application_id,
            ApplicationApprovalTask.approver_type == "SUBSTITUTE_TEACHER",
            ApplicationApprovalTask.approver_user_id == actor_id,
        )
        .with_for_update()
    )
    if task is None:
        raise LookupError("没有需要当前教师确认的代课申请。")
    if task.status != "PENDING":
        return await session.get(ApplicationRequest, application_id)
    task.status = "APPROVED" if approved else "REJECTED"
    task.decided_at = datetime.now(UTC)
    item = await session.get(ApplicationRequest, application_id)
    if not approved:
        item.status = "REJECTED"
    await session.commit()

    # 通知原申请教师：代课教师已确认/已拒绝
    try:
        import json as _json

        from app.db.redis_client import get_redis_client

        if approved:
            decision_msg = "代课教师已确认代课，待管理员审批"
        else:
            decision_msg = "代课教师已拒绝你的代课申请"
        await get_redis_client().lpush(
            f"teacher:{item.applicant_user_id}:notifications",
            _json.dumps(
                {
                    "request_no": item.request_no,
                    "title": "代课确认反馈",
                    "msg": decision_msg,
                    "type": "代课",
                    "time": datetime.now(UTC).strftime("%m-%d %H:%M"),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        pass

    return item


async def create_resource_issue(
    session: AsyncSession,
    *,
    teacher_id: UUID,
    actor_id: UUID,
    body: ResourceIssueCreateRequest,
) -> ResourceIssueReport:
    inventory = await session.get(LabEquipmentInventory, body.inventory_id)
    if inventory is None or inventory.laboratory_id != body.laboratory_id:
        raise ValueError("所选仪器不属于该实验室。")
    if body.affected_quantity > inventory.usable_quantity:
        raise ValueError("异常数量超过当前可用数量。")
    item = ResourceIssueReport(
        report_no=f"RI-{datetime.now(UTC):%Y%m%d}-{uuid4().hex[:8].upper()}",
        reporter_teacher_id=teacher_id,
        issue_type=body.issue_type,
        laboratory_id=body.laboratory_id,
        equipment_type_id=inventory.equipment_type_id,
        inventory_id=inventory.id,
        affected_quantity=body.affected_quantity,
        impact_start=body.impact_start,
        impact_end=body.impact_end,
        severity=body.severity,
        description=body.description,
        status="PENDING_REVIEW",
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    # 推送管理员通知（资源异常上报）
    try:
        import json as _json

        from app.db.redis_client import get_redis_client

        teacher = await session.get(Teacher, teacher_id)
        teacher_name = teacher.name if teacher else ""
        await get_redis_client().lpush(
            "admin:notifications",
            _json.dumps(
                {
                    "request_no": item.report_no,
                    "title": "资源异常上报",
                    "msg": f"{teacher_name}上报资源异常",
                    "type": "资源异常",
                    "status": "PENDING_REVIEW",
                    "time": datetime.now(UTC).strftime("%m-%d %H:%M"),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        pass

    return item


async def resource_impact_summary(
    session: AsyncSession, *, issue: ResourceIssueReport
) -> dict[str, object]:
    """Lightweight impact stats for terminal issues (RESOLVED/CLOSED/SCRAPPED).

    Counts affected sessions/courses/selected students with one aggregate SQL
    instead of the full resource_impact scan (0.3-0.9s per call). Shortage is
    always False: terminal issues no longer reduce capacity.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT COUNT(DISTINCT s.id)::int AS sessions,
                       COUNT(DISTINCT p.course_id)::int AS courses,
                       COALESCE(SUM(s.selected_count), 0)::int AS students
                FROM experiment_session s
                JOIN schedule_version v
                  ON v.id = s.schedule_version_id
                 AND v.status IN ('PUBLISHED', 'DRAFT', 'CANDIDATE')
                JOIN academic_term t ON t.id = v.term_id
                JOIN experiment_project p ON p.id = s.project_id
                JOIN project_equipment_requirement r
                  ON r.project_id = p.id AND r.required
                WHERE s.laboratory_id = :lab
                  AND s.status IN ('OPEN', 'FULL', 'DRAFT')
                  AND r.equipment_type_id = :eqtype
                  AND (t.start_date - EXTRACT(DOW FROM t.start_date)::int
                       + (s.week_no - 1) * 7 + (s.day_of_week - 1))::date
                      BETWEEN :start AND :end
                """
            ),
            {
                "lab": issue.laboratory_id,
                "eqtype": issue.equipment_type_id,
                "start": issue.impact_start.date(),
                "end": issue.impact_end.date(),
            },
        )
    ).first()
    sessions, courses, students = row
    return {
        "known": True,
        "shortage": False,
        "available": 0,
        "required": 0,
        "total_required_relocation_count": 0,
        "course_count": int(courses or 0),
        "session_count": int(sessions or 0),
        "student_count": int(students or 0),
        "affected_sessions": [],
    }


async def resource_impact(
    session: AsyncSession,
    issue: ResourceIssueReport,
    *,
    include_sessions: bool = True,
    pending_deduction: bool = False,
    skip_cache: bool = False,
) -> dict[str, object]:
    variant = "full" if include_sessions else "lite"
    cached = await get_cached_resource_impact(issue.id, issue.status, variant)
    if cached is not None and not (pending_deduction or skip_cache):
        return cached
    inventory = await session.get(LabEquipmentInventory, issue.inventory_id)
    if inventory is None:
        return {
            "shortage": True,
            "available": 0,
            "required": 0,
            "affected_sessions": [],
        }
    rows = list(
        (
            await session.execute(
                select(ExperimentSession, AcademicTerm, ScheduleVersion)
                .join(
                    ScheduleVersion,
                    ScheduleVersion.id == ExperimentSession.schedule_version_id,
                )
                .join(AcademicTerm, AcademicTerm.id == ScheduleVersion.term_id)
                .join(
                    ProjectEquipmentRequirement,
                    ProjectEquipmentRequirement.project_id
                    == ExperimentSession.project_id,
                )
                .where(
                    ScheduleVersion.status.in_({"PUBLISHED", "DRAFT", "CANDIDATE"}),
                    ExperimentSession.laboratory_id == issue.laboratory_id,
                    ExperimentSession.status.in_(["OPEN", "FULL", "DRAFT"]),
                    ProjectEquipmentRequirement.equipment_type_id
                    == issue.equipment_type_id,
                    ProjectEquipmentRequirement.required.is_(True),
                )
            )
        ).all()
    )
    affected: list[dict[str, object]] = []
    total_required = 0
    max_selected = 0
    affected_course_ids: set[UUID] = set()
    affected_session_count = 0
    known = True
    asset_backed = await asset_svc.asset_for_issue(session, issue.id) is not None
    draft_capacity_warnings: list[dict[str, object]] = []

    # 批量预加载：一次查询取回循环内所需的所有 project/course/capability/
    # inventory/requirement/student 数据，避免每个场次逐条查询（原实现
    # 1978 个场次 ≈ 1.6 万次 DB roundtrip，接口耗时 8.5s+）。
    project_ids = {item.project_id for item, _term, _schedule_version in rows}
    laboratory = await session.get(Laboratory, issue.laboratory_id)
    capability_by_project = {
        capability.project_id: capability
        for capability in (
            await session.execute(
                select(LabProjectCapability).where(
                    LabProjectCapability.project_id.in_(project_ids),
                    LabProjectCapability.laboratory_id == issue.laboratory_id,
                    LabProjectCapability.status == "ACTIVE",
                )
            )
        ).scalars()
    }
    inventories_by_type = {
        item.equipment_type_id: item
        for item in (
            await session.execute(
                select(LabEquipmentInventory).where(
                    LabEquipmentInventory.laboratory_id == issue.laboratory_id
                )
            )
        ).scalars()
    }
    requirement_pairs_by_project: dict[UUID, list[tuple[ProjectEquipmentRequirement, EquipmentType]]] = {}
    for requirement, equipment_type in (
        await session.execute(
            select(ProjectEquipmentRequirement, EquipmentType)
            .join(
                EquipmentType,
                EquipmentType.id == ProjectEquipmentRequirement.equipment_type_id,
            )
            .where(
                ProjectEquipmentRequirement.project_id.in_(project_ids),
                ProjectEquipmentRequirement.required.is_(True),
            )
        )
    ).all():
        requirement_pairs_by_project.setdefault(
            requirement.project_id, []
        ).append((requirement, equipment_type))
    projects = {
        project.id: project
        for project in (
            await session.execute(
                select(ExperimentProject).where(ExperimentProject.id.in_(project_ids))
            )
        ).scalars()
    }
    course_ids = {
        project.course_id for project in projects.values() if project.course_id
    }
    courses: dict[UUID, ExperimentCourse] = {}
    if course_ids:
        courses = {
            course.id: course
            for course in (
                await session.execute(
                    select(ExperimentCourse).where(
                        ExperimentCourse.id.in_(course_ids)
                    )
                )
            ).scalars()
        }
    dated_session_ids = [
        item.id
        for item, term, _schedule_version in rows
        if issue.impact_start.date()
        <= session_calendar_date(term, item)
        <= issue.impact_end.date()
    ]
    students_by_session: dict[UUID, list[Student]] = {}
    if dated_session_ids and include_sessions:
        for student, session_id in (
            await session.execute(
                select(Student, StudentProjectRecord.session_id)
                .join(
                    StudentProjectRecord,
                    StudentProjectRecord.student_id == Student.id,
                )
                .where(
                    StudentProjectRecord.session_id.in_(dated_session_ids),
                    StudentProjectRecord.status.in_({"SELECTED", "MAKEUP_PENDING"}),
                )
                .order_by(Student.student_no, Student.id)
            )
        ).all():
            students_by_session.setdefault(session_id, []).append(student)

    for item, term, schedule_version in rows:
        if not (
            issue.impact_start.date()
            <= session_calendar_date(term, item)
            <= issue.impact_end.date()
        ):
            continue
        capacity = _session_capacity_loaded(
            project=projects.get(item.project_id),
            laboratory=laboratory,
            capability=capability_by_project.get(item.project_id),
            inventories_by_type=inventories_by_type,
            requirement_pairs=requirement_pairs_by_project.get(item.project_id, []),
            issue=issue,
            # 资产上报不隔离（审批通过才扣减）；pending_deduction 时预扣
            # 本台（如报废审批/生成方案需按"扣后容量"判断是否触发分流）。
            project_pending_issue=(not asset_backed) or pending_deduction,
            pending_deduction=pending_deduction,
        )
        requirement = relocation_requirement(
            item.selected_count, int(capacity["effective_capacity"])
        )
        known = known and bool(capacity["known"])
        capacity_deficit = int(requirement["required_relocation_count"])
        if schedule_version.status == "PUBLISHED":
            total_required += capacity_deficit
        elif capacity_deficit > 0:
            draft_capacity_warnings.append(
                {
                    "schedule_version_id": str(schedule_version.id),
                    "session_id": str(item.id),
                    "capacity_deficit": capacity_deficit,
                }
            )
        project = projects.get(item.project_id)
        course = courses.get(project.course_id) if project else None
        max_selected = max(max_selected, item.selected_count or 0)
        # 只统计有实际选课学生的场次：空场次不影响教学，避免
        # "1 门课程 · 2000+ 个场次"的误导性统计
        if (item.selected_count or 0) > 0:
            affected_session_count += 1
        if course is not None:
            affected_course_ids.add(course.id)
        if include_sessions:
            student_rows = students_by_session.get(item.id, [])
            affected.append(
                {
                    "session_id": str(item.id),
                    "session_code": item.session_code,
                    "project_id": str(item.project_id),
                    "project_name": project.project_name if project else "",
                    "course_id": str(course.id) if course else None,
                    "course_name": course.course_name if course else "",
                    "week_no": item.week_no,
                    "day_of_week": item.day_of_week,
                    "start_slot": item.start_slot,
                    "end_slot": item.end_slot,
                    **requirement,
                    "required_relocation_count": (
                        capacity_deficit if schedule_version.status == "PUBLISHED" else 0
                    ),
                    "schedule_version_status": schedule_version.status,
                    "requires_draft_revalidation": (
                        schedule_version.status != "PUBLISHED" and capacity_deficit > 0
                    ),
                    "students": [
                        {"student_id": str(student.id), "student_no": student.student_no,
                         "student_name": student.name}
                        for student in student_rows
                    ],
                    "capacity_snapshot": capacity,
                }
            )
    result: dict[str, object] = {
        "shortage": total_required > 0,
        "known": known,
        "available": inventory.usable_quantity,
        "required": max_selected,
        "total_required_relocation_count": total_required,
        "course_count": len(affected_course_ids),
        "session_count": affected_session_count,
        "draft_capacity_warnings": draft_capacity_warnings,
        "affected_sessions": affected,
    }
    await cache_resource_impact(issue.id, issue.status, result, variant)
    return result


def _session_capacity_loaded(
    *,
    project: ExperimentProject | None,
    laboratory: Laboratory | None,
    capability: LabProjectCapability | None,
    inventories_by_type: dict[UUID, LabEquipmentInventory],
    requirement_pairs: list[tuple[ProjectEquipmentRequirement, EquipmentType]],
    issue: ResourceIssueReport,
    project_pending_issue: bool,
    pending_deduction: bool = False,
) -> dict[str, object]:
    """批量预加载版的场次容量计算，逻辑与 calculate_session_resource_capacity 等价。

    原函数对每个场次重复查询 project/laboratory/capability/requirement/inventory，
    这里全部改由调用方一次预加载后传入，循环内零查询。
    """

    if project is None or laboratory is None or capability is None:
        return {
            "known": False,
            "effective_capacity": 0,
            "warnings": ["实验室或项目能力配置不完整，无法确认有效容量。"],
            "equipment": [],
        }

    warnings: list[str] = []
    equipment_details: list[dict[str, object]] = []
    capacities = [laboratory.safety_capacity, capability.effective_capacity]
    known = True
    for requirement, equipment_type in requirement_pairs:
        inventory = inventories_by_type.get(requirement.equipment_type_id)
        if inventory is None:
            known = False
            capacity = 0
            multiplier = project.default_group_size
            usable = 0
            status = "MISSING"
            warnings.append(f"实验室未配置必需仪器“{equipment_type.name}”。")
        else:
            usable = inventory.usable_quantity
            if (
                issue is not None
                and project_pending_issue
                # pending_deduction（报废审批/生成方案预扣本台）不受状态限制；
                # 普通预扣仅在上报/待审阶段，避免 PROCESSING 后重复扣减
                and (pending_deduction or issue.status in {"REPORTED", "PENDING_REVIEW"})
                and inventory.id == issue.inventory_id
            ):
                usable = max(0, usable - issue.affected_quantity)
            multiplier = (
                inventory.students_per_unit
                if inventory.sharing_rule_status == "CONFIRMED"
                and inventory.students_per_unit
                else 1
            )
            status = inventory.sharing_rule_status
            if inventory.usage_note and status != "CONFIRMED":
                known = False
                warnings.append(
                    f"仪器“{equipment_type.name}”的使用备注尚未确认，不能据此自动生成迁移方案。"
                )
            units = max(1, requirement.units_per_group)
            capacity = equipment_student_capacity(
                usable_quantity=usable,
                units_per_group=units,
                students_per_unit=multiplier,
            )
        capacities.append(capacity)
        equipment_details.append(
            {
                "equipment_type_id": str(requirement.equipment_type_id),
                "equipment_name": equipment_type.name,
                "usable_quantity": usable,
                "units_per_group": max(1, requirement.units_per_group),
                "students_per_unit": multiplier,
                "sharing_rule_status": status,
                "capacity": capacity,
            }
        )

    effective = minimum_resource_capacity(
        laboratory_capacity=laboratory.safety_capacity,
        capability_capacity=capability.effective_capacity,
        equipment_capacities=capacities[2:],
        group_size=project.default_group_size,
        group_mode=project.group_mode,
    )
    return {
        "known": known,
        "effective_capacity": max(0, effective),
        "laboratory_capacity": laboratory.safety_capacity,
        "project_capability_capacity": capability.effective_capacity,
        "equipment": equipment_details,
        "warnings": list(dict.fromkeys(warnings)),
    }


async def review_resource_issue(
    session: AsyncSession,
    *,
    issue_id: UUID,
    actor_id: UUID,
    approved: bool,
    approved_quantity: int | None,
) -> tuple[ResourceIssueReport, dict[str, object]]:
    issue = await session.scalar(
        select(ResourceIssueReport)
        .where(ResourceIssueReport.id == issue_id)
        .with_for_update()
    )
    if issue is None:
        raise LookupError("资源异常记录不存在。")

    async def _notify_reporter(result_label: str) -> None:
        """通知上报教师审核结果；Redis 故障不影响主流程。"""

        try:
            import json as _json

            from app.db.redis_client import get_redis_client

            if issue.reporter_teacher_id is None:
                return
            teacher = await session.get(Teacher, issue.reporter_teacher_id)
            if teacher is None or teacher.user_id is None:
                return
            await get_redis_client().lpush(
                f"teacher:{teacher.user_id}:notifications",
                _json.dumps(
                    {
                        "request_no": issue.report_no,
                        "title": "资源异常审核结果",
                        "msg": f"你的资源异常上报（{issue.report_no}）{result_label}",
                        "type": "资源异常",
                        "time": datetime.now(UTC).strftime("%m-%d %H:%M"),
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception:
            pass

    asset_row = await asset_svc.asset_for_issue(session, issue.id)
    if asset_row is not None:
        # 锁定库存行，串行化多台故障的审批扣减：避免并发批准时各自读到
        # 未扣减的 usable_quantity，导致累计缺口无人认领（全部进检修）。
        if getattr(issue, "inventory_id", None) is not None:
            await session.execute(
                select(LabEquipmentInventory.id)
                .where(LabEquipmentInventory.id == issue.inventory_id)
                .with_for_update()
            )
        asset, link = asset_row
        if issue.status in {"RESOLVED", "REJECTED", "SCRAPPED"}:
            return issue, await resource_impact(session, issue)
        if not approved:
            await asset_svc.restore_or_transition_issue_asset(
                session, issue, actor_id=actor_id,
                target_status=link.previous_status, close_link=True,
            )
            issue.status = "REJECTED"; issue.approved_by = actor_id; issue.approved_at = datetime.now(UTC)
            if issue.issue_type == "EQUIPMENT_SCRAP" and issue.source_issue_id:
                source_issue = await session.get(ResourceIssueReport, issue.source_issue_id)
                source_link = await session.scalar(
                    select(ResourceIssueAsset).where(
                        ResourceIssueAsset.resource_issue_id == issue.source_issue_id
                    )
                )
                if source_issue is not None and source_link is not None:
                    source_link.active = True
                    source_issue.status = (
                        "PROCESSING" if link.previous_status == "UNDER_REPAIR"
                        else "PENDING_REVIEW"
                    )
            await session.commit()
            await _notify_reporter("未通过")
            return issue, {"shortage": False, "affected_sessions": []}
        if issue.issue_type == "EQUIPMENT_SCRAP":
            issue.approved_quantity = 1
            # 预扣本台后判断容量（与故障"审批即扣减"一致）：
            # 23 台报 4 次报废，前 3 次扣后仍够直接报废，第 4 次扣后 19 < 20 才触发分流。
            impact = await resource_impact(session, issue, pending_deduction=True)
            if impact.get("shortage"):
                issue.status = "RELOCATION_REQUIRED"
                issue.remediation_status = "REMEDIATION_REQUIRED"
                issue.approved_by = actor_id; issue.approved_at = datetime.now(UTC)
                await session.commit()
                await _notify_reporter("通过，等待资源调拨")
                return issue, impact
            await asset_svc.restore_or_transition_issue_asset(
                session, issue, actor_id=actor_id,
                target_status="SCRAPPED", close_link=True,
            )
            issue.status = "SCRAPPED"; issue.remediation_status = "REMEDIATED"
            issue.approved_by = actor_id; issue.approved_at = datetime.now(UTC); issue.resolved_at = datetime.now(UTC)
            if issue.source_issue_id:
                source_issue = await session.get(ResourceIssueReport, issue.source_issue_id)
                if source_issue is not None:
                    source_issue.status = "CLOSED"
                    source_issue.resolved_at = datetime.now(UTC)
            await session.commit()
            await _notify_reporter("通过，已报废处理")
            return issue, impact
        await asset_svc.restore_or_transition_issue_asset(
            session, issue, actor_id=actor_id,
            target_status="UNDER_REPAIR", close_link=False,
        )
        issue.approved_quantity = 1
        issue.approved_by = actor_id; issue.approved_at = datetime.now(UTC)
        # restore 已扣减：绕过缓存重算，避免命中"扣减前"的 PENDING_REVIEW 缓存
        impact = await resource_impact(session, issue, skip_cache=True)
        if impact.get("shortage"):
            # 容量不足：审批未完成，先进入待分流，分流方案生成后才转检修
            issue.status = "RELOCATION_REQUIRED"
            issue.remediation_status = "REMEDIATION_REQUIRED"
            await session.commit()
            await _notify_reporter("通过，等待学生分流方案")
            return issue, impact
        issue.status = "PROCESSING"
        issue.remediation_status = "NOT_REQUIRED"
        await session.commit()
        await _notify_reporter("通过，已转维修处理")
        return issue, impact
    if issue.status in {"PROCESSING", "RESOLVED", "REJECTED"}:
        return issue, await resource_impact(session, issue)
    if not approved:
        issue.status = "REJECTED"
        issue.approved_by = actor_id
        issue.approved_at = datetime.now(UTC)
        await session.commit()
        await _notify_reporter("未通过")
        return issue, {"shortage": False, "affected_sessions": []}
    quantity = approved_quantity or issue.affected_quantity
    if quantity > issue.affected_quantity:
        raise ValueError("批准停用数量不能超过上报异常数量。")
    inventory = await session.scalar(
        select(LabEquipmentInventory)
        .where(LabEquipmentInventory.id == issue.inventory_id)
        .with_for_update()
    )
    if inventory is None:
        raise LookupError("资源台账不存在。")
    if inventory.usable_quantity < quantity:
        raise ValueError("当前可用数量不足，无法执行本次停用。")
    movement_key = f"resource-issue:{issue.id}:disable"
    existing = await session.scalar(
        select(EquipmentInventoryMovement).where(
            EquipmentInventoryMovement.idempotency_key == movement_key
        )
    )
    if existing is None:
        before = {
            "usable_quantity": inventory.usable_quantity,
            "disabled_quantity": inventory.disabled_quantity,
            "total_quantity": inventory.total_quantity,
        }
        inventory.usable_quantity -= quantity
        inventory.disabled_quantity += quantity
        after = {
            "usable_quantity": inventory.usable_quantity,
            "disabled_quantity": inventory.disabled_quantity,
            "total_quantity": inventory.total_quantity,
        }
        session.add(
            EquipmentInventoryMovement(
                inventory_id=inventory.id,
                resource_issue_id=issue.id,
                movement_type="ISSUE_DISABLE",
                quantity=quantity,
                before_snapshot=before,
                after_snapshot=after,
                idempotency_key=movement_key,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
    issue.approved_quantity = quantity
    issue.approved_by = actor_id
    issue.approved_at = datetime.now(UTC)
    issue.updated_by = actor_id
    await session.flush()
    impact = await resource_impact(session, issue)
    if impact.get("shortage"):
        # 容量不足：审批未完成，先进入待分流，分流方案生成后才转检修
        issue.status = "RELOCATION_REQUIRED"
        issue.remediation_status = "REMEDIATION_REQUIRED"
        await session.commit()
        await _notify_reporter("通过，等待学生分流方案")
        return issue, impact
    issue.status = "PROCESSING"
    issue.remediation_status = "NOT_REQUIRED"
    await session.commit()
    await _notify_reporter("通过，已转维修处理")
    return issue, impact


async def create_repair_update(
    session: AsyncSession,
    *,
    issue_id: UUID,
    actor_id: UUID,
    body: RepairUpdateCreateRequest,
) -> ResourceRepairUpdate:
    issue = await session.get(ResourceIssueReport, issue_id)
    if issue is None or issue.status != "PROCESSING":
        raise ValueError("只有处理中资源异常可以报备检修进展。")
    outstanding = issue.approved_quantity - issue.restored_quantity
    restored = body.restored_quantity
    if body.update_type == "COMPLETE_RESTORE":
        restored = outstanding
    if body.update_type == "EXTEND_REPAIR":
        if body.proposed_end_time is None or body.proposed_end_time <= issue.impact_end:
            raise ValueError("延长检修必须提供晚于原预计时间的新完成时间。")
        restored = 0
    elif restored <= 0 or restored > outstanding:
        raise ValueError("恢复数量必须大于0且不能超过尚未恢复数量。")
    item = ResourceRepairUpdate(
        resource_issue_id=issue.id,
        update_type=body.update_type,
        restored_quantity=restored,
        proposed_end_time=body.proposed_end_time,
        note=body.note,
        approval_status="PENDING",
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def review_repair_update(
    session: AsyncSession,
    *,
    update_id: UUID,
    actor_id: UUID,
    approved: bool,
) -> tuple[ResourceRepairUpdate, ResourceIssueReport]:
    update = await session.scalar(
        select(ResourceRepairUpdate)
        .where(ResourceRepairUpdate.id == update_id)
        .with_for_update()
    )
    if update is None:
        raise LookupError("检修更新不存在。")
    issue = await session.scalar(
        select(ResourceIssueReport)
        .where(ResourceIssueReport.id == update.resource_issue_id)
        .with_for_update()
    )
    if update.approval_status != "PENDING":
        return update, issue
    update.approval_status = "APPROVED" if approved else "REJECTED"
    update.approved_by = actor_id
    update.approved_at = datetime.now(UTC)
    if approved and update.update_type == "EXTEND_REPAIR":
        issue.impact_end = update.proposed_end_time
    elif approved:
        asset_row = await asset_svc.asset_for_issue(session, issue.id)
        if asset_row is not None:
            outstanding = issue.approved_quantity - issue.restored_quantity
            quantity = min(update.restored_quantity, outstanding)
            issue.restored_quantity += quantity
            if issue.restored_quantity >= issue.approved_quantity:
                await asset_svc.restore_or_transition_issue_asset(
                    session, issue, actor_id=actor_id,
                    target_status="AVAILABLE", close_link=True,
                )
                issue.status = "RESOLVED"
                issue.resolved_at = datetime.now(UTC)
        else:
            await _restore_legacy_inventory_for_update(
                session, issue=issue, update=update, actor_id=actor_id
            )
    if approved:
        stale_plans = list(
            (
                await session.execute(
                    select(ResourceRelocationPlan).where(
                        ResourceRelocationPlan.resource_issue_id == issue.id,
                        ResourceRelocationPlan.status == "VALIDATED",
                    )
                )
            ).scalars()
        )
        for plan in stale_plans:
            plan.status = "STALE"
            plan.updated_by = actor_id
        impact = await resource_impact(session, issue)
        issue.remediation_status = (
            "REMEDIATION_REQUIRED"
            if impact.get("shortage")
            else "REMEDIATED"
        )
        if update.update_type == "EXTEND_REPAIR":
            from json import dumps as _json_dumps

            from app.db.redis_client import get_redis_client

            await get_redis_client().lpush(
                "admin:notifications",
                _json_dumps(
                    {
                        "request_no": issue.report_no,
                        "msg": (
                            f"资源异常 {issue.report_no} 检修延期已批准，"
                            "请确认受影响场次的学生迁移方案"
                        ),
                        "time": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
                    }
                ),
            )

    # 通知上报教师检修更新审核结果
    try:
        import json as _json

        from app.db.redis_client import get_redis_client

        if issue.reporter_teacher_id is not None:
            teacher = await session.get(Teacher, issue.reporter_teacher_id)
            if teacher is not None and teacher.user_id is not None:
                decision_text = "已批准" if approved else "未通过"
                update_label = (
                    "检修延期" if update.update_type == "EXTEND_REPAIR" else "检修完成"
                )
                await get_redis_client().lpush(
                    f"teacher:{teacher.user_id}:notifications",
                    _json.dumps(
                        {
                            "request_no": issue.report_no,
                            "title": f"{update_label}审核结果",
                            "msg": (
                                f"你的资源异常（{issue.report_no}）"
                                f"{update_label}审核{decision_text}"
                            ),
                            "type": "资源异常",
                            "time": datetime.now(UTC).strftime("%m-%d %H:%M"),
                        },
                        ensure_ascii=False,
                    ),
                )
    except Exception:
        pass

    await session.commit()
    return update, issue


async def _restore_legacy_inventory_for_update(
    session: AsyncSession, *, issue: ResourceIssueReport,
    update: ResourceRepairUpdate, actor_id: UUID,
) -> None:
        inventory = await session.scalar(
            select(LabEquipmentInventory)
            .where(LabEquipmentInventory.id == issue.inventory_id)
            .with_for_update()
        )
        if inventory is None:
            raise LookupError("资源台账不存在。")
        outstanding = issue.approved_quantity - issue.restored_quantity
        quantity = min(update.restored_quantity, outstanding)
        if inventory.disabled_quantity < quantity:
            raise ValueError("台账停用数量不足，不能执行恢复。")
        key = f"repair-update:{update.id}:restore"
        if (
            await session.scalar(
                select(EquipmentInventoryMovement.id).where(
                    EquipmentInventoryMovement.idempotency_key == key
                )
            )
            is None
        ):
            before = {
                "usable_quantity": inventory.usable_quantity,
                "disabled_quantity": inventory.disabled_quantity,
                "total_quantity": inventory.total_quantity,
            }
            inventory.disabled_quantity -= quantity
            inventory.usable_quantity += quantity
            after = {
                "usable_quantity": inventory.usable_quantity,
                "disabled_quantity": inventory.disabled_quantity,
                "total_quantity": inventory.total_quantity,
            }
            session.add(
                EquipmentInventoryMovement(
                    inventory_id=inventory.id,
                    resource_issue_id=issue.id,
                    repair_update_id=update.id,
                    movement_type="REPAIR_RESTORE",
                    quantity=quantity,
                    before_snapshot=before,
                    after_snapshot=after,
                    idempotency_key=key,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            issue.restored_quantity += quantity
        await invalidate_resource_impact_cache(issue.id, issue.status)
        if issue.restored_quantity >= issue.approved_quantity:
            issue.status = "RESOLVED"
            issue.resolved_at = datetime.now(UTC)


async def auto_extend_overdue_issues(
    session: AsyncSession,
    *,
    actor_id: UUID | None = None,
) -> int:
    """Scan PROCESSING issues past impact_end and auto-extend by 7 days.

    Returns the number of issues processed.
    """
    from json import dumps as _json_dumps

    from app.db.redis_client import get_redis_client

    now = datetime.now(UTC)
    rows = list(
        (
            await session.execute(
                select(ResourceIssueReport)
                .where(
                    ResourceIssueReport.status == "PROCESSING",
                    ResourceIssueReport.impact_end < now,
                )
                .with_for_update()
            )
        ).scalars()
    )
    if not rows:
        return 0

    redis = get_redis_client()
    count = 0
    for issue in rows:
        issue.impact_end = now + timedelta(days=7)
        issue.updated_by = actor_id
        await invalidate_resource_impact_cache(issue.id, issue.status)

        impact = await resource_impact(session, issue)
        issue.remediation_status = (
            "REMEDIATION_REQUIRED"
            if impact.get("shortage")
            else "REMEDIATED"
        )

        # Invalidate stale plans
        stale = (
            await session.execute(
                select(ResourceRelocationPlan).where(
                    ResourceRelocationPlan.resource_issue_id == issue.id,
                    ResourceRelocationPlan.status == "VALIDATED",
                )
            )
        ).scalars()
        for plan in stale:
            plan.status = "STALE"

        msg = (
            f"资源异常 {issue.report_no} 已超期，自动延长一周。"
            "如需变更请手动提交延期报备。"
        )
        await redis.lpush(
            "admin:notifications",
            _json_dumps(
                {
                    "request_no": issue.report_no,
                    "title": "资源检修自动延期",
                    "msg": msg,
                    "type": "资源异常",
                    "time": now.strftime("%m-%d %H:%M"),
                },
                ensure_ascii=False,
            ),
        )
        if issue.reporter_teacher_id is not None:
            teacher = await session.get(Teacher, issue.reporter_teacher_id)
            if teacher is not None and teacher.user_id is not None:
                await redis.lpush(
                    f"teacher:{teacher.user_id}:notifications",
                    _json_dumps(
                        {
                            "request_no": issue.report_no,
                            "title": "资源检修自动延期",
                            "msg": msg,
                            "type": "资源异常",
                            "time": now.strftime("%m-%d %H:%M"),
                        },
                        ensure_ascii=False,
                    ),
                )
        count += 1

    await session.commit()
    return count


async def periodic_resource_issue_overdue_scan() -> None:
    """Scan PROCESSING resource issues past impact_end and auto-extend them.

    Runs periodically in the app lifespan; no admin action required.
    """

    settings = get_settings()
    while True:
        try:
            async with AsyncSessionFactory() as session:
                await auto_extend_overdue_issues(session, actor_id=None)
            await asyncio.sleep(settings.resource_issue_overdue_scan_seconds)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning(
                "Periodic resource-issue overdue scan failed", exc_info=True
            )
            await asyncio.sleep(60)
