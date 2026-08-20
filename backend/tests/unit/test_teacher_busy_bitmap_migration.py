from pathlib import Path
from runpy import run_path
from unittest.mock import patch

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "05ad2bf43756_add_teacher_busy_bitmap.py"
)
COMPLETION_MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "28cc400ecde2_add_student_course_completion.py"
)


def test_upgrade_reuses_teacher_availability_from_baseline() -> None:
    migration = run_path(str(MIGRATION_PATH))

    with (
        patch.object(migration["op"], "get_bind") as get_bind,
        patch.object(migration["op"], "f", side_effect=lambda name: name),
        patch.object(migration["op"], "create_table") as create_table,
        patch.object(migration["op"], "create_index") as create_index,
        patch.object(migration["sa"], "inspect") as inspect,
    ):
        inspect.return_value.has_table.return_value = True
        migration["upgrade"]()

    inspect.assert_called_once_with(get_bind.return_value)
    assert [call.args[0] for call in create_table.call_args_list] == [
        "teacher_busy_bitmap"
    ]
    create_index.assert_not_called()


def test_downgrade_preserves_baseline_teacher_availability() -> None:
    migration = run_path(str(MIGRATION_PATH))

    with (
        patch.object(migration["op"], "drop_table") as drop_table,
        patch.object(migration["op"], "drop_index") as drop_index,
    ):
        migration["downgrade"]()

    drop_table.assert_called_once_with("teacher_busy_bitmap")
    drop_index.assert_not_called()


def test_course_completion_migration_only_owns_its_new_table() -> None:
    migration = run_path(str(COMPLETION_MIGRATION_PATH))

    with (
        patch.object(migration["op"], "f", side_effect=lambda name: name),
        patch.object(migration["op"], "create_table") as create_table,
    ):
        migration["upgrade"]()

    assert [call.args[0] for call in create_table.call_args_list] == [
        "student_course_completion"
    ]

    with patch.object(migration["op"], "drop_table") as drop_table:
        migration["downgrade"]()

    drop_table.assert_called_once_with("student_course_completion")
