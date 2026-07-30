from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
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
    from app.models.identity import Major


class AcademicTerm(AuditMixin, BaseModel):
    __tablename__ = "academic_term"
    __table_args__ = (
        UniqueConstraint(
            "academic_year", "semester_no", name="academic_year_semester"
        ),
        CheckConstraint("semester_no BETWEEN 1 AND 3", name="semester_valid"),
        CheckConstraint("total_weeks >= 1", name="total_weeks_positive"),
        CheckConstraint("days_per_week BETWEEN 1 AND 7", name="days_valid"),
        CheckConstraint("slots_per_day >= 1", name="slots_positive"),
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'CLOSED')",
            name="status_allowed",
        ),
    )

    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    academic_year: Mapped[str] = mapped_column(String(16), nullable=False)
    semester_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_weeks: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    days_per_week: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=7
    )
    slots_per_day: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=12
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )


class ExperimentCourse(AuditMixin, BaseModel):
    __tablename__ = "experiment_course"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name="status_allowed",
        ),
        CheckConstraint("credits >= 0", name="credits_nonnegative"),
        CheckConstraint(
            "course_type IN ('EXPERIMENT', 'THEORY')",
            name="course_type_allowed",
        ),
    )

    course_code: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True
    )
    course_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 理论课程只可作为先修课程；培养方案的修读课程必须为实验课程。
    course_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="EXPERIMENT"
    )
    credits: Mapped[Decimal] = mapped_column(
        Numeric(4, 1), nullable=False, default=Decimal("1.0")
    )
    default_slots: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=4
    )
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )


class ExperimentProject(AuditMixin, BaseModel):
    __tablename__ = "experiment_project"
    __table_args__ = (
        UniqueConstraint(
            "course_id", "project_name", name="course_project_name"
        ),
        CheckConstraint(
            "category IN ('BASIC', 'MECHANICS', 'ELECTRICITY', "
            "'OPTICS', 'MODERN', 'OTHER')",
            name="category_allowed",
        ),
        CheckConstraint("required_slots >= 1", name="slots_positive"),
        CheckConstraint("default_group_size >= 1", name="group_size_positive"),
        CheckConstraint(
            "group_mode IN ('INDIVIDUAL', 'GROUP')",
            name="group_mode_allowed",
        ),
        CheckConstraint(
            "(group_mode = 'INDIVIDUAL' AND default_group_size = 1) OR "
            "(group_mode = 'GROUP' AND default_group_size >= 2)",
            name="group_mode_size_consistent",
        ),
        CheckConstraint(
            "historical_selection_ratio BETWEEN 0 AND 1",
            name="selection_ratio_valid",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name="status_allowed",
        ),
        Index("ix_experiment_project_course_status", "course_id", "status"),
    )

    project_code: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True
    )
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_course.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_name: Mapped[str] = mapped_column(String(150), nullable=False)
    category: Mapped[str] = mapped_column(
        String(20), nullable=False, default="OTHER"
    )
    required_slots: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=4
    )
    default_group_size: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1
    )
    group_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="INDIVIDUAL"
    )
    material_note: Mapped[str | None] = mapped_column(Text)
    historical_selection_ratio: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal("0.5000")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )


class TrainingPlan(AuditMixin, BaseModel):
    __tablename__ = "training_plan"
    __table_args__ = (
        UniqueConstraint(
            "major_id",
            "enrollment_year",
            "version_no",
            name="major_year_version",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="status_allowed",
        ),
        Index(
            "uq_training_plan_published",
            "major_id",
            "enrollment_year",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )

    plan_code: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    major_id: Mapped[UUID] = mapped_column(
        ForeignKey("major.id", ondelete="RESTRICT"), nullable=False
    )
    enrollment_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    version_no: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    published_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )

    # relationships
    major: Mapped["Major"] = relationship("Major", viewonly=True)
    courses: Mapped[list["TrainingPlanCourse"]] = relationship(
        "TrainingPlanCourse", back_populates="plan", viewonly=True,
    )


class TrainingPlanCourse(AuditMixin, BaseModel):
    __tablename__ = "training_plan_course"
    __table_args__ = (
        UniqueConstraint("plan_id", "course_id", name="plan_course"),
        CheckConstraint(
            "course_nature IN ('REQUIRED', 'ELECTIVE')",
            name="course_nature_allowed",
        ),
        CheckConstraint("study_year BETWEEN 1 AND 6", name="study_year_valid"),
        CheckConstraint("semester_no BETWEEN 1 AND 3", name="semester_valid"),
        CheckConstraint(
            "required_project_count >= 0", name="required_count_nonnegative"
        ),
        CheckConstraint(
            "optional_project_min_count >= 0",
            name="optional_count_nonnegative",
        ),
    )

    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("training_plan.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_course.id", ondelete="RESTRICT"),
        nullable=False,
    )
    course_nature: Mapped[str] = mapped_column(
        String(20), nullable=False, default="REQUIRED"
    )
    study_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    semester_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    required_project_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0
    )
    optional_project_min_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0
    )
    order_rule_text: Mapped[str | None] = mapped_column(Text)
    allow_order_override: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    # relationships
    plan: Mapped["TrainingPlan"] = relationship(
        "TrainingPlan", back_populates="courses", viewonly=True,
    )
    course: Mapped["ExperimentCourse"] = relationship(
        "ExperimentCourse", viewonly=True,
    )
    projects: Mapped[list["TrainingPlanProject"]] = relationship(
        "TrainingPlanProject", back_populates="plan_course", viewonly=True,
    )
    prerequisites: Mapped[list["CoursePrerequisite"]] = relationship(
        "CoursePrerequisite", back_populates="plan_course", viewonly=True,
    )
    order_constraints: Mapped[list["ProjectOrderConstraint"]] = relationship(
        "ProjectOrderConstraint", back_populates="plan_course", viewonly=True,
    )


class TrainingPlanProject(BaseModel):
    __tablename__ = "training_plan_project"
    __table_args__ = (
        UniqueConstraint(
            "plan_course_id", "project_id", name="plan_course_project"
        ),
        CheckConstraint(
            "requirement_type IN ('REQUIRED', 'OPTIONAL')",
            name="requirement_type_allowed",
        ),
    )

    plan_course_id: Mapped[UUID] = mapped_column(
        ForeignKey("training_plan_course.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="RESTRICT"),
        nullable=False,
    )
    requirement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    display_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1
    )

    # relationships
    plan_course: Mapped["TrainingPlanCourse"] = relationship(
        "TrainingPlanCourse", back_populates="projects", viewonly=True,
    )
    project: Mapped["ExperimentProject"] = relationship(
        "ExperimentProject", viewonly=True,
    )


class CoursePrerequisite(BaseModel):
    __tablename__ = "course_prerequisite"
    __table_args__ = (
        UniqueConstraint(
            "plan_course_id",
            "prerequisite_course_id",
            name="plan_course_prerequisite",
        ),
        CheckConstraint(
            "requirement_type IN ('MUST_COMPLETE', 'MUST_ENROLL', 'ADVISORY')",
            name="requirement_type_allowed",
        ),
    )

    plan_course_id: Mapped[UUID] = mapped_column(
        ForeignKey("training_plan_course.id", ondelete="CASCADE"),
        nullable=False,
    )
    prerequisite_course_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_course.id", ondelete="RESTRICT"),
        nullable=False,
    )
    requirement_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="MUST_COMPLETE"
    )
    description: Mapped[str | None] = mapped_column(Text)

    plan_course: Mapped["TrainingPlanCourse"] = relationship(
        "TrainingPlanCourse", back_populates="prerequisites", viewonly=True,
    )
    prerequisite_course: Mapped["ExperimentCourse"] = relationship(
        "ExperimentCourse", viewonly=True,
    )


class ProjectOrderConstraint(BaseModel):
    __tablename__ = "project_order_constraint"
    __table_args__ = (
        UniqueConstraint(
            "plan_course_id",
            "before_project_id",
            "after_project_id",
            name="project_order",
        ),
        CheckConstraint(
            "before_project_id <> after_project_id",
            name="different_projects",
        ),
    )

    plan_course_id: Mapped[UUID] = mapped_column(
        ForeignKey("training_plan_course.id", ondelete="CASCADE"),
        nullable=False,
    )
    before_project_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="RESTRICT"),
        nullable=False,
    )
    after_project_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="RESTRICT"),
        nullable=False,
    )
    allow_override: Mapped[bool] = mapped_column(nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(Text)

    plan_course: Mapped["TrainingPlanCourse"] = relationship(
        "TrainingPlanCourse", back_populates="order_constraints", viewonly=True,
    )
    before_project: Mapped["ExperimentProject"] = relationship(
        "ExperimentProject",
        foreign_keys=[before_project_id],
        viewonly=True,
    )
    after_project: Mapped["ExperimentProject"] = relationship(
        "ExperimentProject",
        foreign_keys=[after_project_id],
        viewonly=True,
    )
