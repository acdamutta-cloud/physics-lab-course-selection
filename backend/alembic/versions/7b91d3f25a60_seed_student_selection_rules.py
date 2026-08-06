"""seed student selection consultation rules

Revision ID: 7b91d3f25a60
Revises: 28cc400ecde2
Create Date: 2026-08-04 10:00:00
"""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7b91d3f25a60"
down_revision: str | Sequence[str] | None = "28cc400ecde2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMESPACE = UUID("d4fe8d5e-a91e-4e1a-8b16-8ed7698c70dd")
_RULES = (
    ("STUDENT_INACTIVE", "学籍状态有效", "只有学籍状态有效的学生可以选择实验场次。", "BLOCK"),
    ("STUDY_PERIOD_NOT_REACHED", "达到培养方案修读学期", "未达到培养方案规定的修读学年和学期时不能选择该课程场次。", "BLOCK"),
    ("PREREQUISITE_COURSE_NOT_PASSED", "先修课程要求", "培养方案要求必须完成的先修课程尚未通过时不能选择该课程场次。", "BLOCK"),
    ("COURSE_ALREADY_PASSED", "已通过课程不得重复修读", "实验课程已经通过时不能重复修读；未通过课程允许按规则重修。", "BLOCK"),
    ("SCHEDULE_NOT_PUBLISHED", "课表必须发布", "场次所属课表尚未发布时不能选择该场次。", "BLOCK"),
    ("SESSION_NOT_OPEN", "场次必须开放", "实验场次未开放选课时不能选择。", "BLOCK"),
    ("SESSION_FULL", "场次容量限制", "实验场次没有剩余名额时不能选择。", "BLOCK"),
    ("SESSION_ALREADY_SELECTED", "相同场次幂等提示", "重复提交已经选择的同一场次时返回已选择提示。", "WARN"),
    ("PROJECT_ALREADY_SELECTED", "同一项目仅选一个场次", "同一学期同一实验项目只能保留一个有效场次，退选后可改选其他场次。", "BLOCK"),
    ("PROJECT_OCCUPIED_BY_APPLICATION", "处理中申请占用项目", "待审核或处理中的有效申请占用项目唯一名额，处理完成前不能重复选择。", "BLOCK"),
    ("BASE_SCHEDULE_CONFLICT", "学生基础课表时间冲突", "实验场次与学生当前学期Busy课表冲突时不能选择。", "BLOCK"),
    ("EXPERIMENT_SESSION_CONFLICT", "实验安排时间冲突", "实验场次与已选或处理中的实验安排冲突时不能选择。", "BLOCK"),
    ("PROJECT_ORDER_VIOLATION", "项目修读顺序", "场次时间违反培养方案中的项目先后约束时不能选择。", "BLOCK"),
    ("PROJECT_ORDER_PENDING", "前置项目待选择提醒", "先选择后置项目时，仍需选择时间更早且符合资格的前置项目场次。", "WARN"),
)
_RULE_CODES = tuple(item[0] for item in _RULES)


def _tables() -> tuple[sa.Table, sa.Table]:
    rule_set = sa.table(
        "rule_set",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("rule_domain", sa.String()),
        sa.column("status", sa.String()),
    )
    rule_config = sa.table(
        "rule_config",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("rule_set_id", postgresql.UUID(as_uuid=True)),
        sa.column("rule_code", sa.String()),
        sa.column("rule_name", sa.String()),
        sa.column("enforcement_type", sa.String()),
        sa.column("scope_config", postgresql.JSONB()),
        sa.column("condition_config", postgresql.JSONB()),
        sa.column("action_config", postgresql.JSONB()),
        sa.column("weight", sa.Numeric()),
        sa.column("priority", sa.Integer()),
        sa.column("description", sa.Text()),
        sa.column("enabled", sa.Boolean()),
        sa.column("created_by", postgresql.UUID(as_uuid=True)),
        sa.column("updated_by", postgresql.UUID(as_uuid=True)),
    )
    return rule_set, rule_config


def upgrade() -> None:
    connection = op.get_bind()
    rule_set, rule_config = _tables()
    published_ids = connection.execute(
        sa.select(rule_set.c.id).where(
            rule_set.c.rule_domain == "SELECTION",
            rule_set.c.status == "PUBLISHED",
        )
    ).scalars()
    for rule_set_id in published_ids:
        existing = set(
            connection.execute(
                sa.select(rule_config.c.rule_code).where(
                    rule_config.c.rule_set_id == rule_set_id,
                    rule_config.c.rule_code.in_(_RULE_CODES),
                )
            ).scalars()
        )
        for code, name, description, enforcement in _RULES:
            if code in existing:
                continue
            connection.execute(
                rule_config.insert().values(
                    id=uuid5(_NAMESPACE, f"{rule_set_id}:{code}"),
                    rule_set_id=rule_set_id,
                    rule_code=code,
                    rule_name=name,
                    enforcement_type=enforcement,
                    scope_config={"audience": "STUDENT"},
                    condition_config={},
                    action_config={},
                    weight=Decimal(0),
                    priority=100,
                    description=description,
                    enabled=True,
                    created_by=None,
                    updated_by=None,
                )
            )


def downgrade() -> None:
    connection = op.get_bind()
    rule_set, rule_config = _tables()
    selection_ids = sa.select(rule_set.c.id).where(
        rule_set.c.rule_domain == "SELECTION"
    )
    connection.execute(
        rule_config.delete().where(
            rule_config.c.rule_set_id.in_(selection_ids),
            rule_config.c.rule_code.in_(_RULE_CODES),
        )
    )
