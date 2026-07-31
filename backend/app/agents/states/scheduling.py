from __future__ import annotations

from typing import Any, TypedDict


class SchedulingState(TypedDict, total=False):
    preference_text: str
    base_weights: dict[str, float]
    applicability: dict[str, bool]
    teacher_directory: dict[str, str]
    course_directory: dict[str, str]
    project_directory: dict[str, str]
    total_weeks: int
    rule_priorities: dict[str, int]
    max_candidate_count: int

    parsed_preferences: list[dict[str, Any]]
    comparison_weights: dict[str, float]
    profiles: list[dict[str, Any]]
    warnings: list[str]
    validation_errors: list[str]
