from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class GenerateScheduleRequest(BaseModel):
    term_id: UUID | None = None
    preference_text: str = Field(default="", max_length=1000)


class SelectScheduleCandidateRequest(BaseModel):
    schedule_version_id: UUID


class PublishScheduleRequest(BaseModel):
    schedule_version_id: UUID


class ScheduleSessionOut(BaseModel):
    id: UUID
    session_code: str
    task_id: UUID
    project_id: UUID
    course_name: str
    project_name: str
    week_no: int
    day_of_week: int
    start_slot: int
    end_slot: int
    teacher_id: UUID
    teacher_name: str
    laboratory_id: UUID
    laboratory_code: str
    laboratory_name: str
    capacity: int
    selected_count: int


class ScheduleCandidateOut(BaseModel):
    id: UUID
    version_no: int
    status: str
    profile_code: str
    hard_constraint_passed: bool
    soft_score: float
    score_details: dict[str, Any]
    runtime_weights: dict[str, float]
    session_count: int
    sessions: list[ScheduleSessionOut]


class ScheduleJobOut(BaseModel):
    id: UUID
    term_id: UUID
    term_code: str
    status: str
    progress: int
    preference_text: str
    parsed_preferences: list[dict[str, Any]]
    comparison_weights: dict[str, float]
    warnings: list[str]
    selected_candidate_version_id: UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    candidates: list[ScheduleCandidateOut]
