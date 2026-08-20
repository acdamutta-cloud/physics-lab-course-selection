from pathlib import Path
from runpy import run_path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "d6b8f2a4c901_split_rule_library_domains.py"
)


def test_clean_install_baseline_covers_required_initial_rules() -> None:
    migration = run_path(str(MIGRATION_PATH))
    rules = migration["BASELINE_RULES"]
    codes = {code for code, _, _ in rules}

    assert migration["revision"] == "d6b8f2a4c901"
    assert migration["down_revision"] == "c4a7f0912d3e"
    assert migration["SCHEDULING_RULE_CODES"] <= codes
    assert migration["SELECTION_RULE_CODES"] <= codes
    assert len(codes) == len(rules)


def test_clean_install_baseline_uses_supported_legacy_rule_types() -> None:
    migration = run_path(str(MIGRATION_PATH))

    assert {
        rule_type for _, _, rule_type in migration["BASELINE_RULES"]
    } <= {"HARD", "SOFT"}
