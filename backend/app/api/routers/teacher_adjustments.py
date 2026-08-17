import json
from datetime import UTC, datetime, time
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import StreamingResponse

from app.agents.registry import invoke_registered_graph, stream_registered_graph
from app.agents.nodes.teacher_adjustment_agent import extract_teacher_preferences
from app.api.dependencies import get_current_user
from app.crud.teaching_tasks import get_or_create_active_term
from app.db.session import get_db_session
from app.models.application import ApplicationRequest
from app.models.curriculum import AcademicTerm
from app.models.identity import Student, Teacher
from app.models.resources import LabEquipmentInventory, Laboratory, ResourceIssueReport
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.models.teaching_adjustment import (
    AdjustmentRemediationItem,
    AdjustmentRemediationPlan,
    ApplicationApprovalTask,
    EquipmentInventoryMovement,
    ResourceRelocationPlan,
    ResourceRepairUpdate,
    SessionExecutionOverride,
)
from app.schemas.auth import UserProfile
from app.schemas.teacher_adjustment import (
    AdjustmentReviewRequest,
    LabChangePreviewRequest,
    RepairUpdateCreateRequest,
    RepairUpdateReviewRequest,
    ResourceIssueCreateRequest,
    EquipmentScrapCreateRequest,
    ResourceIssueReviewRequest,
    ResourceRelocationPlanUpdateRequest,
    ResourceRelocationRecommendationRequest,
    ResourceRemediationCreateRequest,
    SubstituteConfirmationRequest,
    SubstitutionPreviewRequest,
    TeacherAdjustmentCreateRequest,
    TeacherRescheduleAgentPlan,
    TeacherReschedulePreviewRequest,
    TeacherRescheduleRecommendationRequest,
)
from app.schemas.student_consultation import SelectionPreferences
from app.services.resource_relocation_service import (
    execute_resource_relocation_plan,
    generate_resource_relocation_plans,
    serialize_resource_relocation_plan,
    validate_resource_relocation_plan,
)
from app.services.teacher_adjustment_service import (
    approve_teacher_adjustment,
    auto_extend_overdue_issues,
    confirm_substitution,
    create_repair_update,
    create_resource_issue,
    create_teacher_adjustment,
    generate_remediation_plans,
    list_teacher_adjustments,
    resource_impact,
    review_repair_update,
    review_resource_issue,
    validate_lab_change,
    validate_reschedule,
    validate_substitution,
)
from app.services import equipment_asset_service as asset_svc

router = APIRouter(tags=["教师教学调整"])


async def _teacher(session: AsyncSession, user: UserProfile) -> Teacher:
    if user.user_type != "TEACHER":
        raise HTTPException(status_code=403, detail="仅教师可执行此操作。")
    item = await session.scalar(select(Teacher).where(Teacher.user_id == user.id))
    if item is None:
        raise HTTPException(status_code=404, detail="教师信息不存在。")
    return item


def _admin(user: UserProfile) -> None:
    if user.user_type != "ADMIN":
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作。")


def _application(item: ApplicationRequest) -> dict[str, object]:
    return {
        "id": str(item.id),
        "request_no": item.request_no,
        "request_type": item.request_type,
        "status": item.status,
        "teacher_id": str(item.teacher_id) if item.teacher_id else None,
        "original_session_id": str(item.original_session_id)
        if item.original_session_id
        else None,
        "reason": item.reason,
        "payload": item.payload,
        "validation_result": item.validation_result,
        "approval_route": item.approval_route,
        "submitted_at": item.submitted_at,
    }


def _issue(item: ResourceIssueReport) -> dict[str, object]:
    return {
        "id": str(item.id),
        "report_no": item.report_no,
        "reporter_teacher_id": str(item.reporter_teacher_id),
        "issue_type": item.issue_type,
        "status": item.status,
        "remediation_status": item.remediation_status,
        "source_issue_id": str(item.source_issue_id) if item.source_issue_id else None,
        "laboratory_id": str(item.laboratory_id),
        "inventory_id": str(item.inventory_id) if item.inventory_id else None,
        "equipment_type_id": str(item.equipment_type_id)
        if item.equipment_type_id
        else None,
        "affected_quantity": item.affected_quantity,
        "approved_quantity": item.approved_quantity,
        "restored_quantity": item.restored_quantity,
        "impact_start": item.impact_start,
        "impact_end": item.impact_end,
        "severity": item.severity,
        "description": item.description,
        "created_at": item.created_at,
        "approved_at": item.approved_at,
        "resolved_at": item.resolved_at,
        "resolution_note": item.resolution_note,
    }


async def _remediation_plan(
    session: AsyncSession, plan: AdjustmentRemediationPlan
) -> dict[str, object]:
    items = list(
        (
            await session.execute(
                select(AdjustmentRemediationItem).where(
                    AdjustmentRemediationItem.plan_id == plan.id
                )
            )
        ).scalars()
    )
    student_ids = {item.student_id for item in items}
    target_ids = {item.target_session_id for item in items}
    students = {
        item.id: item
        for item in (
            await session.execute(select(Student).where(Student.id.in_(student_ids)))
        ).scalars()
    }
    targets = {
        item.id: item
        for item in (
            await session.execute(
                select(ExperimentSession)
                .options(
                    selectinload(ExperimentSession.project),
                    selectinload(ExperimentSession.laboratory),
                    selectinload(ExperimentSession.teacher),
                )
                .where(ExperimentSession.id.in_(target_ids))
            )
        ).scalars()
    }
    result_items = []
    for value in items:
        student = students.get(value.student_id)
        target = targets.get(value.target_session_id)
        result_items.append(
            {
                "student_id": str(value.student_id),
                "student_no": student.student_no if student else "",
                "student_name": student.name if student else "",
                "target_session_id": str(value.target_session_id),
                "project_name": target.project.project_name
                if target and target.project
                else "",
                "week_no": target.week_no if target else None,
                "day_of_week": target.day_of_week if target else None,
                "start_slot": target.start_slot if target else None,
                "end_slot": target.end_slot if target else None,
                "laboratory_name": target.laboratory.name
                if target and target.laboratory
                else "",
                "teacher_name": target.teacher.name
                if target and target.teacher
                else "",
                "reason": value.reason,
            }
        )
    return {
        "id": str(plan.id),
        "plan_no": plan.plan_no,
        "status": plan.status,
        "summary": plan.summary,
        "items": result_items,
    }


@router.get("/teachers/me/adjustments/context")
async def teacher_adjustment_context(
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    term = await get_or_create_active_term(session)
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
                    ExperimentSession.teacher_id == teacher.id,
                    ExperimentSession.status.in_(["DRAFT", "OPEN", "FULL"]),
                )
                .order_by(
                    ExperimentSession.week_no,
                    ExperimentSession.day_of_week,
                    ExperimentSession.start_slot,
                )
            )
        ).scalars()
    )
    labs = list(
        (
            await session.execute(
                select(Laboratory)
                .where(Laboratory.status.in_(["ACTIVE", "LIMITED"]))
                .order_by(Laboratory.name)
            )
        ).scalars()
    )
    teachers = list(
        (
            await session.execute(
                select(Teacher)
                .where(Teacher.status == "ACTIVE", Teacher.id != teacher.id)
                .order_by(Teacher.name)
            )
        ).scalars()
    )
    inventories = list(
        (
            await session.execute(
                select(LabEquipmentInventory)
                .options(selectinload(LabEquipmentInventory.equipment_type))
                .order_by(LabEquipmentInventory.laboratory_id)
            )
        ).scalars()
    )
    return {
        "term": {"id": str(term.id), "total_weeks": term.total_weeks},
        "sessions": [
            {
                "id": str(item.id),
                "project_name": item.project.project_name if item.project else "",
                "week_no": item.week_no,
                "day_of_week": item.day_of_week,
                "start_slot": item.start_slot,
                "end_slot": item.end_slot,
                "laboratory_id": str(item.laboratory_id),
                "laboratory_name": item.laboratory.name if item.laboratory else "",
                "selected_count": item.selected_count,
            }
            for item in sessions
        ],
        "laboratories": [
            {"id": str(item.id), "name": item.name, "capacity": item.safety_capacity}
            for item in labs
        ],
        "substitute_teachers": [
            {"id": str(item.id), "name": item.name, "department": item.department}
            for item in teachers
        ],
        "inventories": [
            {
                "id": str(item.id),
                "laboratory_id": str(item.laboratory_id),
                "equipment_name": item.equipment_type.name
                if item.equipment_type
                else "",
                "total_quantity": item.total_quantity,
                "usable_quantity": item.usable_quantity,
                "disabled_quantity": item.disabled_quantity,
            }
            for item in inventories
        ],
    }


@router.post("/teachers/me/adjustments/reschedule/preview")
async def preview_reschedule(
    body: TeacherReschedulePreviewRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    term = await get_or_create_active_term(session)
    return await validate_reschedule(
        session,
        teacher_id=teacher.id,
        term=term,
        original_session_id=body.original_session_id,
        target=body.target,
    )


@router.post("/teachers/me/adjustments/lab/preview")
async def preview_lab_change(
    body: LabChangePreviewRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    return await validate_lab_change(session, teacher_id=teacher.id, body=body)


@router.post("/teachers/me/adjustments/substitution/preview")
async def preview_substitution(
    body: SubstitutionPreviewRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    return await validate_substitution(session, teacher_id=teacher.id, body=body)


@router.post("/teachers/me/adjustments/reschedule/recommend/stream")
async def recommend_reschedule_stream(
    body: TeacherRescheduleRecommendationRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    term = await get_or_create_active_term(session)
    trace_id = uuid4().hex

    async def events():
        try:
            async for event in stream_registered_graph(
                business_type="TEACHER_ADJUSTMENT",
                actor_type=user.user_type,
                payload={
                    "session": session,
                    "teacher_id": teacher.id,
                    "term": term,
                    "trace_id": trace_id,
                    "operation": "RECOMMEND_TEACHER_RESCHEDULE",
                    "original_session_id": body.original_session_id,
                    "message": body.message,
                    "max_options": body.max_options,
                },
            ):
                if await request.is_disconnected():
                    break
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
        except Exception as error:  # noqa: BLE001
            payload = {
                "code": "TEACHER_ADJUSTMENT_STREAM_FAILED",
                "message": str(error),
                "trace_id": trace_id,
            }
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/teachers/me/adjustments")
async def submit_teacher_adjustment(
    body: TeacherAdjustmentCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    term = await get_or_create_active_term(session)
    try:
        return _application(
            await create_teacher_adjustment(
                session, teacher_id=teacher.id, actor_id=user.id, term=term, body=body
            )
        )
    except (ValueError, LookupError, PermissionError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/teachers/me/adjustments")
async def my_teacher_adjustments(
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    items = await list_teacher_adjustments(session, teacher_id=teacher.id)
    # 同时查询该教师作为代课教师被指定的申请
    sub_items = list(
        (
            await session.execute(
                select(ApplicationRequest).where(
                    ApplicationRequest.request_type == "TEACHER_SUBSTITUTION",
                )
            )
        ).scalars()
    )
    seen = {item.id for item in items}
    for sub in sub_items:
        match = sub.payload.get("substitute_teacher_id") == str(teacher.id)
        print(f"[DEBUG] sub={sub.request_no} sub_tid={sub.payload.get('substitute_teacher_id')} tid={teacher.id} match={match}", flush=True)
        if sub.id not in seen and match:
            items.append(sub)
    print(f"[DEBUG] total items for {teacher.name}: {len(items)}", flush=True)
    if not items:
        return []
    session_ids = {item.original_session_id for item in items if item.original_session_id}
    original_sessions: dict[UUID, ExperimentSession] = {}
    if session_ids:
        original_sessions = {
            orig.id: orig
            for orig in (
                await session.execute(
                    select(ExperimentSession)
                    .options(
                        selectinload(ExperimentSession.project),
                        selectinload(ExperimentSession.laboratory),
                        selectinload(ExperimentSession.teacher),
                    )
                    .where(ExperimentSession.id.in_(session_ids))
                )
            ).scalars()
        }
    # For executed adjustments, get original time from SessionExecutionOverride
    executed_overrides: dict[UUID, dict[str, object]] = {}
    executed_ids = {item.id for item in items if item.status == "EXECUTED"}
    if executed_ids:
        override_rows = (
            await session.execute(
                select(SessionExecutionOverride).where(
                    SessionExecutionOverride.application_id.in_(executed_ids),
                    SessionExecutionOverride.status == "ACTIVE",
                )
            )
        ).scalars()
        for ov in override_rows:
            executed_overrides[ov.application_id] = ov.before_snapshot

    # Gather target lab and teacher info
    target_lab_ids = {
        UUID(str(item.payload["target_laboratory_id"]))
        for item in items
        if item.payload.get("target_laboratory_id")
    }
    target_teacher_ids = {
        UUID(str(item.payload["substitute_teacher_id"]))
        for item in items
        if item.payload.get("substitute_teacher_id")
    }
    target_labs: dict[UUID, Laboratory] = {}
    target_teachers: dict[UUID, Teacher] = {}
    if target_lab_ids:
        target_labs = {
            lab.id: lab
            for lab in (await session.execute(
                select(Laboratory).where(Laboratory.id.in_(target_lab_ids))
            )).scalars()
        }
    if target_teacher_ids:
        target_teachers = {
            t.id: t
            for t in (await session.execute(
                select(Teacher).where(Teacher.id.in_(target_teacher_ids))
            )).scalars()
        }
    result: list[dict[str, object]] = []
    for item in items:
        entry = _application(item)
        entry["submitted_at"] = item.submitted_at.isoformat() if item.submitted_at else None
        entry["teacher_name"] = teacher.name
        orig = original_sessions.get(item.original_session_id)
        if orig is not None:
            override_before = executed_overrides.get(item.id)
            entry["source_info"] = {
                "project_name": orig.project.project_name if orig.project else "",
                "week_no": (override_before.get("week_no") if override_before and "week_no" in override_before else orig.week_no),
                "day_of_week": (override_before.get("day_of_week") if override_before and "day_of_week" in override_before else orig.day_of_week),
                "start_slot": (override_before.get("start_slot") if override_before and "start_slot" in override_before else orig.start_slot),
                "end_slot": (override_before.get("end_slot") if override_before and "end_slot" in override_before else orig.end_slot),
                "laboratory_name": (
                    override_before.get("laboratory_name")
                    if override_before and "laboratory_name" in override_before
                    else (orig.laboratory.name if orig.laboratory else "")
                ),
            }
            if item.request_type == "TEACHER_ADJUSTMENT":
                entry["target_info"] = {
                    "project_name": orig.project.project_name if orig.project else "",
                    **dict(item.payload.get("target_time") or {}),
                }
            elif item.request_type == "LAB_CHANGE":
                tl = target_labs.get(UUID(str(item.payload["target_laboratory_id"])))
                entry["target_info"] = {
                    "project_name": orig.project.project_name if orig.project else "",
                    "laboratory_name": tl.name if tl else "",
                }
            elif item.request_type == "TEACHER_SUBSTITUTION":
                tt = target_teachers.get(UUID(str(item.payload["substitute_teacher_id"])))
                entry["target_info"] = {
                    "project_name": orig.project.project_name if orig.project else "",
                    "teacher_name": tt.name if tt else "",
                }
        result.append(entry)
    return result


@router.post("/teachers/me/substitution-tasks/{application_id}/confirm")
async def substitute_confirm(
    application_id: UUID,
    body: SubstituteConfirmationRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    await _teacher(session, user)
    try:
        return _application(
            await confirm_substitution(
                session,
                application_id=application_id,
                actor_id=user.id,
                approved=body.approved,
            )
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/teachers/me/substitution-tasks")
async def my_substitution_tasks(
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    await _teacher(session, user)
    tasks = list(
        (
            await session.execute(
                select(ApplicationApprovalTask).where(
                    ApplicationApprovalTask.approver_type == "SUBSTITUTE_TEACHER",
                    ApplicationApprovalTask.approver_user_id == user.id,
                )
            )
        ).scalars()
    )
    applications = {
        item.id: item
        for item in (
            await session.execute(
                select(ApplicationRequest).where(
                    ApplicationRequest.id.in_({task.application_id for task in tasks})
                )
            )
        ).scalars()
    }
    # 批量加载原始场次
    sess_ids = [applications[t.application_id].original_session_id for t in tasks if t.application_id in applications]
    session_map = {}
    if sess_ids:
        rows = (await session.execute(
            select(ExperimentSession).options(selectinload(ExperimentSession.project), selectinload(ExperimentSession.laboratory))
            .where(ExperimentSession.id.in_(sess_ids))
        )).scalars().all()
        session_map = {s.id: s for s in rows}

    result = []
    for task in tasks:
        if task.application_id not in applications:
            continue
        app = applications[task.application_id]
        item = dict(**_application(app), task_status=task.status)
        sess = session_map.get(app.original_session_id)
        if sess:
            item["original_session"] = {
                "week_no": sess.week_no, "day_of_week": sess.day_of_week,
                "start_slot": sess.start_slot, "end_slot": sess.end_slot,
                "project_name": sess.project.project_name if sess.project else "",
                "lab_name": sess.laboratory.name if sess.laboratory else "",
            }
        result.append(item)
    return result


@router.post("/teachers/me/resource-issues")
async def report_resource_issue(
    body: ResourceIssueCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    try:
        if body.issue_type == "EQUIPMENT_FAILURE":
            item, deduplicated = await asset_svc.create_asset_issue(
                session, teacher_id=teacher.id, actor_id=user.id,
                asset_id=body.asset_id, issue_type=body.issue_type,
                severity=body.severity, description=body.description,
                impact_start=body.impact_start, impact_end=body.impact_end,
            )
            result = _issue(item); result["deduplicated"] = deduplicated
            result["asset"] = await asset_svc.issue_asset_payload(session, item.id)
            return result
        return _issue(
            await create_resource_issue(
                session, teacher_id=teacher.id, actor_id=user.id, body=body
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/teachers/me/reportable-equipment-assets")
async def teacher_reportable_equipment_assets(
    session: AsyncSession = Depends(get_db_session), user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    return await asset_svc.reportable_assets(session, teacher.id)


@router.post("/teachers/me/equipment-scrap-requests", status_code=201)
async def create_equipment_scrap_request(
    body: EquipmentScrapCreateRequest,
    session: AsyncSession = Depends(get_db_session), user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    term = await get_or_create_active_term(session)
    try:
        item, _ = await asset_svc.create_asset_issue(
            session, teacher_id=teacher.id, actor_id=user.id, asset_id=body.asset_id,
            issue_type="EQUIPMENT_SCRAP", severity=body.severity, description=body.reason,
            impact_start=datetime.now(UTC), impact_end=datetime.combine(term.end_date, time.max, tzinfo=UTC),
        )
        result = _issue(item); result["asset"] = await asset_svc.issue_asset_payload(session, item.id)
        return result
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/teachers/me/equipment-scrap-requests")
async def teacher_equipment_scrap_requests(
    session: AsyncSession = Depends(get_db_session), user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    rows = list((await session.execute(select(ResourceIssueReport).where(ResourceIssueReport.reporter_teacher_id == teacher.id, ResourceIssueReport.issue_type == "EQUIPMENT_SCRAP").order_by(ResourceIssueReport.created_at.desc()))).scalars())
    result = []
    for item in rows:
        value = _issue(item); value["asset"] = await asset_svc.issue_asset_payload(session, item.id); result.append(value)
    return result


@router.get("/teachers/me/resource-issues")
async def my_resource_issues(
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    rows = list(
        (
            await session.execute(
                select(ResourceIssueReport)
                .where(ResourceIssueReport.reporter_teacher_id == teacher.id)
                .order_by(ResourceIssueReport.created_at.desc())
            )
        ).scalars()
    )
    issue_ids = [item.id for item in rows]
    pending_updates: dict[UUID, list[dict[str, object]]] = {}
    if issue_ids:
        update_rows = list(
            (
                await session.execute(
                    select(ResourceRepairUpdate).where(
                        ResourceRepairUpdate.resource_issue_id.in_(issue_ids),
                        ResourceRepairUpdate.approval_status == "PENDING",
                    )
                )
            ).scalars()
        )
        for upd in update_rows:
            pending_updates.setdefault(upd.resource_issue_id, []).append(
                {
                    "update_type": upd.update_type,
                    "restored_quantity": upd.restored_quantity,
                    "proposed_end_time": (
                        upd.proposed_end_time.isoformat()
                        if upd.proposed_end_time
                        else None
                    ),
                    "note": upd.note,
                }
            )
    result: list[dict[str, object]] = []
    for item in rows:
        entry = _issue(item)
        entry["asset"] = await asset_svc.issue_asset_payload(session, item.id)
        entry["pending_update"] = pending_updates.get(item.id)
        result.append(entry)
    return result


@router.post("/teachers/me/resource-issues/{issue_id}/repair-updates")
async def submit_repair_update(
    issue_id: UUID,
    body: RepairUpdateCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    teacher = await _teacher(session, user)
    issue = await session.get(ResourceIssueReport, issue_id)
    if issue is None or issue.reporter_teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="资源异常记录不存在。")
    try:
        item = await create_repair_update(
            session, issue_id=issue_id, actor_id=user.id, body=body
        )
        return {
            "id": str(item.id),
            "approval_status": item.approval_status,
            "update_type": item.update_type,
            "restored_quantity": item.restored_quantity,
        }
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/admin/teacher-adjustments")
async def admin_teacher_adjustments(
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    items = await list_teacher_adjustments(session)
    teacher_ids = {item.teacher_id for item in items if item.teacher_id}
    session_ids = {item.original_session_id for item in items if item.original_session_id}
    original_sessions = {
        original.id: original
        for original in (
            await session.execute(
                select(ExperimentSession)
                .options(
                    selectinload(ExperimentSession.project),
                    selectinload(ExperimentSession.laboratory),
                    selectinload(ExperimentSession.teacher),
                )
                .where(ExperimentSession.id.in_(session_ids))
            )
        ).scalars()
    }
    target_lab_ids = {
        UUID(str(item.payload["target_laboratory_id"]))
        for item in items
        if item.payload.get("target_laboratory_id")
    }
    target_teacher_ids = {
        UUID(str(item.payload["substitute_teacher_id"]))
        for item in items
        if item.payload.get("substitute_teacher_id")
    }
    target_labs = {
        lab.id: lab
        for lab in (
            await session.execute(
                select(Laboratory).where(Laboratory.id.in_(target_lab_ids))
            )
        ).scalars()
    }
    target_teachers = {
        teacher.id: teacher
        for teacher in (
            await session.execute(
                select(Teacher).where(Teacher.id.in_(target_teacher_ids))
            )
        ).scalars()
    }
    # For executed adjustments, get original time from SessionExecutionOverride
    executed_overrides: dict[UUID, dict[str, object]] = {}
    executed_ids = {item.id for item in items if item.status == "EXECUTED"}
    if executed_ids:
        for ov in (
            await session.execute(
                select(SessionExecutionOverride).where(
                    SessionExecutionOverride.application_id.in_(executed_ids),
                    SessionExecutionOverride.status == "ACTIVE",
                )
            )
        ).scalars():
            executed_overrides[ov.application_id] = ov.before_snapshot

    names = (
        {
            item.id: item.name
            for item in (
                await session.execute(
                    select(Teacher).where(Teacher.id.in_(teacher_ids))
                )
            ).scalars()
        }
        if teacher_ids
        else {}
    )
    result = []
    for item in items:
        value = _application(item)
        value["teacher_name"] = names.get(item.teacher_id, "")
        original = original_sessions.get(item.original_session_id)
        if original is not None:
            override_before = executed_overrides.get(item.id)
            source_info = {
                "session_id": str(original.id),
                "project_id": str(original.project_id),
                "project_name": original.project.project_name
                if original.project
                else "",
                "week_no": (override_before.get("week_no") if override_before and "week_no" in override_before else original.week_no),
                "day_of_week": (override_before.get("day_of_week") if override_before and "day_of_week" in override_before else original.day_of_week),
                "start_slot": (override_before.get("start_slot") if override_before and "start_slot" in override_before else original.start_slot),
                "end_slot": (override_before.get("end_slot") if override_before and "end_slot" in override_before else original.end_slot),
                "laboratory_id": str(original.laboratory_id),
                "laboratory_name": original.laboratory.name
                if original.laboratory
                else "",
                "teacher_id": str(original.teacher_id),
                "teacher_name": original.teacher.name if original.teacher else "",
            }
            target_info = dict(source_info)
            if item.request_type == "TEACHER_ADJUSTMENT":
                target_info.update(item.payload.get("target_time") or {})
            elif item.request_type == "LAB_CHANGE":
                target_lab_id = UUID(str(item.payload["target_laboratory_id"]))
                target_lab = target_labs.get(target_lab_id)
                target_info.update(
                    {
                        "laboratory_id": str(target_lab_id),
                        "laboratory_name": target_lab.name if target_lab else "",
                    }
                )
            elif item.request_type == "TEACHER_SUBSTITUTION":
                target_teacher_id = UUID(str(item.payload["substitute_teacher_id"]))
                target_teacher = target_teachers.get(target_teacher_id)
                target_info.update(
                    {
                        "teacher_id": str(target_teacher_id),
                        "teacher_name": target_teacher.name
                        if target_teacher
                        else "",
                    }
                )
            value["source_info"] = source_info
            value["target_info"] = target_info
        result.append(value)

    # 为已执行的申请附加安置方案
    executed_ids = [item.get("id") for item in result if item.get("status") == "EXECUTED"]
    if executed_ids:
        plan_rows = list(
            (
                await session.execute(
                    select(AdjustmentRemediationPlan).where(
                        AdjustmentRemediationPlan.application_id.in_(
                            [UUID(rid) for rid in executed_ids]
                        ),
                        AdjustmentRemediationPlan.status == "EXECUTED",
                    )
                )
            ).scalars()
        )
        plan_items: dict[UUID, list[dict]] = {}
        if plan_rows:
            item_rows = list(
                (
                    await session.execute(
                        select(AdjustmentRemediationItem, Student, ExperimentSession)
                        .join(Student, Student.id == AdjustmentRemediationItem.student_id)
                        .join(
                            ExperimentSession,
                            ExperimentSession.id == AdjustmentRemediationItem.target_session_id,
                        )
                        .where(
                            AdjustmentRemediationItem.plan_id.in_(
                                [p.id for p in plan_rows]
                            )
                        )
                    )
                ).all()
            )
            for ri, stu, sess in item_rows:
                plan_items.setdefault(ri.plan_id, []).append(
                    {
                        "student_name": stu.name,
                        "student_no": stu.student_no,
                        "week_no": sess.week_no,
                        "day_of_week": sess.day_of_week,
                        "start_slot": sess.start_slot,
                        "end_slot": sess.end_slot,
                    }
                )
        plan_by_app: dict[UUID, dict] = {
            plan.application_id: {
                "id": str(plan.id),
                "plan_no": plan.plan_no,
                "summary": plan.summary,
                "items": plan_items.get(plan.id, []),
            }
            for plan in plan_rows
        }
        for item in result:
            pid = UUID(item["id"])
            if pid in plan_by_app:
                item["executed_plan"] = plan_by_app[pid]

    return result


@router.post("/admin/teacher-adjustments/{application_id}/review")
async def admin_review_adjustment(
    application_id: UUID,
    body: AdjustmentReviewRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    try:
        return _application(
            await approve_teacher_adjustment(
                session,
                application_id=application_id,
                actor_id=user.id,
                approved=body.approved,
                remediation_plan_id=body.remediation_plan_id,
                comment=body.comment,
            )
        )
    except (ValueError, LookupError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/admin/teacher-adjustments/{application_id}/executed-plan")
async def admin_get_remediation_plan(
    application_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    plan = await session.scalar(
        select(AdjustmentRemediationPlan).where(
            AdjustmentRemediationPlan.application_id == application_id,
            AdjustmentRemediationPlan.status == "EXECUTED",
        )
    )
    if plan is None:
        return None
    return await _remediation_plan(session, plan)


@router.post("/admin/teacher-adjustments/{application_id}/remediation/recommend")
async def admin_recommend_remediation(
    application_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    try:
        plans = await generate_remediation_plans(
            session,
            application_id=application_id,
            actor_id=user.id,
            max_plans=3,
        )
        return [await _remediation_plan(session, plan) for plan in plans]
    except (ValueError, LookupError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/admin/resource-issues")
async def admin_resource_issues(
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    rows = list(
        (
            await session.execute(
                select(ResourceIssueReport).order_by(
                    ResourceIssueReport.created_at.desc()
                )
            )
        ).scalars()
    )
    result = []
    teacher_ids = {item.reporter_teacher_id for item in rows}
    teachers = {
        item.id: item
        for item in (
            await session.execute(select(Teacher).where(Teacher.id.in_(teacher_ids)))
        ).scalars()
    } if teacher_ids else {}
    for item in rows:
        value = _issue(item)
        reporter = teachers.get(item.reporter_teacher_id)
        value["reporter"] = (
            {
                "id": str(reporter.id),
                "name": reporter.name,
                "employee_no": reporter.employee_no,
                "department": reporter.department or "",
            }
            if reporter
            else None
        )
        value["asset"] = await asset_svc.issue_asset_payload(session, item.id)
        source_issue = await session.get(ResourceIssueReport, item.source_issue_id) if item.source_issue_id else None
        value["source_issue"] = (
            {"id": str(source_issue.id), "report_no": source_issue.report_no,
             "status": source_issue.status}
            if source_issue else None
        )
        value["impact"] = await resource_impact(session, item)
        affected = value["impact"].get("affected_sessions", [])
        value["impact_course_count"] = len(
            {entry.get("course_id") for entry in affected if entry.get("course_id")}
        )
        value["impact_session_count"] = len(affected)
        value["impact_student_count"] = int(
            value["impact"].get("total_required_relocation_count", 0)
        )
        result.append(value)
    return result


@router.post("/admin/resource-issues/{issue_id}/review")
async def admin_review_resource(
    issue_id: UUID,
    body: ResourceIssueReviewRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    try:
        item, impact = await review_resource_issue(
            session,
            issue_id=issue_id,
            actor_id=user.id,
            approved=body.approved,
            approved_quantity=body.approved_quantity,
        )
        return {"issue": _issue(item), "impact": impact}
    except (ValueError, LookupError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/admin/resource-issues/{issue_id}/remediation/recommend")
async def admin_recommend_partial_resource_relocation(
    issue_id: UUID,
    body: ResourceRelocationRecommendationRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    try:
        preferences = body.preferences
        if preferences is None and body.message:
            parsed = await extract_teacher_preferences(body.message)
            if parsed.get("model_error"):
                raise ValueError(str(parsed["model_error"]))
            plan = parsed.get("plan")
            if not isinstance(plan, TeacherRescheduleAgentPlan):
                raise ValueError("无法理解迁移偏好，请换一种方式描述。")
            if plan.needs_clarification:
                raise ValueError(
                    plan.clarification_question or "迁移偏好存在冲突，请明确优先条件。"
                )
            preferences = plan.preferences
        plans = await generate_resource_relocation_plans(
            session,
            issue_id=issue_id,
            actor_id=user.id,
            preferences=preferences or SelectionPreferences(),
            max_plans=body.max_plans,
        )
        return [
            await serialize_resource_relocation_plan(session, plan)
            for plan in plans
        ]
    except (ValueError, LookupError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/admin/resource-issues/{issue_id}/remediation/plans/{plan_id}/validate"
)
async def admin_validate_resource_relocation(
    issue_id: UUID,
    plan_id: UUID,
    body: ResourceRelocationPlanUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    try:
        plan = await validate_resource_relocation_plan(
            session,
            plan_id=plan_id,
            actor_id=user.id,
            selections=body.items,
        )
        if plan.resource_issue_id != issue_id:
            raise ValueError("迁移方案不属于该资源异常。")
        return await serialize_resource_relocation_plan(session, plan)
    except (ValueError, LookupError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/admin/resource-issues/{issue_id}/remediation/plans/{plan_id}/execute"
)
async def admin_execute_resource_relocation(
    issue_id: UUID,
    plan_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    plan = await session.get(ResourceRelocationPlan, plan_id)
    if plan is None or plan.resource_issue_id != issue_id:
        raise HTTPException(status_code=404, detail="迁移方案不存在。")
    try:
        plan = await execute_resource_relocation_plan(
            session, plan_id=plan_id, actor_id=user.id
        )
        return await serialize_resource_relocation_plan(session, plan)
    except (ValueError, LookupError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/admin/resource-issues/{issue_id}/execute-relocation-and-scrap")
async def admin_execute_relocation_and_scrap(
    issue_id: UUID, body: dict,
    session: AsyncSession = Depends(get_db_session), user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    issue = await session.scalar(select(ResourceIssueReport).where(ResourceIssueReport.id == issue_id).with_for_update())
    if issue is None or issue.issue_type != "EQUIPMENT_SCRAP":
        raise HTTPException(status_code=404, detail="报废申请不存在。")
    if issue.status != "RELOCATION_REQUIRED":
        raise HTTPException(status_code=409, detail="该报废申请当前不需要执行学生分流。")
    try:
        plan_ids = [UUID(value) for value in body.get("plan_ids", [])]
        if not plan_ids:
            raise ValueError("请选择覆盖受影响场次的学生分流方案。")
        for plan_id in plan_ids:
            plan = await session.get(ResourceRelocationPlan, plan_id)
            if plan is None or plan.resource_issue_id != issue.id:
                raise ValueError("分流方案不属于当前报废申请。")
            await execute_resource_relocation_plan(session, plan_id=plan_id, actor_id=user.id, commit=False)
        remaining = await resource_impact(session, issue)
        if remaining.get("shortage"):
            raise ValueError("分流方案尚未覆盖全部受影响学生，不能报废。")
        await asset_svc.restore_or_transition_issue_asset(session, issue, actor_id=user.id, target_status="SCRAPPED", close_link=True)
        issue.status = "SCRAPPED"; issue.remediation_status = "REMEDIATED"; issue.resolved_at = datetime.now(UTC)
        if issue.source_issue_id:
            source_issue = await session.get(ResourceIssueReport, issue.source_issue_id)
            if source_issue is not None:
                source_issue.status = "CLOSED"
                source_issue.resolved_at = datetime.now(UTC)
        await session.commit()
        return {"issue": _issue(issue), "impact": remaining, "asset": await asset_svc.issue_asset_payload(session, issue.id)}
    except (ValueError, LookupError) as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/admin/resource-issues/auto-extend-overdue")
async def admin_auto_extend_overdue(
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    count = await auto_extend_overdue_issues(session, actor_id=user.id)
    return {"processed": count}


@router.post("/admin/resource-issues/{issue_id}/remediation/recommend-legacy")
async def admin_recommend_resource_remediation(
    issue_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    raise HTTPException(
        status_code=410,
        detail="整场迁移已停用，请使用部分学生迁移推荐接口。",
    )
    issue = await session.get(ResourceIssueReport, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="资源异常记录不存在。")
    if issue.status != "PROCESSING":
        raise HTTPException(
            status_code=409, detail="资源异常批准后才能生成教学调整方案。"
        )
    impact = await resource_impact(session, issue)
    if not impact.get("shortage"):
        return []
    recommendations = []
    for affected in impact.get("affected_sessions", []):
        original = await session.get(ExperimentSession, UUID(affected["session_id"]))
        version = (
            await session.get(ScheduleVersion, original.schedule_version_id)
            if original
            else None
        )
        term = await session.get(AcademicTerm, version.term_id) if version else None
        if original is None or term is None:
            continue
        state = await invoke_registered_graph(
            business_type="TEACHER_ADJUSTMENT",
            actor_type=user.user_type,
            payload={
                "session": session,
                "teacher_id": original.teacher_id,
                "term": term,
                "operation": "RECOMMEND_TEACHER_RESCHEDULE",
                "original_session_id": original.id,
                "message": "",
                "max_options": 3,
            },
        )
        recommendations.append(
            {
                "source_session_id": str(original.id),
                "selected_students": original.selected_count,
                "answer": state.get("answer", ""),
                "options": [
                    option.model_dump(mode="json")
                    if hasattr(option, "model_dump")
                    else option
                    for option in state.get("options", [])
                ],
            }
        )
    return recommendations


@router.post("/admin/resource-issues/{issue_id}/remediation/create")
async def admin_create_resource_remediation(
    issue_id: UUID,
    body: ResourceRemediationCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    raise HTTPException(
        status_code=410,
        detail="整场迁移接口已停用；请生成部分学生迁移方案，校验后再执行。",
    )
    issue = await session.get(ResourceIssueReport, issue_id)
    original = await session.get(ExperimentSession, body.original_session_id)
    if issue is None or issue.status != "PROCESSING":
        raise HTTPException(status_code=409, detail="资源异常尚未批准或已处理完成。")
    impact = await resource_impact(session, issue)
    affected_ids = {value["session_id"] for value in impact["affected_sessions"]}
    if original is None or str(original.id) not in affected_ids:
        raise HTTPException(
            status_code=409, detail="所选场次不属于该资源异常影响范围。"
        )
    version = await session.get(ScheduleVersion, original.schedule_version_id)
    term = await session.get(AcademicTerm, version.term_id) if version else None
    if term is None:
        raise HTTPException(status_code=409, detail="无法确定场次所属学期。")
    try:
        application = await create_teacher_adjustment(
            session,
            teacher_id=original.teacher_id,
            actor_id=user.id,
            term=term,
            body=TeacherAdjustmentCreateRequest(
                request_type="TEACHER_ADJUSTMENT",
                original_session_id=original.id,
                target_time=body.target,
                reason=body.reason,
                idempotency_key=uuid4().hex,
            ),
        )
        application.payload = {
            **application.payload,
            "resource_issue_id": str(issue.id),
        }
        await session.commit()
        return _application(application)
    except (ValueError, LookupError, PermissionError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/admin/resource-repair-updates/{update_id}/review")
async def admin_review_repair(
    update_id: UUID,
    body: RepairUpdateReviewRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    try:
        update, issue = await review_repair_update(
            session, update_id=update_id, actor_id=user.id, approved=body.approved
        )
        return {
            "update_id": str(update.id),
            "approval_status": update.approval_status,
            "issue": _issue(issue),
        }
    except (ValueError, LookupError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/admin/resource-repair-updates")
async def admin_repair_updates(
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    rows = list(
        (
            await session.execute(
                select(ResourceRepairUpdate).order_by(
                    ResourceRepairUpdate.created_at.desc()
                )
            )
        ).scalars()
    )
    return [
        {
            "id": str(item.id),
            "resource_issue_id": str(item.resource_issue_id),
            "update_type": item.update_type,
            "restored_quantity": item.restored_quantity,
            "proposed_end_time": item.proposed_end_time,
            "note": item.note,
            "approval_status": item.approval_status,
            "created_at": item.created_at,
        }
        for item in rows
    ]


@router.get("/admin/equipment-inventory/{inventory_id}/movements")
async def inventory_movements(
    inventory_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    _admin(user)
    rows = list(
        (
            await session.execute(
                select(EquipmentInventoryMovement)
                .where(EquipmentInventoryMovement.inventory_id == inventory_id)
                .order_by(EquipmentInventoryMovement.created_at.desc())
            )
        ).scalars()
    )
    return [
        {
            "id": str(item.id),
            "movement_type": item.movement_type,
            "quantity": item.quantity,
            "before": item.before_snapshot,
            "after": item.after_snapshot,
            "created_at": item.created_at,
        }
        for item in rows
    ]
