"""add training plan order override flag

Revision ID: 53a4d9f6c210
Revises: 098ffc3a9b59
Create Date: 2026-07-29 12:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "53a4d9f6c210"
down_revision: str | Sequence[str] | None = "098ffc3a9b59"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "training_plan_course",
        sa.Column(
            "allow_order_override",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("training_plan_course", "allow_order_override")
