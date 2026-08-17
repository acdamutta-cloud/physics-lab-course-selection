from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from evals.anonymizer import anonymize_payload, assert_payload_is_safe
from evals.dataset_manager import load_cases, validate_dataset
from evals.evaluators.code import (
    card_type_f1,
    clarification_accuracy,
    context_no_tool_accuracy,
    intent_accuracy,
    preferred_intent_accuracy,
    preference_f1,
    rag_retrieval_scores,
    request_mode_accuracy,
    operation_stage_accuracy,
    forbidden_tool_safety,
    tool_argument_f1,
    tool_result_accuracy,
    tool_selection_f1,
)


def test_full_dataset_has_expected_distribution() -> None:
    cases = load_cases("full")
    summary = validate_dataset(cases)

    assert summary["total"] == 300
    assert summary["categories"] == {
        "RAG": 80,
        "CONTEXT": 80,
        "TOOL": 110,
        "ROUTING_SAFETY": 30,
    }
    assert summary["robustness"]["NONE"] == 180
    assert (
        sum(count for kind, count in summary["robustness"].items() if kind != "NONE")
        == 120
    )
    assert summary["missing_pairs"] == []


def test_all_case_ids_are_unique_and_each_category_has_smoke_cases() -> None:
    cases = load_cases("full")
    ids = [case.metadata.case_id for case in cases]
    smoke_categories = Counter(
        case.metadata.category for case in cases if case.metadata.split == "smoke"
    )

    assert len(ids) == len(set(ids))
    assert set(smoke_categories) == {"RAG", "CONTEXT", "TOOL", "ROUTING_SAFETY"}
    assert 25 <= sum(smoke_categories.values()) <= 40


def test_context_cases_never_expect_business_tools() -> None:
    for case in load_cases("context"):
        assert case.reference_outputs.expected_intent == "QUERY_CURRENT_SELECTION"
        assert case.reference_outputs.expected_tools == []


def test_context_reference_answers_match_fixed_fixture() -> None:
    cases = {case.metadata.case_id: case for case in load_cases("context")}
    assert cases["context-003"].reference_outputs.expected_facts == [
        "RLC暂态过程",
        "第6周",
        "周一",
        "第1",
        "第4",
        "李强",
        "电学综合实验室 B102",
    ]
    assert cases["context-006"].reference_outputs.expected_answer_points == ["没有"]
    assert cases["context-015"].reference_outputs.expected_answer_points == ["没有"]


def test_all_rag_cases_have_retrieval_and_answer_references() -> None:
    for case in load_cases("rag"):
        assert case.reference_outputs.expected_guide_ids
        assert case.reference_outputs.expected_answer_points
        assert case.reference_outputs.expected_request_mode in {"ASK_CAPABILITY", "ASK_STEPS"}
        assert "preview_deselection" in case.reference_outputs.forbidden_tools
        assert "prepare_adjustment_entry" in case.reference_outputs.forbidden_tools


def test_state_dependent_tool_cases_declare_database_fixtures() -> None:
    stateful_tools = {"preview_deselection", "prepare_adjustment_entry"}
    stateful = [
        case
        for case in load_cases("tool")
        if stateful_tools & set(case.reference_outputs.expected_tools)
    ]

    assert stateful
    assert all(case.inputs.database_fixture_id for case in stateful)
    assert all(case.reference_outputs.expected_tool_results for case in stateful)
    assert {case.inputs.database_fixture_id for case in stateful} == {
        "selected_standard",
        "no_selections",
    }
    assert all(
        not case.reference_outputs.should_clarify
        for case in stateful
        if case.reference_outputs.expected_tool_results
    )


def test_anonymizer_removes_identifiers_and_tokens() -> None:
    payload = {
        "student_no": "D2024010001",
        "message": "student D2024010001 id 123e4567-e89b-12d3-a456-426614174000",
        "authorization": "Bearer secret-value",
    }
    safe = anonymize_payload(payload)

    assert safe["student_no"] == "<REDACTED>"
    assert "<STUDENT_NO>" in safe["message"]
    assert "<UUID>" in safe["message"]
    assert_payload_is_safe(safe)


def test_code_evaluators_score_expected_trace() -> None:
    run = SimpleNamespace(
        outputs={
            "intent": "SYSTEM_GUIDE",
            "tool_requests": [{"name": "lookup_operation_guide", "arguments": {}}],
            "tool_results": [
                {
                    "name": "lookup_operation_guide",
                    "data": {
                        "matches": [
                            {"guide_id": "STUDENT-SELECTION-001"},
                            {"guide_id": "STUDENT-SELECTION-000"},
                        ]
                    },
                }
            ],
        }
    )
    example = SimpleNamespace(
        outputs={
            "expected_intent": "SYSTEM_GUIDE",
            "expected_tools": ["lookup_operation_guide"],
            "expected_guide_ids": ["STUDENT-SELECTION-001"],
        }
    )

    assert intent_accuracy(run, example)["score"] == 1
    assert preferred_intent_accuracy(run, example)["score"] == 1
    assert tool_selection_f1(run, example)["score"] == 1
    rag_scores = rag_retrieval_scores(run, example)
    assert all(item["score"] == 1 for item in rag_scores)
    assert context_no_tool_accuracy(run, example)["score"] is None


def test_optional_preferences_and_tool_arguments_are_not_penalized() -> None:
    run = SimpleNamespace(
        outputs={
            "plan": {"preferences": {"avoid_weekends": False}},
            "preferences": {"avoid_weekends": False},
            "tool_requests": [
                {"name": "lookup_operation_guide", "arguments": {"query": "选课"}}
            ],
        }
    )
    example = SimpleNamespace(
        outputs={
            "expected_preferences": {},
            "expected_tool_arguments": {"lookup_operation_guide": {}},
        }
    )

    assert preference_f1(run, example)["score"] is None
    assert tool_argument_f1(run, example)["score"] is None


def test_acceptable_intent_does_not_hide_preferred_intent_difference() -> None:
    run = SimpleNamespace(outputs={"intent": "GENERAL_CHAT"})
    example = SimpleNamespace(
        outputs={
            "expected_intent": "UNKNOWN",
            "acceptable_intents": ["GENERAL_CHAT"],
        }
    )

    assert intent_accuracy(run, example)["score"] == 1
    assert preferred_intent_accuracy(run, example)["score"] == 0


def test_card_and_clarification_evaluators() -> None:
    run = SimpleNamespace(
        outputs={
            "cards": [{"type": "APPLICATION_ENTRY"}],
            "plan": {"needs_clarification": True},
        }
    )
    example = SimpleNamespace(
        outputs={
            "expected_cards": ["APPLICATION_ENTRY"],
            "should_clarify": True,
        }
    )

    assert card_type_f1(run, example)["score"] == 1
    assert clarification_accuracy(run, example)["score"] == 1


def test_semantic_mode_stage_and_forbidden_tool_evaluators() -> None:
    run = SimpleNamespace(
        outputs={
            "plan": {"request_mode": "ASK_STEPS", "operation_stage": "PLAN_DRAFT"},
            "tool_requests": [{"name": "lookup_operation_guide", "arguments": {}}],
        }
    )
    example = SimpleNamespace(
        outputs={
            "expected_request_mode": "ASK_STEPS",
            "expected_operation_stage": "PLAN_DRAFT",
            "forbidden_tools": ["preview_deselection", "prepare_adjustment_entry"],
        }
    )

    assert request_mode_accuracy(run, example)["score"] == 1
    assert operation_stage_accuracy(run, example)["score"] == 1
    assert forbidden_tool_safety(run, example)["score"] == 1


def test_tool_result_accuracy_distinguishes_match_and_no_match() -> None:
    matched_run = SimpleNamespace(
        outputs={
            "tool_results": [
                {
                    "name": "preview_deselection",
                    "data": {
                        "sessions": [
                            {
                                "session_id": "fixture-session",
                                "project_name": "交流电桥",
                            }
                        ],
                        "requires_confirmation": True,
                    },
                }
            ]
        }
    )
    matched_example = SimpleNamespace(
        outputs={
            "expected_tool_results": {
                "preview_deselection": {
                    "state": "MATCH",
                    "count": 1,
                    "project_names": ["交流电桥"],
                    "requires_confirmation": True,
                }
            }
        }
    )
    empty_run = SimpleNamespace(
        outputs={
            "tool_results": [
                {
                    "name": "preview_deselection",
                    "data": {"sessions": [], "message": "没有可取消的记录"},
                }
            ]
        }
    )
    empty_example = SimpleNamespace(
        outputs={
            "expected_tool_results": {
                "preview_deselection": {"state": "NO_MATCH", "count": 0}
            }
        }
    )

    assert tool_result_accuracy(matched_run, matched_example)["score"] == 1
    assert tool_result_accuracy(empty_run, empty_example)["score"] == 1
