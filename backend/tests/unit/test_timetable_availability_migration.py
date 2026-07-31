from pathlib import Path
from runpy import run_path

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "b7c1d4e8f920_add_timetable_and_course_availability.py"
)


def test_timetable_availability_migration_follows_current_head() -> None:
    migration = run_path(str(MIGRATION_PATH))

    assert migration["revision"] == "b7c1d4e8f920"
    assert migration["down_revision"] == "a24e8c7f5b19"
    assert migration["OLD_RULE_CODE"] == "TEACHER_TERM_REDUCED_LOAD"
    assert migration["NEW_RULE_CODE"] == "TEACHER_TARGET_LOAD_SCORE"
