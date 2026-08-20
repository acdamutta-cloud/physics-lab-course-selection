"""验证 planner 偏好兜底合并：模型把偏好误写进工具参数时，后端应合并进顶层 preferences。"""

from app.agents.nodes.student_advisor import _merge_tool_preference_arguments
from app.schemas.student_consultation import (
    SelectionPreferences,
    StudentAgentPlan,
    StudentToolRequest,
)


def _plan(
    *,
    top: SelectionPreferences | None = None,
    tool_preference: dict[str, object] | str | None = None,
) -> StudentAgentPlan:
    return StudentAgentPlan(
        intent="RECOMMEND_SELECTION",
        preferences=top or SelectionPreferences(),
        tool_requests=(
            [
                StudentToolRequest(
                    name="recommend_selection_plans",
                    arguments={"preference": tool_preference},
                )
            ]
            if tool_preference is not None
            else []
        ),
    )


def test_merges_missing_fields_from_tool_arguments():
    plan = _plan(
        top=SelectionPreferences(
            week_range={"start_week": 7, "start_inclusive": False}
        ),
        tool_preference={
            "preferred_teacher_names": ["李强", "王芳"],
            "preferred_categories": ["ELECTRICITY"],
            "avoid_weekend": True,
            "avoid_evening": True,
        },
    )
    merged = _merge_tool_preference_arguments(plan)
    assert merged.preferences.preferred_teacher_names == ["李强", "王芳"]
    assert merged.preferences.preferred_categories == ["ELECTRICITY"]
    assert merged.preferences.avoid_weekend is True
    assert merged.preferences.avoid_evening is True
    # 顶层已填字段保持不变
    assert merged.preferences.week_range is not None
    assert merged.preferences.week_range.start_inclusive is False


def test_top_level_fields_take_precedence():
    plan = _plan(
        top=SelectionPreferences(
            preferred_teacher_names=["王芳"], preferred_categories=["OPTICS"]
        ),
        tool_preference={
            "preferred_teacher_names": ["李强"],
            "preferred_categories": ["ELECTRICITY"],
        },
    )
    merged = _merge_tool_preference_arguments(plan)
    assert merged.preferences.preferred_teacher_names == ["王芳"]
    assert merged.preferences.preferred_categories == ["OPTICS"]


def test_json_string_preference_is_supported():
    plan = _plan(
        tool_preference='{"preferred_teacher_names":["李强"]}',
    )
    merged = _merge_tool_preference_arguments(plan)
    assert merged.preferences.preferred_teacher_names == ["李强"]


def test_ignores_invalid_preference_arguments():
    plan = _plan(
        top=SelectionPreferences(preferred_teacher_names=["王芳"]),
        tool_preference={"preferred_teacher_names": [1, 2]},
    )
    merged = _merge_tool_preference_arguments(plan)
    assert merged.preferences.preferred_teacher_names == ["王芳"]


def test_ignores_unrelated_tool_arguments():
    plan = StudentAgentPlan(
        intent="RECOMMEND_SELECTION",
        preferences=SelectionPreferences(),
        tool_requests=[
            StudentToolRequest(name="recommend_selection_plans", arguments={}),
            StudentToolRequest(
                name="get_training_plan_context", arguments={"preference": {"avoid_weekend": True}}
            ),
        ],
    )
    merged = _merge_tool_preference_arguments(plan)
    assert merged.preferences.avoid_weekend is False
