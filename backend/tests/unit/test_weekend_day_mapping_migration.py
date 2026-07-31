from pathlib import Path
from runpy import run_path

from scripts.seed_demo_data import NEW_SCHEDULING_SOFT_CONDITIONS

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "f2c7a91e4b63_fix_weekend_day_mapping.py"
)


def test_weekend_mapping_migration_follows_current_head() -> None:
    migration = run_path(str(MIGRATION_PATH))

    assert migration["revision"] == "f2c7a91e4b63"
    assert migration["down_revision"] == "b7c1d4e8f920"
    assert NEW_SCHEDULING_SOFT_CONDITIONS["WEEKEND_PENALTY"] == {
        "weekend_days": [1, 7]
    }
