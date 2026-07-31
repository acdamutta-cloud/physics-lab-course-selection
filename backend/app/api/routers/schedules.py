from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.api.routers.training_plans import require_admin
from app.db.session import get_db_session
from app.schemas.auth import UserProfile
from app.schemas.schedule import (
    GenerateScheduleRequest,
    PublishScheduleRequest,
    ScheduleJobOut,
    SelectScheduleCandidateRequest,
)
from app.services import schedule_service

router = APIRouter(prefix="/schedule-jobs", tags=["AI 排课"])
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
CurrentUser = Annotated[UserProfile, Depends(get_current_user)]


def _raise_service_error(error: schedule_service.ScheduleServiceError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.post(
    "/generate",
    response_model=ScheduleJobOut,
    status_code=status.HTTP_201_CREATED,
)
async def generate_schedule(
    body: GenerateScheduleRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
):
    require_admin(current_user)
    try:
        return await schedule_service.generate_initial_schedule(
            session,
            body,
            current_user.id,
        )
    except schedule_service.ScheduleServiceError as error:
        _raise_service_error(error)


@router.get("/published", response_model=ScheduleJobOut)
async def get_published_schedule(
    session: DatabaseSession,
    current_user: CurrentUser,
    term_id: UUID | None = None,
):
    require_admin(current_user)
    try:
        return await schedule_service.get_published_schedule(
            session,
            term_id=term_id,
        )
    except schedule_service.ScheduleServiceError as error:
        _raise_service_error(error)


@router.get("/{job_id}", response_model=ScheduleJobOut)
async def get_schedule_job(
    job_id: UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
):
    require_admin(current_user)
    try:
        return await schedule_service.get_schedule_job(session, job_id)
    except schedule_service.ScheduleServiceError as error:
        _raise_service_error(error)


@router.post("/{job_id}/select", response_model=ScheduleJobOut)
async def select_candidate(
    job_id: UUID,
    body: SelectScheduleCandidateRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
):
    require_admin(current_user)
    try:
        return await schedule_service.select_schedule_candidate(
            session,
            job_id=job_id,
            version_id=body.schedule_version_id,
            actor_id=current_user.id,
        )
    except schedule_service.ScheduleServiceError as error:
        _raise_service_error(error)


@router.post("/{job_id}/publish", response_model=ScheduleJobOut)
async def publish_schedule(
    job_id: UUID,
    body: PublishScheduleRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
):
    require_admin(current_user)
    try:
        return await schedule_service.publish_selected_schedule(
            session,
            job_id=job_id,
            version_id=body.schedule_version_id,
            actor_id=current_user.id,
        )
    except schedule_service.ScheduleServiceError as error:
        _raise_service_error(error)
