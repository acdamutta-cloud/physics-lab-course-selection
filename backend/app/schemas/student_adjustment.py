from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.student_consultation import SelectionPreferences

AdjustmentRequestType = Literal["RESCHEDULE", "PROJECT_CHANGE", "MAKEUP", "TEACHER_ADJUSTMENT", "LAB_CHANGE", "TEACHER_SUBSTITUTION"]
AdjustmentDecision = Literal["ALLOW", "BLOCK", "REVIEW"]
ApprovalRoute = Literal["AUTO", "ADMIN", "TEACHER", "TEACHER_THEN_ADMIN"]
AdjustmentIntent = Literal[
    "CHECK_ADJUSTMENT_ELIGIBILITY",
    "RECOMMEND_RESCHEDULE",
    "RECOMMEND_PROJECT_CHANGE",
    "RECOMMEND_MAKEUP",
    "EXPLAIN_ADJUSTMENT_CONFLICT",
]


class AdjustmentViolation(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AdjustmentSessionSummary(BaseModel):
    session_id: UUID
    project_id: UUID
    project_name: str
    course_id: UUID
    course_name: str
    requirement_type: str
    week_no: int
    day_of_week: int
    day_name: str
    start_slot: int
    end_slot: int
    session_date: date
    started: bool
    teacher_name: str
    laboratory_name: str
    remaining: int


class AdjustmentSourceRecord(BaseModel):
    record_id: UUID
    status: str
    session: AdjustmentSessionSummary
    available_for: list[AdjustmentRequestType] = Field(default_factory=list)


class AdjustmentValidationResult(BaseModel):
    decision: AdjustmentDecision
    request_type: AdjustmentRequestType
    approval_route: ApprovalRoute
    source: AdjustmentSourceRecord | None = None
    target: AdjustmentSessionSummary | None = None
    violations: list[AdjustmentViolation] = Field(default_factory=list)
    warnings: list[AdjustmentViolation] = Field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision in {"ALLOW", "REVIEW"} and not self.violations


class AdjustmentPreviewRequest(BaseModel):
    request_type: AdjustmentRequestType
    source_record_id: UUID
    target_session_id: UUID


class AdjustmentRecommendationRequest(BaseModel):
    request_type: AdjustmentRequestType
    source_record_id: UUID
    preferences: SelectionPreferences = Field(default_factory=SelectionPreferences)
    max_options: Literal[1, 2, 3] = 3


class AdjustmentRecommendationOption(BaseModel):
    target: AdjustmentSessionSummary
    score: int
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    approval_route: ApprovalRoute
    can_submit: bool = True


class AdjustmentCreateRequest(AdjustmentPreviewRequest):
    reason: str = Field(min_length=2, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=64)


class AdjustmentReviewRequest(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    comment: str | None = Field(default=None, max_length=1000)


class AdjustmentApplicationOut(BaseModel):
    id: UUID
    request_no: str
    request_type: AdjustmentRequestType
    project_id: UUID | None
    target_project_id: UUID | None
    original_session_id: UUID | None
    target_session_id: UUID | None
    reason: str
    status: str
    approval_route: str | None
    reservation_status: str
    validation_result: dict[str, Any]
    payload: dict[str, Any]
    submitted_at: datetime | None
    executed_at: datetime | None
    created_at: datetime


class AdjustmentAgentRequest(BaseModel):
    request_type: AdjustmentRequestType
    source_record_id: UUID
    message: str = Field(min_length=1, max_length=4000)
    max_options: Literal[1, 2, 3] = 3


class AdjustmentAgentPlan(BaseModel):
    intent: AdjustmentIntent
    request_type: AdjustmentRequestType
    preferences: SelectionPreferences = Field(default_factory=SelectionPreferences)
    needs_clarification: bool = False
    clarification_question: str | None = None

    @model_validator(mode="after")
    def validate_intent_matches_request(self):
        expected = {
            "RESCHEDULE": "RECOMMEND_RESCHEDULE",
            "PROJECT_CHANGE": "RECOMMEND_PROJECT_CHANGE",
            "MAKEUP": "RECOMMEND_MAKEUP",
        }
        if self.intent.startswith("RECOMMEND_") and self.intent != expected[self.request_type]:
            raise ValueError("recommendation intent does not match request_type")
        return self
