from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, BaseModel


class SelectionWindow(AuditMixin, BaseModel):
    __tablename__ = "selection_window"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="time_range_valid"),
        CheckConstraint(
            "withdraw_end_at IS NULL OR withdraw_end_at >= end_at",
            name="withdraw_time_valid",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'OPEN', 'CLOSED')",
            name="status_allowed",
        ),
        Index("ix_selection_window_term_status", "term_id", "status"),
    )

    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_term.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experiment_course.id", ondelete="CASCADE")
    )
    selection_rule_set_id: Mapped[UUID] = mapped_column(
        ForeignKey("rule_set.id", ondelete="RESTRICT"), nullable=False
    )
    start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    withdraw_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )


class StudentProjectRecord(AuditMixin, BaseModel):
    __tablename__ = "student_project_record"
    __table_args__ = (
        CheckConstraint(
            "requirement_type IN ('REQUIRED', 'OPTIONAL')",
            name="requirement_type_allowed",
        ),
        CheckConstraint(
            "status IN ('AVAILABLE', 'SELECTED', 'COMPLETED', 'ABSENT', "
            "'WITHDRAWN', 'MAKEUP_PENDING')",
            name="status_allowed",
        ),
        CheckConstraint(
            "report_status IN ('NOT_REQUIRED', 'PENDING', 'SUBMITTED', "
            "'PASSED', 'REJECTED')",
            name="report_status_allowed",
        ),
        Index("ix_student_project_record_student_term", "student_id", "term_id"),
        Index("ix_student_project_record_session_status", "session_id", "status"),
        Index(
            "uq_student_project_active",
            "student_id",
            "term_id",
            "project_id",
            unique=True,
            postgresql_where=text(
                "status IN ('SELECTED', 'COMPLETED', 'ABSENT', 'MAKEUP_PENDING')"
            ),
        ),
    )

    student_id: Mapped[UUID] = mapped_column(
        ForeignKey("student.id", ondelete="CASCADE"), nullable=False
    )
    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_term.id", ondelete="RESTRICT"), nullable=False
    )
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_course.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="RESTRICT"),
        nullable=False,
    )
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experiment_session.id", ondelete="SET NULL")
    )
    requirement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="AVAILABLE"
    )
    selected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    absence_reason: Mapped[str | None] = mapped_column(Text)
    report_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NOT_REQUIRED"
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
