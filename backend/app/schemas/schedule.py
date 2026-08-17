from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# 排课软约束规则码规范列表：LLM 偏好解析的输出白名单，
# validation_agent 的校验白名单也使用同一来源。
PREFERENCE_RULE_CODES: tuple[str, ...] = (
    "STUDENT_AVAILABILITY_COVERAGE",
    "TEACHER_BALANCE",
    "EVENING_PENALTY",
    "WEEKEND_PENALTY",
    "TEACHER_COMPACTNESS",
    "TEACHER_CONSECUTIVE_LOAD",
    "TEACHER_PREFERRED_TIME",
    "LAB_UTILIZATION_BALANCE",
    "TEACHER_TARGET_LOAD_SCORE",
    "COURSE_EARLY_WEEK_PREFERENCE",
    "PROJECT_EARLY_WEEK_PREFERENCE",
)


class SchedulingWeekPreference(BaseModel):
    """课程/项目前置周偏好条目（LLM 输出的一部分）。"""

    course_id: UUID | None = None
    course_name: str | None = None
    project_id: UUID | None = None
    project_name: str | None = None
    preferred_end_week: int = Field(ge=1)


class SchedulingPreferenceItem(BaseModel):
    """LLM 输出的单条排课偏好，仅允许白名单规则码。"""

    rule_code: Literal[PREFERENCE_RULE_CODES]  # type: ignore[valid-type]
    preference_level: Literal["IGNORE", "DEFAULT", "PREFER", "STRONGLY_PREFER"]
    evidence: str = ""
    target_teacher_ids: list[UUID] = Field(default_factory=list)
    course_week_preferences: list[SchedulingWeekPreference] = Field(
        default_factory=list
    )
    project_week_preferences: list[SchedulingWeekPreference] = Field(
        default_factory=list
    )


class SchedulingPreferencePlan(BaseModel):
    """排课偏好解析 LLM 的整体输出。"""

    preferences: list[SchedulingPreferenceItem] = Field(default_factory=list)


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
