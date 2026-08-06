from decimal import Decimal

import pytest

from app.models import CourseTimeAvailability
from app.services.course_availability_service import (
    bitmap_slot_is_busy,
    estimate_block_availability,
)


def test_bitmap_slot_is_busy_uses_student_bitmap_mapping() -> None:
    bitmap = bytes([0b00100000])

    assert (
        bitmap_slot_is_busy(
            bitmap,
            start_week=1,
            days_per_week=1,
            slots_per_day=4,
            week_no=1,
            day_of_week=1,
            slot_no=3,
        )
        is True
    )
    assert (
        bitmap_slot_is_busy(
            bitmap,
            start_week=1,
            days_per_week=1,
            slots_per_day=4,
            week_no=1,
            day_of_week=1,
            slot_no=2,
        )
        is False
    )


def test_bitmap_slot_is_busy_returns_none_outside_bitmap() -> None:
    assert (
        bitmap_slot_is_busy(
            b"",
            start_week=1,
            days_per_week=7,
            slots_per_day=12,
            week_no=1,
            day_of_week=1,
            slot_no=1,
        )
        is None
    )


def test_block_availability_uses_minimum_single_slot_value() -> None:
    rows = [
        CourseTimeAvailability(
            free_student_count=80,
            free_ratio=Decimal("0.800000"),
            data_coverage_ratio=Decimal("1.000000"),
        ),
        CourseTimeAvailability(
            free_student_count=60,
            free_ratio=Decimal("0.600000"),
            data_coverage_ratio=Decimal("0.900000"),
        ),
    ]

    result = estimate_block_availability(rows)

    assert result.free_student_count == 60
    assert result.free_ratio == Decimal("0.600000")
    assert result.data_coverage_ratio == Decimal("0.900000")
    assert result.assessment == "APPROXIMATE"


def test_block_availability_rejects_empty_period() -> None:
    with pytest.raises(ValueError, match="至少需要"):
        estimate_block_availability([])
