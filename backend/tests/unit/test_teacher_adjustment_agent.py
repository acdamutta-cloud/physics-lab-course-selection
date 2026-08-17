from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.agents.registry import resolve_graph
from app.schemas.student_consultation import SelectionPreferences, WeekRangePreference
from app.schemas.teacher_adjustment import ResourceIssueCreateRequest, TimeTarget
from app.services.schedule_constraint_validation_service import time_overlaps
from app.services.teacher_adjustment_service import (
    _inside_week_range,
    _preference_score,
)


def test_main_registry_routes_teacher_adjustment_without_llm() -> None:
    registration = resolve_graph("TEACHER_ADJUSTMENT", "TEACHER")
    assert registration.graph_name == "teacher_adjustment"
    assert registration.graph_version == "v1"


def test_main_registry_blocks_cross_role_access() -> None:
    with pytest.raises(PermissionError):
        resolve_graph("SYSTEM_SCHEDULING", "STUDENT")


def test_teacher_preference_uses_sunday_first_day_names() -> None:
    preferences = SelectionPreferences(
        preferred_days=["周日"], preferred_periods=["MORNING"]
    )
    score, reasons, warnings = _preference_score(
        preferences,
        TimeTarget(week_no=5, day_of_week=1, start_slot=1, end_slot=4),
    )
    assert score == 70
    assert "符合周日偏好" in reasons
    assert not warnings


def test_teacher_week_range_is_a_hard_filter() -> None:
    preferences = SelectionPreferences(
        week_range=WeekRangePreference(start_week=8, start_inclusive=False, end_week=10)
    )
    assert not _inside_week_range(preferences, 8)
    assert _inside_week_range(preferences, 9)
    assert _inside_week_range(preferences, 10)
    assert not _inside_week_range(preferences, 11)


def test_time_overlap_uses_same_week_and_sunday_first_day_number() -> None:
    assert time_overlaps(
        week_a=7,
        day_a=2,
        start_a=5,
        end_a=8,
        week_b=7,
        day_b=2,
        start_b=6,
        end_b=6,
    )
    assert not time_overlaps(
        week_a=7,
        day_a=2,
        start_a=5,
        end_a=8,
        week_b=7,
        day_b=3,
        start_b=5,
        end_b=8,
    )


def test_resource_issue_rejects_invalid_repair_window() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        ResourceIssueCreateRequest(
            laboratory_id="00000000-0000-0000-0000-000000000001",
            inventory_id="00000000-0000-0000-0000-000000000002",
            affected_quantity=1,
            impact_start=now,
            impact_end=now - timedelta(hours=1),
            description="仪器故障",
        )
