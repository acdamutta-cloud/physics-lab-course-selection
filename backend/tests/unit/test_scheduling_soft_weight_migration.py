from decimal import Decimal
from pathlib import Path
from runpy import run_path

from app.data.scheduling_rule_defaults import SCHEDULING_SOFT_RULE_INITIALS

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "a24e8c7f5b19_initialize_scheduling_soft_weights.py"
)


def test_soft_weight_migration_matches_demo_initial_values() -> None:
    migration = run_path(str(MIGRATION_PATH))
    migration_values = migration["INITIAL_VALUES"]
    seed_values = {
        code: (config["weight"], config["priority"], True)
        for code, config in SCHEDULING_SOFT_RULE_INITIALS.items()
        if code
        not in {
            "COURSE_EARLY_WEEK_PREFERENCE",
            "PROJECT_EARLY_WEEK_PREFERENCE",
        }
    }
    seed_values["TEACHER_TERM_REDUCED_LOAD"] = seed_values.pop(
        "TEACHER_TARGET_LOAD_SCORE"
    )

    assert migration["revision"] == "a24e8c7f5b19"
    assert migration["down_revision"] == "91f0c3d8a742"
    assert migration_values == seed_values
    assert sum(value[0] for value in migration_values.values()) == Decimal(
        100
    )


def test_soft_weight_migration_has_complete_downgrade_values() -> None:
    migration = run_path(str(MIGRATION_PATH))

    assert set(migration["PREVIOUS_VALUES"]) == set(
        migration["INITIAL_VALUES"]
    )
