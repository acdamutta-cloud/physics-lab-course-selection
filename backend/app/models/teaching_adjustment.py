from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, BaseModel


class SessionExecutionOverride(AuditMixin, BaseModel):
    """An approved execution-time change without mutating a published schedule."""

    __tablename__ = "session_execution_override"
    __table_args__ = (
        CheckConstraint(
            "override_type IN ('TIME', 'LAB', 'TEACHER')",
            name="override_type_allowed",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'SUPERSEDED', 'CANCELLED')",
            name="override_status_allowed",
        ),
        Index(
            "ix_session_override_session_type_status",
            "session_id",
            "override_type",
            "status",
        ),
    )

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_session.id", ondelete="RESTRICT"), nullable=False
    )
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_request.id", ondelete="RESTRICT"), nullable=False
    )
    override_type: Mapped[str] = mapped_column(String(20), nullable=False)
    before_snapshot: Mapped[dict] = mapped_column(nullable=False, default=dict)
    after_snapshot: Mapped[dict] = mapped_column(nullable=False, default=dict)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")


class ApplicationApprovalTask(AuditMixin, BaseModel):
    __tablename__ = "application_approval_task"
    __table_args__ = (
        CheckConstraint(
            "approver_type IN ('SUBSTITUTE_TEACHER', 'ADMIN')",
            name="approver_type_allowed",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')",
            name="approval_task_status_allowed",
        ),
        UniqueConstraint(
            "application_id", "sequence_no", name="application_approval_sequence"
        ),
        Index("ix_approval_task_assignee_status", "approver_user_id", "status"),
    )

    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_request.id", ondelete="CASCADE"), nullable=False
    )
    approver_type: Mapped[str] = mapped_column(String(24), nullable=False)
    approver_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdjustmentRemediationPlan(AuditMixin, BaseModel):
    __tablename__ = "adjustment_remediation_plan"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'VALIDATED', 'SELECTED', 'EXECUTED', 'STALE')",
            name="remediation_plan_status_allowed",
        ),
        UniqueConstraint(
            "application_id", "plan_no", name="application_remediation_plan"
        ),
    )

    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_request.id", ondelete="CASCADE"), nullable=False
    )
    plan_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    summary: Mapped[dict] = mapped_column(nullable=False, default=dict)
    validation_result: Mapped[dict] = mapped_column(nullable=False, default=dict)


class AdjustmentRemediationItem(AuditMixin, BaseModel):
    __tablename__ = "adjustment_remediation_item"
    __table_args__ = (
        UniqueConstraint("plan_id", "student_id", name="remediation_plan_student"),
    )

    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("adjustment_remediation_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[UUID] = mapped_column(
        ForeignKey("student.id", ondelete="RESTRICT"), nullable=False
    )
    original_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_session.id", ondelete="RESTRICT"), nullable=False
    )
    target_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_session.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(500))


class ResourceRelocationPlan(AuditMixin, BaseModel):
    __tablename__ = "resource_relocation_plan"
    __table_args__ = (
        CheckConstraint(
            "status IN ('VALIDATED', 'EXECUTED', 'STALE')",
            name="resource_relocation_plan_status_allowed",
        ),
        UniqueConstraint(
            "resource_issue_id",
            "source_session_id",
            "plan_no",
            name="resource_relocation_plan_number",
        ),
        Index(
            "ix_resource_relocation_issue_session_status",
            "resource_issue_id",
            "source_session_id",
            "status",
        ),
    )

    resource_issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_issue_report.id", ondelete="CASCADE"), nullable=False
    )
    source_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_session.id", ondelete="RESTRICT"), nullable=False
    )
    plan_no: Mapped[int] = mapped_column(Integer, nullable=False)
    required_relocation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_relocation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_unresolved_count: Mapped[int] = mapped_column(Integer, nullable=False)
    capacity_snapshot: Mapped[dict] = mapped_column(nullable=False, default=dict)
    validation_result: Mapped[dict] = mapped_column(nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="VALIDATED")


class ResourceRelocationItem(AuditMixin, BaseModel):
    __tablename__ = "resource_relocation_item"
    __table_args__ = (
        CheckConstraint(
            "status IN ('VALIDATED', 'EXECUTED', 'STALE')",
            name="resource_relocation_item_status_allowed",
        ),
        UniqueConstraint(
            "plan_id", "student_id", name="resource_relocation_plan_student"
        ),
    )

    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_relocation_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[UUID] = mapped_column(
        ForeignKey("student.id", ondelete="RESTRICT"), nullable=False
    )
    student_project_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_project_record.id", ondelete="RESTRICT"), nullable=False
    )
    target_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_session.id", ondelete="RESTRICT"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasons: Mapped[dict] = mapped_column(nullable=False, default=dict)
    validation_result: Mapped[dict] = mapped_column(nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="VALIDATED")


class ResourceRepairUpdate(AuditMixin, BaseModel):
    __tablename__ = "resource_repair_update"
    __table_args__ = (
        CheckConstraint(
            "update_type IN ('PARTIAL_RESTORE', 'COMPLETE_RESTORE', 'EXTEND_REPAIR')",
            name="repair_update_type_allowed",
        ),
        CheckConstraint(
            "approval_status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="repair_update_approval_allowed",
        ),
        CheckConstraint("restored_quantity >= 0", name="restored_nonnegative"),
    )

    resource_issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_issue_report.id", ondelete="CASCADE"), nullable=False
    )
    update_type: Mapped[str] = mapped_column(String(24), nullable=False)
    restored_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    proposed_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(1000))
    approval_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING"
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EquipmentInventoryMovement(AuditMixin, BaseModel):
    __tablename__ = "equipment_inventory_movement"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="movement_quantity_positive"),
        CheckConstraint(
            "movement_type IN ('ISSUE_DISABLE', 'REPAIR_RESTORE', 'MANUAL_REVERSAL')",
            name="movement_type_allowed",
        ),
        UniqueConstraint("idempotency_key", name="inventory_movement_idempotency"),
        Index("ix_inventory_movement_inventory_time", "inventory_id", "created_at"),
    )

    inventory_id: Mapped[UUID] = mapped_column(
        ForeignKey("lab_equipment_inventory.id", ondelete="RESTRICT"), nullable=False
    )
    resource_issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_issue_report.id", ondelete="RESTRICT"), nullable=False
    )
    repair_update_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("resource_repair_update.id", ondelete="SET NULL")
    )
    movement_type: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    before_snapshot: Mapped[dict] = mapped_column(nullable=False, default=dict)
    after_snapshot: Mapped[dict] = mapped_column(nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
