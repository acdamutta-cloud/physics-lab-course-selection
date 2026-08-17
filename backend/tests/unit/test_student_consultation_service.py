import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
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
    _format_current_selection_answer,
    _format_remaining_projects_answer,
    _planner_input,
    compose_answer_stream,
    plan_with_llm,
    resolve_entities,
    route_after_plan,
    route_after_tools,
    validate_final_answer,
    validate_plan,
)
from app.agents.tools.student_tools import (
    _canonical_name_matches,
    _session_resolution_error,
    prepare_adjustment_entry_tool,
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
    _load_context,
    _preference_explanations,
    _preference_score,
    _week_matches_range,
    session_end_ordinal,
    session_start_ordinal,
    sessions_overlap,
)


@pytest.mark.asyncio
async def test_eligibility_context_uses_batched_queries() -> None:
    student_id = uuid4()
    session_id = uuid4()
    target = SimpleNamespace(
        id=session_id,
        schedule_version_id=uuid4(),
        project=SimpleNamespace(course_id=uuid4()),
    )
    schedule = SimpleNamespace(status="PUBLISHED")
    term = SimpleNamespace(id=uuid4())
    student = SimpleNamespace(id=student_id)
    plan = SimpleNamespace(id=uuid4())
    plan_course = SimpleNamespace()

    core_result = MagicMock()
    core_result.one_or_none.return_value = (target, schedule, term)
    student_plan_result = MagicMock()
    student_plan_result.first.return_value = (student, plan)
    plan_course_result = MagicMock()
    plan_course_result.unique.return_value.scalar_one_or_none.return_value = plan_course
    bitmap_result = MagicMock()
    bitmap_result.scalar_one_or_none.return_value = None
    records_result = MagicMock()
    records_result.scalars.return_value.all.return_value = []
    applications_result = MagicMock()
    applications_result.scalars.return_value.all.return_value = []

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                core_result,
                student_plan_result,
                plan_course_result,
                bitmap_result,
                records_result,
                applications_result,
            ]
        ),
        get=AsyncMock(side_effect=AssertionError("unexpected point query")),
    )

    context = await _load_context(
        db,
        student_id=student_id,
        session_id=session_id,
    )

    assert context is not None
    assert context.target is target
    assert context.student is student
    assert context.plan_course is plan_course
    assert db.execute.await_count == 6
    db.get.assert_not_awaited()


def test_remaining_projects_answer_reports_progress_before_candidates() -> None:
    answer = _format_remaining_projects_answer(
        {
            "summary": {"total_remaining_to_select": 2},
            "course_progress": [
                {
                    "course_name": "工程物理实验",
                    "eligible": True,
                    "required": {
                        "total": 3,
                        "selected": 2,
                        "completed": 1,
                        "arranged_not_completed": 1,
                        "remaining_to_select": 1,
                        "candidates": [{"project_name": "光电效应"}],
                    },
                    "optional": {
                        "minimum": 2,
                        "selected": 1,
                        "completed": 0,
                        "arranged_not_completed": 1,
                        "remaining_to_select": 1,
                        "candidates": [
                            {"project_name": "单摆实验"},
                            {"project_name": "振动系统频率响应"},
                        ],
                    },
                }
            ],
        }
    )

    assert "必做：已选择 2/3，还需选择 1 个" in answer
    assert "选做：已选择 1/2，还需选择 1 个" in answer
    assert "本学期还需新选择 2 个实验项目" in answer
    assert "还需选择 1 个必做项目：光电效应" in answer
    assert "从以下选做项目中选择 1 个：单摆实验、振动系统频率响应" in answer
    assert answer.index("必做：") < answer.index("本学期还需新选择")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("SELECTED", "当前已经选择了“RLC暂态过程”"),
        ("COMPLETED", "已经完成了“RLC暂态过程”"),
        ("NOT_SELECTED", "当前尚未选择“RLC暂态过程”"),
        ("ABSENT", "当前记录状态为缺席"),
        ("MAKEUP_PENDING", "正在等待补做安排完成"),
    ],
)
def test_current_selection_answer_uses_trusted_student_status(
    status: str,
    expected: str,
) -> None:
    answer = _format_current_selection_answer(
        {
            "project_name": "RLC暂态过程",
            "course_name": "工程物理实验",
            "student_status": status,
        }
    )

    assert expected in answer


def test_current_selection_answer_lists_all_selected_projects() -> None:
    answer = _format_current_selection_answer(
        {
            "selection_items": [
                {
                    "course_name": "工程物理实验",
                    "project_name": "RLC暂态过程",
                    "student_status": "SELECTED",
                },
                {
                    "course_name": "工程物理实验",
                    "project_name": "霍尔效应与磁场测量",
                    "student_status": "COMPLETED",
                },
            ]
        }
    )

    assert "共选择过 2 个实验项目" in answer
    assert "RLC暂态过程（已选）" in answer
    assert "霍尔效应与磁场测量（已完成）" in answer


def test_current_selection_answer_is_composed_by_model_from_grounded_context() -> None:
    class ContextAnswerModel:
        async def astream(self, messages):
            assert messages
            assert "RLC暂态过程" in str(messages[-1].content)
            yield SimpleNamespace(content="你本学期已经选择了 RLC暂态过程。")

    state = {
        "intent": "QUERY_CURRENT_SELECTION",
        "current_question": "我这个学期已经选了哪些实验？",
        "model": ContextAnswerModel(),
        "resolved_entities": {
            "selection_items": [
                {
                    "course_name": "工程物理实验",
                    "project_name": "RLC暂态过程",
                    "student_status": "SELECTED",
                }
            ]
        },
        "grounding_bundle": {
            "immutable_facts": {
                "base_context_selection": {
                    "selection_items": [
                        {
                            "course_name": "工程物理实验",
                            "project_name": "RLC暂态过程",
                            "student_status": "SELECTED",
                        }
                    ]
                }
            }
        },
        "tool_results": [],
    }
    result = asyncio.run(compose_answer_stream(state))

    assert result["answer"] == "你本学期已经选择了 RLC暂态过程。"


def test_current_selection_plan_uses_base_context_without_tool() -> None:
    plan = StudentAgentPlan(
        intent="QUERY_CURRENT_SELECTION",
        entity_reference=EntityReference(project_name="RLC暂态过程"),
        tool_requests=[],
    )

    assert asyncio.run(validate_plan({"plan": plan})) == {
        "plan_validation_errors": []
    }

    conservative_plan = StudentAgentPlan(
        intent="QUERY_CURRENT_SELECTION",
        entity_reference=EntityReference(project_name="RLC暂态过程"),
        tool_requests=[StudentToolRequest(name="get_training_plan_context")],
    )
    assert asyncio.run(validate_plan({"plan": conservative_plan})) == {
        "plan_validation_errors": []
    }


def test_current_selection_resolves_status_from_loaded_base_context() -> None:
    plan = StudentAgentPlan(
        intent="QUERY_CURRENT_SELECTION",
        entity_reference=EntityReference(project_name="RLC 暂态过程"),
        tool_requests=[],
    )
    result = asyncio.run(
        resolve_entities(
            {
                "plan": plan,
                "current_question": "我是否已经选择了 RLC 暂态过程？",
                "base_context": {
                    "training_plan_summary": {
                        "courses": [
                            {
                                "course_id": str(uuid4()),
                                "course_name": "工程物理实验",
                                "projects": [
                                    {
                                        "project_id": str(uuid4()),
                                        "project_name": "RLC暂态过程",
                                        "student_status": "SELECTED",
                                    }
                                ],
                            }
                        ]
                    }
                },
            }
        )
    )

    assert result["clarification_question"] is None
    assert result["resolved_entities"]["project_name"] == "RLC暂态过程"
    assert result["resolved_entities"]["student_status"] == "SELECTED"


def test_current_selection_resolves_all_selected_projects_from_context() -> None:
    plan = StudentAgentPlan(
        intent="QUERY_CURRENT_SELECTION",
        entity_reference=None,
        tool_requests=[],
    )
    result = asyncio.run(
        resolve_entities(
            {
                "plan": plan,
                "current_question": "我是否已经选择了 RLC 暂态过程？",
                "base_context": {
                    "training_plan_summary": {
                        "courses": [
                            {
                                "course_id": str(uuid4()),
                                "course_name": "工程物理实验",
                                "projects": [
                                    {
                                        "project_id": str(uuid4()),
                                        "project_name": "RLC暂态过程",
                                        "student_status": "SELECTED",
                                    },
                                    {
                                        "project_id": str(uuid4()),
                                        "project_name": "热电偶定标",
                                        "student_status": "NOT_SELECTED",
                                    },
                                ],
                            }
                        ]
                    }
                },
            }
        )
    )

    assert result["clarification_question"] is None
    assert len(result["resolved_entities"]["selection_items"]) == 1
    assert (
        result["resolved_entities"]["selection_items"][0]["project_name"]
        == "RLC暂态过程"
    )


def test_current_selection_prefers_complete_current_selection_records() -> None:
    plan = StudentAgentPlan(
        intent="QUERY_CURRENT_SELECTION",
        entity_reference=None,
        tool_requests=[],
    )
    result = asyncio.run(
        resolve_entities(
            {
                "plan": plan,
                "current_question": "我这个学期已经选了哪些实验？",
                "base_context": {
                    "current_selections": [
                        {
                            "status": "SELECTED",
                            "course_name": "工程物理实验",
                            "project_name": "手动选择项目",
                        },
                        {
                            "status": "SELECTED",
                            "course_name": "工程物理实验",
                            "project_name": "AI方案选择项目",
                        },
                    ],
                    "training_plan_summary": {"courses": []},
                },
            }
        )
    )

    names = {
        item["project_name"]
        for item in result["resolved_entities"]["selection_items"]
    }
    assert names == {"手动选择项目", "AI方案选择项目"}


def test_current_selection_validation_rejects_incomplete_model_list() -> None:
    state = {
        "intent": "QUERY_CURRENT_SELECTION",
        "answer_buffer": "你已经选择了手动选择项目。",
        "resolved_entities": {
            "selection_items": [
                {"project_name": "手动选择项目", "student_status": "SELECTED"},
                {"project_name": "AI方案选择项目", "student_status": "SELECTED"},
            ]
        },
        "tool_results": [],
    }

    result = asyncio.run(validate_final_answer(state))

    assert result["grounding_passed"] is False


def test_current_selection_filters_schedule_from_context_without_tool() -> None:
    plan = StudentAgentPlan(
        intent="QUERY_CURRENT_SELECTION",
        entity_reference=EntityReference(day_name="周一", teacher_name="李强"),
        tool_requests=[],
    )
    result = asyncio.run(
        resolve_entities(
            {
                "plan": plan,
                "current_question": "我周一有哪些李强老师的实验？",
                "base_context": {
                    "current_selections": [
                        {
                            "status": "SELECTED",
                            "course_name": "工程物理实验",
                            "project_name": "RLC暂态过程",
                            "week_no": 6,
                            "day_name": "周一",
                            "start_slot": 1,
                            "end_slot": 4,
                            "teacher_name": "李强",
                            "laboratory_name": "电学综合实验室",
                        },
                        {
                            "status": "SELECTED",
                            "course_name": "工程物理实验",
                            "project_name": "交流电桥",
                            "week_no": 7,
                            "day_name": "周三",
                            "start_slot": 5,
                            "end_slot": 8,
                            "teacher_name": "王芳",
                            "laboratory_name": "基础实验室",
                        },
                    ],
                    "training_plan_summary": {"courses": []},
                },
            }
        )
    )

    assert result["clarification_question"] is None
    assert result["resolved_entities"]["selection_scope"] == "FILTERED"
    assert result["resolved_entities"]["selection_items"] == [
        {
            "status": "SELECTED",
            "student_status": "SELECTED",
            "course_name": "工程物理实验",
            "project_name": "RLC暂态过程",
            "week_no": 6,
            "day_name": "周一",
            "start_slot": 1,
            "end_slot": 4,
            "teacher_name": "李强",
            "laboratory_name": "电学综合实验室",
        }
    ]


def test_current_selection_fallback_answer_includes_schedule_details() -> None:
    answer = _format_current_selection_answer(
        {
            "selection_items": [
                {
                    "student_status": "SELECTED",
                    "course_name": "工程物理实验",
                    "project_name": "RLC暂态过程",
                    "week_no": 6,
                    "day_name": "周一",
                    "start_slot": 1,
                    "end_slot": 4,
                    "teacher_name": "李强",
                    "laboratory_name": "电学综合实验室",
                }
            ]
        }
    )

    assert "第6周周一" in answer
    assert "第1—4节" in answer
    assert "李强老师" in answer
    assert "电学综合实验室" in answer


def test_filtered_current_selection_validation_rejects_unmatched_project() -> None:
    state = {
        "intent": "QUERY_CURRENT_SELECTION",
        "answer_buffer": "李强老师教授RLC暂态过程；另外还有交流电桥。",
        "resolved_entities": {
            "selection_scope": "FILTERED",
            "selection_items": [
                {"project_name": "RLC暂态过程", "student_status": "SELECTED"}
            ],
        },
        "base_context": {
            "current_selections": [
                {"project_name": "RLC暂态过程"},
                {"project_name": "交流电桥"},
            ]
        },
        "tool_results": [],
    }

    result = asyncio.run(validate_final_answer(state))

    assert result["grounding_passed"] is False


def test_current_selection_discards_project_inferred_only_from_context() -> None:
    plan = StudentAgentPlan(
        intent="QUERY_CURRENT_SELECTION",
        entity_reference=EntityReference(project_name="RLC暂态过程"),
        tool_requests=[],
    )
    result = asyncio.run(
        resolve_entities(
            {
                "plan": plan,
                "current_question": "我这个学期已经选了哪些实验？",
                "base_context": {
                    "training_plan_summary": {
                        "courses": [
                            {
                                "course_name": "工程物理实验",
                                "projects": [
                                    {
                                        "project_name": "RLC暂态过程",
                                        "student_status": "SELECTED",
                                    },
                                    {
                                        "project_name": "热电偶定标",
                                        "student_status": "NOT_SELECTED",
                                    },
                                ],
                            }
                        ]
                    }
                },
            }
        )
    )

    assert result["clarification_question"] is None
    assert "selection_items" in result["resolved_entities"]
    assert result["resolved_entities"]["selection_items"][0]["project_name"] == (
        "RLC暂态过程"
    )


def test_remaining_projects_answer_excludes_ineligible_course_from_count() -> None:
    answer = _format_remaining_projects_answer(
        {
            "summary": {"total_remaining_to_select": 0},
            "course_progress": [
                {
                    "course_name": "大学物理实验",
                    "eligible": False,
                    "eligibility_violations": [
                        {"message": "尚未达到培养方案要求的修读学期"}
                    ],
                    "required": {},
                    "optional": {},
                }
            ],
        }
    )

    assert "不计入当前待选数量" in answer
    assert "尚未达到培养方案要求的修读学期" in answer
    assert "当前无需再选择新的实验项目" in answer


def test_deselection_plan_accepts_natural_language_entities_and_preview_tool() -> None:
    plan = StudentAgentPlan(
        intent="DESELECT_SELECTION",
        entity_reference=EntityReference(
            course_names=["工程物理实验", "近代物理实验"],
            project_names=["超声波声速测量（模拟）"],
            teacher_name="杨静",
            week_no=3,
            day_name="周一",
            start_slot=1,
            end_slot=4,
        ),
        tool_requests=[StudentToolRequest(name="preview_deselection")],
    )

    assert asyncio.run(validate_plan({"plan": plan})) == {
        "plan_validation_errors": []
    }
    assert plan.entity_reference is not None
    assert plan.entity_reference.course_names == ["工程物理实验", "近代物理实验"]


def test_deselection_preview_requires_confirmation_in_deterministic_answer() -> None:
    answer = _deterministic_answer(
        {
            "tool_results": [
                {
                    "name": "preview_deselection",
                    "data": {
                        "requires_confirmation": True,
                        "sessions": [{"project_name": "超声波声速测量（模拟）"}],
                    },
                }
            ]
        }
    )

    assert "匹配到 1 个已选实验场次" in answer
    assert "确认取消" in answer


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


def test_teacher_preferences_are_normalized_and_equally_weighted() -> None:
    item = _recommendation_session()
    item.teacher_name = "张老师"
    preferences = SelectionPreferences(
        preferred_teacher_names=["张老师", " 李老师 ", "张"]
    )
    base_score, _, _ = _preference_score(
        item, SelectionPreferences(), student_campus="主校区"
    )
    preferred_score, reasons, _ = _preference_score(
        item, preferences, student_campus="主校区"
    )

    assert preferences.preferred_teacher_names == ["张", "李"]
    assert preferred_score == base_score + 50
    assert any("教师偏好" in reason for reason in reasons)


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
            intent="BASIC_INFO_QUERY",
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
                    '{"intent":"BASIC_INFO_QUERY",'
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

    assert result["intent"] == "BASIC_INFO_QUERY"
    assert result["tool_requests"][0].name == "get_training_plan_context"


def test_planner_reports_connection_failure_without_json_repair_retry() -> None:
    class APIConnectionError(Exception):
        pass

    class UnavailableModel:
        calls = 0

        async def ainvoke(self, messages):
            assert messages
            self.calls += 1
            raise APIConnectionError("Connection error")

    model = UnavailableModel()
    result = asyncio.run(
        plan_with_llm(
            {
                "model": model,
                "current_question": "帮我推荐选课方案",
                "conversation_context": [],
                "base_context": {},
            }
        )
    )

    assert result["model_error"] == "AI服务连接暂时失败，请稍后重试。"
    assert result["repaired_plan_attempted"] is False
    assert model.calls == 1


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


def test_general_chat_graph_streams_with_preloaded_context() -> None:
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
                "base_context": {},
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


def test_basic_info_rule_query_requires_lookup_and_topics() -> None:
    invalid = StudentAgentPlan(intent="BASIC_INFO_QUERY")
    result = asyncio.run(validate_plan({"plan": invalid}))
    assert result["model_error"] == "模型执行计划未通过安全校验。"

    valid = StudentAgentPlan(
        intent="BASIC_INFO_QUERY",
        rule_topics=["PROJECT_UNIQUENESS"],
        tool_requests=[StudentToolRequest(name="lookup_student_rules")],
    )
    state = {"plan": valid}
    assert asyncio.run(validate_plan(state)) == {"plan_validation_errors": []}
    assert route_after_plan(state) == "execute_student_tools"

    training_plan = StudentAgentPlan(
        intent="BASIC_INFO_QUERY",
        entity_reference=EntityReference(course_name="工程物理实验"),
        tool_requests=[StudentToolRequest(name="get_training_plan_context")],
    )
    training_state = {"plan": training_plan}
    assert asyncio.run(validate_plan(training_state)) == {
        "plan_validation_errors": []
    }
    assert route_after_plan(training_state) == "resolve_entities"

    remaining = StudentAgentPlan(
        intent="BASIC_INFO_QUERY",
        tool_requests=[StudentToolRequest(name="get_remaining_projects")],
    )
    assert asyncio.run(validate_plan({"plan": remaining})) == {
        "plan_validation_errors": []
    }

    unrelated = StudentAgentPlan(
        intent="BASIC_INFO_QUERY",
        tool_requests=[StudentToolRequest(name="lookup_operation_guide")],
    )
    assert "model_error" in asyncio.run(validate_plan({"plan": unrelated}))


def test_rule_query_not_found_uses_deterministic_branch() -> None:
    state = {
        "intent": "BASIC_INFO_QUERY",
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


def test_student_graph_loads_context_before_semantic_planning() -> None:
    graph_file = (
        Path(__file__).parents[2] / "app" / "agents" / "graphs" / "student_graph.py"
    )
    source = graph_file.read_text(encoding="utf-8")

    assert 'builder.add_node("load_base_context", load_base_context)' in source
    assert 'builder.add_edge("normalize_request", "load_base_context")' in source
    assert 'builder.add_edge("load_base_context", "plan_with_llm")' in source


def test_start_adjustment_plan_requires_personal_adjustment_tool() -> None:
    plan = StudentAgentPlan(
        intent="START_ADJUSTMENT",
        requested_application_type="RESCHEDULE",
        entity_reference=EntityReference(
            week_no=4,
            day_name="周一",
            start_slot=5,
            end_slot=8,
            teacher_name="李强",
        ),
        tool_requests=[StudentToolRequest(name="prepare_adjustment_entry")],
    )

    assert asyncio.run(validate_plan({"plan": plan})) == {
        "plan_validation_errors": []
    }


@pytest.mark.parametrize(
    "request_type",
    ["RESCHEDULE", "PROJECT_CHANGE", "MAKEUP"],
)
def test_prepare_adjustment_entry_matches_source_for_all_application_types(
    monkeypatch: pytest.MonkeyPatch,
    request_type: str,
) -> None:
    source = {
        "record_id": str(uuid4()),
        "status": "SELECTED",
        "available_for": [request_type],
        "session": {
            "project_name": "霍尔效应与磁场测量",
            "course_name": "工程物理实验",
            "week_no": 4,
            "day_name": "周一",
            "start_slot": 5,
            "end_slot": 8,
            "teacher_name": "李强",
            "laboratory_name": "电学综合实验室",
        },
    }

    async def fake_context(*args, **kwargs):
        assert kwargs["request_type"] is None
        return {"sources": [source]}

    monkeypatch.setattr(
        "app.agents.tools.student_tools.get_adjustment_context",
        fake_context,
    )
    plan = StudentAgentPlan(
        intent="START_ADJUSTMENT",
        requested_application_type=request_type,  # type: ignore[arg-type]
        entity_reference=EntityReference(
            project_name="霍尔效应与磁场测量",
            week_no=4,
            day_name="周一",
            start_slot=5,
            end_slot=8,
            teacher_name="李强老师",
        ),
        tool_requests=[StudentToolRequest(name="prepare_adjustment_entry")],
    )
    result = asyncio.run(
        prepare_adjustment_entry_tool(  # type: ignore[arg-type]
            None,
            student_id=uuid4(),
            term=SimpleNamespace(id=uuid4()),
            plan=plan,
            question="请帮我处理这个实验",
        )
    )

    assert result["status"] == "UNIQUE"
    assert result["request_type"] == request_type
    assert result["sources"] == [source]


def test_prepare_adjustment_entry_explains_started_source_instead_of_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        "record_id": str(uuid4()),
        "status": "SELECTED",
        "available_for": ["MAKEUP"],
        "session": {
            "project_name": "RLC暂态过程",
            "course_name": "工程物理实验",
            "requirement_type": "REQUIRED",
            "week_no": 6,
            "day_name": "周一",
            "start_slot": 1,
            "end_slot": 4,
            "teacher_name": "王芳",
            "laboratory_name": "电学综合实验室",
            "started": True,
        },
    }

    async def fake_context(*args, **kwargs):
        assert kwargs["request_type"] is None
        return {"sources": [source]}

    monkeypatch.setattr(
        "app.agents.tools.student_tools.get_adjustment_context",
        fake_context,
    )
    plan = StudentAgentPlan(
        intent="START_ADJUSTMENT",
        requested_application_type="RESCHEDULE",
        entity_reference=EntityReference(
            project_name="RLC暂态过程",
            week_no=6,
            day_name="周一",
            start_slot=1,
            end_slot=4,
            teacher_name="王芳老师",
        ),
        tool_requests=[StudentToolRequest(name="prepare_adjustment_entry")],
    )
    result = asyncio.run(
        prepare_adjustment_entry_tool(  # type: ignore[arg-type]
            None,
            student_id=uuid4(),
            term=SimpleNamespace(id=uuid4()),
            plan=plan,
            question="我想调课",
        )
    )

    assert result["status"] == "INELIGIBLE"
    assert result["sources"] == []
    assert result["matched_sources"] == [source]
    assert result["requires_confirmation"] is False
    assert result["title"] == "当前不能调课"
    assert result["message"] == (
        "已找到“RLC暂态过程”（第6周周一 第1—4节），"
        "但该场次已经开始，当前不能申请调课。"
    )


def test_project_change_explains_required_project_before_started_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        "record_id": str(uuid4()),
        "status": "SELECTED",
        "available_for": ["MAKEUP"],
        "session": {
            "project_name": "超声波声速测量",
            "course_name": "工程物理实验",
            "requirement_type": "REQUIRED",
            "week_no": 7,
            "day_name": "周三",
            "start_slot": 1,
            "end_slot": 4,
            "teacher_name": "杨静",
            "laboratory_name": "综合实验室",
            "started": True,
        },
    }

    async def fake_context(*args, **kwargs):
        return {"sources": [source]}

    monkeypatch.setattr(
        "app.agents.tools.student_tools.get_adjustment_context", fake_context
    )
    plan = StudentAgentPlan(
        intent="START_ADJUSTMENT",
        requested_application_type="PROJECT_CHANGE",
        entity_reference=EntityReference(project_name="超声波声速测量"),
        tool_requests=[StudentToolRequest(name="prepare_adjustment_entry")],
    )

    result = asyncio.run(
        prepare_adjustment_entry_tool(  # type: ignore[arg-type]
            None,
            student_id=uuid4(),
            term=SimpleNamespace(id=uuid4()),
            plan=plan,
            question="把超声波声速测量换成其他项目",
        )
    )

    assert result["status"] == "INELIGIBLE"
    assert "必做项目，不能申请换组" in result["message"]
    assert "场次已经开始" not in result["message"]


def test_project_change_without_locator_does_not_choose_arbitrary_required_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = [
        {
            "record_id": str(uuid4()),
            "status": "SELECTED",
            "available_for": ["RESCHEDULE"],
            "session": {
                "project_name": name,
                "course_name": "工程物理实验",
                "requirement_type": "REQUIRED",
                "week_no": week,
                "day_name": "周三",
                "start_slot": 1,
                "end_slot": 4,
                "started": False,
            },
        }
        for name, week in (("超声波声速测量", 7), ("交流电桥", 8))
    ]

    async def fake_context(*args, **kwargs):
        return {"sources": sources}

    monkeypatch.setattr(
        "app.agents.tools.student_tools.get_adjustment_context", fake_context
    )
    plan = StudentAgentPlan(
        intent="START_ADJUSTMENT",
        requested_application_type="PROJECT_CHANGE",
        tool_requests=[StudentToolRequest(name="prepare_adjustment_entry")],
    )

    result = asyncio.run(
        prepare_adjustment_entry_tool(  # type: ignore[arg-type]
            None,
            student_id=uuid4(),
            term=SimpleNamespace(id=uuid4()),
            plan=plan,
            question="我要更换已选项目",
        )
    )

    assert result["status"] == "INELIGIBLE"
    assert result["requires_confirmation"] is False
    assert "没有可以申请换组的项目" in result["message"]
    assert "2个是必做项目" in result["message"]
    assert "超声波声速测量" not in result["message"]
