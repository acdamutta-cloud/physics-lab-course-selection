from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.training_plan import CourseInfo, MajorInfo, ProjectInfo


class TermInfo(BaseModel):
    id: UUID
    code: str
    academic_year: str
    semester_no: int
    start_date: date
    end_date: date
    total_weeks: int
    status: str
    current_week: int = 1


class TeachingTaskCohortOut(BaseModel):
    id: UUID
    major: MajorInfo
    enrollment_year: int
    student_count: int


class ProjectDemandOut(BaseModel):
    id: UUID
    project: ProjectInfo
    requirement_type: str  # REQUIRED / OPTIONAL
    base_demand: int
    prediction_ratio: Decimal
    buffer_ratio: Decimal
    required_capacity: int
    required_session_count: int
    teachers: list[str] = []
    equipment: list[str] = []
    teacher_ids: list[UUID] = []
    equipment_ids: list[UUID] = []


class TeachingTaskOut(BaseModel):
    id: UUID
    task_code: str
    course: CourseInfo
    term: TermInfo
    planned_student_count: int
    week_start: int
    week_end: int
    status: str
    cohorts: list[TeachingTaskCohortOut] = []
    demands: list[ProjectDemandOut] = []


class CreateTeachingTaskRequest(BaseModel):
    course_id: UUID
    week_start: int = Field(ge=1, le=20)
    week_end: int = Field(ge=1, le=20)


class UpdateTeachingTaskRequest(BaseModel):
    week_start: int = Field(ge=1, le=20)
    week_end: int = Field(ge=1, le=20)


class TeachingTaskListResponse(BaseModel):
    items: list[TeachingTaskOut]
    total: int
