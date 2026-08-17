import asyncio
import json
from decimal import Decimal
from uuid import UUID

from openai import APIConnectionError

from app.agents.graphs.scheduling_graph import resolve_runtime_strategy
from app.agents.nodes.scheduling_agent import parse_preferences
from app.agents.nodes.validation_agent import (
    evaluate_candidate_preference_effects,
    review_candidate_soft_constraints,
)
from app.scheduler.cp_sat_solver import (
    SolverDemand,
    SolverLabOption,
    SolverTeacherOption,
    is_weekend_day,
    solve_candidate,
)
from app.scheduler.objective import (
    build_comparison_weights,
    build_profile_weights,
)

BASE_WEIGHTS = {
    "STUDENT_AVAILABILITY_COVERAGE": 25,
    "TEACHER_BALANCE": 15,
    "EVENING_PENALTY": 12,
    "WEEKEND_PENALTY": 10,
    "TEACHER_COMPACTNESS": 10,
    "TEACHER_CONSECUTIVE_LOAD": 10,
    "TEACHER_PREFERRED_TIME": 10,
    "LAB_UTILIZATION_BALANCE": 8,
    "TEACHER_TARGET_LOAD_SCORE": 0,
    "COURSE_EARLY_WEEK_PREFERENCE": 0,
    "PROJECT_EARLY_WEEK_PREFERENCE": 0,
}


def test_runtime_weight_adjustment_does_not_mutate_base() -> None:
    original = dict(BASE_WEIGHTS)
    comparison = build_comparison_weights(
        BASE_WEIGHTS,
        applicability={
            code: code != "TEACHER_PREFERRED_TIME"
            for code in BASE_WEIGHTS
        },
        preference_levels={"WEEKEND_PENALTY": "PREFER"},
    )
    solver = build_profile_weights(comparison, "WEEKEND_PENALTY")

    assert BASE_WEIGHTS == original
    assert sum(comparison.values()) == Decimal(100)
    assert sum(solver.values()) == Decimal(100)
    assert comparison["TEACHER_PREFERRED_TIME"] == 0
    assert solver["WEEKEND_PENALTY"] > comparison["WEEKEND_PENALTY"]


def test_weekend_is_first_and_last_day_of_week_grid() -> None:
    assert is_weekend_day(1, 7)
    assert is_weekend_day(7, 7)
    assert not is_weekend_day(2, 7)
    assert not is_weekend_day(6, 7)


def test_agent_parses_whitelisted_preferences_and_teacher() -> None:
    teacher_id = "00000000-0000-0000-0000-000000000001"
    parsed = parse_preferences(
        "尽量减少周末实验，课时排得紧凑，张老师本学期少排课",
        teacher_directory={teacher_id: "张老师"},
    )
    by_code = {item["rule_code"]: item for item in parsed}

    assert set(by_code) == {
        "WEEKEND_PENALTY",
        "TEACHER_COMPACTNESS",
        "TEACHER_TARGET_LOAD_SCORE",
    }
    assert by_code["TEACHER_TARGET_LOAD_SCORE"]["target_teacher_ids"] == [
        teacher_id
    ]
    assert all(item["preference_level"] == "PREFER" for item in parsed)


def test_agent_parses_course_and_preferred_end_week() -> None:
    course_id = "00000000-0000-0000-0000-000000000010"
    parsed = parse_preferences(
        "大学物理实验尽量排在前八周",
        course_directory={course_id: "大学物理实验"},
    )

    assert parsed == [
        {
            "rule_code": "COURSE_EARLY_WEEK_PREFERENCE",
            "preference_level": "PREFER",
            "evidence": "前8周",
            "course_week_preferences": [
                {
                    "course_id": course_id,
                    "course_name": "大学物理实验",
                    "preferred_end_week": 8,
                }
            ],
        }
    ]


def test_agent_parses_project_and_preferred_end_week() -> None:
    project_id = "00000000-0000-0000-0000-000000000020"
    parsed = parse_preferences(
        "单摆测重力加速度尽量安排在前4周",
        project_directory={project_id: "单摆测重力加速度"},
    )

    assert parsed == [
        {
            "rule_code": "PROJECT_EARLY_WEEK_PREFERENCE",
            "preference_level": "PREFER",
            "evidence": "前4周",
            "project_week_preferences": [
                {
                    "project_id": project_id,
                    "project_name": "单摆测重力加速度",
                    "preferred_end_week": 4,
                }
            ],
        }
    ]


def _strategy_state(preference_text: str, **overrides) -> dict:
    state = {
        "preference_text": preference_text,
        "base_weights": BASE_WEIGHTS,
        "applicability": {code: True for code in BASE_WEIGHTS},
        "teacher_directory": {},
        "rule_priorities": {
            "STUDENT_AVAILABILITY_COVERAGE": 90,
            "WEEKEND_PENALTY": 70,
            "TEACHER_COMPACTNESS": 60,
        },
        "max_candidate_count": 5,
    }
    state.update(overrides)
    return state


def _parsed_codes(state: dict) -> set[str]:
    return {item["rule_code"] for item in state["parsed_preferences"]}


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChatModel:
    """按给定顺序返回 canned 输出；用尽后重复最后一条。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def ainvoke(self, messages):
        index = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return _FakeResponse(self._responses[index])


class FailingChatModel:
    async def ainvoke(self, messages):
        raise APIConnectionError(message="connection failed")


def _patch_model(monkeypatch, model) -> None:
    monkeypatch.setattr(
        "app.agents.nodes.scheduling_agent.get_chat_model",
        lambda: model,
    )


def test_langgraph_automatically_derives_candidate_count(monkeypatch) -> None:
    _patch_model(monkeypatch, None)  # 测试环境固定走确定性关键词回退
    state = asyncio.run(
        resolve_runtime_strategy(
            _strategy_state(
                "尽量减少周末实验，课时排得紧凑，优先考虑学生空闲人数",
                applicability={
                    code: code != "TEACHER_PREFERRED_TIME"
                    for code in BASE_WEIGHTS
                },
            )
        )
    )

    assert state["validation_errors"] == []
    assert len(state["profiles"]) == 4
    assert state["profiles"][0]["profile_code"] == "BALANCED"
    assert round(sum(state["comparison_weights"].values()), 4) == 100


def test_langgraph_generates_at_least_two_candidates_without_preferences(
    monkeypatch,
) -> None:
    _patch_model(monkeypatch, None)  # 测试环境固定走确定性关键词回退
    state = asyncio.run(resolve_runtime_strategy(_strategy_state("")))

    assert state["validation_errors"] == []
    assert len(state["profiles"]) == 2
    assert state["profiles"][0]["profile_code"] == "BALANCED"
    assert state["profiles"][1]["profile_code"] != "BALANCED"


def test_llm_parses_preferences_keyword_parser_misses(monkeypatch) -> None:
    llm_json = json.dumps(
        {
            "preferences": [
                {
                    "rule_code": "WEEKEND_PENALTY",
                    "preference_level": "PREFER",
                    "evidence": "减少周末实验",
                },
                {
                    "rule_code": "TEACHER_COMPACTNESS",
                    "preference_level": "PREFER",
                    "evidence": "更紧凑",
                },
            ]
        },
        ensure_ascii=False,
    )
    _patch_model(monkeypatch, FakeChatModel([llm_json]))
    state = asyncio.run(
        resolve_runtime_strategy(_strategy_state("减少周末实验，教师课时更紧凑"))
    )

    assert {"WEEKEND_PENALTY", "TEACHER_COMPACTNESS"} <= _parsed_codes(state)
    assert state["validation_errors"] == []
    assert not any("回退" in warning for warning in state["warnings"])


def test_llm_repair_retry_recovers_parse_error(monkeypatch) -> None:
    good_json = json.dumps(
        {
            "preferences": [
                {
                    "rule_code": "WEEKEND_PENALTY",
                    "preference_level": "PREFER",
                    "evidence": "周末",
                }
            ]
        },
        ensure_ascii=False,
    )
    _patch_model(monkeypatch, FakeChatModel(["这不是JSON", good_json]))
    state = asyncio.run(
        resolve_runtime_strategy(_strategy_state("减少周末实验"))
    )

    assert _parsed_codes(state) == {"WEEKEND_PENALTY"}
    assert not any("回退" in warning for warning in state["warnings"])


def test_provider_failure_falls_back_to_keyword_parser(monkeypatch) -> None:
    _patch_model(monkeypatch, FailingChatModel())
    state = asyncio.run(
        resolve_runtime_strategy(
            _strategy_state("尽量减少周末实验，课时排得紧凑")
        )
    )

    assert _parsed_codes(state) == {"WEEKEND_PENALTY", "TEACHER_COMPACTNESS"}
    assert state["validation_errors"] == []
    assert any("回退" in warning for warning in state["warnings"])


def test_llm_unknown_rule_code_falls_back_to_keyword_parser(monkeypatch) -> None:
    bad_json = json.dumps(
        {
            "preferences": [
                {
                    "rule_code": "MAGIC_RULE",
                    "preference_level": "PREFER",
                    "evidence": "x",
                }
            ]
        },
        ensure_ascii=False,
    )
    _patch_model(monkeypatch, FakeChatModel([bad_json, bad_json]))
    state = asyncio.run(
        resolve_runtime_strategy(_strategy_state("尽量减少周末实验"))
    )

    assert _parsed_codes(state) == {"WEEKEND_PENALTY"}
    assert any("回退" in warning for warning in state["warnings"])


def test_validation_agent_returns_soft_constraint_pros_and_cons() -> None:
    review = review_candidate_soft_constraints(
        metrics={
            "WEEKEND_PENALTY": 1.0,
            "TEACHER_COMPACTNESS": 8.0,
            "TEACHER_BALANCE": 5.0,
        },
        comparison_weights={
            "WEEKEND_PENALTY": 30.0,
            "TEACHER_COMPACTNESS": 30.0,
            "TEACHER_BALANCE": 40.0,
        },
        peer_metrics=[
            {
                "WEEKEND_PENALTY": 4.0,
                "TEACHER_COMPACTNESS": 2.0,
                "TEACHER_BALANCE": 5.0,
            },
            {
                "WEEKEND_PENALTY": 1.0,
                "TEACHER_COMPACTNESS": 8.0,
                "TEACHER_BALANCE": 5.0,
            },
        ],
    )

    assert review["advantages"][0]["rule_code"] == "WEEKEND_PENALTY"
    assert review["tradeoffs"][0]["rule_code"] == "TEACHER_COMPACTNESS"
    assert all(item["text"] for items in review.values() for item in items)


def test_validation_agent_does_not_invent_differences_for_equal_candidates() -> None:
    metrics = {
        "WEEKEND_PENALTY": 0.0,
        "TEACHER_BALANCE": 0.2,
    }
    review = review_candidate_soft_constraints(
        metrics=metrics,
        comparison_weights={
            "WEEKEND_PENALTY": 50.0,
            "TEACHER_BALANCE": 50.0,
        },
        peer_metrics=[metrics, dict(metrics)],
    )

    assert review == {"advantages": [], "tradeoffs": []}


def test_validation_agent_explains_each_requested_preference_effect() -> None:
    effects = evaluate_candidate_preference_effects(
        parsed_preferences=[
            {
                "rule_code": "WEEKEND_PENALTY",
                "preference_level": "PREFER",
                "evidence": "周末",
            },
            {
                "rule_code": "TEACHER_COMPACTNESS",
                "preference_level": "PREFER",
                "evidence": "课时紧凑",
            },
            {
                "rule_code": "TEACHER_PREFERRED_TIME",
                "preference_level": "PREFER",
                "evidence": "教师偏好时间",
            },
        ],
        metrics={
            "WEEKEND_PENALTY": 0.1,
            "TEACHER_COMPACTNESS": 0.65,
            "TEACHER_PREFERRED_TIME": 0.0,
        },
        comparison_weights={
            "WEEKEND_PENALTY": 30.0,
            "TEACHER_COMPACTNESS": 25.0,
            "TEACHER_PREFERRED_TIME": 0.0,
        },
    )

    by_code = {item["rule_code"]: item for item in effects}
    assert by_code["WEEKEND_PENALTY"]["status"] == "SATISFIED"
    assert by_code["WEEKEND_PENALTY"]["achievement_rate"] == 90.0
    assert by_code["TEACHER_COMPACTNESS"]["status"] == "NOT_SATISFIED"
    assert by_code["TEACHER_COMPACTNESS"]["achievement_rate"] == 35.0
    assert by_code["TEACHER_PREFERRED_TIME"]["status"] == "NOT_APPLIED"
    assert by_code["TEACHER_PREFERRED_TIME"]["achievement_rate"] is None


def test_solver_enforces_teacher_and_lab_time_conflicts() -> None:
    teacher = SolverTeacherOption(
        UUID("00000000-0000-0000-0000-000000000001"),
        "测试教师",
    )
    laboratory = SolverLabOption(
        UUID("00000000-0000-0000-0000-000000000002"),
        "LAB-1",
        "测试实验室",
        20,
    )
    demand = SolverDemand(
        task_id=UUID("00000000-0000-0000-0000-000000000003"),
        course_id=UUID("00000000-0000-0000-0000-000000000004"),
        course_name="测试课程",
        project_id=UUID("00000000-0000-0000-0000-000000000005"),
        project_name="测试项目",
        week_start=1,
        week_end=1,
        required_slots=4,
        required_capacity=40,
        occurrence_count=2,
        teachers=(teacher,),
        laboratories=(laboratory,),
    )
    result = solve_candidate(
        demands=[demand],
        days_per_week=1,
        slots_per_day=8,
        solver_weights={
            "TEACHER_BALANCE": 50,
            "LAB_UTILIZATION_BALANCE": 50,
        },
        availability={},
        target_teacher_ids=set(),
        variation_seed=0,
    )

    assert result.hard_constraint_passed
    assert len(result.sessions) == 2
    occupied = {
        (item.week_no, item.day_of_week, slot)
        for item in result.sessions
        for slot in range(item.start_slot, item.end_slot + 1)
    }
    assert len(occupied) == 8


def test_solver_scores_target_course_sessions_after_preferred_week() -> None:
    teacher = SolverTeacherOption(
        UUID("00000000-0000-0000-0000-000000000011"),
        "测试教师",
    )
    laboratory = SolverLabOption(
        UUID("00000000-0000-0000-0000-000000000012"),
        "LAB-2",
        "测试实验室",
        20,
    )
    course_id = UUID("00000000-0000-0000-0000-000000000013")
    demand = SolverDemand(
        task_id=UUID("00000000-0000-0000-0000-000000000014"),
        course_id=course_id,
        course_name="目标课程",
        project_id=UUID("00000000-0000-0000-0000-000000000015"),
        project_name="测试项目",
        week_start=2,
        week_end=2,
        required_slots=4,
        required_capacity=20,
        occurrence_count=1,
        teachers=(teacher,),
        laboratories=(laboratory,),
    )
    result = solve_candidate(
        demands=[demand],
        days_per_week=7,
        slots_per_day=12,
        solver_weights={"COURSE_EARLY_WEEK_PREFERENCE": 100},
        availability={},
        target_teacher_ids=set(),
        variation_seed=0,
        course_early_week_preferences={course_id: 1},
        project_early_week_preferences={demand.project_id: 1},
    )

    assert result.metrics["COURSE_EARLY_WEEK_PREFERENCE"] == 1.0
    assert result.metrics["PROJECT_EARLY_WEEK_PREFERENCE"] == 1.0


def _teacher_loads(result) -> dict[UUID, int]:
    loads: dict[UUID, int] = {}
    for item in result.sessions:
        loads[item.teacher_id] = loads.get(item.teacher_id, 0) + 1
    return loads


def _build_multi_teacher_demand(
    teachers: tuple[SolverTeacherOption, ...],
    *,
    occurrence_count: int,
    week_end: int = 2,
) -> SolverDemand:
    laboratory = SolverLabOption(
        UUID("00000000-0000-0000-0000-000000000031"),
        "LAB-3",
        "测试实验室",
        40,
    )
    return SolverDemand(
        task_id=UUID("00000000-0000-0000-0000-000000000032"),
        course_id=UUID("00000000-0000-0000-0000-000000000033"),
        course_name="测试课程",
        project_id=UUID("00000000-0000-0000-0000-000000000034"),
        project_name="测试项目",
        week_start=1,
        week_end=week_end,
        required_slots=4,
        required_capacity=40,
        occurrence_count=occurrence_count,
        teachers=teachers,
        laboratories=(laboratory,),
    )


def test_solver_prioritizes_below_floor_teachers() -> None:
    """低于最少场次下限的教师优先拿场次，补齐下限后恢复均衡分配。"""

    t1 = SolverTeacherOption(
        UUID("00000000-0000-0000-0000-000000000041"), "教师一"
    )
    t2 = SolverTeacherOption(
        UUID("00000000-0000-0000-0000-000000000042"), "教师二"
    )
    result = solve_candidate(
        demands=[
            _build_multi_teacher_demand((t1, t2), occurrence_count=30)
        ],
        days_per_week=7,
        slots_per_day=12,
        solver_weights={"TEACHER_BALANCE": 50, "LAB_UTILIZATION_BALANCE": 50},
        availability={},
        target_teacher_ids=set(),
        variation_seed=0,
        min_session_counts={t1.teacher_id: 5, t2.teacher_id: 20},
    )

    assert result.hard_constraint_passed
    loads = _teacher_loads(result)
    # t1 先补齐下限 5 场；t2 未达下限期间持续拿场次至 20；
    # 之后恢复按均衡分配，剩余 5 场全部给 t1。
    assert loads[t1.teacher_id] == 10
    assert loads[t2.teacher_id] == 20


def test_solver_floor_beats_reduce_load_preference() -> None:
    """减负软偏好不能把目标教师减到少于场次下限。"""

    target = SolverTeacherOption(
        UUID("00000000-0000-0000-0000-000000000051"), "减负教师"
    )
    other = SolverTeacherOption(
        UUID("00000000-0000-0000-0000-000000000052"), "普通教师"
    )
    result = solve_candidate(
        demands=[
            _build_multi_teacher_demand(
                (target, other), occurrence_count=30
            )
        ],
        days_per_week=7,
        slots_per_day=12,
        solver_weights={"TEACHER_TARGET_LOAD_SCORE": 100},
        availability={},
        target_teacher_ids={target.teacher_id},
        variation_seed=0,
        min_session_counts={target.teacher_id: 20},
    )

    assert result.hard_constraint_passed
    loads = _teacher_loads(result)
    assert loads[target.teacher_id] == 20
    assert loads[other.teacher_id] == 10


def test_solver_shortfall_below_floor_does_not_fail() -> None:
    """教师可承担场次总数低于下限时正常排完，不因下限而失败。"""

    teacher = SolverTeacherOption(
        UUID("00000000-0000-0000-0000-000000000061"), "独任教师"
    )
    result = solve_candidate(
        demands=[
            _build_multi_teacher_demand(
                (teacher,), occurrence_count=10, week_end=1
            )
        ],
        days_per_week=7,
        slots_per_day=12,
        solver_weights={"TEACHER_BALANCE": 50, "LAB_UTILIZATION_BALANCE": 50},
        availability={},
        target_teacher_ids=set(),
        variation_seed=0,
        min_session_counts={teacher.teacher_id: 20},
    )

    assert result.hard_constraint_passed
    assert len(result.sessions) == 10
    assert _teacher_loads(result)[teacher.teacher_id] == 10
