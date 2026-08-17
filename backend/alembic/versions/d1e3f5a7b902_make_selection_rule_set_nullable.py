"""make selection_window.selection_rule_set_id nullable

Revision ID: d1e3f5a7b902
Revises: c2d4e6f8a101
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e3f5a7b902"
down_revision: str | Sequence[str] | None = "c2d4e6f8a101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "selection_window",
        "selection_rule_set_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "selection_window",
        "selection_rule_set_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
