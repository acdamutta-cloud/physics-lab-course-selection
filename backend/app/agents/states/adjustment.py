from typing import Any, TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import AcademicTerm
from app.schemas.student_adjustment import (
    AdjustmentAgentPlan,
    AdjustmentRecommendationOption,
    AdjustmentRequestType,
)


class StudentAdjustmentState(TypedDict, total=False):
    model: Any
    session: AsyncSession
    student_id: UUID
    term: AcademicTerm
    trace_id: str
    request_type: AdjustmentRequestType
    source_record_id: UUID
    message: str
    max_options: int
    actor_type: str
    change_scope: str
    plan: AdjustmentAgentPlan | None
    context: dict[str, object]
    tool_results: list[AdjustmentRecommendationOption]
    grounding_bundle: dict[str, object]
    answer: str
    cards: list[dict[str, object]]
    warnings: list[str]
    model_error: str | None
    clarification_question: str | None
