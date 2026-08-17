from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.crud.teaching_tasks import get_or_create_active_term
from app.db.redis_client import get_redis_client
from app.db.session import get_db_session
from app.models.identity import Student
from app.schemas.auth import UserProfile
from app.schemas.selection_plan import (
    SelectionPlanCreate,
    SelectionPlanExecute,
    SelectionPlanItemUpdate,
    SelectionPlanPrepare,
    SelectionPlanProjectReplace,
)
from app.services import selection_plan_service

router = APIRouter(prefix="/students/me/selection-plans", tags=["学生选课方案"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
CurrentUser = Annotated[UserProfile, Depends(get_current_user)]


async def _student(session: AsyncSession, user: UserProfile) -> Student:
    if user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可使用选课方案")
    value = await session.scalar(
        select(Student).where(Student.student_no == user.login_name.upper())
    )
    if value is None:
        raise HTTPException(status_code=404, detail="学生信息不存在")
    return value


def _raise_service_error(error: Exception) -> None:
    if isinstance(error, LookupError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("")
async def create_selection_plan(
    body: SelectionPlanCreate,
    session: DbSession,
    user: CurrentUser,
):
    student = await _student(session, user)
    term = await get_or_create_active_term(session)
    try:
        return await selection_plan_service.create_plan(
            get_redis_client(),
            session,
            student_id=student.id,
            term=term,
            plan=body.plan,
            preferences=body.preferences,
        )
    except (LookupError, ValueError) as error:
        _raise_service_error(error)


@router.get("/{plan_id}")
async def get_selection_plan(
    plan_id: UUID,
    session: DbSession,
    user: CurrentUser,
):
    student = await _student(session, user)
    try:
        return await selection_plan_service.get_plan(
            get_redis_client(), student_id=student.id, plan_id=plan_id
        )
    except (LookupError, ValueError) as error:
        _raise_service_error(error)


@router.post("/{plan_id}/items/{project_id}")
async def update_selection_plan_item(
    plan_id: UUID,
    project_id: UUID,
    body: SelectionPlanItemUpdate,
    session: DbSession,
    user: CurrentUser,
):
    student = await _student(session, user)
    try:
        return await selection_plan_service.update_item(
            get_redis_client(),
            session,
            student_id=student.id,
            plan_id=plan_id,
            project_id=project_id,
            session_id=body.session_id,
        )
    except (LookupError, ValueError) as error:
        _raise_service_error(error)


@router.post("/{plan_id}/items/{project_id}/project-alternatives")
async def get_optional_project_alternatives(
    plan_id: UUID,
    project_id: UUID,
    session: DbSession,
    user: CurrentUser,
):
    student = await _student(session, user)
    term = await get_or_create_active_term(session)
    try:
        (
            plan,
            alternatives,
        ) = await selection_plan_service.recommend_optional_project_replacements(
            get_redis_client(),
            session,
            student_id=student.id,
            term=term,
            plan_id=plan_id,
            project_id=project_id,
            limit=3,
        )
        return {"plan": plan, "alternatives": alternatives}
    except (LookupError, ValueError) as error:
        _raise_service_error(error)


@router.post("/{plan_id}/items/{project_id}/replace-project")
async def replace_optional_project(
    plan_id: UUID,
    project_id: UUID,
    body: SelectionPlanProjectReplace,
    session: DbSession,
    user: CurrentUser,
):
    student = await _student(session, user)
    try:
        return await selection_plan_service.replace_optional_project(
            get_redis_client(),
            session,
            student_id=student.id,
            plan_id=plan_id,
            project_id=project_id,
            target_project_id=body.target_project_id,
            session_id=body.session_id,
        )
    except (LookupError, ValueError) as error:
        _raise_service_error(error)


@router.post("/{plan_id}/preview")
async def preview_selection_plan(
    plan_id: UUID,
    session: DbSession,
    user: CurrentUser,
):
    student = await _student(session, user)
    try:
        return await selection_plan_service.preview_plan(
            get_redis_client(), session, student_id=student.id, plan_id=plan_id
        )
    except (LookupError, ValueError) as error:
        _raise_service_error(error)


@router.post("/{plan_id}/prepare")
async def prepare_selection_plan(
    plan_id: UUID,
    body: SelectionPlanPrepare,
    session: DbSession,
    user: CurrentUser,
):
    student = await _student(session, user)
    try:
        plan, preview = await selection_plan_service.prepare_plan(
            get_redis_client(),
            session,
            student_id=student.id,
            plan_id=plan_id,
            version=body.version,
        )
        return {"plan": plan, "preview": preview}
    except (LookupError, ValueError) as error:
        _raise_service_error(error)


@router.post("/{plan_id}/execute")
async def execute_selection_plan(
    plan_id: UUID,
    body: SelectionPlanExecute,
    session: DbSession,
    user: CurrentUser,
):
    student = await _student(session, user)
    term = await get_or_create_active_term(session)
    try:
        return await selection_plan_service.execute_plan(
            get_redis_client(),
            session,
            student_id=student.id,
            term=term,
            plan_id=plan_id,
            confirmation_token=body.confirmation_token,
        )
    except (LookupError, ValueError) as error:
        _raise_service_error(error)
