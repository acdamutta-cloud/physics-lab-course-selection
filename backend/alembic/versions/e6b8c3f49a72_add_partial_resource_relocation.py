"""add partial resource relocation and equipment sharing rules

Revision ID: e6b8c3f49a72
Revises: d9f2148c6a31
Create Date: 2026-08-06 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e6b8c3f49a72"
down_revision: str | Sequence[str] | None = "d9f2148c6a31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column]:
    return [
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
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True)),
    ]


def upgrade() -> None:
    op.add_column("lab_equipment_inventory", sa.Column("usage_note", sa.Text()))
    op.add_column(
        "lab_equipment_inventory", sa.Column("students_per_unit", sa.SmallInteger())
    )
    op.add_column(
        "lab_equipment_inventory",
        sa.Column(
            "sharing_rule_status",
            sa.String(20),
            nullable=False,
            server_default="UNPARSED",
        ),
    )
    op.add_column(
        "lab_equipment_inventory", sa.Column("sharing_rule_source", sa.String(20))
    )
    op.add_column(
        "lab_equipment_inventory", sa.Column("sharing_rule_evidence", sa.Text())
    )
    op.create_check_constraint(
        "students_per_unit_positive",
        "lab_equipment_inventory",
        "students_per_unit IS NULL OR students_per_unit >= 1",
    )
    op.create_check_constraint(
        "sharing_rule_status_allowed",
        "lab_equipment_inventory",
        "sharing_rule_status IN ('UNPARSED', 'CONFIRMED', 'AMBIGUOUS')",
    )

    # Preserve the sharing meaning previously shown by the UI.  Ratios near an
    # integer become a confirmed deterministic multiplier; broad 'many people'
    # descriptions remain ambiguous and cannot drive automatic relocation.
    op.execute(
        """
        UPDATE lab_equipment_inventory AS inventory
        SET usage_note = ROUND(lab.safety_capacity::numeric /
                               NULLIF(inventory.total_quantity, 0))::int::text || '人一台',
            students_per_unit = ROUND(lab.safety_capacity::numeric /
                                      NULLIF(inventory.total_quantity, 0))::int,
            sharing_rule_status = 'CONFIRMED',
            sharing_rule_source = 'MIGRATED',
            sharing_rule_evidence = '由原实验室容量与设备总量比例迁移'
        FROM laboratory AS lab
        WHERE inventory.laboratory_id = lab.id
          AND inventory.total_quantity > 0
          AND lab.safety_capacity::numeric / inventory.total_quantity >= 1.5
          AND lab.safety_capacity::numeric / inventory.total_quantity < 3.5
        """
    )
    op.execute(
        """
        UPDATE lab_equipment_inventory AS inventory
        SET usage_note = '多人共用，需管理员确认每台可供学生数',
            sharing_rule_status = 'AMBIGUOUS',
            sharing_rule_source = 'MIGRATED'
        FROM laboratory AS lab
        WHERE inventory.laboratory_id = lab.id
          AND inventory.total_quantity > 0
          AND lab.safety_capacity::numeric / inventory.total_quantity >= 3.5
        """
    )

    op.add_column(
        "resource_issue_report",
        sa.Column(
            "remediation_status",
            sa.String(24),
            nullable=False,
            server_default="NOT_REQUIRED",
        ),
    )
    op.create_check_constraint(
        "resource_remediation_status_allowed",
        "resource_issue_report",
        "remediation_status IN ('NOT_REQUIRED', 'REMEDIATION_REQUIRED', "
        "'PARTIALLY_REMEDIATED', 'REMEDIATED')",
    )

    op.create_table(
        "resource_relocation_plan",
        sa.Column("resource_issue_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_no", sa.Integer(), nullable=False),
        sa.Column("required_relocation_count", sa.Integer(), nullable=False),
        sa.Column("planned_relocation_count", sa.Integer(), nullable=False),
        sa.Column("remaining_unresolved_count", sa.Integer(), nullable=False),
        sa.Column("capacity_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("validation_result", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="VALIDATED"),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ["resource_issue_id"], ["resource_issue_report.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_session_id"], ["experiment_session.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_issue_id",
            "source_session_id",
            "plan_no",
            name="resource_relocation_plan_number",
        ),
        sa.CheckConstraint(
            "status IN ('VALIDATED', 'EXECUTED', 'STALE')",
            name="resource_relocation_plan_status_allowed",
        ),
    )
    op.create_index(
        "ix_resource_relocation_issue_session_status",
        "resource_relocation_plan",
        ["resource_issue_id", "source_session_id", "status"],
    )
    op.create_table(
        "resource_relocation_item",
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "student_project_record_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("target_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("validation_result", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="VALIDATED"),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["resource_relocation_plan.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["student_id"], ["student.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["student_project_record_id"],
            ["student_project_record.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_session_id"], ["experiment_session.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id", "student_id", name="resource_relocation_plan_student"
        ),
        sa.CheckConstraint(
            "status IN ('VALIDATED', 'EXECUTED', 'STALE')",
            name="resource_relocation_item_status_allowed",
        ),
    )


def downgrade() -> None:
    op.drop_table("resource_relocation_item")
    op.drop_index(
        "ix_resource_relocation_issue_session_status",
        table_name="resource_relocation_plan",
    )
    op.drop_table("resource_relocation_plan")
    op.drop_constraint(
        op.f("ck_resource_issue_report_resource_remediation_status_allowed"),
        "resource_issue_report",
        type_="check",
    )
    op.drop_column("resource_issue_report", "remediation_status")
    op.drop_constraint(
        op.f("ck_lab_equipment_inventory_sharing_rule_status_allowed"),
        "lab_equipment_inventory",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_lab_equipment_inventory_students_per_unit_positive"),
        "lab_equipment_inventory",
        type_="check",
    )
    for column in (
        "sharing_rule_evidence",
        "sharing_rule_source",
        "sharing_rule_status",
        "students_per_unit",
        "usage_note",
    ):
        op.drop_column("lab_equipment_inventory", column)
