from typing import Any, TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import AcademicTerm
from app.schemas.student_consultation import (
    ConsultationCard,
    ConsultationIntent,
    ConsultationMessage,
    SelectionPreferences,
    StudentAgentPlan,
    StudentPageContext,
    StudentToolRequest,
)


class StudentConsultationState(TypedDict, total=False):
    model: Any
    session: AsyncSession
    student_id: UUID
    term: AcademicTerm
    term_id: UUID
    trace_id: str
    messages: list[ConsultationMessage]
    page_context: StudentPageContext | None
    current_question: str
    conversation_context: list[ConsultationMessage]
    base_context: dict[str, Any]
    plan: StudentAgentPlan | None
    plan_validation_errors: list[str]
    repaired_plan_attempted: bool
    resolved_entities: dict[str, Any]
    clarification_question: str | None
    tool_requests: list[StudentToolRequest]
    tool_results: list[dict[str, Any]]
    grounding_bundle: dict[str, Any]
    intent: ConsultationIntent
    preferences: SelectionPreferences
    cards: list[ConsultationCard]
    warnings: list[str]
    unknowns: list[str]
    answer_buffer: str
    answer: str
    model_error: str | None
    grounding_passed: bool
