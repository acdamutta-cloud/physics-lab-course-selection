from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, BaseModel


class ApplicationRequest(AuditMixin, BaseModel):
    __tablename__ = "application_request"
    __table_args__ = (
        CheckConstraint(
            "request_type IN ('RESCHEDULE', 'MAKEUP', 'PROJECT_CHANGE', "
            "'TEACHER_ADJUSTMENT', 'LAB_CHANGE', 'RESOURCE_ADJUSTMENT', "
            "'TEACHER_SUBSTITUTION')",
            name="request_type_allowed",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'VALIDATING', 'PENDING_REVIEW', "
            "'APPROVED', 'REJECTED', 'EXECUTED', 'FAILED', 'CANCELLED')",
            name="status_allowed",
        ),
        Index("ix_application_request_applicant_status", "applicant_user_id", "status"),
        Index("ix_application_request_type_status", "request_type", "status"),
        Index("ix_application_request_target_status", "target_session_id", "status"),
        UniqueConstraint(
            "student_id",
            "idempotency_key",
            name="application_student_idempotency_key",
        ),
    )

    request_no: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)
    applicant_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )
    student_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("student.id", ondelete="SET NULL")
    )
    teacher_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("teacher.id", ondelete="SET NULL")
    )
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="SET NULL")
    )
    target_project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="SET NULL")
    )
    original_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experiment_session.id", ondelete="SET NULL")
    )
    target_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experiment_session.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(nullable=False, default=dict)
    validation_result: Mapped[dict] = mapped_column(nullable=False, default=dict)
    approval_route: Mapped[str | None] = mapped_column(String(20))
    reservation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NONE"
    )
    reservation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    adjustment_rule_set_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("rule_set.id", ondelete="SET NULL")
    )
    approval_rule_set_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("rule_set.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class AdjustmentExecutionAudit(BaseModel):
    __tablename__ = "adjustment_execution_audit"
    __table_args__ = (
        UniqueConstraint("application_id", name="adjustment_audit_application"),
        Index("ix_adjustment_audit_session", "session_id", "executed_at"),
    )

    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_request.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_session.id", ondelete="RESTRICT"), nullable=False
    )
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    before_snapshot: Mapped[dict] = mapped_column(nullable=False, default=dict)
    after_snapshot: Mapped[dict] = mapped_column(nullable=False, default=dict)
    execution_status: Mapped[str] = mapped_column(String(20), nullable=False)
    executed_by: Mapped[UUID] = mapped_column(nullable=False)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)


class ApprovalRecord(BaseModel):
    __tablename__ = "approval_record"
    __table_args__ = (
        CheckConstraint(
            "approval_type IN ('AUTO', 'MANUAL')",
            name="approval_type_allowed",
        ),
        CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED', 'TRANSFERRED')",
            name="decision_allowed",
        ),
        Index("ix_approval_record_application", "application_id", "decided_at"),
    )

    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_request.id", ondelete="CASCADE"),
        nullable=False,
    )
    approval_type: Mapped[str] = mapped_column(String(20), nullable=False)
    approver_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    matched_rules: Mapped[dict] = mapped_column(nullable=False, default=dict)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
