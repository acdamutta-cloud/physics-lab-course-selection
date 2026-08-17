from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.student_consultation import (
    RecommendationPlan,
    RecommendationSession,
    SelectionPreferences,
)


class SelectionPlanCreate(BaseModel):
    plan: RecommendationPlan
    preferences: SelectionPreferences = Field(default_factory=SelectionPreferences)


class SelectionPlanItemUpdate(BaseModel):
    session_id: UUID


class SelectionPlanProjectReplace(BaseModel):
    target_project_id: UUID
    session_id: UUID


class SelectionPlanPrepare(BaseModel):
    version: int = Field(ge=1)


class SelectionPlanExecute(BaseModel):
    confirmation_token: str = Field(min_length=16, max_length=128)


class OptionalProjectAlternative(BaseModel):
    project_id: UUID
    project_name: str
    category: str
    selected: RecommendationSession
    alternatives: list[RecommendationSession] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SelectionPlanItem(BaseModel):
    project_id: UUID
    selected: RecommendationSession
    alternatives: list[RecommendationSession] = Field(default_factory=list)
    original_project_id: UUID | None = None
    original_project_name: str | None = None
    project_alternatives: list[OptionalProjectAlternative] = Field(default_factory=list)
    adjusted: bool = False
    project_adjusted: bool = False
    status: Literal["PENDING", "SUCCEEDED", "FAILED"] = "PENDING"
    result_message: str | None = None


class SelectionPlanDraft(BaseModel):
    plan_id: UUID
    student_id: UUID
    term_id: UUID
    name: str
    coverage_status: Literal["COMPLETE", "PARTIAL"]
    preferences: SelectionPreferences
    items: list[SelectionPlanItem]
    retained_selections: list[RecommendationSession] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    version: int = 1
    status: Literal[
        "EDITING", "READY", "EXECUTING", "PARTIAL", "COMPLETED", "EXPIRED"
    ] = "EDITING"
    confirmation_token: str | None = None


class SelectionPlanPreview(BaseModel):
    valid: bool
    version: int
    new_count: int
    adjusted_count: int
    violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SelectionPlanExecutionResult(BaseModel):
    plan: SelectionPlanDraft
    succeeded: int
    failed: int
