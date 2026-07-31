"""add project group mode

Revision ID: c4a7f0912d3e
Revises: 7e8a1cf2b431
Create Date: 2026-07-30 16:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4a7f0912d3e"
down_revision: str | Sequence[str] | None = "7e8a1cf2b431"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiment_project",
        sa.Column("group_mode", sa.String(length=20), nullable=True),
    )
    op.execute(
        """
        UPDATE experiment_project
        SET group_mode = CASE
            WHEN default_group_size = 1 THEN 'INDIVIDUAL'
            ELSE 'GROUP'
        END
        """
    )
    op.alter_column(
        "experiment_project",
        "group_mode",
        existing_type=sa.String(length=20),
        nullable=False,
    )
    op.create_check_constraint(
        "group_mode_allowed",
        "experiment_project",
        "group_mode IN ('INDIVIDUAL', 'GROUP')",
    )
    op.create_check_constraint(
        "group_mode_size_consistent",
        "experiment_project",
        "(group_mode = 'INDIVIDUAL' AND default_group_size = 1) OR "
        "(group_mode = 'GROUP' AND default_group_size >= 2)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "group_mode_size_consistent",
        "experiment_project",
        type_="check",
    )
    op.drop_constraint(
        "group_mode_allowed",
        "experiment_project",
        type_="check",
    )
    op.drop_column("experiment_project", "group_mode")
