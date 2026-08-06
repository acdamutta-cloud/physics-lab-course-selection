from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import AcademicTerm
from app.schemas.student_adjustment import AdjustmentRequestType
from app.schemas.student_consultation import SelectionPreferences
from app.services.student_adjustment_service import (
    get_adjustment_context,
    recommend_adjustment_options,
    validate_student_adjustment,
)


async def get_student_adjustment_context(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    request_type: AdjustmentRequestType,
    source_record_id: UUID | None = None,
):
    return await get_adjustment_context(
        session,
        student_id=student_id,
        term=term,
        request_type=request_type,
        source_record_id=source_record_id,
    )


async def validate_adjustment_target(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    request_type: AdjustmentRequestType,
    source_record_id: UUID,
    target_session_id: UUID,
):
    return await validate_student_adjustment(
        session,
        student_id=student_id,
        term=term,
        request_type=request_type,
        source_record_id=source_record_id,
        target_session_id=target_session_id,
    )


async def recommend_student_adjustments(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    request_type: AdjustmentRequestType,
    source_record_id: UUID,
    preferences: SelectionPreferences,
    max_options: int,
):
    return await recommend_adjustment_options(
        session,
        student_id=student_id,
        term=term,
        request_type=request_type,
        source_record_id=source_record_id,
        preferences=preferences,
        max_options=max_options,
    )


async def explain_adjustment_conflicts(**kwargs):
    result = await validate_adjustment_target(**kwargs)
    return {
        "decision": result.decision,
        "violations": [item.model_dump(mode="json") for item in result.violations],
        "warnings": [item.model_dump(mode="json") for item in result.warnings],
        "approval_route": result.approval_route,
    }


async def build_adjustment_draft(**kwargs):
    """Build a read-only draft; this tool never persists an application."""

    result = await validate_adjustment_target(**kwargs)
    return {
        "can_submit": result.allowed,
        "request_type": result.request_type,
        "approval_route": result.approval_route,
        "source": result.source.model_dump(mode="json") if result.source else None,
        "target": result.target.model_dump(mode="json") if result.target else None,
        "violations": [item.model_dump(mode="json") for item in result.violations],
    }
