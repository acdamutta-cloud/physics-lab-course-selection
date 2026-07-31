from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, BaseModel

if TYPE_CHECKING:
    from app.models.curriculum import (
        AcademicTerm,
        ExperimentCourse,
        ExperimentProject,
    )
    from app.models.identity import Major, Teacher
    from app.models.resources import Laboratory


class TeachingTask(AuditMixin, BaseModel):
    __tablename__ = "teaching_task"
    __table_args__ = (
        CheckConstraint("planned_student_count >= 0", name="student_count_valid"),
        CheckConstraint("week_start >= 1", name="week_start_positive"),
        CheckConstraint("week_end >= week_start", name="week_range_valid"),
        CheckConstraint("capacity_buffer_ratio >= 1", name="buffer_ratio_valid"),
        CheckConstraint(
            "status IN ('DRAFT', 'READY', 'SCHEDULING', 'PUBLISHED', 'CLOSED')",
            name="status_allowed",
        ),
        Index("ix_teaching_task_term_status", "term_id", "status"),
    )

    task_code: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_term.id", ondelete="RESTRICT"), nullable=False
    )
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_course.id", ondelete="RESTRICT"),
        nullable=False,
    )
    planned_student_count: Mapped[int] = mapped_column(Integer, nullable=False)
    week_start: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    week_end: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    capacity_buffer_ratio: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("1.20")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )

    # relationships
    course: Mapped["ExperimentCourse"] = relationship("ExperimentCourse", viewonly=True)
    term: Mapped["AcademicTerm"] = relationship("AcademicTerm", viewonly=True)
    cohorts: Mapped[list["TeachingTaskCohort"]] = relationship(
        "TeachingTaskCohort", back_populates="task", viewonly=True,
    )
    demands: Mapped[list["ProjectDemand"]] = relationship(
        "ProjectDemand", back_populates="task", viewonly=True,
    )


class TeachingTaskCohort(BaseModel):
    __tablename__ = "teaching_task_cohort"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "major_id",
            "enrollment_year",
            "class_id",
            name="task_cohort",
        ),
        CheckConstraint("student_count >= 0", name="student_count_nonnegative"),
    )

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("teaching_task.id", ondelete="CASCADE"), nullable=False
    )
    major_id: Mapped[UUID] = mapped_column(
        ForeignKey("major.id", ondelete="RESTRICT"), nullable=False
    )
    enrollment_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    class_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("student_class.id", ondelete="RESTRICT")
    )
    student_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # relationships
    task: Mapped["TeachingTask"] = relationship(
        "TeachingTask", back_populates="cohorts", viewonly=True,
    )
    major: Mapped["Major"] = relationship("Major", viewonly=True)


class ProjectDemand(BaseModel):
    __tablename__ = "project_demand"
    __table_args__ = (
        UniqueConstraint("task_id", "project_id", name="task_project"),
        CheckConstraint(
            "requirement_type IN ('REQUIRED', 'OPTIONAL')",
            name="requirement_type_allowed",
        ),
        CheckConstraint("base_demand >= 0", name="base_demand_nonnegative"),
        CheckConstraint(
            "prediction_ratio BETWEEN 0 AND 1",
            name="prediction_ratio_valid",
        ),
        CheckConstraint("buffer_ratio >= 1", name="buffer_ratio_valid"),
        CheckConstraint(
            "required_capacity >= 0", name="required_capacity_nonnegative"
        ),
        CheckConstraint(
            "required_session_count >= 0",
            name="required_session_count_nonnegative",
        ),
    )

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("teaching_task.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="RESTRICT"),
        nullable=False,
    )
    requirement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    base_demand: Mapped[int] = mapped_column(Integer, nullable=False)
    prediction_ratio: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal(1)
    )
    buffer_ratio: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("1.20")
    )
    required_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    required_session_count: Mapped[int] = mapped_column(Integer, nullable=False)
    calculation_snapshot: Mapped[dict] = mapped_column(
        nullable=False, default=dict
    )

    # relationships
    task: Mapped["TeachingTask"] = relationship(
        "TeachingTask", back_populates="demands", viewonly=True,
    )
    project: Mapped["ExperimentProject"] = relationship(
        "ExperimentProject", viewonly=True,
    )


class ScheduleJob(AuditMixin, BaseModel):
    __tablename__ = "schedule_job"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('INITIAL', 'LOCAL_ADJUSTMENT', 'VALIDATION')",
            name="job_type_allowed",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', "
            "'CANCELLED', 'TIMEOUT')",
            name="status_allowed",
        ),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress_valid"),
        Index("ix_schedule_job_term_status", "term_id", "status"),
    )

    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_term.id", ondelete="RESTRICT"), nullable=False
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("teaching_task.id", ondelete="SET NULL")
    )
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING"
    )
    scheduling_rule_set_id: Mapped[UUID] = mapped_column(
        ForeignKey("rule_set.id", ondelete="RESTRICT"), nullable=False
    )
    input_snapshot: Mapped[dict] = mapped_column(nullable=False, default=dict)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


class ScheduleVersion(AuditMixin, BaseModel):
    __tablename__ = "schedule_version"
    __table_args__ = (
        UniqueConstraint("term_id", "version_no", name="term_version"),
        CheckConstraint(
            "status IN ('DRAFT', 'CANDIDATE', 'PUBLISHED', 'ARCHIVED')",
            name="status_allowed",
        ),
        Index(
            "uq_schedule_version_published",
            "term_id",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )

    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_term.id", ondelete="RESTRICT"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="SET NULL")
    )
    source_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("schedule_job.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    hard_constraint_passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    soft_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    score_details: Mapped[dict] = mapped_column(nullable=False, default=dict)
    optimization_params: Mapped[dict] = mapped_column(
        nullable=False, default=dict
    )
    scheduling_rule_set_id: Mapped[UUID] = mapped_column(
        ForeignKey("rule_set.id", ondelete="RESTRICT"), nullable=False
    )
    published_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class ExperimentSession(AuditMixin, BaseModel):
    __tablename__ = "experiment_session"
    __table_args__ = (
        UniqueConstraint(
            "schedule_version_id", "session_code", name="version_session_code"
        ),
        CheckConstraint("week_no >= 1", name="week_positive"),
        CheckConstraint("day_of_week BETWEEN 1 AND 7", name="day_valid"),
        CheckConstraint("start_slot >= 1", name="start_slot_positive"),
        CheckConstraint("end_slot >= start_slot", name="slot_range_valid"),
        CheckConstraint("capacity >= 1", name="capacity_positive"),
        CheckConstraint("selected_count >= 0", name="selected_nonnegative"),
        CheckConstraint(
            "selected_count <= capacity", name="selected_within_capacity"
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'OPEN', 'FULL', 'SUSPENDED', "
            "'CANCELLED', 'COMPLETED')",
            name="status_allowed",
        ),
        Index(
            "ix_experiment_session_time",
            "schedule_version_id",
            "week_no",
            "day_of_week",
            "start_slot",
        ),
        Index("ix_experiment_session_project_status", "project_id", "status"),
    )

    schedule_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_code: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("teaching_task.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="RESTRICT"),
        nullable=False,
    )
    week_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    end_slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    teacher_id: Mapped[UUID] = mapped_column(
        ForeignKey("teacher.id", ondelete="RESTRICT"), nullable=False
    )
    laboratory_id: Mapped[UUID] = mapped_column(
        ForeignKey("laboratory.id", ondelete="RESTRICT"), nullable=False
    )
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    project: Mapped["ExperimentProject"] = relationship("ExperimentProject", viewonly=True)
    teacher: Mapped["Teacher"] = relationship("Teacher", viewonly=True)
    laboratory: Mapped["Laboratory"] = relationship("Laboratory", viewonly=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class TeacherTimetableEntry(BaseModel):
    __tablename__ = "teacher_timetable_entry"
    __table_args__ = (
        UniqueConstraint(
            "experiment_session_id",
            name="timetable_experiment_session",
        ),
        UniqueConstraint(
            "teacher_id",
            "experiment_session_id",
            name="teacher_experiment_session",
        ),
        Index(
            "ix_teacher_timetable_entry_teacher_term",
            "teacher_id",
            "term_id",
        ),
        Index(
            "ix_teacher_timetable_entry_schedule_version",
            "schedule_version_id",
        ),
    )

    teacher_id: Mapped[UUID] = mapped_column(
        ForeignKey("teacher.id", ondelete="RESTRICT"), nullable=False
    )
    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_term.id", ondelete="RESTRICT"),
        nullable=False,
    )
    schedule_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="RESTRICT"),
        nullable=False,
    )
    experiment_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_session.id", ondelete="RESTRICT"),
        nullable=False,
    )


class CourseTimeAvailability(BaseModel):
    __tablename__ = "course_time_availability"
    __table_args__ = (
        UniqueConstraint(
            "course_id",
            "term_id",
            "week_no",
            "day_of_week",
            "slot_no",
            name="course_term_time_slot",
        ),
        CheckConstraint("week_no >= 1", name="week_positive"),
        CheckConstraint(
            "day_of_week BETWEEN 1 AND 7",
            name="day_valid",
        ),
        CheckConstraint("slot_no >= 1", name="slot_positive"),
        CheckConstraint(
            "target_student_count >= 0",
            name="target_count_nonnegative",
        ),
        CheckConstraint(
            "known_student_count >= 0",
            name="known_count_nonnegative",
        ),
        CheckConstraint(
            "free_student_count >= 0",
            name="free_count_nonnegative",
        ),
        CheckConstraint(
            "busy_student_count >= 0",
            name="busy_count_nonnegative",
        ),
        CheckConstraint(
            "unknown_student_count >= 0",
            name="unknown_count_nonnegative",
        ),
        CheckConstraint(
            "known_student_count = "
            "free_student_count + busy_student_count",
            name="known_count_sum",
        ),
        CheckConstraint(
            "target_student_count = "
            "known_student_count + unknown_student_count",
            name="target_count_sum",
        ),
        CheckConstraint(
            "free_ratio BETWEEN 0 AND 1",
            name="free_ratio_valid",
        ),
        CheckConstraint(
            "data_coverage_ratio BETWEEN 0 AND 1",
            name="coverage_ratio_valid",
        ),
        CheckConstraint(
            "mapping_version >= 1",
            name="mapping_version_positive",
        ),
        CheckConstraint(
            "calculation_version >= 1",
            name="calculation_version_positive",
        ),
        Index(
            "ix_course_time_availability_course_time",
            "course_id",
            "term_id",
            "week_no",
            "day_of_week",
        ),
        Index(
            "ix_course_time_availability_course_free",
            "course_id",
            "term_id",
            text("free_student_count DESC"),
        ),
        Index(
            "ix_course_time_availability_batch",
            "calculation_batch_id",
        ),
    )

    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_course.id", ondelete="CASCADE"),
        nullable=False,
    )
    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_term.id", ondelete="CASCADE"),
        nullable=False,
    )
    week_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    slot_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    target_student_count: Mapped[int] = mapped_column(Integer, nullable=False)
    known_student_count: Mapped[int] = mapped_column(Integer, nullable=False)
    free_student_count: Mapped[int] = mapped_column(Integer, nullable=False)
    busy_student_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unknown_student_count: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    free_ratio: Mapped[Decimal] = mapped_column(
        Numeric(8, 6), nullable=False
    )
    data_coverage_ratio: Mapped[Decimal] = mapped_column(
        Numeric(8, 6), nullable=False
    )
    mapping_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    calculation_version: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    calculation_batch_id: Mapped[UUID] = mapped_column(nullable=False)
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
