from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.api.routers.training_plans import require_admin
from app.db.session import get_db_session
from app.schemas.auth import UserProfile
from app.schemas.teaching_task import (
    CreateTeachingTaskRequest,
    ProjectDemandOut,
    TeachingTaskListResponse,
    TeachingTaskOut,
    UpdateTeachingTaskRequest,
)
from app.schemas.training_plan import (
    CourseInfo,
    CreateProjectRequest,
    MajorInfo,
    ProjectInfo,
    UpdateProjectGroupingRequest,
)
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
                "equipment_name": eq_name,
                "model": getattr(eq, "model", "") or "",
                "total_quantity": inv.total_quantity,
                "usable_quantity": inv.usable_quantity,
                "disabled_quantity": inv.disabled_quantity,
                "note": _get_equipment_note(inv.total_quantity, lab.safety_capacity),
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
    campus = (await session.execute(select(Campus).limit(1))).scalar_one()
    lab = Laboratory(
        lab_code=body.get("lab_code", body["name"][:8]),
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
                equipment_code=f"EQ-{eq_name[:4].upper()}-{uuid4().hex[:4].upper()}",
                name=eq_name, model=eq_model, unit="台", status="ACTIVE",
            )
            session.add(existing_eq)
            await session.flush()

        session.add(LabEquipmentInventory(
            laboratory_id=lab.id,
            equipment_type_id=existing_eq.id,
            total_quantity=eq_data.get("total_quantity", 1),
            usable_quantity=eq_data.get("usable_quantity", 1),
            disabled_quantity=eq_data.get("total_quantity", 1) - eq_data.get("usable_quantity", 1),
        ))
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
    return await res_crud.add_equipment_to_lab(session, lab_id, body)


@router.put("/labs/{lab_id}/equipment/{inv_id}")
async def update_lab_equipment(
    lab_id: UUID, inv_id: UUID, body: dict,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    inv = await res_crud.update_equipment_inventory(session, inv_id, body)
    if inv is None:
        raise HTTPException(status_code=404, detail="设备记录不存在")
    return {
        "id": str(inv.id),
        "total_quantity": inv.total_quantity,
        "usable_quantity": inv.usable_quantity,
        "disabled_quantity": inv.disabled_quantity,
    }


@router.delete("/labs/{lab_id}/equipment/{inv_id}", status_code=204)
async def delete_lab_equipment(
    lab_id: UUID, inv_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
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
