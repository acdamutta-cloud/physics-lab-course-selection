from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
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


@router.get("/active-term")
async def get_active_term(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await sc_svc.get_active_term(session)


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
