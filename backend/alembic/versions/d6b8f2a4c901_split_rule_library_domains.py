"""split rule library into independent business domains

Revision ID: d6b8f2a4c901
Revises: c4a7f0912d3e
Create Date: 2026-07-30 18:00:00
"""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection
from sqlalchemy.sql import TableClause

from alembic import op

revision: str = "d6b8f2a4c901"
down_revision: str | Sequence[str] | None = "c4a7f0912d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MIGRATION_NAMESPACE = UUID("f8be95f8-4b2c-4c58-a2fc-1785ab633d9d")
SELECTION_RULE_CODES = {
    "STUDENT_TIME_CONFLICT",
    "PROJECT_DUPLICATE",
}
SCHEDULING_RULE_CODES = {
    "TEACHER_TIME_CONFLICT",
    "LAB_TIME_CONFLICT",
    "SESSION_CAPACITY",
    "TEACHER_QUALIFICATION",
    "LAB_CAPABILITY",
    "EQUIPMENT_AVAILABLE",
    "TEACHER_BALANCE",
    "EVENING_PENALTY",
}

ADJUSTMENT_RULES = (
    ("RESCHEDULE_SAME_PROJECT", "调课须保持实验项目不变", "BLOCK"),
    ("RESCHEDULE_TARGET_CAPACITY", "调课目标场次须有余量", "BLOCK"),
    ("RESCHEDULE_STUDENT_TIME", "调课不得与学生课表冲突", "BLOCK"),
    ("GROUP_CHANGE_CAPACITY", "换组目标组须有容量", "BLOCK"),
    (
        "GROUP_CHANGE_FEATURE_ENABLED",
        "未启用场次内分组时不得换组",
        "BLOCK",
    ),
    ("MAKEUP_ABSENCE_REQUIRED", "补做须存在缺做记录", "BLOCK"),
    ("MAKEUP_WITHIN_DEADLINE", "补做须在规定期限内申请", "BLOCK"),
    (
        "REPLACEMENT_TEACHER_QUALIFIED",
        "替换教师须具备项目资格",
        "BLOCK",
    ),
    ("RESOURCE_ISSUE_SUSPEND", "资源异常须暂停受影响安排", "WARN"),
    ("LAB_UNAVAILABLE_BLOCK", "停用实验室不得继续使用", "BLOCK"),
    (
        "MINIMIZE_PUBLISHED_SCHEDULE_CHANGE",
        "调整应尽量减少对已发布课表的影响",
        "SCORE",
    ),
)

APPROVAL_RULES = (
    (
        "SINGLE_STUDENT_NO_SESSION_CHANGE_AUTO_APPROVE",
        "单人且不改变正式场次时自动批准",
        "AUTO_APPROVE",
    ),
    (
        "HARD_CONFLICT_AUTO_REJECT",
        "存在阻断级硬冲突时自动驳回",
        "AUTO_REJECT",
    ),
    (
        "OFFICIAL_SESSION_CHANGE_MANUAL_REVIEW",
        "改变正式场次时转管理员审批",
        "MANUAL_REVIEW",
    ),
    (
        "MULTI_STUDENT_IMPACT_MANUAL_REVIEW",
        "影响多名学生时转管理员审批",
        "MANUAL_REVIEW",
    ),
    (
        "INITIAL_SCHEDULE_PUBLISH_ADMIN_CONFIRM",
        "初始课表发布须管理员确认",
        "ADMIN_CONFIRM",
    ),
    (
        "RULE_SET_PUBLISH_ADMIN_CONFIRM",
        "规则启用须管理员确认",
        "ADMIN_CONFIRM",
    ),
    (
        "SCHEDULE_ROLLBACK_ADMIN_CONFIRM",
        "课表回滚须管理员确认",
        "ADMIN_CONFIRM",
    ),
)


def _split_id(source_id: UUID, domain: str) -> UUID:
    return uuid5(MIGRATION_NAMESPACE, f"{source_id}:{domain}")


def _rule_id(rule_set_id: UUID, rule_code: str) -> UUID:
    return uuid5(MIGRATION_NAMESPACE, f"{rule_set_id}:{rule_code}")


def _domain_code(source_code: str, domain: str) -> str:
    suffix = f"-{domain}"
    return f"{source_code[: 64 - len(suffix)]}{suffix}"


def _rule_tables() -> tuple[TableClause, TableClause]:
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
    )
    return rule_set, rule_config


def _insert_domain_rules(
    connection: Connection,
    rule_config: TableClause,
    *,
    rule_set_id: UUID,
    domain: str,
) -> None:
    if domain == "ADJUSTMENT":
        rows = [
            {
                "id": _rule_id(rule_set_id, code),
                "rule_set_id": rule_set_id,
                "rule_code": code,
                "rule_name": name,
                "enforcement_type": enforcement,
                "scope_config": {"source": "PRD_V1"},
                "condition_config": {
                    "source": "PRD_V1",
                    "requires_runtime_context": True,
                },
                "action_config": {"action": enforcement},
                "weight": Decimal(0),
                "priority": 100,
                "description": f"{name}（PRD 初始草稿）",
                "enabled": True,
            }
            for code, name, enforcement in ADJUSTMENT_RULES
        ]
    else:
        rows = [
            {
                "id": _rule_id(rule_set_id, code),
                "rule_set_id": rule_set_id,
                "rule_code": code,
                "rule_name": name,
                "enforcement_type": "ROUTE",
                "scope_config": {"source": "PRD_V1"},
                "condition_config": {
                    "source": "PRD_V1",
                    "requires_runtime_context": True,
                },
                "action_config": {"action": "ROUTE", "route": route},
                "weight": Decimal(0),
                "priority": 100,
                "description": f"{name}（PRD 初始草稿）",
                "enabled": True,
            }
            for code, name, route in APPROVAL_RULES
        ]
    if rows:
        connection.execute(rule_config.insert(), rows)


def _split_existing_rule_sets() -> dict[UUID, dict[str, UUID]]:
    connection = op.get_bind()
    rule_set, rule_config = _rule_tables()
    sources = connection.execute(sa.select(rule_set)).mappings().all()
    domain_ids_by_source: dict[UUID, dict[str, UUID]] = {}

    domain_suffixes = {
        domain: f"-{domain}"
        for domain in (
            "SCHEDULING",
            "SELECTION",
            "ADJUSTMENT",
            "APPROVAL",
        )
    }
    previously_split = [
        source
        for source in sources
        if any(
            source["rule_set_code"].endswith(suffix)
            for suffix in domain_suffixes.values()
        )
    ]
    if previously_split:
        if len(previously_split) != len(sources):
            raise RuntimeError(
                "检测到已拆分和未拆分规则集混合，无法安全自动迁移"
            )
        groups: dict[tuple[str, int], dict[str, UUID]] = {}
        for source in sources:
            matched_domain = next(
                domain
                for domain, suffix in domain_suffixes.items()
                if source["rule_set_code"].endswith(suffix)
            )
            suffix = domain_suffixes[matched_domain]
            base_code = source["rule_set_code"][: -len(suffix)]
            groups.setdefault(
                (base_code, source["version_no"]), {}
            )[matched_domain] = source["id"]
            connection.execute(
                rule_set.update()
                .where(rule_set.c.id == source["id"])
                .values(rule_domain=matched_domain)
            )

        for group in groups.values():
            missing_domains = set(domain_suffixes) - set(group)
            if missing_domains:
                raise RuntimeError(
                    "已拆分规则集缺少业务域："
                    + "、".join(sorted(missing_domains))
                )
            domain_ids_by_source[group["SCHEDULING"]] = group
        return domain_ids_by_source

    for source in sources:
        source_id = source["id"]
        existing_configs = connection.execute(
            sa.select(
                rule_config.c.rule_code,
                rule_config.c.enforcement_type,
            ).where(rule_config.c.rule_set_id == source_id)
        ).all()
        has_selection_rules = any(
            row.rule_code in SELECTION_RULE_CODES for row in existing_configs
        )

        domain_ids = {
            "SCHEDULING": source_id,
            "SELECTION": _split_id(source_id, "SELECTION"),
            "ADJUSTMENT": _split_id(source_id, "ADJUSTMENT"),
            "APPROVAL": _split_id(source_id, "APPROVAL"),
        }
        domain_ids_by_source[source_id] = domain_ids

        source_code = source["rule_set_code"]
        connection.execute(
            rule_set.update()
            .where(rule_set.c.id == source_id)
            .values(
                rule_domain="SCHEDULING",
                rule_set_code=_domain_code(source_code, "SCHEDULING"),
                name=f"{source['name']}（排课）",
            )
        )

        for domain in ("SELECTION", "ADJUSTMENT", "APPROVAL"):
            inherits_publication = domain == "SELECTION" and has_selection_rules
            status = source["status"] if inherits_publication else "DRAFT"
            connection.execute(
                rule_set.insert().values(
                    id=domain_ids[domain],
                    rule_domain=domain,
                    rule_set_code=_domain_code(source_code, domain),
                    version_no=source["version_no"],
                    name=f"{source['name']}（"
                    + {
                        "SELECTION": "选课",
                        "ADJUSTMENT": "调整",
                        "APPROVAL": "审批",
                    }[domain]
                    + "）",
                    status=status,
                    published_at=(
                        source["published_at"]
                        if status == "PUBLISHED"
                        else None
                    ),
                    published_by=(
                        source["published_by"]
                        if status == "PUBLISHED"
                        else None
                    ),
                    created_by=source["created_by"],
                    updated_by=source["updated_by"],
                )
            )

        connection.execute(
            rule_config.update()
            .where(
                rule_config.c.rule_set_id == source_id,
                rule_config.c.rule_code.in_(SELECTION_RULE_CODES),
            )
            .values(rule_set_id=domain_ids["SELECTION"])
        )
        connection.execute(
            rule_config.update()
            .where(
                rule_config.c.rule_set_id == source_id,
                rule_config.c.enforcement_type == "RUNTIME",
            )
            .values(rule_set_id=domain_ids["ADJUSTMENT"])
        )
        connection.execute(
            rule_config.update()
            .where(
                rule_config.c.rule_set_id == source_id,
                rule_config.c.enforcement_type == "APPROVAL",
            )
            .values(rule_set_id=domain_ids["APPROVAL"])
        )

        _insert_domain_rules(
            connection,
            rule_config,
            rule_set_id=domain_ids["ADJUSTMENT"],
            domain="ADJUSTMENT",
        )
        _insert_domain_rules(
            connection,
            rule_config,
            rule_set_id=domain_ids["APPROVAL"],
            domain="APPROVAL",
        )

    return domain_ids_by_source


def _backfill_business_references(
    domain_ids_by_source: dict[UUID, dict[str, UUID]],
) -> None:
    connection = op.get_bind()
    for source_id, domain_ids in domain_ids_by_source.items():
        connection.execute(
            sa.text(
                """
                UPDATE application_request
                SET adjustment_rule_set_id = :adjustment_id,
                    approval_rule_set_id = :approval_id
                WHERE rule_set_id = :source_id
                """
            ),
            {
                "adjustment_id": domain_ids["ADJUSTMENT"],
                "approval_id": domain_ids["APPROVAL"],
                "source_id": source_id,
            },
        )

    selection_id = next(
        (
            domain_ids["SELECTION"]
            for domain_ids in domain_ids_by_source.values()
        ),
        None,
    )
    selection_window_count = connection.scalar(
        sa.text("SELECT count(*) FROM selection_window")
    )
    if selection_window_count and selection_id is None:
        raise RuntimeError(
            "selection_window 存在数据，但没有可用于回填的选课规则集"
        )
    if selection_id is not None:
        connection.execute(
            sa.text(
                """
                UPDATE selection_window
                SET selection_rule_set_id = :selection_id
                WHERE selection_rule_set_id IS NULL
                """
            ),
            {"selection_id": selection_id},
        )


def _create_domain_guard_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_rule_set_domain()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            referenced_rule_set_id uuid;
            actual_domain varchar(20);
        BEGIN
            referenced_rule_set_id :=
                (to_jsonb(NEW) ->> TG_ARGV[0])::uuid;
            IF referenced_rule_set_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT rule_domain
            INTO actual_domain
            FROM rule_set
            WHERE id = referenced_rule_set_id;

            IF actual_domain IS DISTINCT FROM TG_ARGV[1] THEN
                RAISE EXCEPTION
                    'rule set % belongs to domain %, expected %',
                    referenced_rule_set_id,
                    actual_domain,
                    TG_ARGV[1]
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    trigger_specs = (
        (
            "trg_schedule_job_rule_domain",
            "schedule_job",
            "scheduling_rule_set_id",
            "SCHEDULING",
        ),
        (
            "trg_schedule_version_rule_domain",
            "schedule_version",
            "scheduling_rule_set_id",
            "SCHEDULING",
        ),
        (
            "trg_selection_window_rule_domain",
            "selection_window",
            "selection_rule_set_id",
            "SELECTION",
        ),
        (
            "trg_application_adjustment_rule_domain",
            "application_request",
            "adjustment_rule_set_id",
            "ADJUSTMENT",
        ),
        (
            "trg_application_approval_rule_domain",
            "application_request",
            "approval_rule_set_id",
            "APPROVAL",
        ),
    )
    for trigger_name, table_name, column_name, domain in trigger_specs:
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE INSERT OR UPDATE OF {column_name}
            ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION enforce_rule_set_domain(
                '{column_name}',
                '{domain}'
            )
            """
        )


def _drop_domain_guard_triggers() -> None:
    for trigger_name, table_name in (
        ("trg_schedule_job_rule_domain", "schedule_job"),
        ("trg_schedule_version_rule_domain", "schedule_version"),
        ("trg_selection_window_rule_domain", "selection_window"),
        ("trg_application_adjustment_rule_domain", "application_request"),
        ("trg_application_approval_rule_domain", "application_request"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS enforce_rule_set_domain()")


def upgrade() -> None:
    op.drop_index("uq_rule_set_published", table_name="rule_set")
    op.drop_constraint("code_version", "rule_set", type_="unique")
    op.add_column(
        "rule_set",
        sa.Column("rule_domain", sa.String(length=20), nullable=True),
    )

    op.drop_index("ix_rule_config_type_enabled", table_name="rule_config")
    op.drop_constraint(
        "rule_type_allowed",
        "rule_config",
        type_="check",
    )
    op.alter_column(
        "rule_config",
        "rule_type",
        new_column_name="enforcement_type",
        existing_type=sa.String(length=20),
        existing_nullable=False,
    )

    op.drop_constraint(
        "fk_schedule_job_rule_set_id_rule_set",
        "schedule_job",
        type_="foreignkey",
    )
    op.alter_column(
        "schedule_job",
        "rule_set_id",
        new_column_name="scheduling_rule_set_id",
        existing_type=sa.Uuid(),
        existing_nullable=False,
    )
    op.create_foreign_key(
        "fk_schedule_job_scheduling_rule_set_id_rule_set",
        "schedule_job",
        "rule_set",
        ["scheduling_rule_set_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "fk_schedule_version_rule_set_id_rule_set",
        "schedule_version",
        type_="foreignkey",
    )
    op.alter_column(
        "schedule_version",
        "rule_set_id",
        new_column_name="scheduling_rule_set_id",
        existing_type=sa.Uuid(),
        existing_nullable=False,
    )
    op.create_foreign_key(
        "fk_schedule_version_scheduling_rule_set_id_rule_set",
        "schedule_version",
        "rule_set",
        ["scheduling_rule_set_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "selection_window",
        sa.Column("selection_rule_set_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "application_request",
        sa.Column("adjustment_rule_set_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "application_request",
        sa.Column("approval_rule_set_id", sa.Uuid(), nullable=True),
    )

    domain_ids_by_source = _split_existing_rule_sets()
    _backfill_business_references(domain_ids_by_source)

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE rule_config
            SET enforcement_type = CASE
                WHEN enforcement_type = 'HARD' THEN 'BLOCK'
                WHEN enforcement_type = 'SOFT' THEN 'SCORE'
                WHEN enforcement_type = 'APPROVAL' THEN 'ROUTE'
                WHEN enforcement_type = 'RUNTIME'
                     AND action_config ->> 'action' = 'SCORE' THEN 'SCORE'
                WHEN enforcement_type = 'RUNTIME'
                     AND action_config ->> 'action' = 'WARN' THEN 'WARN'
                WHEN enforcement_type = 'RUNTIME' THEN 'BLOCK'
                ELSE enforcement_type
            END
            """
        )
    )
    _, rule_config = _rule_tables()
    for domain_ids in domain_ids_by_source.values():
        connection.execute(
            rule_config.update()
            .where(
                rule_config.c.rule_set_id == domain_ids["SCHEDULING"],
                rule_config.c.rule_code.not_in(SCHEDULING_RULE_CODES),
            )
            .values(enabled=False)
        )
    connection.execute(
        sa.text(
            """
            UPDATE rule_config
            SET weight = 0
            WHERE enforcement_type <> 'SCORE'
            """
        )
    )

    op.alter_column(
        "rule_set",
        "rule_domain",
        existing_type=sa.String(length=20),
        nullable=False,
    )
    op.create_check_constraint(
        "domain_allowed",
        "rule_set",
        "rule_domain IN "
        "('SCHEDULING', 'SELECTION', 'ADJUSTMENT', 'APPROVAL')",
    )
    op.create_unique_constraint(
        "domain_code_version",
        "rule_set",
        ["rule_domain", "rule_set_code", "version_no"],
    )
    op.create_index(
        "uq_rule_set_published",
        "rule_set",
        ["rule_domain", "rule_set_code"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )

    op.create_check_constraint(
        "enforcement_type_allowed",
        "rule_config",
        "enforcement_type IN ('BLOCK', 'SCORE', 'WARN', 'ROUTE')",
    )
    op.create_check_constraint(
        "non_score_weight_zero",
        "rule_config",
        "enforcement_type = 'SCORE' OR weight = 0",
    )
    op.create_index(
        "ix_rule_config_enforcement_enabled",
        "rule_config",
        ["enforcement_type", "enabled"],
    )

    op.alter_column(
        "selection_window",
        "selection_rule_set_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_selection_window_selection_rule_set_id_rule_set",
        "selection_window",
        "rule_set",
        ["selection_rule_set_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_application_request_adjustment_rule_set_id_rule_set",
        "application_request",
        "rule_set",
        ["adjustment_rule_set_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_application_request_approval_rule_set_id_rule_set",
        "application_request",
        "rule_set",
        ["approval_rule_set_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "fk_application_request_rule_set_id_rule_set",
        "application_request",
        type_="foreignkey",
    )
    op.drop_column("application_request", "rule_set_id")
    _create_domain_guard_triggers()


def downgrade() -> None:
    _drop_domain_guard_triggers()
    op.add_column(
        "application_request",
        sa.Column("rule_set_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        """
        UPDATE application_request
        SET rule_set_id = COALESCE(
            adjustment_rule_set_id,
            approval_rule_set_id
        )
        """
    )
    op.create_foreign_key(
        "fk_application_request_rule_set_id_rule_set",
        "application_request",
        "rule_set",
        ["rule_set_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "fk_application_request_approval_rule_set_id_rule_set",
        "application_request",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_application_request_adjustment_rule_set_id_rule_set",
        "application_request",
        type_="foreignkey",
    )
    op.drop_column("application_request", "approval_rule_set_id")
    op.drop_column("application_request", "adjustment_rule_set_id")

    op.drop_constraint(
        "fk_selection_window_selection_rule_set_id_rule_set",
        "selection_window",
        type_="foreignkey",
    )
    op.drop_column("selection_window", "selection_rule_set_id")

    op.drop_constraint(
        "fk_schedule_version_scheduling_rule_set_id_rule_set",
        "schedule_version",
        type_="foreignkey",
    )
    op.alter_column(
        "schedule_version",
        "scheduling_rule_set_id",
        new_column_name="rule_set_id",
        existing_type=sa.Uuid(),
        existing_nullable=False,
    )
    op.create_foreign_key(
        "fk_schedule_version_rule_set_id_rule_set",
        "schedule_version",
        "rule_set",
        ["rule_set_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "fk_schedule_job_scheduling_rule_set_id_rule_set",
        "schedule_job",
        type_="foreignkey",
    )
    op.alter_column(
        "schedule_job",
        "scheduling_rule_set_id",
        new_column_name="rule_set_id",
        existing_type=sa.Uuid(),
        existing_nullable=False,
    )
    op.create_foreign_key(
        "fk_schedule_job_rule_set_id_rule_set",
        "schedule_job",
        "rule_set",
        ["rule_set_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_index(
        "ix_rule_config_enforcement_enabled",
        table_name="rule_config",
    )
    op.drop_constraint(
        "non_score_weight_zero",
        "rule_config",
        type_="check",
    )
    op.drop_constraint(
        "enforcement_type_allowed",
        "rule_config",
        type_="check",
    )
    op.execute(
        """
        UPDATE rule_config rc
        SET enforcement_type = CASE
            WHEN rs.rule_domain = 'SCHEDULING'
                 AND rc.enforcement_type = 'SCORE' THEN 'SOFT'
            WHEN rs.rule_domain = 'SCHEDULING' THEN 'HARD'
            WHEN rs.rule_domain = 'SELECTION' THEN 'HARD'
            WHEN rs.rule_domain = 'ADJUSTMENT' THEN 'RUNTIME'
            WHEN rs.rule_domain = 'APPROVAL' THEN 'APPROVAL'
            ELSE 'HARD'
        END
        FROM rule_set rs
        WHERE rc.rule_set_id = rs.id
        """
    )
    op.alter_column(
        "rule_config",
        "enforcement_type",
        new_column_name="rule_type",
        existing_type=sa.String(length=20),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "rule_type_allowed",
        "rule_config",
        "rule_type IN ('HARD', 'SOFT', 'RUNTIME', 'APPROVAL')",
    )
    op.create_index(
        "ix_rule_config_type_enabled",
        "rule_config",
        ["rule_type", "enabled"],
    )

    op.drop_index("uq_rule_set_published", table_name="rule_set")
    op.drop_constraint(
        "domain_code_version",
        "rule_set",
        type_="unique",
    )
    op.drop_constraint(
        "domain_allowed",
        "rule_set",
        type_="check",
    )
    op.drop_column("rule_set", "rule_domain")
    op.create_unique_constraint(
        "code_version",
        "rule_set",
        ["rule_set_code", "version_no"],
    )
    op.create_index(
        "uq_rule_set_published",
        "rule_set",
        ["rule_set_code"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
