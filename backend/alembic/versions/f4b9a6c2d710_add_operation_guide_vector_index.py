"""add operation guide vector index

Revision ID: f4b9a6c2d710
Revises: e6b8c3f49a72
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f4b9a6c2d710"
down_revision: str | None = "e6b8c3f49a72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    vector_available = bool(
        bind.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM pg_available_extensions "
                "WHERE name = 'vector')"
            )
        ).scalar()
    )
    if vector_available:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "operation_guide_index",
        sa.Column("guide_id", sa.String(length=64), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("audience", sa.String(length=20), nullable=False),
        sa.Column("knowledge_type", sa.String(length=32), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("platform_version", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),
    )
    if vector_available:
        op.execute(
            "ALTER TABLE operation_guide_index "
            "ALTER COLUMN embedding TYPE vector(1024) USING embedding::vector"
        )
    op.create_index(
        "ix_operation_guide_filter",
        "operation_guide_index",
        ["audience", "knowledge_type", "status", "locale", "platform_version"],
    )
    if vector_available:
        op.execute(
            "CREATE INDEX ix_operation_guide_embedding_hnsw "
            "ON operation_guide_index USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    op.drop_table("operation_guide_index")
