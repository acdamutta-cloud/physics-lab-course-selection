"""add course early-week scheduling soft rule

Revision ID: 3e8f1a6c2d74
Revises: f2c7a91e4b63
Create Date: 2026-07-31 19:00:00
"""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection
from sqlalchemy.sql import TableClause

from alembic import op

revision: str = "3e8f1a6c2d74"
down_revision: str | Sequence[str] | None = "f2c7a91e4b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULE_NAMESPACE = UUID("72bb4302-7d4a-49b9-b3c6-969fe2846cb2")
RULE_CODE = "COURSE_EARLY_WEEK_PREFERENCE"


def _tables() -> tuple[TableClause, TableClause]:
    rule_set = sa.table(
        "rule_set",
        sa.column("id", sa.Uuid()),
        sa.column("rule_domain", sa.String()),
        sa.column("version_no", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("created_by", sa.Uuid()),
        sa.column("updated_by", sa.Uuid()),
    )
    rule_config = sa.table(
        "rule_config",
        sa.column("id", sa.Uuid()),
        sa.column("rule_set_id", sa.Uuid()),
        sa.column("rule_code", sa.String()),
        sa.column("rule_name", sa.String()),
        sa.column("enforcement_type", sa.String()),
        sa.column("scope_config", postgresql.JSONB()),
        sa.column("condition_config", postgresql.JSONB()),
        sa.column("action_config", postgresql.JSONB()),
        sa.column("weight", sa.Numeric(8, 4)),
        sa.column("priority", sa.Integer()),
        sa.column("description", sa.Text()),
        sa.column("enabled", sa.Boolean()),
        sa.column("created_by", sa.Uuid()),
        sa.column("updated_by", sa.Uuid()),
    )
    return rule_set, rule_config


def _target_rule_set(
    connection: Connection,
    rule_set: TableClause,
) -> dict:
    targets = connection.execute(
        sa.select(rule_set).where(
            rule_set.c.rule_domain == "SCHEDULING",
            rule_set.c.version_no == 2,
            rule_set.c.status == "DRAFT",
        )
    ).mappings().all()
    if len(targets) != 1:
        raise RuntimeError(
            "必须存在且只能存在一个 SCHEDULING V2 DRAFT 规则集"
        )
    return dict(targets[0])


def upgrade() -> None:
    connection = op.get_bind()
    rule_set, rule_config = _tables()
    target = _target_rule_set(connection, rule_set)
    existing = connection.scalar(
        sa.select(sa.func.count())
        .select_from(rule_config)
        .where(
            rule_config.c.rule_set_id == target["id"],
            rule_config.c.rule_code == RULE_CODE,
        )
    )
    if existing:
        raise RuntimeError(f"SCHEDULING V2 已存在 {RULE_CODE}")

    connection.execute(
        rule_config.insert().values(
            id=uuid5(RULE_NAMESPACE, f"{target['id']}:{RULE_CODE}"),
            rule_set_id=target["id"],
            rule_code=RULE_CODE,
            rule_name="指定课程前置周安排评分",
            enforcement_type="SCORE",
            scope_config={
                "domain": "SCHEDULING",
                "runtime_only": True,
            },
            condition_config={
                "configuration_status": "RUNTIME",
                "target_source": "SCHEDULE_JOB_INPUT",
                "parameter": "preferred_end_week",
            },
            action_config={
                "action": "SCORE",
                "metric": "target_course_late_session_ratio",
            },
            weight=Decimal(0),
            priority=40,
            description=(
                "管理员指定课程及截止周时，降低该课程晚于截止周的"
                "场次比例；基础权重为 0"
            ),
            enabled=True,
            created_by=target["created_by"],
            updated_by=target["updated_by"],
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    rule_set, rule_config = _tables()
    target = _target_rule_set(connection, rule_set)
    connection.execute(
        rule_config.delete().where(
            rule_config.c.rule_set_id == target["id"],
            rule_config.c.rule_code == RULE_CODE,
        )
    )
