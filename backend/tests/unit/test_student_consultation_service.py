import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents.graphs.student_graph import (
    AGENT_CODE,
    BUSINESS_TYPE,
    GRAPH_NAME,
    GRAPH_VERSION,
    build_student_graph,
    run_student_consultation,
)
from app.agents.nodes.student_advisor import (
    _deterministic_answer,
    _planner_input,
    compose_answer_stream,
    plan_with_llm,
    route_after_plan,
    route_after_tools,
    validate_final_answer,
    validate_plan,
)
from app.agents.tools.student_tools import (
    _canonical_name_matches,
    _session_resolution_error,
)
from app.data.student_selection_rules import RULES_BY_CODE
from app.models.identity import StudentBusyBitmap
from app.schemas.student_consultation import (
    ConsultationMessage,
    EntityReference,
    RecommendationScope,
    RecommendationSession,
    SelectionPreferences,
    StudentAgentPlan,
    StudentToolRequest,
    WeekRangePreference,
    weekday_full_name,
    weekday_number,
)
from app.services.student_consultation_service import (
    _base_schedule_conflict_message,
    _bitmap_busy,
    _experiment_session_conflict_message,
    _fixed_selection_preference_warnings,
    _preference_explanations,
    _preference_score,
    _week_matches_range,
    session_end_ordinal,
    session_start_ordinal,
    sessions_overlap,
)


def _session(week: int, day: int, start: int, end: int):
    return SimpleNamespace(
        week_no=week,
        day_of_week=day,
        start_slot=start,
        end_slot=end,
    )


def _recommendation_session(
    *, week: int = 7, day: int = 2, start: int = 1, end: int = 4
) -> RecommendationSession:
    return RecommendationSession(
        session_id=uuid4(),
        project_id=uuid4(),
        project_name="长度与密度测量",
        course_name="大学物理实验",
        requirement_type="OPTIONAL",
        category="BASIC",
        week_no=week,
        day_of_week=day,
        start_slot=start,
        end_slot=end,
        laboratory_name="基础实验室A101",
        campus_name="主校区",
        remaining=10,
    )


def test_busy_bitmap_maps_week_sunday_and_last_slot_msb_first() -> None:
    bitmap = bytearray(189)
    bitmap[0] |= 1 << 7
    last_index = 18 * 7 * 12 - 1
    bitmap[last_index // 8] |= 1 << (7 - (last_index % 8))
    model = StudentBusyBitmap(
        student_id=uuid4(),
        term_id=uuid4(),
        start_week=1,
        end_week=18,
        days_per_week=7,
        slots_per_day=12,
        bitmap=bytes(bitmap),
        mapping_version=1,
    )

    assert _bitmap_busy(model, week=1, day=1, slot=1)
    assert not _bitmap_busy(model, week=1, day=1, slot=2)
    assert _bitmap_busy(model, week=18, day=7, slot=12)


@pytest.mark.parametrize(
    ("day_name", "expected"),
    [
        ("周日", 1),
        ("周一", 2),
        ("周二", 3),
        ("周三", 4),
        ("周四", 5),
        ("周五", 6),
        ("周六", 7),
    ],
)
def test_weekday_name_maps_to_sunday_first_number(day_name: str, expected: int) -> None:
    assert weekday_number(day_name) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("day_of_week", "expected"),
    [
        (1, "星期日"),
        (2, "星期一"),
        (3, "星期二"),
        (4, "星期三"),
        (5, "星期四"),
        (6, "星期五"),
        (7, "星期六"),
    ],
)
def test_weekday_full_name_uses_sunday_first_numbering(
    day_of_week: int, expected: str
) -> None:
    assert weekday_full_name(day_of_week) == expected


@pytest.mark.parametrize(
    ("week_range", "allowed", "blocked"),
    [
        (WeekRangePreference(start_week=8, start_inclusive=False), 9, 8),
        (WeekRangePreference(start_week=8, start_inclusive=True), 8, 7),
        (WeekRangePreference(end_week=8, end_inclusive=False), 7, 8),
        (WeekRangePreference(end_week=8, end_inclusive=True), 8, 9),
        (
            WeekRangePreference(start_week=6, end_week=10),
            6,
            11,
        ),
    ],
)
def test_week_range_respects_inclusive_and_exclusive_boundaries(
    week_range: WeekRangePreference, allowed: int, blocked: int
) -> None:
    assert _week_matches_range(allowed, week_range)
    assert not _week_matches_range(blocked, week_range)


def test_invalid_or_empty_week_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        WeekRangePreference()
    with pytest.raises(ValidationError):
        WeekRangePreference(
            start_week=8,
            start_inclusive=False,
            end_week=9,
            end_inclusive=False,
        )


def test_preference_score_handles_period_day_and_avoided_week() -> None:
    item = _recommendation_session(week=7, day=2, start=1, end=4)
    base_score, _, _ = _preference_score(
        item, SelectionPreferences(), student_campus="主校区"
    )
    preferred_score, _, _ = _preference_score(
        item,
        SelectionPreferences(preferred_periods=["MORNING"], preferred_days=["周一"]),
        student_campus="主校区",
    )
    avoided_score, _, _ = _preference_score(
        item,
        SelectionPreferences(
            avoided_periods=["MORNING"],
            avoided_days=["周一"],
            avoided_weeks=[7],
        ),
        student_campus="主校区",
    )

    assert preferred_score == base_score + 70
    assert avoided_score == base_score - 270


def test_cross_period_session_only_matches_avoidance_overlap() -> None:
    item = _recommendation_session(start=3, end=6)
    base_score, _, _ = _preference_score(
        item, SelectionPreferences(), student_campus="主校区"
    )
    preferred_score, _, _ = _preference_score(
        item,
        SelectionPreferences(preferred_periods=["MORNING"]),
        student_campus="主校区",
    )
    avoided_score, _, _ = _preference_score(
        item,
        SelectionPreferences(avoided_periods=["MORNING"]),
        student_campus="主校区",
    )

    assert preferred_score == base_score
    assert avoided_score == base_score - 100


def test_preference_explanations_report_unmet_soft_preferences() -> None:
    morning = _recommendation_session(week=7, day=2, start=1, end=4)
    evening = _recommendation_session(week=9, day=4, start=9, end=12)
    evening.project_name = "交流电桥"
    reasons, warnings = _preference_explanations(
        [morning, evening],
        SelectionPreferences(
            preferred_periods=["MORNING"],
            preferred_days=["周一"],
            avoided_periods=["EVENING"],
            avoided_weeks=[9],
        ),
    )

    assert "1个新增场次符合早上偏好" in reasons
    assert "1个新增场次符合周一偏好" in reasons
    assert "交流电桥未能安排在偏好的早上时段" in warnings
    assert "交流电桥未能避开晚上时段" in warnings
    assert "交流电桥未能避开第9周" in warnings


def test_fixed_selection_preference_miss_is_explicit() -> None:
    fixed = _recommendation_session(week=7, day=2, start=9, end=12)
    warnings = _fixed_selection_preference_warnings(
        [fixed],
        SelectionPreferences(
            preferred_periods=["AFTERNOON"],
            avoided_periods=["EVENING"],
            week_range=WeekRangePreference(start_week=8),
        ),
    )

    assert len(warnings) == 1
    assert "已选固定场次“长度与密度测量”" in warnings[0]
    assert "本轮保持不变" in warnings[0]


def test_contradictory_preferences_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SelectionPreferences(preferred_periods=["MORNING"], avoided_periods=["MORNING"])
    with pytest.raises(ValidationError):
        SelectionPreferences(preferred_days=["周二"], avoided_days=["周二"])


def test_conflict_messages_use_chinese_weekday_and_preserve_slot_range() -> None:
    target = _session(7, 2, 5, 8)

    assert _base_schedule_conflict_message(target, [5, 6]) == (
        "第7周星期一第5—6节与已有课程冲突。"
    )
    assert _experiment_session_conflict_message(target) == (
        "该场次与已选或处理中的实验安排冲突（第7周，星期一第5—8节）。"
    )


def test_entity_reference_rejects_numeric_weekday() -> None:
    with pytest.raises(ValidationError):
        EntityReference(day_name=1)  # type: ignore[arg-type]


def test_session_resolution_distinguishes_zero_and_multiple_matches() -> None:
    exact = EntityReference(
        project_name="长度与密度测量",
        teacher_name="王芳",
        week_no=7,
        day_name="周一",
        start_slot=1,
        end_slot=4,
    )
    not_found = _session_resolution_error(
        exact,
        {"project_name": "长度与密度测量"},
        candidate_count=0,
    )
    assert not_found == (
        "未找到第7周周一第1—4节王芳老师的长度与密度测量场次，请核对条件。"
    )

    ambiguous = _session_resolution_error(
        EntityReference(project_name="长度与密度测量", week_no=7),
        {"project_name": "长度与密度测量"},
        candidate_count=2,
    )
    assert ambiguous == "找到多个符合条件的长度与密度测量场次，请补充星期、节次、教师。"


def test_session_ordinal_and_overlap_use_inclusive_slots() -> None:
    first = _session(1, 1, 1, 4)
    adjacent = _session(1, 1, 5, 8)
    overlapping = _session(1, 1, 4, 7)

    assert session_end_ordinal(first) < session_start_ordinal(adjacent)
    assert not sessions_overlap(first, adjacent)
    assert sessions_overlap(first, overlapping)


def _recommendation_state(day_of_week: int = 2) -> dict[str, object]:
    session = RecommendationSession(
        session_id=uuid4(),
        project_id=uuid4(),
        project_name="霍尔效应与磁场测量",
        course_name="工程物理实验",
        requirement_type="REQUIRED",
        category="ELECTRICITY",
        week_no=7,
        day_of_week=day_of_week,
        start_slot=1,
        end_slot=4,
        laboratory_name="电学综合实验室 B102",
        campus_name="主校区",
        remaining=20,
    )
    return {
        "intent": "RECOMMEND_SELECTION",
        "tool_results": [
            {
                "name": "recommend_selection_plans",
                "data": {
                    "plans": [
                        {
                            "name": "推荐方案1",
                            "coverage_status": "COMPLETE",
                            "sessions": [session.model_dump(mode="json")],
                            "retained_selections": [],
                            "reasons": ["优先满足必做项目要求"],
                            "warnings": [],
                            "unmet_requirements": [],
                        }
                    ]
                },
            }
        ],
    }


def test_recommendation_session_uses_sunday_first_display_name() -> None:
    state = _recommendation_state()
    item = state["tool_results"][0]["data"]["plans"][0]["sessions"][0]  # type: ignore[index]

    assert item["day_name"] == "周一"
    assert item["display_time"] == "第7周周一 第1—4节"


def test_recommendation_answer_is_rendered_without_llm_weekday_conversion() -> None:
    class MustNotRunModel:
        async def astream(self, messages):
            raise AssertionError("recommendation composer must not call the LLM")
            yield  # pragma: no cover

    state = {**_recommendation_state(), "model": MustNotRunModel()}
    result = asyncio.run(compose_answer_stream(state))  # type: ignore[arg-type]

    assert "第7周周一 第1—4节" in result["answer"]
    assert "周二" not in result["answer"]


def test_recommendation_validation_rejects_wrong_weekday() -> None:
    state = {
        **_recommendation_state(),
        "answer_buffer": "霍尔效应与磁场测量：第7周周二 1—4节。",
    }

    result = asyncio.run(validate_final_answer(state))  # type: ignore[arg-type]

    assert result["grounding_passed"] is False


def test_structured_planner_controls_tools_and_preferences() -> None:
    plan = StudentAgentPlan(
        intent="RECOMMEND_SELECTION",
        entity_reference=EntityReference(course_name="大学物理实验"),
        preferences=SelectionPreferences(
            avoid_weekend=True,
            avoid_evening=True,
            preferred_categories=["MECHANICS"],
        ),
        tool_requests=[StudentToolRequest(name="recommend_selection_plans")],
    )
    assert plan.preferences.avoid_weekend
    assert plan.preferences.preferred_categories == ["MECHANICS"]
    assert plan.tool_requests[0].name == "recommend_selection_plans"


def test_planner_receives_canonical_student_context_and_teacher_reference() -> None:
    reference = EntityReference(
        project_name="长度与密度测量",
        teacher_name="王芳",
        week_no=7,
        start_slot=1,
        end_slot=4,
    )
    messages = _planner_input(
        {
            "base_context": {
                "training_plan_summary": {
                    "courses": [
                        {
                            "course_name": "大学物理实验",
                            "projects": [{"project_name": "长度与密度测量"}],
                        }
                    ]
                }
            },
            "conversation_context": [],
            "page_context": None,
            "current_question": "我能否选择王芳老师的长度与密度测量实验？",
        }
    )

    assert "<student_base_context>" in messages[1].content
    assert "长度与密度测量" in messages[1].content
    assert reference.teacher_name == "王芳"


def test_canonical_project_name_matches_natural_language_modifier() -> None:
    assert _canonical_name_matches("长度与密度测量", "王芳老师的长度与密度测量实验")


def test_personal_course_eligibility_uses_backend_fact() -> None:
    state = {
        "tool_results": [
            {
                "name": "get_training_plan_context",
                "data": {
                    "courses": [
                        {
                            "course_name": "大学物理实验",
                            "eligibility": {
                                "decision": "BLOCK",
                                "violations": [
                                    {
                                        "code": "PREREQUISITE_COURSE_NOT_PASSED",
                                        "message": "先修课程尚未通过：高等数学（上）",
                                    }
                                ],
                            },
                        }
                    ]
                },
            }
        ]
    }

    answer = _deterministic_answer(state)  # type: ignore[arg-type]
    assert "本学期暂不能修读大学物理实验" in answer
    assert "高等数学（上）" in answer

    invalid = asyncio.run(
        validate_final_answer(
            {
                **state,
                "answer_buffer": "目前无法确定，因为缺少判断所需的个人信息。",
            }
        )
    )
    assert invalid["grounding_passed"] is False


def test_recommendation_planner_keeps_scope_and_preferences() -> None:
    class RecommendationModel:
        async def ainvoke(self, messages):
            assert messages
            return SimpleNamespace(
                content=(
                    '{"intent":"RECOMMEND_SELECTION","entity_reference":null,'
                    '"preferences":{"avoid_weekend":true,'
                    '"preferred_periods":["AFTERNOON"],'
                    '"avoided_periods":["EVENING"],'
                    '"preferred_days":["周二","周四"],'
                    '"week_range":{"start_week":8,"start_inclusive":false,'
                    '"end_week":12,"end_inclusive":true},'
                    '"avoided_weeks":[9],'
                    '"preferred_categories":["MECHANICS"]},'
                    '"tool_requests":[{"name":"recommend_selection_plans",'
                    '"arguments":{}}],"rule_topics":[],'
                    '"recommendation_scope":{"mode":"COURSES",'
                    '"course_names":["大学物理实验","工程物理实验"],'
                    '"project_names":[]},"needs_clarification":false,'
                    '"clarification_question":null,"direct_answer_allowed":false}'
                )
            )

    result = asyncio.run(
        plan_with_llm(
            {
                "model": RecommendationModel(),
                "current_question": (
                    "请推荐两门课程的方案，喜欢周二周四下午，避开晚上和第9周，"
                    "安排在第8周以后到第12周"
                ),
                "conversation_context": [],
                "page_context": None,
            }
        )
    )
    plan = result["plan"]
    assert plan.recommendation_scope == RecommendationScope(
        mode="COURSES",
        course_names=["大学物理实验", "工程物理实验"],
    )
    assert plan.preferences.avoid_weekend
    assert plan.preferences.preferred_periods == ["AFTERNOON"]
    assert plan.preferences.avoided_periods == ["EVENING"]
    assert plan.preferences.preferred_days == ["周二", "周四"]
    assert plan.preferences.week_range == WeekRangePreference(
        start_week=8,
        start_inclusive=False,
        end_week=12,
        end_inclusive=True,
    )
    assert plan.preferences.avoided_weeks == [9]
    assert plan.preferences.preferred_categories == ["MECHANICS"]


def test_planner_rejects_more_than_three_tools() -> None:
    with pytest.raises(ValidationError):
        StudentAgentPlan(
            intent="QUERY_TRAINING_PLAN",
            tool_requests=[
                StudentToolRequest(name="get_training_plan_context") for _ in range(4)
            ],
        )


def test_planner_parses_plain_model_json_without_response_format() -> None:
    class PlainJsonModel:
        async def ainvoke(self, messages):
            assert messages
            return SimpleNamespace(
                content=(
                    "```json\n"
                    '{"intent":"QUERY_TRAINING_PLAN",'
                    '"entity_reference":null,"preferences":{},'
                    '"tool_requests":[{"name":"get_training_plan_context",'
                    '"arguments":{}}],"needs_clarification":false,'
                    '"clarification_question":null,"direct_answer_allowed":false}'
                    "\n```"
                )
            )

    result = asyncio.run(
        plan_with_llm(
            {
                "model": PlainJsonModel(),
                "current_question": "我的培养方案有哪些实验要求？",
                "conversation_context": [],
                "base_context": {},
            }
        )
    )

    assert result["intent"] == "QUERY_TRAINING_PLAN"
    assert result["tool_requests"][0].name == "get_training_plan_context"


def test_validate_plan_rejects_identity_in_model_arguments() -> None:
    plan = StudentAgentPlan(
        intent="CHECK_ELIGIBILITY",
        tool_requests=[
            StudentToolRequest(
                name="check_selection_eligibility",
                arguments={"student_id": str(uuid4())},
            )
        ],
    )
    result = asyncio.run(validate_plan({"plan": plan}))  # type: ignore[arg-type]
    assert result["model_error"] == "模型执行计划未通过安全校验。"


@pytest.mark.parametrize("question", ["你好", "谢谢", "你能做什么"])
def test_general_chat_routes_without_tools(question: str) -> None:
    plan = StudentAgentPlan(
        intent="GENERAL_CHAT",
        direct_answer_allowed=True,
        tool_requests=[],
    )
    state = {"plan": plan, "current_question": question}

    assert asyncio.run(validate_plan(state)) == {"plan_validation_errors": []}
    assert route_after_plan(state) == "compose_general_chat_stream"


def test_general_chat_graph_streams_without_loading_business_context() -> None:
    class GeneralChatModel:
        async def ainvoke(self, messages):
            assert messages
            return SimpleNamespace(
                content=(
                    '{"intent":"GENERAL_CHAT","entity_reference":null,'
                    '"preferences":{},"tool_requests":[],"rule_topics":[],'
                    '"needs_clarification":false,"clarification_question":null,'
                    '"direct_answer_allowed":true}'
                )
            )

        async def astream(self, messages):
            assert messages
            yield SimpleNamespace(content="你好！")
            yield SimpleNamespace(content="我可以帮你查询物理实验选课信息。")

    state = asyncio.run(
        run_student_consultation(
            {
                "model": GeneralChatModel(),
                "session": object(),
                "student_id": uuid4(),
                "term": SimpleNamespace(id=uuid4()),
                "messages": [ConsultationMessage(role="user", content="你好")],
                "page_context": None,
            }
        )
    )

    assert state["intent"] == "GENERAL_CHAT"
    assert not state.get("tool_results")
    assert state["answer"] == "你好！我可以帮你查询物理实验选课信息。"


def test_out_of_scope_routes_to_boundary_without_tools() -> None:
    plan = StudentAgentPlan(intent="OUT_OF_SCOPE", tool_requests=[])
    state = {"plan": plan}

    assert asyncio.run(validate_plan(state)) == {"plan_validation_errors": []}
    assert route_after_plan(state) == "compose_boundary_notice"


def test_business_rule_query_requires_lookup_and_topics() -> None:
    invalid = StudentAgentPlan(intent="BUSINESS_RULE_QUERY")
    result = asyncio.run(validate_plan({"plan": invalid}))
    assert result["model_error"] == "模型执行计划未通过安全校验。"

    valid = StudentAgentPlan(
        intent="BUSINESS_RULE_QUERY",
        rule_topics=["PROJECT_UNIQUENESS"],
        tool_requests=[StudentToolRequest(name="lookup_student_rules")],
    )
    state = {"plan": valid}
    assert asyncio.run(validate_plan(state)) == {"plan_validation_errors": []}
    assert route_after_plan(state) == "execute_student_tools"


def test_rule_query_not_found_uses_deterministic_branch() -> None:
    state = {
        "intent": "BUSINESS_RULE_QUERY",
        "tool_results": [
            {"name": "lookup_student_rules", "data": {"status": "NOT_FOUND"}}
        ],
    }
    assert route_after_tools(state) == "compose_rule_not_found"


def test_eligibility_policy_codes_exist_in_student_rule_catalog() -> None:
    expected = {
        "STUDENT_INACTIVE",
        "STUDY_PERIOD_NOT_REACHED",
        "COURSE_ALREADY_PASSED",
        "PREREQUISITE_COURSE_NOT_PASSED",
        "SCHEDULE_NOT_PUBLISHED",
        "SESSION_NOT_OPEN",
        "SESSION_FULL",
        "SESSION_ALREADY_SELECTED",
        "PROJECT_ALREADY_SELECTED",
        "PROJECT_OCCUPIED_BY_APPLICATION",
        "BASE_SCHEDULE_CONFLICT",
        "EXPERIMENT_SESSION_CONFLICT",
        "PROJECT_ORDER_VIOLATION",
        "PROJECT_ORDER_PENDING",
    }
    assert expected <= RULES_BY_CODE.keys()


def test_student_graph_is_independent_from_scheduling_graph() -> None:
    graph_file = (
        Path(__file__).parents[2] / "app" / "agents" / "graphs" / "student_graph.py"
    )
    source = graph_file.read_text(encoding="utf-8")

    assert "scheduling_graph" not in source
    assert "scheduling_agent" not in source
    assert "validation_agent" not in source
    assert GRAPH_NAME == "student_consultation"
    assert GRAPH_VERSION == "v2"
    assert AGENT_CODE == "STUDENT_SELECTION_ADVISOR"
    assert BUSINESS_TYPE == "STUDENT_CONSULTATION"
    assert build_student_graph() is not None
