from pathlib import Path
from runpy import run_path

from app.data.scheduling_rule_defaults import SCHEDULING_SOFT_RULE_INITIALS

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "3e8f1a6c2d74_add_course_early_week_soft_rule.py"
)


def test_course_early_week_rule_migration_matches_seed_configuration() -> None:
    migration = run_path(str(MIGRATION_PATH))

    assert migration["revision"] == "3e8f1a6c2d74"
    assert migration["down_revision"] == "f2c7a91e4b63"
    assert migration["RULE_CODE"] == "COURSE_EARLY_WEEK_PREFERENCE"
    assert SCHEDULING_SOFT_RULE_INITIALS[migration["RULE_CODE"]] == {
        "weight": 0,
        "priority": 40,
    }
