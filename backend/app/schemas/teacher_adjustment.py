from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.student_consultation import SelectionPreferences

TeacherAdjustmentType = Literal[
    "TEACHER_ADJUSTMENT", "LAB_CHANGE", "TEACHER_SUBSTITUTION"
]


class TimeTarget(BaseModel):
    week_no: int = Field(ge=1, le=20)
    day_of_week: int = Field(ge=1, le=7)
    start_slot: int = Field(ge=1, le=12)
    end_slot: int = Field(ge=1, le=12)

    @model_validator(mode="after")
    def valid_slots(self):
        if self.end_slot < self.start_slot:
            raise ValueError("结束节次不能早于开始节次")
        return self


class AffectedStudent(BaseModel):
    student_id: UUID
    student_no: str
    name: str
    reasons: list[str]
    remediable: bool = False


class TeacherAdjustmentValidation(BaseModel):
    allowed: bool
    can_submit_for_review: bool = False
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    affected_students: list[AffectedStudent] = Field(default_factory=list)
    impact_summary: dict[str, object] = Field(default_factory=dict)


class TeacherReschedulePreviewRequest(BaseModel):
    original_session_id: UUID
    target: TimeTarget


class TeacherRescheduleRecommendationRequest(BaseModel):
    original_session_id: UUID
    message: str = Field(default="", max_length=2000)
    preferences: SelectionPreferences | None = None
    max_options: Literal[1, 2, 3] = 3


class TeacherRescheduleAgentPlan(BaseModel):
    intent: Literal["RECOMMEND_TEACHER_RESCHEDULE"] = "RECOMMEND_TEACHER_RESCHEDULE"
    preferences: SelectionPreferences = Field(default_factory=SelectionPreferences)
    needs_clarification: bool = False
    clarification_question: str | None = None


class TeacherRescheduleOption(BaseModel):
    target: TimeTarget
    score: int
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    affected_student_count: int = 0
    affected_students: list[AffectedStudent] = Field(default_factory=list)
    can_submit: bool = True


class LabChangePreviewRequest(BaseModel):
    original_session_id: UUID
    target_laboratory_id: UUID


class SubstitutionPreviewRequest(BaseModel):
    original_session_id: UUID
    substitute_teacher_id: UUID


class TeacherAdjustmentCreateRequest(BaseModel):
    request_type: TeacherAdjustmentType
    original_session_id: UUID
    reason: str = Field(min_length=2, max_length=2000)
    target_time: TimeTarget | None = None
    target_laboratory_id: UUID | None = None
    substitute_teacher_id: UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=64)


class ResourceIssueCreateRequest(BaseModel):
    laboratory_id: UUID
    inventory_id: UUID | None = None
    asset_id: UUID | None = None
    issue_type: Literal[
        "EQUIPMENT_FAILURE",
        "MATERIAL_SHORTAGE",
        "LAB_UNAVAILABLE",
        "ENVIRONMENT",
        "OTHER",
    ] = "EQUIPMENT_FAILURE"
    affected_quantity: int = Field(default=1, ge=1)
    impact_start: datetime
    impact_end: datetime
    severity: Literal["LOW", "NORMAL", "HIGH", "CRITICAL"] = "NORMAL"
    description: str = Field(min_length=2, max_length=2000)

    @model_validator(mode="after")
    def valid_range(self):
        if self.impact_end <= self.impact_start:
            raise ValueError("预计检修完成时间必须晚于开始时间")
        if self.issue_type == "EQUIPMENT_FAILURE" and self.asset_id is None:
            raise ValueError("设备故障必须选择具体仪器号")
        if self.issue_type != "EQUIPMENT_FAILURE" and self.inventory_id is None:
            raise ValueError("该异常类型必须选择资源库存")
        return self


class EquipmentScrapCreateRequest(BaseModel):
    asset_id: UUID
    reason: str = Field(min_length=2, max_length=2000)
    severity: Literal["LOW", "NORMAL", "HIGH", "CRITICAL"] = "NORMAL"


class ResourceIssueReviewRequest(BaseModel):
    approved: bool
    approved_quantity: int | None = Field(default=None, ge=1)
    comment: str | None = Field(default=None, max_length=1000)


class ResourceRemediationCreateRequest(BaseModel):
    original_session_id: UUID
    target: TimeTarget
    reason: str = Field(
        default="资源异常影响教学，申请调整实验时间", min_length=2, max_length=1000
    )


class EquipmentUsageNoteInput(BaseModel):
    usage_note: str = Field(default="", max_length=1000)


class EquipmentUsageRuleOut(BaseModel):
    usage_note: str = ""
    students_per_unit: int | None = None
    sharing_rule_status: Literal["UNPARSED", "CONFIRMED", "AMBIGUOUS"]
    evidence: str | None = None


class ResourceRelocationRecommendationRequest(BaseModel):
    message: str = Field(default="", max_length=2000)
    preferences: SelectionPreferences | None = None
    max_plans: Literal[1, 2, 3] = 3


class ResourceRelocationSelection(BaseModel):
    student_id: UUID
    target_session_id: UUID


class ResourceRelocationPlanUpdateRequest(BaseModel):
    items: list[ResourceRelocationSelection] = Field(min_length=1)


class RepairUpdateCreateRequest(BaseModel):
    update_type: Literal["PARTIAL_RESTORE", "COMPLETE_RESTORE", "EXTEND_REPAIR"]
    restored_quantity: int = Field(default=0, ge=0)
    proposed_end_time: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)


class RepairUpdateReviewRequest(BaseModel):
    approved: bool
    comment: str | None = Field(default=None, max_length=1000)


class AdjustmentReviewRequest(BaseModel):
    approved: bool
    remediation_plan_id: UUID | None = None
    comment: str | None = Field(default=None, max_length=1000)


class SubstituteConfirmationRequest(BaseModel):
    approved: bool
    comment: str | None = Field(default=None, max_length=1000)
