from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

PlanStatus = Literal["DRAFT", "PUBLISHED", "ARCHIVED"]
CourseNature = Literal["REQUIRED", "ELECTIVE"]
ProjectRequirement = Literal["REQUIRED", "OPTIONAL"]
ProjectGroupMode = Literal["INDIVIDUAL", "GROUP"]
ProjectCategory = Literal[
    "BASIC", "MECHANICS", "ELECTRICITY", "OPTICS", "MODERN", "OTHER"
]


class MajorInfo(BaseModel):
    id: UUID
    code: str
    name: str


class CourseInfo(BaseModel):
    id: UUID
    course_code: str
    course_name: str
    course_type: Literal["EXPERIMENT", "THEORY"] = "EXPERIMENT"


class ProjectInfo(BaseModel):
    id: UUID
    project_code: str
    project_name: str
    category: str | None = None
    required_slots: int
    group_mode: ProjectGroupMode
    default_group_size: int
    historical_selection_ratio: Decimal


class CreateProjectRequest(BaseModel):
    project_code: str | None = Field(default=None, min_length=1, max_length=32)
    project_name: str = Field(min_length=1, max_length=150)
    category: ProjectCategory
    required_slots: int = Field(ge=1, le=24)
    group_mode: ProjectGroupMode | None = None
    default_group_size: int = Field(ge=1, le=100)
    historical_selection_ratio: Decimal = Field(ge=0, le=1, max_digits=6, decimal_places=4)

    @model_validator(mode="after")
    def validate_grouping(self) -> "CreateProjectRequest":
        if self.group_mode is None:
            self.group_mode = (
                "INDIVIDUAL" if self.default_group_size == 1 else "GROUP"
            )
        if self.group_mode == "INDIVIDUAL":
            self.default_group_size = 1
        elif self.default_group_size < 2:
            raise ValueError("多人分组实验的每组人数必须至少为 2")
        return self


class UpdateProjectGroupingRequest(BaseModel):
    group_mode: ProjectGroupMode
    default_group_size: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def validate_grouping(self) -> "UpdateProjectGroupingRequest":
        if self.group_mode == "INDIVIDUAL":
            self.default_group_size = 1
        elif self.default_group_size < 2:
            raise ValueError("多人分组实验的每组人数必须至少为 2")
        return self


class TrainingPlanProjectIn(BaseModel):
    project_id: UUID
    requirement_type: ProjectRequirement = "REQUIRED"
    display_order: int = Field(default=1, ge=1)


class TrainingPlanProjectOut(BaseModel):
    id: UUID
    project: ProjectInfo
    requirement_type: ProjectRequirement
    display_order: int


class ProjectOrderConstraintOut(BaseModel):
    id: UUID
    before_project: ProjectInfo
    after_project: ProjectInfo
    allow_override: bool = False
    description: str | None = None


class TrainingPlanCourseIn(BaseModel):
    course_id: UUID
    course_nature: CourseNature = "REQUIRED"
    study_year: int = Field(ge=1, le=6)
    semester_no: int = Field(ge=1, le=3)
    prerequisite_course_id: UUID | None = None
    prerequisite_course_ids: list[UUID] = Field(default_factory=list)
    required_project_count: int = Field(default=0, ge=0)
    optional_project_min_count: int = Field(default=0, ge=0)
    order_rule_text: str | None = Field(default=None, max_length=2000)
    allow_order_override: bool = False
    projects: list[TrainingPlanProjectIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_projects(self) -> "TrainingPlanCourseIn":
        if self.prerequisite_course_id and not self.prerequisite_course_ids:
            self.prerequisite_course_ids = [self.prerequisite_course_id]
        if len(self.prerequisite_course_ids) != len(set(self.prerequisite_course_ids)):
            raise ValueError("同一课程中不能重复配置先修课程")
        project_ids = [item.project_id for item in self.projects]
        if len(project_ids) != len(set(project_ids)):
            raise ValueError("同一课程中不能重复配置实验项目")
        required = sum(item.requirement_type == "REQUIRED" for item in self.projects)
        optional = sum(item.requirement_type == "OPTIONAL" for item in self.projects)
        if self.required_project_count > required:
            raise ValueError("必做项目数量不能超过已配置的必做项目数")
        if self.optional_project_min_count > optional:
            raise ValueError("选做项目最低数量不能超过已配置的选做项目数")
        return self


class TrainingPlanCourseOut(BaseModel):
    id: UUID
    course: CourseInfo
    course_nature: CourseNature
    study_year: int
    semester_no: int
    prerequisite_course: CourseInfo | None = None
    prerequisite_courses: list[CourseInfo] = Field(default_factory=list)
    required_project_count: int
    optional_project_min_count: int
    order_rule_text: str | None = None
    allow_order_override: bool = False
    projects: list[TrainingPlanProjectOut] = Field(default_factory=list)
    order_constraints: list[ProjectOrderConstraintOut] = Field(default_factory=list)


class CreateTrainingPlanRequest(BaseModel):
    major_id: UUID
    enrollment_year: int = Field(ge=2000, le=2100)
    effective_from: date | None = None
    courses: list[TrainingPlanCourseIn] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_courses(self) -> "CreateTrainingPlanRequest":
        course_ids = [item.course_id for item in self.courses]
        if len(course_ids) != len(set(course_ids)):
            raise ValueError("同一培养方案中不能重复配置课程")
        return self


class UpdateTrainingPlanRequest(CreateTrainingPlanRequest):
    pass


class TrainingPlanListOut(BaseModel):
    id: UUID
    plan_code: str
    major: MajorInfo
    enrollment_year: int
    version_no: int
    status: PlanStatus
    effective_from: date | None = None
    published_at: datetime | None = None
    updated_at: datetime
    courses_count: int
    required_projects_count: int = 0
    optional_projects_count: int = 0
    prerequisite_names: list[str] = Field(default_factory=list)
    completeness: Literal["COMPLETE", "INCOMPLETE"] = "INCOMPLETE"
    courses: list[TrainingPlanCourseOut] = Field(default_factory=list)


class TrainingPlanDetailOut(BaseModel):
    id: UUID
    plan_code: str
    major: MajorInfo
    enrollment_year: int
    version_no: int
    status: PlanStatus
    effective_from: date | None = None
    published_at: datetime | None = None
    updated_at: datetime
    courses: list[TrainingPlanCourseOut] = Field(default_factory=list)


class TrainingPlanListResponse(BaseModel):
    items: list[TrainingPlanListOut]
    total: int
