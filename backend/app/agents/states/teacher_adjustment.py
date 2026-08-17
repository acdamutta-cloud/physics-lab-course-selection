from typing import Any, TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import AcademicTerm
from app.schemas.teacher_adjustment import (
    TeacherRescheduleAgentPlan,
    TeacherRescheduleOption,
)


class TeacherAdjustmentState(TypedDict, total=False):
    session: AsyncSession
    model: Any
    teacher_id: UUID
    term: AcademicTerm
    trace_id: str
    operation: str
    original_session_id: UUID
    message: str
    max_options: int
    plan: TeacherRescheduleAgentPlan
    options: list[TeacherRescheduleOption]
    answer: str
    model_error: str | None
    clarification_question: str | None
