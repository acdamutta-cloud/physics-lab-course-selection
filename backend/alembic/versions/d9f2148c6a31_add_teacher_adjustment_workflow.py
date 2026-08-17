"""add teacher adjustment and resource inventory workflow

Revision ID: d9f2148c6a31
Revises: c4a81d9e6f20
Create Date: 2026-08-06 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d9f2148c6a31"
down_revision: str | Sequence[str] | None = "c4a81d9e6f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True)),
    ]


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_application_request_request_type_allowed"),
        "application_request",
        type_="check",
    )
    op.create_check_constraint(
        "request_type_allowed",
        "application_request",
        "request_type IN ('RESCHEDULE', 'MAKEUP', 'PROJECT_CHANGE', "
        "'TEACHER_ADJUSTMENT', 'LAB_CHANGE', 'RESOURCE_ADJUSTMENT', "
        "'TEACHER_SUBSTITUTION')",
    )

    op.drop_constraint(
        op.f("ck_resource_issue_report_status_allowed"),
        "resource_issue_report",
        type_="check",
    )
    op.create_check_constraint(
        "status_allowed",
        "resource_issue_report",
        "status IN ('REPORTED', 'PENDING_REVIEW', 'PROCESSING', "
        "'RESOLVED', 'REJECTED', 'CLOSED')",
    )
    op.add_column(
        "resource_issue_report",
        sa.Column("inventory_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "resource_issue_report",
        sa.Column("affected_quantity", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "resource_issue_report",
        sa.Column("approved_quantity", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "resource_issue_report",
        sa.Column("restored_quantity", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "resource_issue_report",
        sa.Column("approved_by", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "resource_issue_report", sa.Column("approved_at", sa.DateTime(timezone=True))
    )
    op.create_foreign_key(
        "fk_resource_issue_report_inventory_id_lab_equipment_inventory",
        "resource_issue_report", "lab_equipment_inventory", ["inventory_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_resource_issue_report_approved_by_user_account",
        "resource_issue_report", "user_account", ["approved_by"], ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "resource_issue_quantities_valid",
        "resource_issue_report",
        "affected_quantity > 0 AND approved_quantity >= 0 AND "
        "restored_quantity >= 0 AND restored_quantity <= approved_quantity",
    )

    op.create_table(
        "session_execution_override",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("override_type", sa.String(20), nullable=False),
        sa.Column("before_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("after_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        *_base_columns(),
        sa.ForeignKeyConstraint(["session_id"], ["experiment_session.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["application_id"], ["application_request.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("override_type IN ('TIME', 'LAB', 'TEACHER')", name="override_type_allowed"),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUPERSEDED', 'CANCELLED')", name="override_status_allowed"),
    )
    op.create_index("ix_session_override_session_type_status", "session_execution_override", ["session_id", "override_type", "status"])

    op.create_table(
        "application_approval_task",
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approver_type", sa.String(24), nullable=False),
        sa.Column("approver_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        *_base_columns(),
        sa.ForeignKeyConstraint(["application_id"], ["application_request.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approver_user_id"], ["user_account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "sequence_no", name="application_approval_sequence"),
        sa.CheckConstraint("approver_type IN ('SUBSTITUTE_TEACHER', 'ADMIN')", name="approver_type_allowed"),
        sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')", name="approval_task_status_allowed"),
    )
    op.create_index("ix_approval_task_assignee_status", "application_approval_task", ["approver_user_id", "status"])

    op.create_table(
        "adjustment_remediation_plan",
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("validation_result", postgresql.JSONB(), nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(["application_id"], ["application_request.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "plan_no", name="application_remediation_plan"),
        sa.CheckConstraint("status IN ('DRAFT', 'VALIDATED', 'SELECTED', 'EXECUTED', 'STALE')", name="remediation_plan_status_allowed"),
    )
    op.create_table(
        "adjustment_remediation_item",
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(500)),
        *_base_columns(),
        sa.ForeignKeyConstraint(["plan_id"], ["adjustment_remediation_plan.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["student.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["original_session_id"], ["experiment_session.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_session_id"], ["experiment_session.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "student_id", name="remediation_plan_student"),
    )

    op.create_table(
        "resource_repair_update",
        sa.Column("resource_issue_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("update_type", sa.String(24), nullable=False),
        sa.Column("restored_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("proposed_end_time", sa.DateTime(timezone=True)),
        sa.Column("note", sa.String(1000)),
        sa.Column("approval_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        *_base_columns(),
        sa.ForeignKeyConstraint(["resource_issue_id"], ["resource_issue_report.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approved_by"], ["user_account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("update_type IN ('PARTIAL_RESTORE', 'COMPLETE_RESTORE', 'EXTEND_REPAIR')", name="repair_update_type_allowed"),
        sa.CheckConstraint("approval_status IN ('PENDING', 'APPROVED', 'REJECTED')", name="repair_update_approval_allowed"),
        sa.CheckConstraint("restored_quantity >= 0", name="restored_nonnegative"),
    )

    op.create_table(
        "equipment_inventory_movement",
        sa.Column("inventory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_issue_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repair_update_id", postgresql.UUID(as_uuid=True)),
        sa.Column("movement_type", sa.String(24), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("before_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("after_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(["inventory_id"], ["lab_equipment_inventory.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resource_issue_id"], ["resource_issue_report.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["repair_update_id"], ["resource_repair_update.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="inventory_movement_idempotency"),
        sa.CheckConstraint("quantity > 0", name="movement_quantity_positive"),
        sa.CheckConstraint("movement_type IN ('ISSUE_DISABLE', 'REPAIR_RESTORE', 'MANUAL_REVERSAL')", name="movement_type_allowed"),
    )
    op.create_index("ix_inventory_movement_inventory_time", "equipment_inventory_movement", ["inventory_id", "created_at"])


def downgrade() -> None:
    op.drop_table("equipment_inventory_movement")
    op.drop_table("resource_repair_update")
    op.drop_table("adjustment_remediation_item")
    op.drop_table("adjustment_remediation_plan")
    op.drop_table("application_approval_task")
    op.drop_table("session_execution_override")
    op.drop_constraint(op.f("ck_resource_issue_report_resource_issue_quantities_valid"), "resource_issue_report", type_="check")
    op.drop_constraint("fk_resource_issue_report_approved_by_user_account", "resource_issue_report", type_="foreignkey")
    op.drop_constraint("fk_resource_issue_report_inventory_id_lab_equipment_inventory", "resource_issue_report", type_="foreignkey")
    for column in ("approved_at", "approved_by", "restored_quantity", "approved_quantity", "affected_quantity", "inventory_id"):
        op.drop_column("resource_issue_report", column)
    op.drop_constraint(op.f("ck_resource_issue_report_status_allowed"), "resource_issue_report", type_="check")
    op.create_check_constraint("status_allowed", "resource_issue_report", "status IN ('REPORTED', 'PROCESSING', 'RESOLVED', 'CLOSED')")
    op.drop_constraint(op.f("ck_application_request_request_type_allowed"), "application_request", type_="check")
    op.create_check_constraint("request_type_allowed", "application_request", "request_type IN ('RESCHEDULE', 'MAKEUP', 'PROJECT_CHANGE', 'TEACHER_ADJUSTMENT', 'LAB_CHANGE', 'RESOURCE_ADJUSTMENT')")
