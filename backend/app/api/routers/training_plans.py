from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db_session
from app.schemas.auth import UserProfile
from app.schemas.training_plan import (
    CreateTrainingPlanRequest,
    TrainingPlanDetailOut,
    TrainingPlanListOut,
    TrainingPlanListResponse,
    UpdateTrainingPlanRequest,
)
from app.services import training_plan_service as tp_svc

router = APIRouter(prefix="/training-plans", tags=["培养方案"])


def require_admin(user: UserProfile) -> None:
    if user.user_type != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可操作培养方案",
        )


def raise_service_error(error: tp_svc.TrainingPlanError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.get("", response_model=TrainingPlanListResponse)
async def list_plans(
    major_id: UUID | None = Query(None),
    enrollment_year: int | None = Query(None, ge=2000, le=2100),
    status_: Literal["DRAFT", "PUBLISHED", "ARCHIVED"] | None = Query(
        None, alias="status"
    ),
    keyword: str | None = Query(None, max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    return await tp_svc.list_plans(
        session,
        major_id,
        enrollment_year,
        status_,
        keyword,
        offset,
        limit,
    )


@router.get("/{plan_id}", response_model=TrainingPlanDetailOut)
async def get_plan_detail(
    plan_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    result = await tp_svc.get_plan_detail(session, plan_id)
    if result is None:
        raise HTTPException(status_code=404, detail="培养方案不存在")
    return result


@router.post("", response_model=TrainingPlanDetailOut, status_code=201)
async def create_plan(
    body: CreateTrainingPlanRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    try:
        return await tp_svc.create_plan(session, body, current_user.id)
    except tp_svc.TrainingPlanError as error:
        raise_service_error(error)


@router.put("/{plan_id}", response_model=TrainingPlanDetailOut)
async def update_plan(
    plan_id: UUID,
    body: UpdateTrainingPlanRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    try:
        return await tp_svc.update_plan(session, plan_id, body, current_user.id)
    except tp_svc.TrainingPlanError as error:
        raise_service_error(error)


@router.post("/{plan_id}/draft-copy", response_model=TrainingPlanDetailOut)
async def copy_plan_to_draft(
    plan_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    try:
        return await tp_svc.copy_plan_to_draft(
            session, plan_id, current_user.id
        )
    except tp_svc.TrainingPlanError as error:
        raise_service_error(error)


@router.post("/{plan_id}/publish", response_model=TrainingPlanListOut)
async def publish_plan(
    plan_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    try:
        return await tp_svc.publish_plan(session, plan_id, current_user.id)
    except tp_svc.TrainingPlanError as error:
        raise_service_error(error)


@router.post("/{plan_id}/archive", response_model=TrainingPlanListOut)
async def archive_plan(
    plan_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    require_admin(current_user)
    try:
        return await tp_svc.archive_plan(session, plan_id, current_user.id)
    except tp_svc.TrainingPlanError as error:
        raise_service_error(error)
