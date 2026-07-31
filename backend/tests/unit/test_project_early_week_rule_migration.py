from pathlib import Path
from runpy import run_path

from scripts.seed_demo_data import SCHEDULING_SOFT_RULE_INITIALS

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "84bd2c5e7f10_add_project_early_week_soft_rule.py"
)


def test_project_early_week_rule_migration_matches_seed_configuration() -> None:
    migration = run_path(str(MIGRATION_PATH))

    assert migration["revision"] == "84bd2c5e7f10"
    assert migration["down_revision"] == "3e8f1a6c2d74"
    assert migration["RULE_CODE"] == "PROJECT_EARLY_WEEK_PREFERENCE"
    assert SCHEDULING_SOFT_RULE_INITIALS[migration["RULE_CODE"]] == {
        "weight": 0,
        "priority": 40,
    }
