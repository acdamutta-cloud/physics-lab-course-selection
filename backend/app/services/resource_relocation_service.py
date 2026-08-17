from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.application import (
    AdjustmentExecutionAudit,
    ApplicationRequest,
    ApprovalRecord,
)
from app.models.curriculum import AcademicTerm
from app.models.enrollment import StudentProjectRecord
from app.models.identity import Student
from app.models.resources import ResourceIssueReport
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.models.teaching_adjustment import (
    ResourceRelocationItem,
    ResourceRelocationPlan,
)
from app.schemas.student_consultation import SelectionPreferences
from app.schemas.teacher_adjustment import ResourceRelocationSelection
from app.services.resource_capacity_service import (
    calculate_session_resource_capacity,
)
from app.services.student_adjustment_service import (
    recommend_adjustment_options,
    validate_student_adjustment,
)
from app.services.teacher_adjustment_service import resource_impact


async def _term_for_session(
    session: AsyncSession, item: ExperimentSession
) -> AcademicTerm:
    version = await session.get(ScheduleVersion, item.schedule_version_id)
    term = await session.get(AcademicTerm, version.term_id) if version else None
    if term is None:
        raise LookupError("无法确定受影响场次所属学期。")
    return term


async def _target_is_safe(
    session: AsyncSession,
    *,
    issue: ResourceIssueReport,
    target_id: UUID,
    additions: int,
) -> bool:
    target = await session.get(ExperimentSession, target_id)
    if target is None:
        return False
    if target.laboratory_id != issue.laboratory_id:
        return target.selected_count + additions <= target.capacity
    capacity = await calculate_session_resource_capacity(session, target, issue=issue)
    return bool(capacity["known"]) and (
        target.selected_count + additions <= int(capacity["effective_capacity"])
    )


async def generate_resource_relocation_plans(
    session: AsyncSession,
    *,
    issue_id: UUID,
    actor_id: UUID,
    preferences: SelectionPreferences,
    max_plans: int = 3,
) -> list[ResourceRelocationPlan]:
    issue = await session.get(ResourceIssueReport, issue_id)
    if issue is None:
        raise LookupError("资源异常记录不存在。")
    if issue.status not in {"PROCESSING", "RELOCATION_REQUIRED"}:
        raise ValueError("资源异常审批通过且确认需要分流后才能生成学生迁移方案。")
    impact = await resource_impact(session, issue)
    if not impact.get("known", False):
        raise ValueError("仪器使用备注或项目能力配置尚未确认，不能自动生成迁移方案。")
    if not impact.get("shortage"):
        issue.remediation_status = "NOT_REQUIRED"
        await session.commit()
        return []

    # 删除同一 issue 下所有旧方案和 item，确保 plan_no 从 1 开始
    old_items = (
        await session.execute(
            select(ResourceRelocationItem).where(
                ResourceRelocationItem.plan_id.in_(
                    select(ResourceRelocationPlan.id).where(
                        ResourceRelocationPlan.resource_issue_id == issue.id,
                    )
                )
            )
        )
    ).scalars()
    for item in old_items:
        await session.delete(item)
    old_plans = (
        await session.execute(
            select(ResourceRelocationPlan).where(
                ResourceRelocationPlan.resource_issue_id == issue.id,
            )
        )
    ).scalars()
    for plan in old_plans:
        await session.delete(plan)
    await session.flush()
    plans: list[ResourceRelocationPlan] = []
    for affected in impact["affected_sessions"]:
        required = int(affected["required_relocation_count"])
        if required <= 0:
            continue
        source_session = await session.get(
            ExperimentSession, UUID(str(affected["session_id"]))
        )
        if source_session is None:
            continue
        term = await _term_for_session(session, source_session)
        records = list(
            (
                await session.execute(
                    select(StudentProjectRecord, Student)
                    .join(Student, Student.id == StudentProjectRecord.student_id)
                    .where(
                        StudentProjectRecord.session_id == source_session.id,
                        StudentProjectRecord.status.in_({"SELECTED", "MAKEUP_PENDING"}),
                    )
                    .order_by(Student.student_no, Student.id)
                )
            ).all()
        )
        candidates: list[tuple[StudentProjectRecord, Student, list]] = []
        for record, student in records:
            options = await recommend_adjustment_options(
                session,
                student_id=student.id,
                term=term,
                request_type="RESCHEDULE",
                source_record_id=record.id,
                preferences=preferences,
                max_options=3,
            )
            if options:
                candidates.append((record, student, options))
        candidates.sort(
            key=lambda value: (
                -value[2][0].score,
                -len(value[2]),
                value[1].student_no,
            )
        )

        for plan_no in range(1, max(1, min(3, max_plans)) + 1):
            target_additions: dict[UUID, int] = {}
            chosen: list[tuple[StudentProjectRecord, Student, object]] = []
            for record, student, options in candidates:
                if len(chosen) >= required:
                    break
                ordered = options[plan_no - 1 :] + options[: plan_no - 1]
                selected = None
                for option in ordered:
                    addition = target_additions.get(option.target.session_id, 0) + 1
                    if addition > option.target.remaining:
                        continue
                    if await _target_is_safe(
                        session,
                        issue=issue,
                        target_id=option.target.session_id,
                        additions=addition,
                    ):
                        selected = option
                        target_additions[option.target.session_id] = addition
                        break
                if selected is not None:
                    chosen.append((record, student, selected))

            plan = ResourceRelocationPlan(
                resource_issue_id=issue.id,
                source_session_id=source_session.id,
                plan_no=plan_no,
                required_relocation_count=required,
                planned_relocation_count=len(chosen),
                remaining_unresolved_count=max(0, required - len(chosen)),
                capacity_snapshot=affected["capacity_snapshot"],
                validation_result={
                    "valid": bool(chosen),
                    "partial": len(chosen) < required,
                    "message": (
                        "已覆盖全部超容量学生。"
                        if len(chosen) == required
                        else f"仍有{required - len(chosen)}名学生暂无可行场次。"
                    ),
                },
                status="VALIDATED",
                created_by=actor_id,
                updated_by=actor_id,
            )
            session.add(plan)
            await session.flush()
            for record, student, option in chosen:
                validation = await validate_student_adjustment(
                    session,
                    student_id=student.id,
                    term=term,
                    request_type="RESCHEDULE",
                    source_record_id=record.id,
                    target_session_id=option.target.session_id,
                )
                session.add(
                    ResourceRelocationItem(
                        plan_id=plan.id,
                        student_id=student.id,
                        student_project_record_id=record.id,
                        target_session_id=option.target.session_id,
                        score=option.score,
                        reasons={
                            "reasons": option.reasons,
                            "warnings": option.warnings,
                        },
                        validation_result=validation.model_dump(mode="json"),
                        status="VALIDATED",
                        created_by=actor_id,
                        updated_by=actor_id,
                    )
                )
            plans.append(plan)
    issue.remediation_status = "REMEDIATION_REQUIRED"
    await session.commit()
    return plans


async def validate_resource_relocation_plan(
    session: AsyncSession,
    *,
    plan_id: UUID,
    actor_id: UUID,
    selections: list[ResourceRelocationSelection],
) -> ResourceRelocationPlan:
    plan = await session.get(ResourceRelocationPlan, plan_id)
    if plan is None or plan.status != "VALIDATED":
        raise LookupError("迁移方案不存在或已经失效。")
    issue = await session.get(ResourceIssueReport, plan.resource_issue_id)
    source = await session.get(ExperimentSession, plan.source_session_id)
    if issue is None or source is None:
        raise LookupError("迁移方案关联数据不存在。")
    impact = await resource_impact(session, issue)
    current = next(
        (
            item
            for item in impact["affected_sessions"]
            if item["session_id"] == str(source.id)
        ),
        None,
    )
    required = int(current["required_relocation_count"]) if current else 0
    if len(selections) > required:
        raise ValueError(f"当前最多只需迁移{required}名学生，不能扩大迁移范围。")
    if len({item.student_id for item in selections}) != len(selections):
        raise ValueError("迁移学生不能重复。")
    term = await _term_for_session(session, source)
    target_additions: dict[UUID, int] = {}
    validated: list[tuple[StudentProjectRecord, ResourceRelocationSelection, dict]] = []
    for choice in selections:
        record = await session.scalar(
            select(StudentProjectRecord).where(
                StudentProjectRecord.student_id == choice.student_id,
                StudentProjectRecord.session_id == source.id,
                StudentProjectRecord.status.in_({"SELECTED", "MAKEUP_PENDING"}),
            )
        )
        if record is None:
            raise ValueError("所选学生已不在原场次中，请刷新方案。")
        result = await validate_student_adjustment(
            session,
            student_id=choice.student_id,
            term=term,
            request_type="RESCHEDULE",
            source_record_id=record.id,
            target_session_id=choice.target_session_id,
        )
        if not result.allowed:
            messages = "；".join(item.message for item in result.violations)
            raise ValueError(messages or "目标场次不再可用。")
        addition = target_additions.get(choice.target_session_id, 0) + 1
        if result.target is None or addition > result.target.remaining:
            raise ValueError("目标场次可申请名额不足，无法容纳所选迁移学生。")
        if not await _target_is_safe(
            session,
            issue=issue,
            target_id=choice.target_session_id,
            additions=addition,
        ):
            raise ValueError("目标场次容量不足，无法容纳所选迁移学生。")
        target_additions[choice.target_session_id] = addition
        validated.append((record, choice, result.model_dump(mode="json")))

    old_items = list(
        (
            await session.execute(
                select(ResourceRelocationItem).where(
                    ResourceRelocationItem.plan_id == plan.id
                )
            )
        ).scalars()
    )
    for item in old_items:
        await session.delete(item)
    await session.flush()
    for record, choice, validation in validated:
        session.add(
            ResourceRelocationItem(
                plan_id=plan.id,
                student_id=choice.student_id,
                student_project_record_id=record.id,
                target_session_id=choice.target_session_id,
                score=0,
                reasons={"reasons": ["管理员调整后的迁移安排"]},
                validation_result=validation,
                status="VALIDATED",
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
    plan.required_relocation_count = required
    plan.planned_relocation_count = len(validated)
    plan.remaining_unresolved_count = max(0, required - len(validated))
    plan.validation_result = {
        "valid": bool(validated) or required == 0,
        "partial": len(validated) < required,
    }
    plan.updated_by = actor_id
    await session.commit()
    return plan


async def execute_resource_relocation_plan(
    session: AsyncSession,
    *,
    plan_id: UUID,
    actor_id: UUID,
    commit: bool = True,
) -> ResourceRelocationPlan:
    plan = await session.scalar(
        select(ResourceRelocationPlan)
        .where(ResourceRelocationPlan.id == plan_id)
        .with_for_update()
    )
    if plan is None:
        raise LookupError("迁移方案不存在。")
    if plan.status == "EXECUTED":
        return plan
    if plan.status != "VALIDATED":
        raise ValueError("迁移方案已经失效，请重新生成。")
    issue = await session.scalar(
        select(ResourceIssueReport)
        .where(ResourceIssueReport.id == plan.resource_issue_id)
        .with_for_update()
    )
    source = await session.scalar(
        select(ExperimentSession)
        .where(ExperimentSession.id == plan.source_session_id)
        .with_for_update()
    )
    if issue is None or source is None:
        raise LookupError("迁移方案关联数据不存在。")
    impact = await resource_impact(session, issue)
    current = next(
        (item for item in impact["affected_sessions"] if item["session_id"] == str(source.id)),
        None,
    )
    required = int(current["required_relocation_count"]) if current else 0
    if required == 0:
        plan.status = "STALE"
        issue.remediation_status = "REMEDIATED"
        if commit:
            await session.commit()
        else:
            await session.flush()
        return plan
    items = list(
        (
            await session.execute(
                select(ResourceRelocationItem)
                .where(ResourceRelocationItem.plan_id == plan.id)
                .with_for_update()
            )
        ).scalars()
    )
    if not items:
        raise ValueError("方案中没有可执行的学生迁移安排。")
    if len(items) > required:
        raise ValueError("当前需迁移人数已经减少，请重新生成方案。")
    term = await _term_for_session(session, source)
    target_additions: dict[UUID, int] = {}
    locked: list[tuple[ResourceRelocationItem, StudentProjectRecord, ExperimentSession, dict]] = []
    for item in items:
        record = await session.scalar(
            select(StudentProjectRecord)
            .where(StudentProjectRecord.id == item.student_project_record_id)
            .with_for_update()
        )
        target = await session.scalar(
            select(ExperimentSession)
            .where(ExperimentSession.id == item.target_session_id)
            .with_for_update()
        )
        if record is None or target is None or record.session_id != source.id:
            raise ValueError("学生或场次状态已变化，请重新生成方案。")
        result = await validate_student_adjustment(
            session,
            student_id=item.student_id,
            term=term,
            request_type="RESCHEDULE",
            source_record_id=record.id,
            target_session_id=target.id,
            lock_rows=False,
        )
        if not result.allowed:
            raise ValueError("学生迁移资格已变化，请重新生成方案。")
        addition = target_additions.get(target.id, 0) + 1
        if result.target is None or addition > result.target.remaining:
            raise ValueError("目标场次可申请名额已变化，请重新生成方案。")
        if not await _target_is_safe(
            session, issue=issue, target_id=target.id, additions=addition
        ):
            raise ValueError("目标场次容量已变化，请重新生成方案。")
        target_additions[target.id] = addition
        locked.append((item, record, target, result.model_dump(mode="json")))

    now = datetime.now(UTC)
    for item, record, target, validation in locked:
        before = {
            "record_id": str(record.id),
            "session_id": str(source.id),
            "source_selected_count": source.selected_count,
            "target_selected_count": target.selected_count,
        }
        source.selected_count -= 1
        target.selected_count += 1
        record.session_id = target.id
        record.version_no += 1

        # 通知被迁移的学生
        from json import dumps as _json_dumps

        from app.db.redis_client import get_redis_client
        from app.models.identity import Student as Stu

        stu = await session.get(Stu, item.student_id)
        if stu is not None:
            from app.models.curriculum import ExperimentProject as EP2
            proj = await session.get(EP2, target.project_id) if target.project_id else None
            proj_name = proj.project_name if proj else ""
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
                        "request_no": issue.report_no,
                        "msg": msg,
                        "time": now.strftime("%m-%d %H:%M"),
                    },
                    ensure_ascii=False,
                ),
            )

        application = ApplicationRequest(
            request_no=f"RA-{now:%Y%m%d}-{uuid4().hex[:8].upper()}",
            request_type="RESCHEDULE",
            applicant_user_id=actor_id,
            student_id=item.student_id,
            project_id=record.project_id,
            original_session_id=source.id,
            target_session_id=target.id,
            reason="资源异常导致原场次容量下降，管理员执行部分学生迁移。",
            payload={
                "resource_issue_id": str(issue.id),
                "resource_relocation_plan_id": str(plan.id),
            },
            validation_result=validation,
            approval_route="ADMIN",
            reservation_status="CONSUMED",
            idempotency_key=f"rr-{plan.id.hex[:16]}-{item.student_id.hex[:16]}",
            status="EXECUTED",
            submitted_at=now,
            executed_at=now,
            created_by=actor_id,
            updated_by=actor_id,
        )
        session.add(application)
        await session.flush()
        session.add(
            ApprovalRecord(
                application_id=application.id,
                approval_type="MANUAL",
                approver_user_id=actor_id,
                decision="APPROVED",
                matched_rules={"resource_issue_id": str(issue.id)},
                comment="资源异常部分学生迁移",
                decided_at=now,
            )
        )
        session.add(
            AdjustmentExecutionAudit(
                application_id=application.id,
                session_id=target.id,
                change_type="RESOURCE_PARTIAL_RELOCATION",
                before_snapshot=before,
                after_snapshot={
                    "record_id": str(record.id),
                    "session_id": str(target.id),
                    "source_selected_count": source.selected_count,
                    "target_selected_count": target.selected_count,
                },
                execution_status="SUCCESS",
                executed_by=actor_id,
                executed_at=now,
                idempotency_key=f"rr-audit-{application.id.hex}",
            )
        )
        item.status = "EXECUTED"
        item.updated_by = actor_id
    plan.status = "EXECUTED"
    plan.updated_by = actor_id
    await session.flush()
    remaining = await resource_impact(session, issue)
    issue.remediation_status = (
        "PARTIALLY_REMEDIATED"
        if remaining.get("shortage")
        else "REMEDIATED"
    )
    if commit:
        await session.commit()
    else:
        await session.flush()
    return plan


async def serialize_resource_relocation_plan(
    session: AsyncSession, plan: ResourceRelocationPlan
) -> dict[str, object]:
    source = await session.get(ExperimentSession, plan.source_session_id)
    rows = (
        await session.execute(
            select(ResourceRelocationItem, Student, ExperimentSession)
            .join(Student, Student.id == ResourceRelocationItem.student_id)
            .join(ExperimentSession, ExperimentSession.id == ResourceRelocationItem.target_session_id)
            .where(ResourceRelocationItem.plan_id == plan.id)
            .order_by(Student.student_no)
        )
    ).all()
    return {
        "id": str(plan.id),
        "resource_issue_id": str(plan.resource_issue_id),
        "source_session_id": str(plan.source_session_id),
        "plan_no": plan.plan_no,
        "status": plan.status,
        "required_relocation_count": plan.required_relocation_count,
        "planned_relocation_count": plan.planned_relocation_count,
        "remaining_unresolved_count": plan.remaining_unresolved_count,
        "capacity_snapshot": plan.capacity_snapshot,
        "validation_result": plan.validation_result,
        "source": (
            {
                "week_no": source.week_no,
                "day_of_week": source.day_of_week,
                "start_slot": source.start_slot,
                "end_slot": source.end_slot,
            }
            if source
            else None
        ),
        "items": [
            {
                "id": str(item.id),
                "student_id": str(student.id),
                "student_no": student.student_no,
                "student_name": student.name,
                "target_session_id": str(target.id),
                "target": {
                    "week_no": target.week_no,
                    "day_of_week": target.day_of_week,
                    "start_slot": target.start_slot,
                    "end_slot": target.end_slot,
                },
                "score": item.score,
                "reasons": item.reasons,
                "status": item.status,
            }
            for item, student, target in rows
        ],
    }
