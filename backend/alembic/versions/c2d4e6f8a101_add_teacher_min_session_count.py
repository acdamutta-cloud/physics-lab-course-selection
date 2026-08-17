"""add teacher min_session_count

Revision ID: c2d4e6f8a101
Revises: b8d3f1a6c920
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d4e6f8a101"
down_revision: str | Sequence[str] | None = "b8d3f1a6c920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "teacher",
        sa.Column(
            "min_session_count",
            sa.Integer(),
            nullable=False,
            server_default="20",
        ),
    )


def downgrade() -> None:
    op.drop_column("teacher", "min_session_count")
