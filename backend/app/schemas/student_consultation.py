from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

WEEKDAY_NAMES = ("周日", "周一", "周二", "周三", "周四", "周五", "周六")
WeekdayName = Literal["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
TimePeriod = Literal["MORNING", "AFTERNOON", "EVENING"]
WeekNumber = Annotated[int, Field(ge=1, le=18)]
WEEKDAY_NUMBERS = {name: index for index, name in enumerate(WEEKDAY_NAMES, start=1)}


def weekday_name(day_of_week: int) -> str:
    """Return the canonical Sunday-first display name used by the timetable."""

    if not 1 <= day_of_week <= 7:
        raise ValueError("day_of_week must be between 1 and 7")
    return WEEKDAY_NAMES[day_of_week - 1]


def weekday_full_name(day_of_week: int) -> str:
    """Return 星期日..星期六 using the canonical Sunday-first numbering."""

    return f"星期{weekday_name(day_of_week)[1:]}"


def weekday_number(day_name: WeekdayName) -> int:
    """Convert a Chinese weekday name to the canonical Sunday-first number."""

    return WEEKDAY_NUMBERS[day_name]


ConsultationIntent = Literal[
    "GENERAL_CHAT",
    "OUT_OF_SCOPE",
    "BASIC_INFO_QUERY",
    "CHECK_ELIGIBILITY",
    "EXPLAIN_CONFLICT",
    "QUERY_CURRENT_SELECTION",
    "RECOMMEND_SELECTION",
    "DESELECT_SELECTION",
    "SYSTEM_GUIDE",
    "START_ADJUSTMENT",
    "UNKNOWN",
]


class ConsultationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class StudentPageContext(BaseModel):
    view: Literal["home", "schedule", "selection", "applications", "ai"] = "ai"
    course_id: UUID | None = None
    project_id: UUID | None = None
    session_id: UUID | None = None


class ConsultationRequest(BaseModel):
    messages: list[ConsultationMessage] = Field(min_length=1, max_length=20)
    page_context: StudentPageContext | None = None


StudentToolName = Literal[
    "lookup_student_rules",
    "get_training_plan_context",
    "get_remaining_projects",
    "check_selection_eligibility",
    "explain_selection_conflicts",
    "recommend_selection_plans",
    "preview_deselection",
    "lookup_operation_guide",
    "prepare_adjustment_entry",
]

StudentRuleTopic = Literal[
    "ACADEMIC_STATUS",
    "STUDY_PERIOD",
    "PREREQUISITE",
    "COURSE_COMPLETION",
    "SESSION_AVAILABILITY",
    "PROJECT_UNIQUENESS",
    "TIME_CONFLICT",
    "APPLICATION_OCCUPANCY",
    "PROJECT_ORDER",
    "OTHER",
]


class EntityReference(BaseModel):
    course_name: str | None = None
    course_names: list[str] = Field(default_factory=list, max_length=8)
    project_name: str | None = None
    project_names: list[str] = Field(default_factory=list, max_length=16)
    teacher_name: str | None = None
    session_id: UUID | None = None
    week_no: int | None = Field(default=None, ge=1, le=18)
    day_name: WeekdayName | None = None
    start_slot: int | None = Field(default=None, ge=1, le=12)
    end_slot: int | None = Field(default=None, ge=1, le=12)
    conversation_reference: str | None = None


class StudentToolRequest(BaseModel):
    name: StudentToolName
    arguments: dict[str, object] = Field(default_factory=dict)


class RecommendationScope(BaseModel):
    mode: Literal["ALL_ELIGIBLE", "COURSES", "PROJECTS"] = "ALL_ELIGIBLE"
    course_names: list[str] = Field(default_factory=list, max_length=8)
    project_names: list[str] = Field(default_factory=list, max_length=16)


class StudentAgentPlan(BaseModel):
    intent: ConsultationIntent
    request_mode: Literal[
        "ASK_CAPABILITY", "ASK_STEPS", "EXECUTE", "QUERY", "SAFETY_REFUSAL"
    ] = "QUERY"
    operation_stage: Literal["PLAN_DRAFT", "ENROLLED", "UNSPECIFIED"] = (
        "UNSPECIFIED"
    )
    requested_application_type: Literal["RESCHEDULE", "PROJECT_CHANGE", "MAKEUP"] | None = None
    entity_reference: EntityReference | None = None
    preferences: "SelectionPreferences" = Field(
        default_factory=lambda: SelectionPreferences()
    )
    tool_requests: list[StudentToolRequest] = Field(default_factory=list, max_length=3)
    rule_topics: list[StudentRuleTopic] = Field(default_factory=list, max_length=3)
    recommendation_scope: RecommendationScope = Field(
        default_factory=RecommendationScope
    )
    deselection_scope: Literal["TARGETED", "ALL"] = "TARGETED"
    needs_clarification: bool = False
    clarification_question: str | None = None
    direct_answer_allowed: bool = False
    term_fact_query: Literal["NONE", "CURRENT_WEEK", "SELECTION_WINDOW"] = "NONE"


class SelectionViolation(BaseModel):
    code: str
    scope: Literal["COURSE", "PROJECT", "SESSION", "DATA", "WINDOW"]
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class SelectionEligibilityResult(BaseModel):
    decision: Literal["ALLOW", "BLOCK", "UNKNOWN"]
    student_id: UUID
    session_id: UUID
    term_id: UUID | None = None
    project_id: UUID | None = None
    course_id: UUID | None = None
    violations: list[SelectionViolation] = Field(default_factory=list)
    warnings: list[SelectionViolation] = Field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return self.decision == "ALLOW"


class ConsultationCard(BaseModel):
    type: Literal[
        "ELIGIBILITY",
        "CONFLICT",
        "TRAINING_PLAN",
        "RECOMMENDATION",
        "DESELECTION",
        "GUIDE",
        "APPLICATION_ENTRY",
    ]
    title: str
    summary: str
    data: dict[str, object] = Field(default_factory=dict)


class WeekRangePreference(BaseModel):
    start_week: WeekNumber | None = None
    start_inclusive: bool = True
    end_week: WeekNumber | None = None
    end_inclusive: bool = True

    @model_validator(mode="after")
    def validate_effective_range(self) -> Self:
        if self.start_week is None and self.end_week is None:
            raise ValueError("week_range must define at least one boundary")
        effective_start = (
            self.start_week + (0 if self.start_inclusive else 1)
            if self.start_week is not None
            else 1
        )
        effective_end = (
            self.end_week - (0 if self.end_inclusive else 1)
            if self.end_week is not None
            else 18
        )
        if effective_start > effective_end:
            raise ValueError("week_range does not contain any teaching week")
        return self


class SelectionPreferences(BaseModel):
    avoid_weekend: bool = Field(
        default=False, description="学生表达不要/尽量不安排周末时设为 true"
    )
    avoid_evening: bool = Field(
        default=False, description="学生表达不要/尽量不安排晚上(第9-12节)时设为 true"
    )
    preferred_periods: list[TimePeriod] = Field(
        default_factory=list,
        description="学生喜欢/优先的时间段:MORNING上午、AFTERNOON下午、EVENING晚上",
    )
    avoided_periods: list[TimePeriod] = Field(
        default_factory=list,
        description="学生不喜欢/尽量避开的时间段:MORNING上午、AFTERNOON下午、EVENING晚上",
    )
    preferred_days: list[WeekdayName] = Field(
        default_factory=list, description="学生喜欢/优先的星期(周日、周一…周六)"
    )
    avoided_days: list[WeekdayName] = Field(
        default_factory=list, description="学生不喜欢/尽量避开的星期(周日、周一…周六)"
    )
    week_range: WeekRangePreference | None = Field(
        default=None, description="学生指定的周次范围,如第9周及以后、第5周以前、第6到第10周"
    )
    avoided_weeks: list[WeekNumber] = Field(
        default_factory=list, description="学生明确表示尽量不选的单个教学周"
    )
    preferred_categories: list[
        Literal["BASIC", "MECHANICS", "ELECTRICITY", "OPTICS", "MODERN"]
    ] = Field(
        default_factory=list,
        description=(
            "学生偏好的实验模块:BASIC基础、MECHANICS力学、"
            "ELECTRICITY电学/电磁、OPTICS光学、MODERN近代物理"
        ),
    )
    preferred_teacher_names: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="学生优先选择的教师姓名(去掉'老师'称谓,可多个)",
    )

    @field_validator("preferred_teacher_names")
    @classmethod
    def normalize_teacher_names(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            name = value.strip()
            if name.endswith("老师"):
                name = name[:-2].strip()
            if name and name not in normalized:
                normalized.append(name)
        return normalized

    @model_validator(mode="after")
    def validate_consistent_preferences(self) -> Self:
        period_overlap = set(self.preferred_periods).intersection(self.avoided_periods)
        if self.avoid_evening and "EVENING" in self.preferred_periods:
            period_overlap.add("EVENING")
        if period_overlap:
            raise ValueError("the same time period cannot be preferred and avoided")
        if set(self.preferred_days).intersection(self.avoided_days):
            raise ValueError("the same weekday cannot be preferred and avoided")
        return self


class RecommendationSession(BaseModel):
    session_id: UUID
    project_id: UUID
    project_name: str
    course_name: str
    requirement_type: Literal["REQUIRED", "OPTIONAL"]
    category: str
    week_no: int
    day_of_week: int
    start_slot: int
    end_slot: int
    laboratory_name: str
    campus_name: str
    teacher_id: UUID | None = None
    teacher_name: str = ""
    remaining: int
    preference_score: int = 0
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def day_name(self) -> str:
        return weekday_name(self.day_of_week)

    @computed_field
    @property
    def display_time(self) -> str:
        return (
            f"第{self.week_no}周{self.day_name} 第{self.start_slot}—{self.end_slot}节"
        )


class CourseCoverage(BaseModel):
    course_id: UUID
    course_name: str
    required_total: int
    required_satisfied: int
    optional_min: int
    optional_satisfied: int


class ExcludedCourse(BaseModel):
    course_id: UUID
    course_name: str
    reasons: list[str]


class UnmetRequirement(BaseModel):
    course_name: str
    project_name: str | None = None
    reason: str


class RecommendationPlan(BaseModel):
    name: str
    coverage_status: Literal["COMPLETE", "PARTIAL"] = "COMPLETE"
    scope: RecommendationScope = Field(default_factory=RecommendationScope)
    sessions: list[RecommendationSession]
    retained_selections: list[RecommendationSession] = Field(default_factory=list)
    course_requirements: list[CourseCoverage] = Field(default_factory=list)
    excluded_courses: list[ExcludedCourse] = Field(default_factory=list)
    unmet_requirements: list[UnmetRequirement] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConsultationResponse(BaseModel):
    intent: ConsultationIntent
    answer: str
    cards: list[ConsultationCard] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
