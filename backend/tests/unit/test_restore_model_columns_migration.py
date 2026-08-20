from pathlib import Path
from runpy import run_path

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "f6a8c0d2e4b1_restore_model_columns.py"
)


def test_restore_migration_covers_every_removed_model_column() -> None:
    migration = run_path(str(MIGRATION_PATH))

    assert migration["revision"] == "f6a8c0d2e4b1"
    assert migration["down_revision"] == "b3c5a7d9e1f0"
    assert len(migration["RESTORED_COLUMN_KEYS"]) == 23
    assert ("campus", "address") in migration["RESTORED_COLUMN_KEYS"]
    assert ("rule_config", "action_config") in migration["RESTORED_COLUMN_KEYS"]
