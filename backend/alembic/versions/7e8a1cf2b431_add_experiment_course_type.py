"""add experiment course type

Revision ID: 7e8a1cf2b431
Revises: 53a4d9f6c210
Create Date: 2026-07-29 15:30:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "7e8a1cf2b431"
down_revision: str | Sequence[str] | None = "53a4d9f6c210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiment_course",
        sa.Column(
            "course_type",
            sa.String(length=20),
            server_default="EXPERIMENT",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "course_type_allowed",
        "experiment_course",
        "course_type IN ('EXPERIMENT', 'THEORY')",
    )
    op.alter_column("experiment_course", "course_type", server_default=None)


def downgrade() -> None:
    op.drop_constraint("course_type_allowed", "experiment_course", type_="check")
    op.drop_column("experiment_course", "course_type")
