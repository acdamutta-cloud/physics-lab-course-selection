from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.api.routers.training_plans import require_admin
from app.db.session import get_db_session
from app.schemas.auth import UserProfile
from app.schemas.training_plan import (
    CourseInfo,
    CreateProjectRequest,
    MajorInfo,
    ProjectInfo,
)
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
