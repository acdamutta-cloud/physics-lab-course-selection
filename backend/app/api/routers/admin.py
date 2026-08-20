import re
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.api.routers.training_plans import require_admin
from app.db.session import get_db_session
from app.schemas.auth import UserProfile
from app.schemas.selection_window import SelectionWindowConfigRequest
from app.schemas.teaching_task import (
    CreateTeachingTaskRequest,
    ProjectDemandOut,
    TeachingTaskListResponse,
    TeachingTaskOut,
    UpdateTeachingTaskRequest,
)
from app.schemas.training_plan import (
    CourseInfo,
    CreateCourseRequest,
    CreateProjectRequest,
    MajorInfo,
    ProjectInfo,
    UpdateProjectGroupingRequest,
)
from app.services import equipment_asset_service as asset_svc
from app.services import semester_course_service as sc_svc
from app.services import training_plan_service as tp_svc

router = APIRouter(prefix="/admin", tags=["管理"])


@router.get("/majors", response_model=list[MajorInfo])
async def list_majors(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await tp_svc.get_majors(session)


@router.get("/courses", response_model=list[CourseInfo])
async def list_courses(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await tp_svc.get_courses(session)


@router.post(
    "/courses",
    response_model=CourseInfo,
    status_code=status.HTTP_201_CREATED,
)
async def create_course(
    body: CreateCourseRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    try:
        return await tp_svc.create_course(session, body, current_user.id)
    except tp_svc.TrainingPlanError as error:
        raise HTTPException(
            status_code=error.status_code, detail=error.message
        )


@router.get("/courses/{course_id}/projects", response_model=list[ProjectInfo])
async def list_course_projects(
    course_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await tp_svc.get_course_projects(session, course_id)


@router.post(
    "/courses/{course_id}/projects",
    response_model=ProjectInfo,
    status_code=status.HTTP_201_CREATED,
)
async def create_course_project(
    course_id: UUID,
    body: CreateProjectRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    try:
        return await tp_svc.create_course_project(
            session, course_id, body, current_user.id
        )
    except tp_svc.TrainingPlanError as error:
        raise HTTPException(
            status_code=error.status_code, detail=error.message
        )


@router.delete(
    "/courses/{course_id}/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_course_project(
    course_id: UUID,
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    try:
        await tp_svc.delete_course_project(
            session, course_id, project_id, current_user.id
        )
    except tp_svc.TrainingPlanError as error:
        raise HTTPException(
            status_code=error.status_code, detail=error.message
        )


@router.put(
    "/courses/{course_id}/projects/{project_id}/grouping",
    response_model=ProjectInfo,
)
async def update_course_project_grouping(
    course_id: UUID,
    project_id: UUID,
    body: UpdateProjectGroupingRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    try:
        return await tp_svc.update_course_project_grouping(
            session, course_id, project_id, body, current_user.id
        )
    except tp_svc.TrainingPlanError as error:
        raise HTTPException(
            status_code=error.status_code, detail=error.message
        )


@router.get("/active-term")
async def get_active_term(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await sc_svc.get_active_term(session)


@router.put("/active-term")
async def update_active_term(
    body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await sc_svc.update_active_term(session, body)


@router.get("/selection-window")
async def get_selection_window(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """获取当前学期的选课时间窗口（未配置时返回 null）。"""

    require_admin(current_user)
    from app.services import selection_window_service as window_svc

    term = await sc_svc.get_active_term(session)
    window = await window_svc.get_term_window(session, term.id)
    if window is None:
        return None
    return {
        "id": str(window.id),
        "term_id": str(window.term_id),
        "start_at": window.start_at,
        "end_at": window.end_at,
        "withdraw_end_at": window.withdraw_end_at,
        "status": window.status,
    }


@router.put("/selection-window")
async def update_selection_window(
    body: SelectionWindowConfigRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """配置当前学期的选课时间窗口，保存后立即生效（Redis 缓存同步失效）。"""

    require_admin(current_user)
    from app.services import selection_window_service as window_svc

    term = await sc_svc.get_active_term(session)
    try:
        window = await window_svc.configure_term_window(
            session,
            term_id=term.id,
            start_at=body.start_at,
            end_at=body.end_at,
            withdraw_end_at=body.withdraw_end_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "id": str(window.id),
        "term_id": str(window.term_id),
        "start_at": window.start_at,
        "end_at": window.end_at,
        "withdraw_end_at": window.withdraw_end_at,
        "status": window.status,
    }


@router.get("/teaching-tasks", response_model=TeachingTaskListResponse)
async def list_teaching_tasks(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await sc_svc.list_teaching_tasks(session)


@router.post("/teaching-tasks", response_model=TeachingTaskOut, status_code=201)
async def sync_teaching_task(
    body: CreateTeachingTaskRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await sc_svc.sync_teaching_task(session, body, current_user.id)


@router.put("/teaching-tasks/{task_id}", response_model=TeachingTaskOut)
async def update_teaching_task(
    task_id: UUID,
    body: UpdateTeachingTaskRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    result = await sc_svc.update_teaching_task(session, task_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail="教学任务不存在")
    return result


@router.get("/students/total")
async def get_total_students(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    from app.crud.students import count_total_active_students
    total = await count_total_active_students(session)
    return {"total": total}


@router.delete("/teaching-tasks/{task_id}", status_code=204)
async def delete_teaching_task(
    task_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    success = await sc_svc.delete_teaching_task(session, task_id)
    if not success:
        raise HTTPException(status_code=404, detail="教学任务不存在")


@router.delete("/teaching-tasks/{task_id}/demands/{demand_id}", status_code=204)
async def delete_project_demand(
    task_id: UUID,
    demand_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    success = await sc_svc.delete_project_demand(session, demand_id)
    if not success:
        raise HTTPException(status_code=404, detail="项目需求不存在")


@router.post("/teaching-tasks/{task_id}/demands", response_model=TeachingTaskOut, status_code=201)
async def add_project_to_task(
    task_id: UUID,
    body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    result = await sc_svc.add_project_to_task(session, task_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail="教学任务不存在")
    return result


@router.put("/teaching-tasks/{task_id}/demands/{demand_id}", response_model=ProjectDemandOut)
async def update_project_demand(
    task_id: UUID,
    demand_id: UUID,
    body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    result = await sc_svc.update_project_demand(session, demand_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail="项目需求不存在")
    return result

@router.post("/teaching-tasks/sync-all", response_model=TeachingTaskListResponse)
async def sync_all_teaching_tasks(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await sc_svc.sync_all_teaching_tasks(session, current_user.id)


# ── 实验室 / 设备管理 ──

from app.crud import resources as res_crud


def _get_equipment_note(total_qty: int, capacity: int) -> str:
    if capacity <= 0 or total_qty <= 0:
        return ""
    ratio = capacity / total_qty if total_qty > 0 else 999
    if ratio >= 3.5:
        # 设备数量 ≤ 容量/3.5 → 多人共用
        return f"多人共用，{total_qty}台供全班轮流使用"
    if ratio >= 1.5:
        # 设备数量 ≈ 容量/2 → 两人一组
        per_group = round(ratio)
        return f"{per_group}人一台"
    return ""  # 每人一台或接近，不需要备注


@router.get("/labs")
async def list_labs(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    labs = await res_crud.get_labs(session)
    result = []
    for lab in labs:
        equip_list = []
        for inv in (lab.equipment_inventory or []):
            eq = inv.equipment_type
            eq_name = getattr(eq, "name", "")
            equip_list.append({
                "id": str(inv.id),
                "equipment_type_id": str(inv.equipment_type_id),
                "equipment_name": eq_name,
                "model": getattr(eq, "model", "") or "",
                "total_quantity": inv.total_quantity,
                "usable_quantity": inv.usable_quantity,
                "disabled_quantity": inv.disabled_quantity,
                "note": inv.usage_note or _get_equipment_note(inv.total_quantity, lab.safety_capacity),
                "usage_note": inv.usage_note or "",
                "students_per_unit": inv.students_per_unit,
                "sharing_rule_status": inv.sharing_rule_status,
                "sharing_rule_evidence": inv.sharing_rule_evidence,
            })
        result.append({
            "id": str(lab.id),
            "lab_code": lab.lab_code,
            "name": lab.name,
            "room_type": lab.room_type or "",
            "safety_capacity": lab.safety_capacity,
            "status": lab.status,
            "equipment": equip_list,
        })
    return result


@router.post("/labs", status_code=201)
async def create_lab(
    body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await res_crud.create_lab(session, body, current_user.id)


@router.put("/labs/{lab_id}")
async def update_lab(
    lab_id: UUID, body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    lab = await res_crud.update_lab(session, lab_id, body)
    if lab is None:
        raise HTTPException(status_code=404, detail="实验室不存在")
    return {"message": "ok"}


@router.delete("/labs/{lab_id}", status_code=204)
async def delete_lab(
    lab_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    from app.models.resources import (
        EquipmentAsset,
        LabEquipmentInventory,
        ResourceIssueReport,
    )
    from app.models.scheduling import ExperimentSession

    asset_count = await session.scalar(
        select(func.count(func.distinct(EquipmentAsset.id)))
        .join(
            LabEquipmentInventory,
            EquipmentAsset.current_inventory_id == LabEquipmentInventory.id,
        )
        .where(
            (EquipmentAsset.origin_laboratory_id == lab_id)
            | (LabEquipmentInventory.laboratory_id == lab_id)
        )
    )
    session_count = await session.scalar(
        select(func.count(ExperimentSession.id)).where(
            ExperimentSession.laboratory_id == lab_id
        )
    )
    issue_count = await session.scalar(
        select(func.count(ResourceIssueReport.id)).where(
            ResourceIssueReport.laboratory_id == lab_id
        )
    )
    asset_count = asset_count or 0
    blockers = []
    if asset_count:
        blockers.append(f"{asset_count} 台有永久编号的仪器资产")
    if session_count:
        blockers.append(f"{session_count} 个实验场次")
    if issue_count:
        blockers.append(f"{issue_count} 条资源异常记录")
    if blockers:
        raise HTTPException(
            status_code=409,
            detail=(
                f"无法删除实验室：仍存在{'、'.join(blockers)}。"
                "请先完成仪器调拨或报废归档，并解除相关排课引用。"
            ),
        )
    ok = await res_crud.delete_lab(session, lab_id)
    if not ok:
        raise HTTPException(status_code=404, detail="实验室不存在")


@router.post("/labs/batch-create", status_code=201)
async def batch_create_lab_with_equipment(
    body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """创建实验室并批量添加设备类型和台账。"""
    require_admin(current_user)
    from app.models.identity import Campus
    from app.models.resources import EquipmentType, LabEquipmentInventory, Laboratory
    from app.services.equipment_usage_note_service import interpret_equipment_usage_note
    campus = (await session.execute(select(Campus).limit(1))).scalar_one()
    lab_code = str(body.get("lab_code", "")).strip().upper()
    code_pattern = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,15}$")
    if not code_pattern.fullmatch(lab_code):
        raise HTTPException(
            status_code=422,
            detail="实验室编号须为 2 至 16 位大写字母、数字、下划线或短横线",
        )
    if await session.scalar(
        select(func.count()).select_from(Laboratory).where(
            Laboratory.lab_code == lab_code
        )
    ):
        raise HTTPException(status_code=409, detail="实验室编号已存在")
    lab = Laboratory(
        lab_code=lab_code,
        name=body["name"], campus_id=campus.id,
        safety_capacity=body.get("safety_capacity", 24), status="ACTIVE",
    )
    session.add(lab)
    await session.flush()

    equip_created = 0
    for eq_data in body.get("equipment", []):
        if not eq_data.get("name"):
            continue
        # 创建或查找 EquipmentType
        eq_name = eq_data["name"]
        eq_model = eq_data.get("model", "")
        existing_eq = (await session.execute(
            select(EquipmentType).where(
                EquipmentType.name == eq_name,
                EquipmentType.model == eq_model,
            )
        )).scalar_one_or_none()
        if existing_eq is None:
            existing_eq = EquipmentType(
                equipment_code=f"EQ-{uuid4().hex[:8].upper()}",
                name=eq_name, model=eq_model, unit="台", status="ACTIVE",
            )
            session.add(existing_eq)
            await session.flush()

        rule = interpret_equipment_usage_note(
            eq_data.get("usage_note", eq_data.get("note"))
        )
        inventory = LabEquipmentInventory(
            laboratory_id=lab.id,
            equipment_type_id=existing_eq.id,
            total_quantity=0,
            usable_quantity=0,
            disabled_quantity=0,
            usage_note=rule.usage_note or None,
            students_per_unit=rule.students_per_unit,
            sharing_rule_status=rule.sharing_rule_status,
            sharing_rule_source=rule.source,
            sharing_rule_evidence=rule.evidence,
        )
        session.add(inventory)
        await session.flush()
        total_quantity = int(eq_data.get("total_quantity", 1))
        usable_quantity = int(eq_data.get("usable_quantity", total_quantity))
        await asset_svc.batch_create_assets(
            session, lab_id=lab.id, equipment_type_id=existing_eq.id,
            quantity=total_quantity, available_quantity=usable_quantity,
            actor_id=current_user.id, commit=False,
        )
        equip_created += 1

    await session.commit()
    return {"id": str(lab.id), "name": lab.name, "equipment_created": equip_created}


@router.post("/labs/{lab_id}/equipment", status_code=201)
async def add_lab_equipment(
    lab_id: UUID, body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    from app.models.resources import (
        EquipmentType,
        LabEquipmentInventory,
        Laboratory,
    )
    from app.services.equipment_usage_note_service import (
        interpret_equipment_usage_note,
    )

    lab = await session.get(Laboratory, lab_id)
    if lab is None:
        raise HTTPException(status_code=404, detail="实验室不存在")
    name = str(body.get("name", "")).strip()
    model = str(body.get("model", "")).strip()
    if not name or len(name) > 100 or len(model) > 100:
        raise HTTPException(status_code=422, detail="请填写有效的器材名称和型号")
    try:
        quantity = int(body.get("quantity", 0))
        available_quantity = int(body.get("available_quantity", quantity))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="器材数量格式不正确") from error
    if quantity < 1 or quantity > 500:
        raise HTTPException(status_code=422, detail="账面数量必须在 1 到 500 之间")
    if available_quantity < 0 or available_quantity > quantity:
        raise HTTPException(status_code=422, detail="可用数量不能超过账面数量")

    equipment = await session.scalar(
        select(EquipmentType).where(
            EquipmentType.name == name,
            EquipmentType.model == model,
        )
    )
    if equipment is None:
        equipment = EquipmentType(
            equipment_code=f"EQ-{uuid4().hex[:8].upper()}",
            name=name,
            model=model,
            unit="台",
            status="ACTIVE",
        )
        session.add(equipment)
        await session.flush()
    existing_inventory = await session.scalar(
        select(LabEquipmentInventory).where(
            LabEquipmentInventory.laboratory_id == lab_id,
            LabEquipmentInventory.equipment_type_id == equipment.id,
        )
    )
    if existing_inventory is not None:
        raise HTTPException(
            status_code=409,
            detail="当前实验室已有该器材，请使用“登记仪器”增加数量",
        )

    assets = await asset_svc.batch_create_assets(
        session,
        lab_id=lab_id,
        equipment_type_id=equipment.id,
        quantity=quantity,
        available_quantity=available_quantity,
        actor_id=current_user.id,
        commit=False,
    )
    inventory = await session.get(
        LabEquipmentInventory, assets[0].current_inventory_id
    )
    rule = interpret_equipment_usage_note(body.get("usage_note"))
    inventory.usage_note = rule.usage_note or None
    inventory.students_per_unit = rule.students_per_unit
    inventory.sharing_rule_status = rule.sharing_rule_status
    inventory.sharing_rule_source = rule.source
    inventory.sharing_rule_evidence = rule.evidence
    await session.commit()
    return {
        "id": str(inventory.id),
        "equipment_type_id": str(equipment.id),
        "equipment_code": equipment.equipment_code,
        "instrument_numbers": [asset.instrument_no for asset in assets],
    }


@router.put("/labs/{lab_id}/equipment/{inv_id}")
async def update_lab_equipment(
    lab_id: UUID, inv_id: UUID, body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    if {"total_quantity", "usable_quantity", "disabled_quantity"} & body.keys():
        raise HTTPException(
            status_code=409,
            detail="汇总数量由单台仪器状态自动计算，禁止直接修改。",
        )
    inv = await res_crud.update_equipment_inventory(session, inv_id, body)
    if inv is None:
        raise HTTPException(status_code=404, detail="设备记录不存在")
    return {
        "id": str(inv.id),
        "total_quantity": inv.total_quantity,
        "usable_quantity": inv.usable_quantity,
        "disabled_quantity": inv.disabled_quantity,
        "usage_note": inv.usage_note or "",
        "students_per_unit": inv.students_per_unit,
        "sharing_rule_status": inv.sharing_rule_status,
        "sharing_rule_evidence": inv.sharing_rule_evidence,
    }


@router.post("/labs/{lab_id}/equipment/{inv_id}/interpret-usage-note")
async def interpret_lab_equipment_usage_note(
    lab_id: UUID, inv_id: UUID, body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    from app.models.resources import LabEquipmentInventory
    from app.services.equipment_usage_note_service import interpret_equipment_usage_note

    inv = await session.get(LabEquipmentInventory, inv_id)
    if inv is None or inv.laboratory_id != lab_id:
        raise HTTPException(status_code=404, detail="设备台账不存在。")
    rule = interpret_equipment_usage_note(body.get("usage_note", body.get("note")))
    return {
        "usage_note": rule.usage_note,
        "students_per_unit": rule.students_per_unit,
        "sharing_rule_status": rule.sharing_rule_status,
        "evidence": rule.evidence,
    }


@router.delete("/labs/{lab_id}/equipment/{inv_id}", status_code=204)
async def delete_lab_equipment(
    lab_id: UUID, inv_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    from app.models.resources import EquipmentAsset, LabEquipmentInventory

    inventory = await session.get(LabEquipmentInventory, inv_id)
    if inventory is None or inventory.laboratory_id != lab_id:
        raise HTTPException(status_code=404, detail="设备台账不存在")

    asset_count = await session.scalar(
        select(func.count(EquipmentAsset.id)).where(
            EquipmentAsset.current_inventory_id == inv_id
        )
    )
    if asset_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"无法删除设备台账：仍关联 {asset_count} 台有永久编号的仪器。"
                "请通过“查看编号”核对资产；需要移除时应先完成调拨或报废归档。"
            ),
        )
    ok = await res_crud.delete_equipment_inventory(session, inv_id)
    if not ok:
        raise HTTPException(status_code=404, detail="设备记录不存在")


@router.get("/equipment-types")
async def list_equipment_types(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    items = await res_crud.get_active_equipment_types(session)
    return [{"id": str(e.id), "name": e.name, "model": e.model or "", "equipment_code": e.equipment_code} for e in items]


@router.get("/labs/{lab_id}/equipment-assets")
async def list_equipment_assets(
    lab_id: UUID, equipment_type_id: UUID | None = None, status: str | None = None,
    q: str = "", page: int = 1, page_size: int = 50,
    session: AsyncSession = Depends(get_db_session), current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await asset_svc.list_lab_assets(
        session, lab_id, equipment_type_id=equipment_type_id, status=status,
        query=q, page=max(1, page), page_size=min(200, max(1, page_size)),
    )


@router.post("/labs/{lab_id}/equipment-assets/batch", status_code=201)
async def create_equipment_assets(
    lab_id: UUID, body: dict,
    session: AsyncSession = Depends(get_db_session), current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    quantity = int(body.get("quantity", 0))
    if quantity < 1 or quantity > 500:
        raise HTTPException(status_code=422, detail="登记数量必须在 1 到 500 之间")
    try:
        assets = await asset_svc.batch_create_assets(session, lab_id=lab_id, equipment_type_id=UUID(body["equipment_type_id"]), quantity=quantity, actor_id=current_user.id)
        return [{"id": str(item.id), "instrument_no": item.instrument_no, "status": item.status} for item in assets]
    except (KeyError, ValueError, LookupError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/equipment-assets/{asset_id}/transfer")
async def transfer_equipment_asset(
    asset_id: UUID, body: dict,
    session: AsyncSession = Depends(get_db_session), current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    try:
        item = await asset_svc.transfer_asset(session, asset_id=asset_id, target_lab_id=UUID(body["target_laboratory_id"]), actor_id=current_user.id)
        return {"id": str(item.id), "instrument_no": item.instrument_no, "status": item.status}
    except (KeyError, ValueError, LookupError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/equipment-assets/{asset_id}/events")
async def list_equipment_asset_events(
    asset_id: UUID, session: AsyncSession = Depends(get_db_session), current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await asset_svc.asset_events(session, asset_id)


@router.get("/project-resource-options")
async def list_project_resource_options(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    from app.models.identity import Teacher
    from app.models.resources import EquipmentType
    teachers = (await session.execute(select(Teacher).where(Teacher.status == "ACTIVE").order_by(Teacher.name))).scalars().all()
    equipment = (await session.execute(select(EquipmentType).where(EquipmentType.status == "ACTIVE").order_by(EquipmentType.name))).scalars().all()
    return {
        "teachers": [{"id": str(item.id), "name": item.name} for item in teachers],
        "equipment": [{"id": str(item.id), "name": item.name, "model": item.model or ""} for item in equipment],
    }


@router.put("/projects/{project_id}/resources")
async def update_project_resources(
    project_id: UUID, body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    try:
        teacher_ids = [UUID(value) for value in body.get("teacher_ids", [])]
        equipment_ids = [UUID(value) for value in body.get("equipment_ids", [])]
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="教师或器材 ID 格式不正确")
    ok = await sc_svc.update_project_resources(session, project_id, teacher_ids, equipment_ids)
    if not ok:
        raise HTTPException(status_code=404, detail="实验项目不存在")
    return {"success": True}


@router.get("/teachers/{teacher_id}/busy-bitmap")
async def get_teacher_bitmap(
    teacher_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    from app.crud.students import get_teacher_bitmap as get_tb
    from app.crud.teaching_tasks import get_or_create_active_term
    import base64
    term = await get_or_create_active_term(session)
    bm = await get_tb(session, teacher_id, term.id)
    if bm is None:
        return {"weeks": 18, "days": 7, "slots": 12, "data": None}
    return {
        "weeks": bm.end_week - bm.start_week + 1,
        "days": bm.days_per_week,
        "slots": bm.slots_per_day,
        "data": base64.b64encode(bm.bitmap).decode(),
        "start_week": bm.start_week,
        "end_week": bm.end_week,
    }
