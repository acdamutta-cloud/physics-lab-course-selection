"""add teacher timetable and course availability statistics

Revision ID: b7c1d4e8f920
Revises: a24e8c7f5b19
Create Date: 2026-07-31 14:00:00
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection
from sqlalchemy.sql import TableClause

from alembic import op

revision: str = "b7c1d4e8f920"
down_revision: str | Sequence[str] | None = "a24e8c7f5b19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_RULE_CODE = "TEACHER_TERM_REDUCED_LOAD"
NEW_RULE_CODE = "TEACHER_TARGET_LOAD_SCORE"


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
        sa.column("rule_set_id", sa.Uuid()),
        sa.column("rule_code", sa.String()),
        sa.column("rule_name", sa.String()),
        sa.column("condition_config", postgresql.JSONB()),
        sa.column("action_config", postgresql.JSONB()),
        sa.column("description", sa.Text()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    return rule_set, rule_config


def _find_scheduling_v2(
    connection: Connection,
    rule_set: TableClause,
) -> UUID:
    ids = connection.execute(
        sa.select(rule_set.c.id).where(
            rule_set.c.rule_domain == "SCHEDULING",
            rule_set.c.version_no == 2,
            rule_set.c.status == "DRAFT",
        )
    ).scalars().all()
    if len(ids) != 1:
        raise RuntimeError(
            "必须存在且只能存在一个 SCHEDULING V2 DRAFT 规则集"
        )
    return ids[0]


def _rename_runtime_load_rule(
    *,
    old_code: str,
    new_code: str,
    new_name: str,
    condition_config: dict[str, str],
    action_config: dict[str, str],
    description: str,
) -> None:
    connection = op.get_bind()
    rule_set, rule_config = _rule_tables()
    rule_set_id = _find_scheduling_v2(connection, rule_set)
    result = connection.execute(
        rule_config.update()
        .where(
            rule_config.c.rule_set_id == rule_set_id,
            rule_config.c.rule_code == old_code,
        )
        .values(
            rule_code=new_code,
            rule_name=new_name,
            condition_config=condition_config,
            action_config=action_config,
            description=description,
            updated_at=sa.func.now(),
        )
    )
    if result.rowcount != 1:
        raise RuntimeError(
            f"SCHEDULING V2 中必须存在且只能更新规则 {old_code}"
        )


def _assert_preference_table_empty() -> None:
    connection = op.get_bind()
    count = connection.scalar(
        sa.text("SELECT COUNT(*) FROM teacher_term_load_preference")
    )
    if int(count or 0) != 0:
        raise RuntimeError(
            "teacher_term_load_preference 中存在数据；"
            "为避免静默丢失，请先导出或人工处理后再迁移"
        )


def upgrade() -> None:
    _assert_preference_table_empty()

    op.create_table(
        "teacher_timetable_entry",
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("term_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_version_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_session_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teacher.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["term_id"],
            ["academic_term.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_version_id"],
            ["schedule_version.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_session_id"],
            ["experiment_session.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_session_id",
            name="timetable_experiment_session",
        ),
        sa.UniqueConstraint(
            "teacher_id",
            "experiment_session_id",
            name="teacher_experiment_session",
        ),
    )
    op.create_index(
        "ix_teacher_timetable_entry_teacher_term",
        "teacher_timetable_entry",
        ["teacher_id", "term_id"],
    )
    op.create_index(
        "ix_teacher_timetable_entry_schedule_version",
        "teacher_timetable_entry",
        ["schedule_version_id"],
    )

    op.execute(
        """
        CREATE FUNCTION validate_teacher_timetable_entry()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            actual_teacher_id uuid;
            actual_version_id uuid;
            actual_term_id uuid;
            actual_version_status varchar;
        BEGIN
            SELECT
                es.teacher_id,
                es.schedule_version_id,
                sv.term_id,
                sv.status
            INTO
                actual_teacher_id,
                actual_version_id,
                actual_term_id,
                actual_version_status
            FROM experiment_session es
            JOIN schedule_version sv
              ON sv.id = es.schedule_version_id
            WHERE es.id = NEW.experiment_session_id;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'experiment session % does not exist',
                    NEW.experiment_session_id;
            END IF;
            IF NEW.teacher_id <> actual_teacher_id THEN
                RAISE EXCEPTION
                    'teacher timetable teacher does not match session';
            END IF;
            IF NEW.schedule_version_id <> actual_version_id THEN
                RAISE EXCEPTION
                    'teacher timetable version does not match session';
            END IF;
            IF NEW.term_id <> actual_term_id THEN
                RAISE EXCEPTION
                    'teacher timetable term does not match version';
            END IF;
            IF actual_version_status <> 'PUBLISHED' THEN
                RAISE EXCEPTION
                    'teacher timetable requires a published schedule version';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_validate_teacher_timetable_entry
        BEFORE INSERT OR UPDATE ON teacher_timetable_entry
        FOR EACH ROW
        EXECUTE FUNCTION validate_teacher_timetable_entry()
        """
    )

    op.create_table(
        "course_time_availability",
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("term_id", sa.Uuid(), nullable=False),
        sa.Column("week_no", sa.SmallInteger(), nullable=False),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
        sa.Column("slot_no", sa.SmallInteger(), nullable=False),
        sa.Column("target_student_count", sa.Integer(), nullable=False),
        sa.Column("known_student_count", sa.Integer(), nullable=False),
        sa.Column("free_student_count", sa.Integer(), nullable=False),
        sa.Column("busy_student_count", sa.Integer(), nullable=False),
        sa.Column("unknown_student_count", sa.Integer(), nullable=False),
        sa.Column("free_ratio", sa.Numeric(8, 6), nullable=False),
        sa.Column(
            "data_coverage_ratio",
            sa.Numeric(8, 6),
            nullable=False,
        ),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("calculation_version", sa.Integer(), nullable=False),
        sa.Column("calculation_batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_hash", sa.String(128), nullable=False),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
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
        sa.CheckConstraint("week_no >= 1", name="week_positive"),
        sa.CheckConstraint(
            "day_of_week BETWEEN 1 AND 7",
            name="day_valid",
        ),
        sa.CheckConstraint("slot_no >= 1", name="slot_positive"),
        sa.CheckConstraint(
            "target_student_count >= 0",
            name="target_count_nonnegative",
        ),
        sa.CheckConstraint(
            "known_student_count >= 0",
            name="known_count_nonnegative",
        ),
        sa.CheckConstraint(
            "free_student_count >= 0",
            name="free_count_nonnegative",
        ),
        sa.CheckConstraint(
            "busy_student_count >= 0",
            name="busy_count_nonnegative",
        ),
        sa.CheckConstraint(
            "unknown_student_count >= 0",
            name="unknown_count_nonnegative",
        ),
        sa.CheckConstraint(
            "known_student_count = "
            "free_student_count + busy_student_count",
            name="known_count_sum",
        ),
        sa.CheckConstraint(
            "target_student_count = "
            "known_student_count + unknown_student_count",
            name="target_count_sum",
        ),
        sa.CheckConstraint(
            "free_ratio BETWEEN 0 AND 1",
            name="free_ratio_valid",
        ),
        sa.CheckConstraint(
            "data_coverage_ratio BETWEEN 0 AND 1",
            name="coverage_ratio_valid",
        ),
        sa.CheckConstraint(
            "mapping_version >= 1",
            name="mapping_version_positive",
        ),
        sa.CheckConstraint(
            "calculation_version >= 1",
            name="calculation_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["experiment_course.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["term_id"],
            ["academic_term.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "course_id",
            "term_id",
            "week_no",
            "day_of_week",
            "slot_no",
            name="course_term_time_slot",
        ),
    )
    op.create_index(
        "ix_course_time_availability_course_time",
        "course_time_availability",
        ["course_id", "term_id", "week_no", "day_of_week"],
    )
    op.create_index(
        "ix_course_time_availability_course_free",
        "course_time_availability",
        ["course_id", "term_id", sa.text("free_student_count DESC")],
    )
    op.create_index(
        "ix_course_time_availability_batch",
        "course_time_availability",
        ["calculation_batch_id"],
    )

    _rename_runtime_load_rule(
        old_code=OLD_RULE_CODE,
        new_code=NEW_RULE_CODE,
        new_name="指定教师课时负荷评分",
        condition_config={
            "configuration_status": "RUNTIME",
            "target_source": "SCHEDULE_JOB_INPUT",
        },
        action_config={
            "action": "SCORE",
            "metric": "target_teacher_assigned_session_count",
        },
        description="对本次排课输入指定教师的实验场次数进行评分",
    )

    op.drop_index(
        "ix_teacher_term_load_preference_lookup",
        table_name="teacher_term_load_preference",
    )
    op.drop_table("teacher_term_load_preference")


def downgrade() -> None:
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
        sa.Column("enabled", sa.Boolean(), nullable=False),
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
            name="max_sessions_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teacher.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["term_id"],
            ["academic_term.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
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

    _rename_runtime_load_rule(
        old_code=NEW_RULE_CODE,
        new_code=OLD_RULE_CODE,
        new_name="指定教师本学期尽量少排",
        condition_config={
            "configuration_status": "PENDING",
            "preference_source": "teacher_term_load_preference",
        },
        action_config={
            "action": "SCORE",
            "metric": "teacher_sessions_over_preferred_max",
        },
        description="指定教师本学期尽量少排；待管理员配置权重与参数",
    )

    op.drop_index(
        "ix_course_time_availability_batch",
        table_name="course_time_availability",
    )
    op.drop_index(
        "ix_course_time_availability_course_free",
        table_name="course_time_availability",
    )
    op.drop_index(
        "ix_course_time_availability_course_time",
        table_name="course_time_availability",
    )
    op.drop_table("course_time_availability")

    op.execute(
        "DROP TRIGGER trg_validate_teacher_timetable_entry "
        "ON teacher_timetable_entry"
    )
    op.execute("DROP FUNCTION validate_teacher_timetable_entry()")
    op.drop_index(
        "ix_teacher_timetable_entry_schedule_version",
        table_name="teacher_timetable_entry",
    )
    op.drop_index(
        "ix_teacher_timetable_entry_teacher_term",
        table_name="teacher_timetable_entry",
    )
    op.drop_table("teacher_timetable_entry")
