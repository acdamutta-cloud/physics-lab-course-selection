import asyncio

import pytest

from app.agents.nodes.student_advisor import build_cards, validate_plan
from app.schemas.student_consultation import StudentAgentPlan, StudentToolRequest
from app.services.operation_guide_service import bm25_search, search_operation_guides


def test_bm25_finds_schedule_export_guide() -> None:
    results = bm25_search("这张实验课表怎么导出PDF")

    assert results
    assert results[0][0] == "STUDENT-SCHEDULE-003"


def test_bm25_distinguishes_selected_reschedule_from_plan_editing() -> None:
    results = bm25_search("已经选课后时间不合适怎么申请调课")

    assert results
    assert results[0][0] == "STUDENT-ADJUST-001"


def test_bm25_finds_each_application_guide_from_natural_how_to_question() -> None:
    expected = {
        "如果我想调课应该怎么操作": "STUDENT-ADJUST-001",
        "如果我想换组应该怎么操作": "STUDENT-ADJUST-002",
        "如果我想补做应该怎么操作": "STUDENT-MAKEUP-001",
    }

    for question, guide_id in expected.items():
        results = bm25_search(question)
        assert results
        assert results[0][0] == guide_id


@pytest.mark.parametrize("question", ["怎么退选", "如何退选", "退选步骤是什么"])
def test_bm25_finds_ai_deselection_guide_for_how_to_questions(question: str) -> None:
    results = bm25_search(question)

    assert results
    assert results[0][0] == "STUDENT-AI-DROP-001"


def test_hybrid_search_prefers_exact_ai_deselection_question(monkeypatch) -> None:
    async def fake_vector_search(session, query, *, limit=10):
        del session, query, limit
        return [("STUDENT-SELECTION-004", 0.99), ("STUDENT-AI-DROP-001", 0.70)]

    monkeypatch.setattr(
        "app.services.operation_guide_service._vector_search", fake_vector_search
    )
    result = asyncio.run(search_operation_guides(None, query="怎么退选？"))  # type: ignore[arg-type]

    assert result["status"] == "FOUND"
    assert result["guide"]["guide_id"] == "STUDENT-AI-DROP-001"


@pytest.mark.parametrize(
    ("question", "guide_id"),
    [
        ("在哪里看我的申请审核到哪一步？", "STUDENT-APPLICATION-001"),
        ("怎么看系统发给我的通知？", "STUDENT-NOTICE-001"),
    ],
)
def test_bm25_finds_tracking_and_notification_guides(
    question: str, guide_id: str
) -> None:
    results = bm25_search(question)

    assert results
    assert results[0][0] == guide_id


def test_how_to_deselect_plan_cannot_call_preview_tool() -> None:
    plan = StudentAgentPlan(
        intent="DESELECT_SELECTION",
        request_mode="ASK_STEPS",
        tool_requests=[StudentToolRequest(name="preview_deselection")],
    )

    result = asyncio.run(validate_plan({"plan": plan}))
    assert any("询问能力或步骤" in error for error in result["plan_validation_errors"])


def test_plan_draft_cannot_use_enrolled_adjustment_tool() -> None:
    plan = StudentAgentPlan(
        intent="START_ADJUSTMENT",
        request_mode="EXECUTE",
        operation_stage="PLAN_DRAFT",
        tool_requests=[StudentToolRequest(name="prepare_adjustment_entry")],
    )

    result = asyncio.run(validate_plan({"plan": plan}))
    assert any("方案草稿" in error for error in result["plan_validation_errors"])


def test_plan_draft_clarification_removes_tool_and_plan_label_entity() -> None:
    plan = StudentAgentPlan(
        intent="START_ADJUSTMENT",
        request_mode="EXECUTE",
        operation_stage="PLAN_DRAFT",
        entity_reference={"project_name": "方案1"},
        needs_clarification=True,
        clarification_question="请说明要更换哪个选做项目。",
        tool_requests=[StudentToolRequest(name="prepare_adjustment_entry")],
    )

    result = asyncio.run(validate_plan({"plan": plan}))
    cleaned = result["plan"]
    assert cleaned.tool_requests == []
    assert cleaned.entity_reference is not None
    assert cleaned.entity_reference.project_name is None


@pytest.mark.parametrize(
    ("question", "guide_id"),
    [
        ("如何进行选课", "STUDENT-SELECTION-000"),
        ("如何进行选课？", "STUDENT-SELECTION-000"),
        ("如何手动进行选课", "STUDENT-SELECTION-001"),
        ("个人操作选课", "STUDENT-SELECTION-001"),
    ],
)
def test_bm25_distinguishes_general_and_manual_selection_guides(
    question: str,
    guide_id: str,
) -> None:
    results = bm25_search(question)

    assert results
    assert results[0][0] == guide_id


def test_makeup_answer_describes_teacher_then_admin_review() -> None:
    result = asyncio.run(
        search_operation_guides(  # type: ignore[arg-type]
            None,
            query="补做是不是老师同意就成功了，管理员还要审核吗",
        )
    )

    assert result["status"] == "FOUND"
    assert "原场次任课教师审核" in str(result["answer"])
    assert "管理员复核" in str(result["answer"])


def test_reschedule_approval_question_returns_direct_automatic_rule() -> None:
    results = bm25_search("调课是否需要经过人工审批")

    assert results
    assert results[0][0] == "STUDENT-ADJUST-003"

    result = asyncio.run(
        search_operation_guides(  # type: ignore[arg-type]
            None,
            query="调课是否需要经过人工审批",
        )
    )

    assert result["status"] == "FOUND"
    assert "不需要人工审批" in str(result["answer"])
    assert "自动执行调课" in str(result["answer"])
    assert "教师发起的整场调课" in str(result["answer"])


def test_project_change_approval_question_returns_admin_workflow() -> None:
    results = bm25_search("实验换组的审批流程")

    assert results
    assert results[0][0] == "STUDENT-ADJUST-004"

    result = asyncio.run(
        search_operation_guides(  # type: ignore[arg-type]
            None,
            query="实验换组的审批流程",
        )
    )

    assert result["status"] == "FOUND"
    assert "由管理员进行审批" in str(result["answer"])
    assert "审批期间原实验项目和场次保持不变" in str(result["answer"])
    assert "换组才会生效" in str(result["answer"])


def test_system_guide_plan_requires_read_only_guide_tool() -> None:
    plan = StudentAgentPlan(
        intent="SYSTEM_GUIDE",
        tool_requests=[StudentToolRequest(name="lookup_operation_guide")],
    )

    assert asyncio.run(validate_plan({"plan": plan})) == {
        "plan_validation_errors": []
    }


def test_application_entry_is_structured_by_planner_not_frontend_matching() -> None:
    plan = StudentAgentPlan(
        intent="SYSTEM_GUIDE",
        requested_application_type="PROJECT_CHANGE",
        tool_requests=[StudentToolRequest(name="lookup_operation_guide")],
    )

    assert asyncio.run(validate_plan({"plan": plan})) == {
        "plan_validation_errors": []
    }


def test_approval_guide_never_requests_application_dialog() -> None:
    plan = StudentAgentPlan(
        intent="SYSTEM_GUIDE",
        requested_application_type="PROJECT_CHANGE",
        tool_requests=[StudentToolRequest(name="lookup_operation_guide")],
    )
    result = asyncio.run(
        build_cards(
            {
                "plan": plan,
                "tool_results": [
                    {
                        "name": "lookup_operation_guide",
                        "data": {
                            "guide": {"topic": "PROJECT_CHANGE_APPROVAL"},
                            "matches": [
                                {
                                    "title": "了解实验换组的审批流程",
                                    "topic": "PROJECT_CHANGE_APPROVAL",
                                }
                            ],
                            "source": "学生端操作指南",
                        },
                    }
                ],
            }
        )
    )

    assert result["cards"][0].data["requested_application_type"] is None


def test_explicit_application_guide_keeps_dialog_request() -> None:
    plan = StudentAgentPlan(
        intent="SYSTEM_GUIDE",
        requested_application_type="PROJECT_CHANGE",
        tool_requests=[StudentToolRequest(name="lookup_operation_guide")],
    )
    result = asyncio.run(
        build_cards(
            {
                "plan": plan,
                "tool_results": [
                    {
                        "name": "lookup_operation_guide",
                        "data": {
                            "guide": {"topic": "PROJECT_CHANGE_APPLICATION"},
                            "matches": [
                                {
                                    "title": "申请更换已选选做项目",
                                    "topic": "PROJECT_CHANGE_APPLICATION",
                                }
                            ],
                            "source": "学生端操作指南",
                        },
                    }
                ],
            }
        )
    )

    assert (
        result["cards"][0].data["requested_application_type"]
        == "PROJECT_CHANGE"
    )
