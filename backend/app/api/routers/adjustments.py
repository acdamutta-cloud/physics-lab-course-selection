import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.agents.registry import stream_registered_graph
from app.api.dependencies import get_current_user
from app.crud.teaching_tasks import get_or_create_active_term
from app.db.session import get_db_session
from app.models.application import ApplicationRequest
from app.models.identity import Student
from app.schemas.auth import UserProfile
from app.schemas.student_adjustment import (
    AdjustmentAgentRequest,
    AdjustmentApplicationOut,
    AdjustmentCreateRequest,
    AdjustmentPreviewRequest,
    AdjustmentRecommendationRequest,
    AdjustmentReviewRequest,
    AdjustmentValidationResult,
)
from app.services.student_adjustment_service import (
    application_out,
    cancel_adjustment_application,
    create_adjustment_application,
    get_adjustment_context,
    list_adjustment_applications,
    recommend_adjustment_options,
    review_adjustment_application,
    validate_student_adjustment,
)

router = APIRouter(tags=["教学调整"])


def _require_type(user: UserProfile, expected: str) -> None:
    if user.user_type != expected:
        raise HTTPException(status_code=403, detail=f"仅{expected}身份可执行此操作。")


async def _student(session: AsyncSession, user: UserProfile) -> Student:
    _require_type(user, "STUDENT")
    item = await session.scalar(
        select(Student).where(Student.student_no == user.login_name.upper())
    )
    if item is None:
        raise HTTPException(status_code=404, detail="学生信息不存在。")
    return item


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, PermissionError):
        return HTTPException(status_code=403, detail=str(error))
    return HTTPException(status_code=409, detail=str(error))


@router.get("/students/me/adjustments/context")
async def adjustment_context(
    request_type: str | None = Query(default=None),
    source_record_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    student = await _student(session, user)
    term = await get_or_create_active_term(session)
    if request_type not in {None, "RESCHEDULE", "PROJECT_CHANGE", "MAKEUP"}:
        raise HTTPException(status_code=422, detail="不支持的申请类型。")
    return await get_adjustment_context(
        session,
        student_id=student.id,
        term=term,
        request_type=request_type,  # type: ignore[arg-type]
        source_record_id=source_record_id,
    )


@router.post(
    "/students/me/adjustments/preview",
    response_model=AdjustmentValidationResult,
)
async def preview_adjustment(
    body: AdjustmentPreviewRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    student = await _student(session, user)
    term = await get_or_create_active_term(session)
    return await validate_student_adjustment(
        session,
        student_id=student.id,
        term=term,
        request_type=body.request_type,
        source_record_id=body.source_record_id,
        target_session_id=body.target_session_id,
    )


@router.post("/students/me/adjustments/recommend")
async def recommend_adjustments(
    body: AdjustmentRecommendationRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    student = await _student(session, user)
    term = await get_or_create_active_term(session)
    return await recommend_adjustment_options(
        session,
        student_id=student.id,
        term=term,
        request_type=body.request_type,
        source_record_id=body.source_record_id,
        preferences=body.preferences,
        max_options=body.max_options,
    )


@router.post("/students/me/adjustments/recommend/stream")
async def stream_adjustment_recommendations(
    body: AdjustmentAgentRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    student = await _student(session, user)
    term = await get_or_create_active_term(session)
    trace_id = uuid4().hex

    async def events():
        try:
            async for event in stream_registered_graph(
                business_type="STUDENT_ADJUSTMENT",
                actor_type=user.user_type,
                payload={
                    "session": session,
                    "student_id": student.id,
                    "term": term,
                    "trace_id": trace_id,
                    "request_type": body.request_type,
                    "source_record_id": body.source_record_id,
                    "message": body.message,
                    "max_options": body.max_options,
                },
            ):
                if await request.is_disconnected():
                    break
                yield (
                    f"event: {event['event']}\n"
                    f"data: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
                )
        except Exception as error:  # noqa: BLE001
            payload = {
                "code": "ADJUSTMENT_STREAM_FAILED",
                "message": str(error),
                "trace_id": trace_id,
            }
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/students/me/adjustments",
    response_model=AdjustmentApplicationOut,
)
async def submit_adjustment(
    body: AdjustmentCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    student = await _student(session, user)
    term = await get_or_create_active_term(session)
    try:
        return await create_adjustment_application(
            session,
            student_id=student.id,
            actor_id=user.id,
            term=term,
            body=body,
        )
    except (ValueError, LookupError, PermissionError) as error:
        raise _http_error(error) from error


@router.get(
    "/students/me/adjustments",
)
async def list_my_adjustments(
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
    limit: int = 20,
    offset: int = 0,
):
    student = await _student(session, user)
    items, total = await list_adjustment_applications(
        session,
        student_id=student.id,
        limit=max(1, min(limit, 100)),
        offset=max(0, offset),
    )
    # 为已驳回项附加驳回理由
    from app.models.application import ApprovalRecord
    from app.models.identity import Teacher as TeacherModel

    # 收集所有已处理申请的审批记录
    processed_ids = [i.id for i in items if i.status in ("REJECTED", "EXECUTED")]
    comment_map = {}
    reviewer_map: dict = {}
    if processed_ids:
        rows = (
            await session.execute(
                select(
                    ApprovalRecord.application_id,
                    ApprovalRecord.decision,
                    ApprovalRecord.comment,
                    ApprovalRecord.approver_user_id,
                    ApprovalRecord.approval_type,
                )
                .where(ApprovalRecord.application_id.in_(processed_ids))
                .order_by(ApprovalRecord.decided_at.desc())
            )
        ).all()
        user_ids = [r[3] for r in rows if r[3]]
        teacher_names = {}
        if user_ids:
            trows = (
                await session.execute(
                    select(TeacherModel.user_id, TeacherModel.name).where(
                        TeacherModel.user_id.in_(user_ids)
                    )
                )
            ).all()
            teacher_names = {r[0]: r[1] for r in trows}
        # 按 app_id 收集所有审核人
        app_reviewers: dict = {}
        for row in rows:
            app_id = row[0]
            if app_id not in comment_map:
                comment_map[app_id] = row[2] or ""
            uid = row[3]
            if uid and uid in teacher_names:
                app_reviewers.setdefault(app_id, []).append(teacher_names[uid])
            elif uid:
                app_reviewers.setdefault(app_id, []).append("管理员")
            elif row[4] == "AUTO" and app_id not in app_reviewers:
                app_reviewers[app_id] = ["系统自动审核"]
        for app_id, names in app_reviewers.items():
            reviewer_map[app_id] = "、".join(dict.fromkeys(names))  # 去重保序
    result = []
    for i in items:
        d = i.model_dump()
        if i.status == "REJECTED":
            d["reject_reason"] = comment_map.get(i.id, "已驳回")
        d["reviewer"] = reviewer_map.get(i.id, "")
        result.append(d)
    return {"items": result, "total": total, "limit": limit, "offset": offset}


@router.get(
    "/students/me/adjustments/{application_id}",
    response_model=AdjustmentApplicationOut,
)
async def get_my_adjustment(
    application_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    student = await _student(session, user)
    item = await session.scalar(
        select(ApplicationRequest).where(
            ApplicationRequest.id == application_id,
            ApplicationRequest.student_id == student.id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="申请不存在。")
    return application_out(item)


@router.post(
    "/students/me/adjustments/{application_id}/cancel",
    response_model=AdjustmentApplicationOut,
)
async def cancel_my_adjustment(
    application_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    student = await _student(session, user)
    try:
        return await cancel_adjustment_application(
            session,
            student_id=student.id,
            application_id=application_id,
            actor_id=user.id,
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error


async def _review(
    *,
    application_id: UUID,
    body: AdjustmentReviewRequest,
    expected_actor: str,
    session: AsyncSession,
    user: UserProfile,
):
    _require_type(user, expected_actor)
    term = await get_or_create_active_term(session)
    try:
        return await review_adjustment_application(
            session,
            application_id=application_id,
            actor_id=user.id,
            actor_type=user.user_type,
            actor_login_name=user.login_name,
            decision=body.decision,
            comment=body.comment,
            term=term,
        )
    except (ValueError, LookupError, PermissionError) as error:
        raise _http_error(error) from error


@router.get("/teachers/me/pending-adjustments")
async def list_teacher_pending(
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    """教师查看待自己审批的补做申请。"""
    if user.user_type != "TEACHER":
        raise HTTPException(status_code=403, detail="仅教师可查")
    from app.models.identity import Teacher
    from app.models.scheduling import ExperimentSession

    teacher = (
        await session.execute(
            select(Teacher).where(Teacher.employee_no == user.login_name.upper())
        )
    ).scalar_one_or_none()
    if teacher is None:
        raise HTTPException(status_code=404, detail="教师信息不存在")
    # 找该教师授课的场次 IDs
    session_ids = (
        (
            await session.execute(
                select(ExperimentSession.id).where(
                    ExperimentSession.teacher_id == teacher.id
                )
            )
        )
        .scalars()
        .all()
    )
    if not session_ids:
        return []
    items = (
        (
            await session.execute(
                select(ApplicationRequest)
                .where(
                    ApplicationRequest.approval_route.in_(
                        ["TEACHER", "TEACHER_THEN_ADMIN"]
                    ),
                    ApplicationRequest.original_session_id.in_(session_ids),
                    ApplicationRequest.status == "PENDING_REVIEW",
                )
                .order_by(ApplicationRequest.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    from app.models.identity import Student as StudentModel

    student_ids = [i.student_id for i in items if i.student_id]
    student_map = {}
    if student_ids:
        rows = (
            await session.execute(
                select(
                    StudentModel.id, StudentModel.name, StudentModel.student_no
                ).where(StudentModel.id.in_(student_ids))
            )
        ).all()
        student_map = {r[0]: (r[1], r[2]) for r in rows}

    result = []
    for item in items:
        out = application_out(item).model_dump()
        sid = item.student_id
        if sid and sid in student_map:
            out["student_name"] = f"{student_map[sid][0]} · {student_map[sid][1]}"
        out["reason_text"] = item.reason
        result.append(out)
    return result


@router.post(
    "/teachers/me/adjustments/{application_id}/review",
    response_model=AdjustmentApplicationOut,
)
async def teacher_review_adjustment(
    application_id: UUID,
    body: AdjustmentReviewRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    return await _review(
        application_id=application_id,
        body=body,
        expected_actor="TEACHER",
        session=session,
        user=user,
    )


@router.get("/admin/adjustments")
async def list_all_adjustments(
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    """管理员查看所有申请（含学生姓名和项目名），分页返回。"""
    if user.user_type != "ADMIN":
        raise HTTPException(status_code=403, detail="仅管理员可查")
    from sqlalchemy import select as sa_select

    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    filters = [ApplicationRequest.status == status] if status is not None else []
    total = (
        await session.scalar(
            sa_select(func.count(ApplicationRequest.id)).where(*filters)
        )
    ) or 0
    stmt = (
        sa_select(ApplicationRequest)
        .where(*filters)
        .order_by(ApplicationRequest.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list((await session.execute(stmt)).scalars())

    # 批量加载学生和项目
    from app.models.curriculum import ExperimentProject
    from app.models.identity import Student as StudentModel

    student_ids = [i.student_id for i in items if i.student_id]
    project_ids = [i.project_id for i in items if i.project_id]
    student_map = {}
    project_map = {}
    if student_ids:
        rows = (
            await session.execute(
                sa_select(
                    StudentModel.id, StudentModel.name, StudentModel.student_no
                ).where(StudentModel.id.in_(student_ids))
            )
        ).all()
        student_map = {r[0]: (r[1], r[2]) for r in rows}
    if project_ids:
        rows = (
            await session.execute(
                sa_select(ExperimentProject.id, ExperimentProject.project_name).where(
                    ExperimentProject.id.in_(project_ids)
                )
            )
        ).all()
        project_map = {r[0]: r[1] for r in rows}

    result = []
    for item in items:
        out = application_out(item).model_dump()
        sid = item.student_id
        if sid and sid in student_map:
            out["student_name"] = f"{student_map[sid][0]} · {student_map[sid][1]}"
        else:
            out["student_name"] = ""
        # 教师申请：查教师姓名
        from app.models.identity import Teacher as TeacherModel, UserAccount
        if item.teacher_id:
            t = await session.get(TeacherModel, item.teacher_id)
            if t:
                out["teacher_name"] = t.name
        elif item.applicant_user_id and not out.get("student_name"):
            u = await session.get(UserAccount, item.applicant_user_id)
            if u:
                out["teacher_name"] = u.login_name
        pid = item.project_id
        out["project_name"] = project_map.get(pid, "") if pid else ""
        out["reason_text"] = item.reason
        payload = item.payload or {}
        out["source_info"] = payload.get("source", {}) or {}
        out["target_info"] = payload.get("target", {}) or {}
        # 教师调课: 从原始场次和 payload 补 source/target 信息
        if item.request_type in ("TEACHER_ADJUSTMENT", "LAB_CHANGE", "TEACHER_SUBSTITUTION") and item.original_session_id:
            from app.models.scheduling import ExperimentSession as ES
            from app.models.teaching_adjustment import SessionExecutionOverride as SEO
            orig_sess = await session.get(ES, item.original_session_id)
            if orig_sess:
                from app.models.curriculum import ExperimentProject as EP
                proj = await session.get(EP, orig_sess.project_id) if orig_sess.project_id else None
                # 已执行的调整从 SessionExecutionOverride.before_snapshot 取原始时间
                before: dict[str, object] = {}
                if item.status == "EXECUTED":
                    ov = await session.scalar(
                        select(SEO).where(
                            SEO.application_id == item.id,
                            SEO.status == "ACTIVE",
                        )
                    )
                    if ov is not None:
                        before = ov.before_snapshot
                out["source_info"] = {
                    "week_no": before.get("week_no") if "week_no" in before else orig_sess.week_no,
                    "day_of_week": before.get("day_of_week") if "day_of_week" in before else orig_sess.day_of_week,
                    "start_slot": before.get("start_slot") if "start_slot" in before else orig_sess.start_slot,
                    "end_slot": before.get("end_slot") if "end_slot" in before else orig_sess.end_slot,
                    "project_name": proj.project_name if proj else "",
                }
            if item.request_type == "TEACHER_ADJUSTMENT":
                tgt = payload.get("target_time", {})
                out["target_info"] = {
                    "week_no": tgt.get("week_no"), "day_of_week": tgt.get("day_of_week"),
                    "start_slot": tgt.get("start_slot"), "end_slot": tgt.get("end_slot"),
                    "project_name": out.get("project_name", ""),
                }
            elif item.request_type == "TEACHER_SUBSTITUTION":
                sub_tid = payload.get("substitute_teacher_id")
                if sub_tid:
                    from app.models.identity import Teacher as TModel
                    st = await session.get(TModel, sub_tid)
                    out["target_info"] = {"teacher_name": st.name if st else ""}
        elif item.original_session_id:
            # 学生调课（含资源异常迁移自动创建）：原场次 + 该学生当前已选场次
            from app.models.curriculum import ExperimentProject as EP
            from app.models.enrollment import StudentProjectRecord as SPR
            from app.models.scheduling import ExperimentSession as ES
            orig_sess = await session.get(ES, item.original_session_id)
            if orig_sess is not None:
                proj = await session.get(EP, orig_sess.project_id) if orig_sess.project_id else None
                out["source_info"] = {
                    "week_no": orig_sess.week_no,
                    "day_of_week": orig_sess.day_of_week,
                    "start_slot": orig_sess.start_slot,
                    "end_slot": orig_sess.end_slot,
                    "project_name": proj.project_name if proj else "",
                }
                if sid and orig_sess.project_id:
                    current = await session.scalar(
                        select(SPR).where(
                            SPR.student_id == sid,
                            SPR.project_id == orig_sess.project_id,
                            SPR.status == "SELECTED",
                        ).limit(1)
                    )
                    if current is not None and current.session_id and current.session_id != item.original_session_id:
                        tgt = await session.get(ES, current.session_id)
                        if tgt is not None:
                            tproj = await session.get(EP, tgt.project_id) if tgt.project_id else None
                            out["target_info"] = {
                                "week_no": tgt.week_no,
                                "day_of_week": tgt.day_of_week,
                                "start_slot": tgt.start_slot,
                                "end_slot": tgt.end_slot,
                                "project_name": tproj.project_name if tproj else "",
                            }
        # 最新审批记录的驳回意见
        if item.status in ("REJECTED",) and "reviewed_at" not in out:
            from app.models.application import ApprovalRecord

            last_record = (
                await session.execute(
                    select(ApprovalRecord.comment)
                    .where(
                        ApprovalRecord.application_id == item.id,
                    )
                    .order_by(ApprovalRecord.decided_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            out["review_comment"] = last_record or ""
        else:
            out["review_comment"] = ""
        result.append(out)

    # 为已执行的教师调整附加安置方案
    from app.models.teaching_adjustment import AdjustmentRemediationPlan as ARP2, AdjustmentRemediationItem as ARI2
    from app.models.identity import Student as Stu2
    from app.models.scheduling import ExperimentSession as ES3
    executed = []
    for r in result:
        rid = r["id"]
        if isinstance(rid, str):
            rid = UUID(rid)
        if r.get("status") == "EXECUTED" and r.get("request_type") in ("TEACHER_ADJUSTMENT", "LAB_CHANGE"):
            executed.append(rid)
    if executed:
        eplans = (await session.execute(select(ARP2).where(ARP2.application_id.in_(executed), ARP2.status == "EXECUTED"))).scalars().all()
        eitems: dict[UUID, list[dict]] = {}
        if eplans:
            eitem_rows = (await session.execute(
                select(ARI2, Stu2, ES3)
                .join(Stu2, Stu2.id == ARI2.student_id)
                .join(ES3, ES3.id == ARI2.target_session_id)
                .where(ARI2.plan_id.in_([p.id for p in eplans]))
            )).all()
            for ri, stu, sess in eitem_rows:
                eitems.setdefault(ri.plan_id, []).append({
                    "student_name": stu.name, "student_no": stu.student_no,
                    "week_no": sess.week_no, "day_of_week": sess.day_of_week,
                    "start_slot": sess.start_slot, "end_slot": sess.end_slot,
                })
        plan_by_app = {p.application_id: {"id": str(p.id), "plan_no": p.plan_no, "summary": p.summary, "items": eitems.get(p.id, [])} for p in eplans}
        for r in result:
            rid2 = r["id"] if isinstance(r["id"], UUID) else UUID(r["id"])
            if rid2 in plan_by_app:
                r["executed_plan"] = plan_by_app[rid2]
    return {"items": result, "total": total, "limit": limit, "offset": offset}


@router.post(
    "/admin/adjustments/{application_id}/review",
    response_model=AdjustmentApplicationOut,
)
async def admin_review_adjustment(
    application_id: UUID,
    body: AdjustmentReviewRequest,
    session: AsyncSession = Depends(get_db_session),
    user: UserProfile = Depends(get_current_user),
):
    return await _review(
        application_id=application_id,
        body=body,
        expected_actor="ADMIN",
        session=session,
        user=user,
    )


@router.get("/admin/notifications")
async def get_notifications(user: UserProfile = Depends(get_current_user)):
    """获取未读通知列表。"""
    if user.user_type != "ADMIN":
        raise HTTPException(status_code=403, detail="仅管理员可查")
    from json import loads

    from app.db.redis_client import get_redis_client

    items = await get_redis_client().lrange("admin:notifications", 0, -1)
    return [loads(i) for i in items]


@router.post("/admin/notifications/read")
async def read_notification(body: dict, user: UserProfile = Depends(get_current_user)):
    """标记单条通知已读（按值删除）。"""
    if user.user_type != "ADMIN":
        raise HTTPException(status_code=403, detail="仅管理员可查")
    from app.services.notification_service import remove_notification_by_value

    await remove_notification_by_value("admin:notifications", body.get("value", ""))
    return {"ok": True}
