"""add scheduling soft rules in a draft v2 rule set

Revision ID: e7a4c9d2b610
Revises: d6b8f2a4c901
Create Date: 2026-07-30 19:00:00
"""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import TableClause

from alembic import op

revision: str = "e7a4c9d2b610"
down_revision: str | Sequence[str] | None = "d6b8f2a4c901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SOFT_RULE_NAMESPACE = UUID("72bb4302-7d4a-49b9-b3c6-969fe2846cb2")
NEW_SOFT_RULES = (
    (
        "TEACHER_COMPACTNESS",
        "减少教师课时过于分散",
        "teacher_schedule_compactness",
    ),
    (
        "TEACHER_CONSECUTIVE_LOAD",
        "避免教师连续承担过多实验",
        "teacher_consecutive_load",
    ),
    (
        "LAB_UTILIZATION_BALANCE",
        "平衡实验室利用率",
        "laboratory_utilization_balance",
    ),
    (
        "STUDENT_AVAILABILITY_COVERAGE",
        "提高学生可选时间覆盖率",
        "student_availability_coverage",
    ),
)


def _draft_rule_set_id(source_id: UUID) -> UUID:
    return uuid5(SOFT_RULE_NAMESPACE, f"{source_id}:SCHEDULING:V2")


def _draft_rule_id(rule_set_id: UUID, rule_code: str) -> UUID:
    return uuid5(SOFT_RULE_NAMESPACE, f"{rule_set_id}:{rule_code}")


def _tables() -> tuple[TableClause, TableClause]:
    rule_set = sa.table(
        "rule_set",
        sa.column("id", sa.Uuid()),
        sa.column("rule_domain", sa.String()),
        sa.column("rule_set_code", sa.String()),
        sa.column("version_no", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("status", sa.String()),
        sa.column("published_at", sa.DateTime(timezone=True)),
        sa.column("published_by", sa.Uuid()),
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


def upgrade() -> None:
    connection = op.get_bind()
    rule_set, rule_config = _tables()
    published_sets = connection.execute(
        sa.select(rule_set).where(
            rule_set.c.rule_domain == "SCHEDULING",
            rule_set.c.status == "PUBLISHED",
        )
    ).mappings().all()

    for source in published_sets:
        draft_id = _draft_rule_set_id(source["id"])
        next_version = connection.scalar(
            sa.select(sa.func.max(rule_set.c.version_no)).where(
                rule_set.c.rule_domain == "SCHEDULING",
                rule_set.c.rule_set_code == source["rule_set_code"],
            )
        )
        next_version = int(next_version or 0) + 1
        connection.execute(
            rule_set.insert().values(
                id=draft_id,
                rule_domain="SCHEDULING",
                rule_set_code=source["rule_set_code"],
                version_no=next_version,
                name=f"排课规则集 V{next_version}（软约束扩展草稿）",
                status="DRAFT",
                published_at=None,
                published_by=None,
                created_by=source["created_by"],
                updated_by=source["updated_by"],
            )
        )

        source_rules = connection.execute(
            sa.select(rule_config).where(
                rule_config.c.rule_set_id == source["id"]
            )
        ).mappings().all()
        copied_rows = [
            {
                "id": _draft_rule_id(draft_id, item["rule_code"]),
                "rule_set_id": draft_id,
                "rule_code": item["rule_code"],
                "rule_name": item["rule_name"],
                "enforcement_type": item["enforcement_type"],
                "scope_config": item["scope_config"],
                "condition_config": item["condition_config"],
                "action_config": item["action_config"],
                "weight": item["weight"],
                "priority": item["priority"],
                "description": item["description"],
                "enabled": item["enabled"],
                "created_by": item["created_by"],
                "updated_by": item["updated_by"],
            }
            for item in source_rules
        ]
        new_rows = [
            {
                "id": _draft_rule_id(draft_id, code),
                "rule_set_id": draft_id,
                "rule_code": code,
                "rule_name": name,
                "enforcement_type": "SCORE",
                "scope_config": {
                    "source": "ADMIN_CONFIRMED",
                    "domain": "SCHEDULING",
                },
                "condition_config": {
                    "configuration_status": "PENDING",
                },
                "action_config": {
                    "action": "SCORE",
                    "metric": metric,
                },
                "weight": Decimal(0),
                "priority": 0,
                "description": f"{name}；待管理员配置权重与参数",
                "enabled": False,
                "created_by": source["created_by"],
                "updated_by": source["updated_by"],
            }
            for code, name, metric in NEW_SOFT_RULES
        ]
        connection.execute(rule_config.insert(), copied_rows + new_rows)


def downgrade() -> None:
    connection = op.get_bind()
    rule_set, _ = _tables()
    published_ids = connection.execute(
        sa.select(rule_set.c.id).where(
            rule_set.c.rule_domain == "SCHEDULING",
            rule_set.c.status == "PUBLISHED",
        )
    ).scalars()
    draft_ids = [_draft_rule_set_id(source_id) for source_id in published_ids]
    if draft_ids:
        connection.execute(
            rule_set.delete().where(rule_set.c.id.in_(draft_ids))
        )
