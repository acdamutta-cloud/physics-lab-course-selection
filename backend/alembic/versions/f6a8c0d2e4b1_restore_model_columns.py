"""restore columns still required by application models

Revision ID: f6a8c0d2e4b1
Revises: b3c5a7d9e1f0
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f6a8c0d2e4b1"
down_revision: str | Sequence[str] | None = "b3c5a7d9e1f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESTORED_COLUMN_KEYS = {
    ("application_request", "adjustment_rule_set_id"),
    ("application_request", "approval_rule_set_id"),
    ("campus", "address"),
    ("equipment_asset", "purchase_date"),
    ("experiment_course", "credits"),
    ("experiment_course", "default_slots"),
    ("experiment_project", "material_note"),
    ("lab_equipment_inventory", "checked_at"),
    ("major", "degree_type"),
    ("operation_log", "ip_address"),
    ("rule_config", "action_config"),
    ("rule_config", "condition_config"),
    ("rule_config", "scope_config"),
    ("rule_set", "rule_set_code"),
    ("schedule_version", "parent_version_id"),
    ("student", "birth_date"),
    ("student", "gender"),
    ("student_project_record", "absence_reason"),
    ("student_project_record", "completed_at"),
    ("teacher_project_qualification", "valid_from"),
    ("teacher_project_qualification", "valid_to"),
    ("teaching_task", "capacity_buffer_ratio"),
    ("training_plan", "effective_to"),
}


def upgrade() -> None:
    op.add_column("student", sa.Column("gender", sa.String(length=16)))
    op.add_column("student", sa.Column("birth_date", sa.Date()))
    op.add_column("campus", sa.Column("address", sa.String(length=255)))
    op.add_column(
        "major",
        sa.Column(
            "degree_type",
            sa.String(length=32),
            nullable=False,
            server_default="ENGINEERING",
        ),
    )
    op.add_column(
        "experiment_course",
        sa.Column(
            "credits",
            sa.Numeric(precision=4, scale=1),
            nullable=False,
            server_default="1.0",
        ),
    )
    op.add_column(
        "experiment_course",
        sa.Column(
            "default_slots",
            sa.SmallInteger(),
            nullable=False,
            server_default="4",
        ),
    )
    op.create_check_constraint(
        "credits_nonnegative", "experiment_course", "credits >= 0"
    )
    op.add_column("experiment_project", sa.Column("material_note", sa.Text()))
    op.add_column(
        "student_project_record", sa.Column("absence_reason", sa.Text())
    )
    op.add_column(
        "student_project_record",
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "schedule_version", sa.Column("parent_version_id", sa.Uuid())
    )
    op.add_column(
        "teaching_task",
        sa.Column(
            "capacity_buffer_ratio",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default="1.20",
        ),
    )
    op.create_check_constraint(
        "buffer_ratio_valid", "teaching_task", "capacity_buffer_ratio >= 1"
    )
    op.add_column("training_plan", sa.Column("effective_to", sa.Date()))
    op.add_column("equipment_asset", sa.Column("purchase_date", sa.Date()))
    op.add_column(
        "lab_equipment_inventory",
        sa.Column("checked_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "teacher_project_qualification", sa.Column("valid_from", sa.Date())
    )
    op.add_column(
        "teacher_project_qualification", sa.Column("valid_to", sa.Date())
    )
    op.add_column(
        "application_request", sa.Column("adjustment_rule_set_id", sa.Uuid())
    )
    op.add_column(
        "application_request", sa.Column("approval_rule_set_id", sa.Uuid())
    )
    op.execute(
        "CREATE TRIGGER trg_application_adjustment_rule_domain "
        "BEFORE INSERT OR UPDATE OF adjustment_rule_set_id "
        "ON application_request FOR EACH ROW "
        "EXECUTE FUNCTION enforce_rule_set_domain("
        "'adjustment_rule_set_id', 'ADJUSTMENT')"
    )
    op.execute(
        "CREATE TRIGGER trg_application_approval_rule_domain "
        "BEFORE INSERT OR UPDATE OF approval_rule_set_id "
        "ON application_request FOR EACH ROW "
        "EXECUTE FUNCTION enforce_rule_set_domain("
        "'approval_rule_set_id', 'APPROVAL')"
    )
    op.add_column(
        "operation_log", sa.Column("ip_address", postgresql.INET())
    )
    op.drop_constraint("domain_code_version", "rule_set", type_="unique")
    op.drop_index("uq_rule_set_published", table_name="rule_set")
    op.add_column(
        "rule_set",
        sa.Column(
            "rule_set_code",
            sa.String(length=64),
            nullable=False,
            server_default="LEGACY",
        ),
    )
    op.create_unique_constraint(
        "domain_code_version",
        "rule_set",
        ["rule_domain", "rule_set_code", "version_no"],
    )
    op.create_index(
        "uq_rule_set_published",
        "rule_set",
        ["rule_domain", "rule_set_code"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.add_column(
        "rule_config",
        sa.Column(
            "scope_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "rule_config",
        sa.Column(
            "condition_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "rule_config",
        sa.Column(
            "action_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("rule_config", "scope_config")
    op.drop_column("rule_config", "condition_config")
    op.drop_column("rule_config", "action_config")
    op.execute(
        "ALTER TABLE rule_set DROP CONSTRAINT IF EXISTS domain_code_version"
    )
    op.drop_index("uq_rule_set_published", table_name="rule_set")
    op.drop_column("rule_set", "rule_set_code")
    op.create_unique_constraint(
        "domain_code_version", "rule_set", ["rule_domain", "version_no"]
    )
    op.create_index(
        "uq_rule_set_published",
        "rule_set",
        ["rule_domain"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_application_adjustment_rule_domain "
        "ON application_request"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_application_approval_rule_domain "
        "ON application_request"
    )
    op.drop_column("application_request", "adjustment_rule_set_id")
    op.drop_column("application_request", "approval_rule_set_id")
    op.drop_column("operation_log", "ip_address")
    op.drop_column("student", "gender")
    op.drop_column("student", "birth_date")
    op.drop_column("campus", "address")
    op.drop_column("major", "degree_type")
    op.drop_constraint(
        "credits_nonnegative", "experiment_course", type_="check"
    )
    op.drop_column("experiment_course", "credits")
    op.drop_column("experiment_course", "default_slots")
    op.drop_column("experiment_project", "material_note")
    op.drop_column("student_project_record", "absence_reason")
    op.drop_column("student_project_record", "completed_at")
    op.drop_column("schedule_version", "parent_version_id")
    op.drop_constraint("buffer_ratio_valid", "teaching_task", type_="check")
    op.drop_column("teaching_task", "capacity_buffer_ratio")
    op.drop_column("training_plan", "effective_to")
    op.drop_column("equipment_asset", "purchase_date")
    op.drop_column("lab_equipment_inventory", "checked_at")
    op.drop_column("teacher_project_qualification", "valid_from")
    op.drop_column("teacher_project_qualification", "valid_to")
