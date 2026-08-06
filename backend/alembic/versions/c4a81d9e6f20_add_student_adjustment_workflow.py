"""add student adjustment workflow

Revision ID: c4a81d9e6f20
Revises: 7b91d3f25a60
Create Date: 2026-08-05 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4a81d9e6f20"
down_revision: str | Sequence[str] | None = "7b91d3f25a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "application_request", sa.Column("approval_route", sa.String(20))
    )
    op.add_column(
        "application_request",
        sa.Column(
            "reservation_status",
            sa.String(20),
            nullable=False,
            server_default="NONE",
        ),
    )
    op.add_column(
        "application_request",
        sa.Column("reservation_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "application_request", sa.Column("idempotency_key", sa.String(64))
    )
    op.create_unique_constraint(
        "application_student_idempotency_key",
        "application_request",
        ["student_id", "idempotency_key"],
    )
    op.create_index(
        "ix_application_request_target_status",
        "application_request",
        ["target_session_id", "status"],
    )
    op.create_table(
        "adjustment_execution_audit",
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("before_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("after_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("execution_status", sa.String(20), nullable=False),
        sa.Column("executed_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["application_request.id"],
            name=op.f(
                "fk_adjustment_execution_audit_application_id_application_request"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["experiment_session.id"],
            name=op.f(
                "fk_adjustment_execution_audit_session_id_experiment_session"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_adjustment_execution_audit")),
        sa.UniqueConstraint(
            "application_id",
            name="adjustment_audit_application",
        ),
    )
    op.create_index(
        "ix_adjustment_audit_session",
        "adjustment_execution_audit",
        ["session_id", "executed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_adjustment_audit_session", table_name="adjustment_execution_audit"
    )
    op.drop_table("adjustment_execution_audit")
    op.drop_index(
        "ix_application_request_target_status", table_name="application_request"
    )
    op.drop_constraint(
        "application_student_idempotency_key",
        "application_request",
        type_="unique",
    )
    op.drop_column("application_request", "idempotency_key")
    op.drop_column("application_request", "reservation_expires_at")
    op.drop_column("application_request", "reservation_status")
    op.drop_column("application_request", "approval_route")
