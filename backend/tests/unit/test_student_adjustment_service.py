from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.student_adjustment import AdjustmentAgentPlan
from app.services.student_adjustment_service import (
    session_calendar_date,
    session_has_started,
)


@pytest.mark.parametrize(
    ("day_of_week", "expected"),
    [
        (1, date(2026, 3, 1)),
        (2, date(2026, 3, 2)),
        (3, date(2026, 3, 3)),
        (4, date(2026, 3, 4)),
        (5, date(2026, 3, 5)),
        (6, date(2026, 3, 6)),
        (7, date(2026, 3, 7)),
    ],
)
def test_session_calendar_date_is_sunday_first(day_of_week: int, expected: date):
    term = SimpleNamespace(start_date=date(2026, 3, 2))  # Monday
    item = SimpleNamespace(week_no=1, day_of_week=day_of_week)

    assert session_calendar_date(term, item) == expected


def test_session_started_includes_same_calendar_date():
    term = SimpleNamespace(start_date=date(2026, 3, 2))
    item = SimpleNamespace(week_no=2, day_of_week=2)

    assert session_has_started(term, item, today=date(2026, 3, 9)) is True
    assert session_has_started(term, item, today=date(2026, 3, 8)) is False


def test_adjustment_plan_rejects_mismatched_recommendation_intent():
    with pytest.raises(ValidationError):
        AdjustmentAgentPlan(
            intent="RECOMMEND_MAKEUP",
            request_type="RESCHEDULE",
        )


def test_student_adjustment_graph_does_not_import_system_scheduling_graph():
    source = (
        Path(__file__).parents[2] / "app" / "agents" / "graphs" / "adjustment_graph.py"
    ).read_text(encoding="utf-8")

    assert "scheduling_graph" not in source
    assert "SchedulingState" not in source
    assert "validation_agent" not in source
