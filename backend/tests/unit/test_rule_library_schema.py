from sqlalchemy import CheckConstraint, UniqueConstraint

from app.data.scheduling_rule_defaults import (
    NEW_SCHEDULING_SOFT_CONDITIONS,
    NEW_SCHEDULING_SOFT_RULES,
    RULE_SET_SPECS,
    RULE_SPECS,
    SCHEDULING_SOFT_RULE_INITIALS,
)
from app.models import Base


def _constraint_sql(table_name: str) -> set[str]:
    table = Base.metadata.tables[table_name]
    return {
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_rule_set_has_independent_business_domain() -> None:
    table = Base.metadata.tables["rule_set"]

    assert "rule_domain" in table.c
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns)
        == ("rule_domain", "rule_set_code", "version_no")
        for constraint in table.constraints
    )
    assert any(
        "SCHEDULING" in sql
        and "SELECTION" in sql
        and "ADJUSTMENT" in sql
        and "APPROVAL" in sql
        for sql in _constraint_sql("rule_set")
    )


def test_rule_config_separates_domain_from_enforcement() -> None:
    table = Base.metadata.tables["rule_config"]

    assert "rule_type" not in table.c
    assert "enforcement_type" in table.c
    checks = _constraint_sql("rule_config")
    assert any(
        all(value in sql for value in ("BLOCK", "SCORE", "WARN", "ROUTE"))
        for sql in checks
    )
    assert any(
        "enforcement_type = 'SCORE' OR weight = 0" in sql
        for sql in checks
    )


def test_business_tables_reference_explicit_rule_domains() -> None:
    assert "scheduling_rule_set_id" in Base.metadata.tables["schedule_job"].c
    assert (
        "scheduling_rule_set_id"
        in Base.metadata.tables["schedule_version"].c
    )
    assert (
        "selection_rule_set_id"
        in Base.metadata.tables["selection_window"].c
    )
    application_columns = Base.metadata.tables["application_request"].c
    assert "rule_set_id" not in application_columns
    assert "adjustment_rule_set_id" in application_columns
    assert "approval_rule_set_id" in application_columns
    assert "rule_set_id" in Base.metadata.tables["operation_log"].c


def test_demo_rule_sets_are_split_without_auto_publishing_drafts() -> None:
    assert set(RULE_SET_SPECS) == {
        "SCHEDULING",
        "SELECTION",
        "ADJUSTMENT",
        "APPROVAL",
    }
    assert RULE_SET_SPECS["SCHEDULING"]["status"] == "PUBLISHED"
    assert RULE_SET_SPECS["SELECTION"]["status"] == "PUBLISHED"
    assert RULE_SET_SPECS["ADJUSTMENT"]["status"] == "DRAFT"
    assert RULE_SET_SPECS["APPROVAL"]["status"] == "DRAFT"


def test_non_score_demo_rules_have_zero_effective_weight() -> None:
    for domain, rules in RULE_SPECS.items():
        for _, _, enforcement, _, _ in rules:
            effective_weight = (
                1
                if domain == "SCHEDULING" and enforcement == "SCORE"
                else 0
            )
            if enforcement != "SCORE":
                assert effective_weight == 0


def test_scheduling_v2_contains_requested_soft_rules() -> None:
    rules = {
        code: (name, enforcement, priority, options)
        for code, name, enforcement, priority, options
        in NEW_SCHEDULING_SOFT_RULES
    }

    assert set(rules) == {
        "TEACHER_COMPACTNESS",
        "TEACHER_CONSECUTIVE_LOAD",
        "LAB_UTILIZATION_BALANCE",
        "STUDENT_AVAILABILITY_COVERAGE",
        "WEEKEND_PENALTY",
        "TEACHER_PREFERRED_TIME",
        "TEACHER_TARGET_LOAD_SCORE",
        "COURSE_EARLY_WEEK_PREFERENCE",
        "PROJECT_EARLY_WEEK_PREFERENCE",
    }
    assert all(item[1] == "SCORE" for item in rules.values())
    assert NEW_SCHEDULING_SOFT_CONDITIONS["WEEKEND_PENALTY"] == {
        "weekend_days": [1, 7]
    }
    assert NEW_SCHEDULING_SOFT_CONDITIONS[
        "TEACHER_PREFERRED_TIME"
    ] == {"availability_type": "PREFERRED"}
    assert NEW_SCHEDULING_SOFT_CONDITIONS[
        "TEACHER_TARGET_LOAD_SCORE"
    ] == {
        "configuration_status": "RUNTIME",
        "target_source": "SCHEDULE_JOB_INPUT",
    }
    assert NEW_SCHEDULING_SOFT_CONDITIONS[
        "COURSE_EARLY_WEEK_PREFERENCE"
    ] == {
        "configuration_status": "RUNTIME",
        "target_source": "SCHEDULE_JOB_INPUT",
        "parameter": "preferred_end_week",
    }
    assert NEW_SCHEDULING_SOFT_CONDITIONS[
        "PROJECT_EARLY_WEEK_PREFERENCE"
    ] == {
        "configuration_status": "RUNTIME",
        "target_source": "SCHEDULE_JOB_INPUT",
        "parameter": "preferred_end_week",
    }


def test_scheduling_v2_uses_balanced_initial_weights_and_priorities() -> None:
    assert SCHEDULING_SOFT_RULE_INITIALS == {
        "STUDENT_AVAILABILITY_COVERAGE": {
            "weight": 25,
            "priority": 90,
        },
        "TEACHER_BALANCE": {"weight": 15, "priority": 80},
        "EVENING_PENALTY": {"weight": 12, "priority": 70},
        "WEEKEND_PENALTY": {"weight": 10, "priority": 70},
        "TEACHER_COMPACTNESS": {"weight": 10, "priority": 60},
        "TEACHER_CONSECUTIVE_LOAD": {
            "weight": 10,
            "priority": 60,
        },
        "TEACHER_PREFERRED_TIME": {"weight": 10, "priority": 60},
        "LAB_UTILIZATION_BALANCE": {"weight": 8, "priority": 50},
        "TEACHER_TARGET_LOAD_SCORE": {
            "weight": 0,
            "priority": 40,
        },
        "COURSE_EARLY_WEEK_PREFERENCE": {
            "weight": 0,
            "priority": 40,
        },
        "PROJECT_EARLY_WEEK_PREFERENCE": {
            "weight": 0,
            "priority": 40,
        },
    }
    assert sum(
        item["weight"]
        for item in SCHEDULING_SOFT_RULE_INITIALS.values()
    ) == 100


def test_teacher_timetable_is_a_published_schedule_index() -> None:
    assert "teacher_term_load_preference" not in Base.metadata.tables
    table = Base.metadata.tables["teacher_timetable_entry"]
    assert {
        "teacher_id",
        "term_id",
        "schedule_version_id",
        "experiment_session_id",
    }.issubset(table.c.keys())
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns)
        == ("experiment_session_id",)
        for constraint in table.constraints
    )


def test_course_time_availability_has_consistent_counts() -> None:
    table = Base.metadata.tables["course_time_availability"]
    assert {
        "course_id",
        "term_id",
        "week_no",
        "day_of_week",
        "slot_no",
        "target_student_count",
        "known_student_count",
        "free_student_count",
        "busy_student_count",
        "unknown_student_count",
        "calculation_batch_id",
        "source_hash",
    }.issubset(table.c.keys())
    assert any(
        "known_student_count = "
        "free_student_count + busy_student_count" in sql
        for sql in _constraint_sql("course_time_availability")
    )
    assert any(
        "target_student_count = "
        "known_student_count + unknown_student_count" in sql
        for sql in _constraint_sql("course_time_availability")
    )
