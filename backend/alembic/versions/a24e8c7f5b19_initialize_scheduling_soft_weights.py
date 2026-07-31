"""initialize scheduling soft-rule weights and priorities

Revision ID: a24e8c7f5b19
Revises: 91f0c3d8a742
Create Date: 2026-07-31 10:00:00
"""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.sql import TableClause

from alembic import op

revision: str = "a24e8c7f5b19"
down_revision: str | Sequence[str] | None = "91f0c3d8a742"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INITIAL_VALUES = {
    "STUDENT_AVAILABILITY_COVERAGE": (Decimal(25), 90, True),
    "TEACHER_BALANCE": (Decimal(15), 80, True),
    "EVENING_PENALTY": (Decimal(12), 70, True),
    "WEEKEND_PENALTY": (Decimal(10), 70, True),
    "TEACHER_COMPACTNESS": (Decimal(10), 60, True),
    "TEACHER_CONSECUTIVE_LOAD": (Decimal(10), 60, True),
    "TEACHER_PREFERRED_TIME": (Decimal(10), 60, True),
    "LAB_UTILIZATION_BALANCE": (Decimal(8), 50, True),
    "TEACHER_TERM_REDUCED_LOAD": (Decimal(0), 40, True),
}

PREVIOUS_VALUES = {
    "TEACHER_BALANCE": (Decimal(1), 50, True),
    "EVENING_PENALTY": (Decimal(1), 40, True),
    "TEACHER_COMPACTNESS": (Decimal(0), 0, False),
    "TEACHER_CONSECUTIVE_LOAD": (Decimal(0), 0, False),
    "LAB_UTILIZATION_BALANCE": (Decimal(0), 0, False),
    "STUDENT_AVAILABILITY_COVERAGE": (Decimal(0), 0, False),
    "WEEKEND_PENALTY": (Decimal(0), 0, False),
    "TEACHER_PREFERRED_TIME": (Decimal(0), 0, False),
    "TEACHER_TERM_REDUCED_LOAD": (Decimal(0), 0, False),
}


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
        sa.column("enforcement_type", sa.String()),
        sa.column("weight", sa.Numeric(8, 4)),
        sa.column("priority", sa.Integer()),
        sa.column("enabled", sa.Boolean()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    return rule_set, rule_config


def _find_target_rule_set(
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


def _validate_rules(
    connection: Connection,
    rule_config: TableClause,
    rule_set_id: UUID,
) -> None:
    rows = connection.execute(
        sa.select(
            rule_config.c.rule_code,
            rule_config.c.enforcement_type,
        ).where(
            rule_config.c.rule_set_id == rule_set_id,
            rule_config.c.rule_code.in_(INITIAL_VALUES),
        )
    ).all()
    actual = {code: enforcement for code, enforcement in rows}
    if set(actual) != set(INITIAL_VALUES):
        missing = sorted(set(INITIAL_VALUES) - set(actual))
        raise RuntimeError(f"SCHEDULING V2 缺少软约束规则: {missing}")
    invalid = sorted(
        code for code, enforcement in actual.items()
        if enforcement != "SCORE"
    )
    if invalid:
        raise RuntimeError(f"以下规则不是 SCORE 类型: {invalid}")


def _apply_values(values: dict[str, tuple[Decimal, int, bool]]) -> None:
    connection = op.get_bind()
    rule_set, rule_config = _tables()
    rule_set_id = _find_target_rule_set(connection, rule_set)
    _validate_rules(connection, rule_config, rule_set_id)

    for rule_code, (weight, priority, enabled) in values.items():
        connection.execute(
            rule_config.update()
            .where(
                rule_config.c.rule_set_id == rule_set_id,
                rule_config.c.rule_code == rule_code,
            )
            .values(
                weight=weight,
                priority=priority,
                enabled=enabled,
                updated_at=sa.func.now(),
            )
        )


def upgrade() -> None:
    _apply_values(INITIAL_VALUES)


def downgrade() -> None:
    _apply_values(PREVIOUS_VALUES)
