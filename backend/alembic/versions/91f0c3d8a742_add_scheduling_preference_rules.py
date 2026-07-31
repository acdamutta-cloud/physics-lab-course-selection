"""add scheduling preference rules

Revision ID: 91f0c3d8a742
Revises: 05ad2bf43756
Create Date: 2026-07-30 20:00:00
"""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection
from sqlalchemy.sql import TableClause

from alembic import op

revision: str = "91f0c3d8a742"
down_revision: str | Sequence[str] | None = "05ad2bf43756"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RULE_NAMESPACE = UUID("72bb4302-7d4a-49b9-b3c6-969fe2846cb2")
NEW_RULES = (
    (
        "WEEKEND_PENALTY",
        "尽量减少周末实验",
        {"configuration_status": "PENDING", "weekend_days": [6, 7]},
        "weekend_session_count",
    ),
    (
        "TEACHER_PREFERRED_TIME",
        "尽量满足教师偏好时间",
        {
            "configuration_status": "PENDING",
            "availability_type": "PREFERRED",
        },
        "teacher_preferred_time_match",
    ),
    (
        "TEACHER_TERM_REDUCED_LOAD",
        "指定教师本学期尽量少排",
        {
            "configuration_status": "PENDING",
            "preference_source": "teacher_term_load_preference",
        },
        "teacher_sessions_over_preferred_max",
    ),
)


def _rule_id(rule_set_id: UUID, rule_code: str) -> UUID:
    return uuid5(RULE_NAMESPACE, f"{rule_set_id}:{rule_code}")


def _rule_tables() -> tuple[TableClause, TableClause]:
    rule_set = sa.table(
        "rule_set",
        sa.column("id", sa.Uuid()),
        sa.column("rule_domain", sa.String()),
        sa.column("version_no", sa.Integer()),
        sa.column("status", sa.String()),
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
    )
    return rule_set, rule_config


def _find_scheduling_v2(
    connection: Connection,
    rule_set: TableClause,
    rule_config: TableClause,
) -> UUID:
    rows = connection.execute(
        sa.select(rule_set.c.id)
        .join(
            rule_config,
            rule_config.c.rule_set_id == rule_set.c.id,
        )
        .where(
            rule_set.c.rule_domain == "SCHEDULING",
            rule_set.c.version_no == 2,
            rule_set.c.status == "DRAFT",
            rule_config.c.rule_code == "TEACHER_COMPACTNESS",
        )
    ).scalars().all()
    if len(rows) != 1:
        raise RuntimeError(
            "必须存在且只能存在一个包含 TEACHER_COMPACTNESS 的"
            " SCHEDULING V2 草稿"
        )
    return rows[0]


def upgrade() -> None:
    op.create_table(
        "teacher_term_load_preference",
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("term_id", sa.Uuid(), nullable=False),
        sa.Column(
            "preferred_max_session_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "preferred_max_session_count >= 0",
            name=op.f(
                "ck_teacher_term_load_preference_"
                "max_sessions_nonnegative"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teacher.id"],
            name=op.f(
                "fk_teacher_term_load_preference_teacher_id_teacher"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["term_id"],
            ["academic_term.id"],
            name=op.f(
                "fk_teacher_term_load_preference_term_id_academic_term"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_teacher_term_load_preference"),
        ),
        sa.UniqueConstraint(
            "teacher_id",
            "term_id",
            name="teacher_term_load",
        ),
    )
    op.create_index(
        "ix_teacher_term_load_preference_lookup",
        "teacher_term_load_preference",
        ["term_id", "enabled"],
    )

    connection = op.get_bind()
    rule_set, rule_config = _rule_tables()
    scheduling_v2_id = _find_scheduling_v2(
        connection,
        rule_set,
        rule_config,
    )
    connection.execute(
        rule_config.insert(),
        [
            {
                "id": _rule_id(scheduling_v2_id, code),
                "rule_set_id": scheduling_v2_id,
                "rule_code": code,
                "rule_name": name,
                "enforcement_type": "SCORE",
                "scope_config": {
                    "domain": "SCHEDULING",
                    "source": "ADMIN_CONFIRMED",
                },
                "condition_config": condition,
                "action_config": {
                    "action": "SCORE",
                    "metric": metric,
                },
                "weight": Decimal(0),
                "priority": 0,
                "description": f"{name}；待管理员配置权重与参数",
                "enabled": False,
            }
            for code, name, condition, metric in NEW_RULES
        ],
    )


def downgrade() -> None:
    connection = op.get_bind()
    rule_set, rule_config = _rule_tables()
    scheduling_v2_id = _find_scheduling_v2(
        connection,
        rule_set,
        rule_config,
    )
    connection.execute(
        rule_config.delete().where(
            rule_config.c.id.in_(
                [
                    _rule_id(scheduling_v2_id, code)
                    for code, _, _, _ in NEW_RULES
                ]
            )
        )
    )
    op.drop_index(
        "ix_teacher_term_load_preference_lookup",
        table_name="teacher_term_load_preference",
    )
    op.drop_table("teacher_term_load_preference")
