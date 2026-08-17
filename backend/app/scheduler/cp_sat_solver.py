"""确定性候选课表求解器。

V1 先采用带硬约束检查的贪心构造器，接口保持为独立求解器，后续可在
不改变 API 和智能体图的前提下替换为完整 CP-SAT 模型。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from math import sqrt
from uuid import UUID


@dataclass(frozen=True)
class SolverLabOption:
    laboratory_id: UUID
    laboratory_code: str
    laboratory_name: str
    effective_capacity: int


@dataclass(frozen=True)
class SolverTeacherOption:
    teacher_id: UUID
    teacher_name: str


@dataclass(frozen=True)
class SolverDemand:
    task_id: UUID
    course_id: UUID
    course_name: str
    project_id: UUID
    project_name: str
    week_start: int
    week_end: int
    required_slots: int
    required_capacity: int
    occurrence_count: int
    teachers: tuple[SolverTeacherOption, ...]
    laboratories: tuple[SolverLabOption, ...]


@dataclass(frozen=True)
class CandidateSessionDraft:
    task_id: UUID
    course_id: UUID
    project_id: UUID
    course_name: str
    project_name: str
    week_no: int
    day_of_week: int
    start_slot: int
    end_slot: int
    teacher_id: UUID
    teacher_name: str
    laboratory_id: UUID
    laboratory_code: str
    laboratory_name: str
    capacity: int
    availability_penalty: float
    data_coverage_ratio: float


@dataclass(frozen=True)
class SolverResult:
    sessions: tuple[CandidateSessionDraft, ...]
    soft_score: float
    metrics: dict[str, float]
    hard_constraint_passed: bool
    errors: tuple[str, ...]


def _coefficient_of_variation(values: Sequence[int]) -> float:
    if not values or sum(values) == 0:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return min(1.0, sqrt(variance) / mean) if mean else 0.0


def is_weekend_day(day_of_week: int, days_per_week: int) -> bool:
    """周末是当前周时间网格的第一天和最后一天。"""

    return day_of_week in (1, days_per_week)


def _aggregate_metrics(
    sessions: Sequence[CandidateSessionDraft],
    target_teacher_ids: set[UUID],
    days_per_week: int,
    course_early_week_preferences: Mapping[UUID, int],
    project_early_week_preferences: Mapping[UUID, int],
) -> dict[str, float]:
    if not sessions:
        return {
            "STUDENT_AVAILABILITY_COVERAGE": 1.0,
            "TEACHER_BALANCE": 0.0,
            "EVENING_PENALTY": 0.0,
            "WEEKEND_PENALTY": 0.0,
            "TEACHER_COMPACTNESS": 0.0,
            "TEACHER_CONSECUTIVE_LOAD": 0.0,
            "TEACHER_PREFERRED_TIME": 0.0,
            "LAB_UTILIZATION_BALANCE": 0.0,
            "TEACHER_TARGET_LOAD_SCORE": 0.0,
            "COURSE_EARLY_WEEK_PREFERENCE": 0.0,
            "PROJECT_EARLY_WEEK_PREFERENCE": 0.0,
        }

    teacher_load: dict[UUID, int] = {}
    lab_load: dict[UUID, int] = {}
    teacher_days: dict[tuple[UUID, int, int], list[int]] = {}
    for item in sessions:
        teacher_load[item.teacher_id] = teacher_load.get(item.teacher_id, 0) + 1
        lab_load[item.laboratory_id] = lab_load.get(item.laboratory_id, 0) + 1
        teacher_days.setdefault(
            (item.teacher_id, item.week_no, item.day_of_week),
            [],
        ).append(item.start_slot)

    compactness_values: list[float] = []
    consecutive_overload = 0
    for starts in teacher_days.values():
        starts.sort()
        if len(starts) == 1:
            compactness_values.append(1.0)
        else:
            gaps = [
                max(0, right - left - 4)
                for left, right in pairwise(starts)
            ]
            compactness_values.append(min(1.0, sum(gaps) / (8 * len(gaps))))
        consecutive_overload += max(0, len(starts) - 2)

    count = len(sessions)
    target_course_sessions = [
        item
        for item in sessions
        if item.course_id in course_early_week_preferences
    ]
    target_project_sessions = [
        item
        for item in sessions
        if item.project_id in project_early_week_preferences
    ]
    return {
        "STUDENT_AVAILABILITY_COVERAGE": sum(
            item.availability_penalty for item in sessions
        )
        / count,
        "TEACHER_BALANCE": _coefficient_of_variation(
            list(teacher_load.values())
        ),
        "EVENING_PENALTY": sum(item.end_slot > 8 for item in sessions) / count,
        "WEEKEND_PENALTY": sum(
            is_weekend_day(item.day_of_week, days_per_week)
            for item in sessions
        )
        / count,
        "TEACHER_COMPACTNESS": (
            sum(compactness_values) / len(compactness_values)
            if compactness_values
            else 0.0
        ),
        "TEACHER_CONSECUTIVE_LOAD": min(
            1.0,
            consecutive_overload / count,
        ),
        "TEACHER_PREFERRED_TIME": 0.0,
        "LAB_UTILIZATION_BALANCE": _coefficient_of_variation(
            list(lab_load.values())
        ),
        "TEACHER_TARGET_LOAD_SCORE": sum(
            item.teacher_id in target_teacher_ids for item in sessions
        )
        / count,
        "COURSE_EARLY_WEEK_PREFERENCE": (
            sum(
                item.week_no
                > course_early_week_preferences[item.course_id]
                for item in target_course_sessions
            )
            / len(target_course_sessions)
            if target_course_sessions
            else 0.0
        ),
        "PROJECT_EARLY_WEEK_PREFERENCE": (
            sum(
                item.week_no
                > project_early_week_preferences[item.project_id]
                for item in target_project_sessions
            )
            / len(target_project_sessions)
            if target_project_sessions
            else 0.0
        ),
    }


def solve_candidate(
    *,
    demands: Sequence[SolverDemand],
    days_per_week: int,
    slots_per_day: int,
    solver_weights: Mapping[str, float],
    availability: Mapping[
        tuple[UUID, int, int, int],
        tuple[float, float],
    ],
    target_teacher_ids: set[UUID],
    variation_seed: int,
    course_early_week_preferences: Mapping[UUID, int] | None = None,
    project_early_week_preferences: Mapping[UUID, int] | None = None,
    min_session_counts: Mapping[UUID, int] | None = None,
) -> SolverResult:
    """构造一个候选版本，并对每一次放置即时执行硬冲突检查。"""

    occupied_teachers: set[tuple[UUID, int, int, int]] = set()
    occupied_labs: set[tuple[UUID, int, int, int]] = set()
    teacher_load: dict[UUID, int] = {}
    lab_load: dict[UUID, int] = {}
    teacher_day_load: dict[tuple[UUID, int, int], int] = {}
    teacher_day_starts: dict[tuple[UUID, int, int], list[int]] = {}
    sessions: list[CandidateSessionDraft] = []
    errors: list[str] = []
    course_early_week_preferences = (
        course_early_week_preferences or {}
    )
    project_early_week_preferences = (
        project_early_week_preferences or {}
    )
    min_session_counts = min_session_counts or {}

    ordered_demands = sorted(
        demands,
        key=lambda item: (
            len(item.teachers) * len(item.laboratories),
            -item.occurrence_count,
            str(item.project_id),
        ),
    )

    for demand_index, demand in enumerate(ordered_demands):
        starts = tuple(
            range(
                1,
                slots_per_day - demand.required_slots + 2,
                demand.required_slots,
            )
        )
        blocks = [
            (week, day, start)
            for week in range(demand.week_start, demand.week_end + 1)
            for day in range(1, days_per_week + 1)
            for start in starts
        ]
        for occurrence_index in range(demand.occurrence_count):
            best: tuple[
                float,
                int,
                int,
                int,
                SolverTeacherOption,
                SolverLabOption,
                float,
                float,
            ] | None = None
            for block_index, (week, day, start) in enumerate(blocks):
                end = start + demand.required_slots - 1
                slot_range = range(start, end + 1)
                availability_rows = [
                    availability.get(
                        (demand.course_id, week, day, slot),
                        (0.0, 0.0),
                    )
                    for slot in slot_range
                ]
                free_ratio = min(row[0] for row in availability_rows)
                coverage = min(row[1] for row in availability_rows)
                availability_penalty = 1.0 - free_ratio

                def _evaluate(teacher):
                    if any(
                        (teacher.teacher_id, week, day, slot)
                        in occupied_teachers
                        for slot in slot_range
                    ):
                        return None
                    teacher_count = teacher_load.get(teacher.teacher_id, 0)
                    day_key = (teacher.teacher_id, week, day)
                    same_day = teacher_day_load.get(day_key, 0)
                    existing_starts = teacher_day_starts.get(day_key, [])
                    compactness = (
                        min(abs(start - other) for other in existing_starts)
                        / max(1, slots_per_day)
                        if existing_starts
                        else 1.0
                    )
                    best_for_teacher = None
                    for laboratory in demand.laboratories:
                        if any(
                            (laboratory.laboratory_id, week, day, slot)
                            in occupied_labs
                            for slot in slot_range
                        ):
                            continue
                        metrics = {
                            "STUDENT_AVAILABILITY_COVERAGE": availability_penalty,
                            "TEACHER_BALANCE": teacher_count
                            / max(1, len(sessions) + 1),
                            "EVENING_PENALTY": float(end > 8),
                            "WEEKEND_PENALTY": float(
                                is_weekend_day(day, days_per_week)
                            ),
                            "TEACHER_COMPACTNESS": compactness,
                            "TEACHER_CONSECUTIVE_LOAD": min(1.0, same_day / 2),
                            "TEACHER_PREFERRED_TIME": 0.0,
                            "LAB_UTILIZATION_BALANCE": lab_load.get(
                                laboratory.laboratory_id,
                                0,
                            )
                            / max(1, len(sessions) + 1),
                            "TEACHER_TARGET_LOAD_SCORE": float(
                                teacher.teacher_id in target_teacher_ids
                            ),
                            "COURSE_EARLY_WEEK_PREFERENCE": float(
                                demand.course_id
                                in course_early_week_preferences
                                and week
                                > course_early_week_preferences[
                                    demand.course_id
                                ]
                            ),
                            "PROJECT_EARLY_WEEK_PREFERENCE": float(
                                demand.project_id
                                in project_early_week_preferences
                                and week
                                > project_early_week_preferences[
                                    demand.project_id
                                ]
                            ),
                        }
                        penalty = sum(
                            solver_weights.get(code, 0.0) * value
                            for code, value in metrics.items()
                        ) / 100
                        # 只用于稳定地打破同分，不改变可解释指标。
                        tie_breaker = (
                            (
                                block_index
                                + demand_index * 17
                                + occurrence_index * 7
                                + variation_seed * 13
                            )
                            % max(1, len(blocks))
                        ) * 1e-9
                        candidate = (
                            penalty + tie_breaker,
                            week,
                            day,
                            start,
                            teacher,
                            laboratory,
                            availability_penalty,
                            coverage,
                        )
                        if (
                            best_for_teacher is None
                            or candidate[0] < best_for_teacher[0]
                        ):
                            best_for_teacher = candidate
                    return best_for_teacher

                # 两阶段教师选择：未达每学期最少场次下限的教师优先，
                # 其在本块可行时不再考虑其他教师；全部不可行时回退，
                # 保证下限不会导致排课失败。
                protected_ids = {
                    teacher.teacher_id
                    for teacher in demand.teachers
                    if teacher_load.get(teacher.teacher_id, 0)
                    < min_session_counts.get(teacher.teacher_id, 0)
                }
                block_best = None
                for teacher in demand.teachers:
                    if teacher.teacher_id not in protected_ids:
                        continue
                    candidate = _evaluate(teacher)
                    if candidate is not None and (
                        block_best is None or candidate[0] < block_best[0]
                    ):
                        block_best = candidate
                if block_best is None:
                    for teacher in demand.teachers:
                        if teacher.teacher_id in protected_ids:
                            continue
                        candidate = _evaluate(teacher)
                        if candidate is not None and (
                            block_best is None or candidate[0] < block_best[0]
                        ):
                            block_best = candidate
                if block_best is not None and (
                    best is None or block_best[0] < best[0]
                ):
                    best = block_best
            if best is None:
                errors.append(
                    f"{demand.project_name} 第 {occurrence_index + 1} 个场次"
                    "没有满足教师与实验室硬约束的时间。"
                )
                return SolverResult(
                    sessions=tuple(sessions),
                    soft_score=0.0,
                    metrics={},
                    hard_constraint_passed=False,
                    errors=tuple(errors),
                )

            (
                _,
                week,
                day,
                start,
                teacher,
                laboratory,
                availability_penalty,
                coverage,
            ) = best
            end = start + demand.required_slots - 1
            for slot in range(start, end + 1):
                occupied_teachers.add(
                    (teacher.teacher_id, week, day, slot)
                )
                occupied_labs.add(
                    (laboratory.laboratory_id, week, day, slot)
                )
            teacher_load[teacher.teacher_id] = (
                teacher_load.get(teacher.teacher_id, 0) + 1
            )
            lab_load[laboratory.laboratory_id] = (
                lab_load.get(laboratory.laboratory_id, 0) + 1
            )
            day_key = (teacher.teacher_id, week, day)
            teacher_day_load[day_key] = teacher_day_load.get(day_key, 0) + 1
            teacher_day_starts.setdefault(day_key, []).append(start)
            sessions.append(
                CandidateSessionDraft(
                    task_id=demand.task_id,
                    course_id=demand.course_id,
                    project_id=demand.project_id,
                    course_name=demand.course_name,
                    project_name=demand.project_name,
                    week_no=week,
                    day_of_week=day,
                    start_slot=start,
                    end_slot=end,
                    teacher_id=teacher.teacher_id,
                    teacher_name=teacher.teacher_name,
                    laboratory_id=laboratory.laboratory_id,
                    laboratory_code=laboratory.laboratory_code,
                    laboratory_name=laboratory.laboratory_name,
                    capacity=laboratory.effective_capacity,
                    availability_penalty=availability_penalty,
                    data_coverage_ratio=coverage,
                )
            )

    metrics = _aggregate_metrics(
        sessions,
        target_teacher_ids,
        days_per_week,
        course_early_week_preferences,
        project_early_week_preferences,
    )
    weighted_penalty = sum(
        solver_weights.get(code, 0.0) * value
        for code, value in metrics.items()
    ) / 100
    return SolverResult(
        sessions=tuple(sessions),
        soft_score=max(0.0, 100 * (1 - weighted_penalty)),
        metrics=metrics,
        hard_constraint_passed=True,
        errors=(),
    )
