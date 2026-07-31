"""fix scheduling weekend day mapping

Revision ID: f2c7a91e4b63
Revises: b7c1d4e8f920
Create Date: 2026-07-31 18:00:00
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection
from sqlalchemy.sql import TableClause

from alembic import op

revision: str = "f2c7a91e4b63"
down_revision: str | Sequence[str] | None = "b7c1d4e8f920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> tuple[TableClause, TableClause]:
    rule_set = sa.table(
        "rule_set",
        sa.column("id", sa.Uuid()),
        sa.column("rule_domain", sa.String()),
        sa.column("version_no", sa.Integer()),
        sa.column("status", sa.String()),
    )
    rule_config = sa.table(
        "rule_config",
        sa.column("rule_set_id", sa.Uuid()),
        sa.column("rule_code", sa.String()),
        sa.column("condition_config", postgresql.JSONB()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    return rule_set, rule_config


def _target_rule_set_id(
    connection: Connection,
    rule_set: TableClause,
) -> UUID:
    target_ids = connection.execute(
        sa.select(rule_set.c.id).where(
            rule_set.c.rule_domain == "SCHEDULING",
            rule_set.c.version_no == 2,
            rule_set.c.status == "DRAFT",
        )
    ).scalars().all()
    if len(target_ids) != 1:
        raise RuntimeError(
            "必须存在且只能存在一个 SCHEDULING V2 DRAFT 规则集"
        )
    return target_ids[0]


def _apply_weekend_days(days: list[int]) -> None:
    connection = op.get_bind()
    rule_set, rule_config = _tables()
    rule_set_id = _target_rule_set_id(connection, rule_set)
    row = connection.execute(
        sa.select(rule_config.c.condition_config).where(
            rule_config.c.rule_set_id == rule_set_id,
            rule_config.c.rule_code == "WEEKEND_PENALTY",
        )
    ).one_or_none()
    if row is None:
        raise RuntimeError(
            "SCHEDULING V2 DRAFT 缺少 WEEKEND_PENALTY 规则"
        )

    condition_config = dict(row.condition_config or {})
    condition_config["weekend_days"] = days
    connection.execute(
        rule_config.update()
        .where(
            rule_config.c.rule_set_id == rule_set_id,
            rule_config.c.rule_code == "WEEKEND_PENALTY",
        )
        .values(
            condition_config=condition_config,
            updated_at=sa.func.now(),
        )
    )


def upgrade() -> None:
    _apply_weekend_days([1, 7])


def downgrade() -> None:
    _apply_weekend_days([6, 7])
