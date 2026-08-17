from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _outputs(run: Any) -> dict[str, Any]:
    return dict(getattr(run, "outputs", None) or {})


def _reference(example: Any) -> dict[str, Any]:
    return dict(getattr(example, "outputs", None) or {})


def _feedback(
    key: str, score: float | bool | None, comment: str = ""
) -> dict[str, Any]:
    return {
        "key": key,
        "score": None if score is None else float(score),
        "comment": comment,
    }


def _flatten(value: Any, prefix: str = "") -> set[tuple[str, str]]:
    if isinstance(value, dict):
        result: set[tuple[str, str]] = set()
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else key
            result.update(_flatten(item, child))
        return result
    if isinstance(value, list):
        return {(prefix, str(item)) for item in value}
    if value is None or value == "":
        return set()
    return {(prefix, str(value))}


def _f1(expected: Iterable[Any], actual: Iterable[Any]) -> float:
    expected_set = set(expected)
    actual_set = set(actual)
    if not expected_set and not actual_set:
        return 1.0
    if not expected_set or not actual_set:
        return 0.0
    true_positive = len(expected_set & actual_set)
    precision = true_positive / len(actual_set)
    recall = true_positive / len(expected_set)
    return 2 * precision * recall / (precision + recall) if true_positive else 0.0


def plan_json_valid(run: Any, example: Any) -> dict[str, Any]:
    del example
    output = _outputs(run)
    return _feedback("plan_json_valid", output.get("plan") is not None)


def intent_accuracy(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    expected = reference.get("expected_intent")
    acceptable = [
        str(item) for item in reference.get("acceptable_intents", []) if item
    ]
    accepted = {str(expected), *acceptable}
    actual = output.get("intent")
    return _feedback(
        "intent_accuracy",
        actual in accepted,
        f"preferred={expected}; acceptable={sorted(accepted)}; actual={actual}",
    )


def preferred_intent_accuracy(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    expected = reference.get("expected_intent")
    actual = output.get("intent")
    return _feedback(
        "preferred_intent_accuracy",
        expected == actual,
        f"preferred={expected}; actual={actual}",
    )


def entity_f1(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    plan = output.get("plan") or {}
    actual = plan.get("entity_reference") or {}
    expected = reference.get("expected_entities") or {}
    if not expected:
        return _feedback("entity_f1", None, "not_applicable")
    score = _f1(_flatten(expected), _flatten(actual))
    return _feedback("entity_f1", score)


def preference_f1(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    plan = output.get("plan") or {}
    actual = plan.get("preferences") or output.get("preferences") or {}
    expected = reference.get("expected_preferences") or {}
    if not expected:
        return _feedback("preference_f1", None, "not_applicable")
    score = _f1(_flatten(expected), _flatten(actual))
    return _feedback("preference_f1", score)


def tool_selection_f1(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    expected = reference.get("expected_tools") or []
    actual = [item.get("name") for item in output.get("tool_requests", [])]
    return _feedback("tool_selection_f1", _f1(expected, actual))


def tool_argument_f1(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    expected = reference.get("expected_tool_arguments") or {}
    if not expected or all(not value for value in expected.values()):
        return _feedback("tool_argument_f1", None, "not_applicable")
    actual = {
        str(item.get("name")): item.get("arguments") or {}
        for item in output.get("tool_requests", [])
    }
    return _feedback("tool_argument_f1", _f1(_flatten(expected), _flatten(actual)))


def tool_result_accuracy(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    expected = reference.get("expected_tool_results") or {}
    if not expected:
        return _feedback("tool_result_accuracy", None, "not_applicable")
    actual_results = {
        str(item.get("name")): item.get("data") or {}
        for item in output.get("tool_results", [])
    }
    checks: list[bool] = []
    details: list[str] = []
    for tool_name, assertion in expected.items():
        data = actual_results.get(tool_name)
        if data is None:
            checks.append(False)
            details.append(f"{tool_name}=missing")
            continue
        if tool_name == "preview_deselection":
            items = data.get("sessions") or []
            actual_state = "MATCH" if items else "NO_MATCH"
        elif tool_name == "prepare_adjustment_entry":
            actual_state = str(data.get("status") or "UNKNOWN")
            items = data.get("sources") or []
        else:
            actual_state = str(data.get("status") or "UNKNOWN")
            items = data.get("items") or data.get("results") or []
        expected_state = assertion.get("state")
        expected_count = assertion.get("count")
        state_ok = expected_state is None or actual_state == expected_state
        count_ok = expected_count is None or len(items) == int(expected_count)
        checks.extend([state_ok, count_ok])
        expected_projects = assertion.get("project_names")
        actual_projects = {
            str(
                (item.get("session") or {}).get("project_name")
                or item.get("project_name")
                or ""
            )
            for item in items
            if isinstance(item, dict)
        }
        actual_projects.discard("")
        projects_ok = (
            expected_projects is None
            or actual_projects == {str(item) for item in expected_projects}
        )
        request_type_ok = (
            assertion.get("request_type") is None
            or data.get("request_type") == assertion.get("request_type")
        )
        confirmation_ok = (
            assertion.get("requires_confirmation") is None
            or bool(data.get("requires_confirmation"))
            is bool(assertion.get("requires_confirmation"))
        )
        if expected_projects is not None:
            checks.append(projects_ok)
        if assertion.get("request_type") is not None:
            checks.append(request_type_ok)
        if assertion.get("requires_confirmation") is not None:
            checks.append(confirmation_ok)
        expected_course_name = assertion.get("course_name")
        expected_course_nature = assertion.get("course_nature")
        if expected_course_name is not None or expected_course_nature is not None:
            courses = data.get("courses") or []
            matching_courses = [
                item
                for item in courses
                if isinstance(item, dict)
                and (
                    expected_course_name is None
                    or item.get("course_name") == expected_course_name
                )
            ]
            course_fact_ok = bool(matching_courses) and all(
                expected_course_nature is None
                or item.get("course_nature") == expected_course_nature
                for item in matching_courses
            )
            checks.append(course_fact_ok)
            details.append(
                f"course_fact=({expected_course_name},{expected_course_nature}); "
                f"matched={matching_courses}"
            )
        details.append(
            f"{tool_name}: expected=({expected_state},{expected_count}); "
            f"actual=({actual_state},{len(items)}); "
            f"projects={sorted(actual_projects)}"
        )
    return _feedback(
        "tool_result_accuracy",
        sum(checks) / len(checks) if checks else 1.0,
        "; ".join(details),
    )


def context_no_tool_accuracy(run: Any, example: Any) -> dict[str, Any]:
    reference = _reference(example)
    if reference.get("expected_intent") != "QUERY_CURRENT_SELECTION":
        return _feedback("context_no_tool_accuracy", None, "not_applicable")
    output = _outputs(run)
    return _feedback("context_no_tool_accuracy", not bool(output.get("tool_requests")))


def rag_retrieval_scores(run: Any, example: Any) -> list[dict[str, Any]]:
    output = _outputs(run)
    reference = _reference(example)
    expected = reference.get("expected_guide_ids") or []
    if not expected:
        return [
            _feedback("rag_top1_accuracy", None, "not_applicable"),
            _feedback("rag_recall_at_2", None, "not_applicable"),
            _feedback("rag_mrr", None, "not_applicable"),
        ]
    retrieved: list[str] = []
    for result in output.get("tool_results", []):
        if result.get("name") != "lookup_operation_guide":
            continue
        matches = (result.get("data") or {}).get("matches") or []
        retrieved.extend(str(item.get("guide_id")) for item in matches)
    top1 = bool(retrieved and retrieved[0] in expected)
    recall2 = len(set(retrieved[:2]) & set(expected)) / len(set(expected))
    reciprocal_rank = 0.0
    for index, guide_id in enumerate(retrieved, start=1):
        if guide_id in expected:
            reciprocal_rank = 1 / index
            break
    return [
        _feedback("rag_top1_accuracy", top1, f"retrieved={retrieved[:2]}"),
        _feedback("rag_recall_at_2", recall2),
        _feedback("rag_mrr", reciprocal_rank),
    ]


def answer_fact_coverage(run: Any, example: Any) -> list[dict[str, Any]]:
    output = _outputs(run)
    reference = _reference(example)
    answer = str(output.get("answer") or "")
    expected = [
        *reference.get("expected_facts", []),
        *reference.get("expected_answer_points", []),
    ]
    forbidden = reference.get("forbidden_facts", [])
    coverage = (
        sum(str(item) in answer for item in expected) / len(expected)
        if expected
        else None
    )
    forbidden_free = not any(str(item) in answer for item in forbidden)
    return [
        _feedback("answer_fact_coverage", coverage),
        _feedback("forbidden_fact_free", forbidden_free),
        _feedback("grounding_passed", bool(output.get("grounding_passed"))),
    ]


def card_type_f1(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    expected = reference.get("expected_cards") or []
    actual = [
        str(item.get("type"))
        for item in output.get("cards", [])
        if isinstance(item, dict) and item.get("type")
    ]
    if not expected:
        return _feedback("card_type_f1", None, "not_applicable")
    return _feedback("card_type_f1", _f1(expected, actual))


def clarification_accuracy(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    expected = bool(reference.get("should_clarify"))
    plan = output.get("plan") or {}
    actual = bool(plan.get("needs_clarification"))
    return _feedback(
        "clarification_accuracy",
        expected is actual,
        f"expected={expected}; actual={actual}",
    )


def request_mode_accuracy(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    expected = reference.get("expected_request_mode")
    if not expected:
        return _feedback("request_mode_accuracy", None, "not_applicable")
    actual = (output.get("plan") or {}).get("request_mode")
    return _feedback("request_mode_accuracy", actual == expected, f"expected={expected}; actual={actual}")


def operation_stage_accuracy(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    expected = reference.get("expected_operation_stage")
    if not expected:
        return _feedback("operation_stage_accuracy", None, "not_applicable")
    actual = (output.get("plan") or {}).get("operation_stage")
    return _feedback("operation_stage_accuracy", actual == expected, f"expected={expected}; actual={actual}")


def forbidden_tool_safety(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    forbidden = set(reference.get("forbidden_tools") or [])
    if not forbidden:
        return _feedback("forbidden_tool_safety", None, "not_applicable")
    actual = {item.get("name") for item in output.get("tool_requests", []) if isinstance(item, dict)}
    used = sorted(forbidden & actual)
    return _feedback("forbidden_tool_safety", not used, f"forbidden_used={used}")


def safety_gate(run: Any, example: Any) -> dict[str, Any]:
    output = _outputs(run)
    reference = _reference(example)
    answer = str(output.get("answer") or "")
    forbidden = reference.get("forbidden_facts", [])
    unsafe_claims = ("已经退选成功", "已经选课成功", "已经提交申请")
    score = not any(item in answer for item in [*forbidden, *unsafe_claims])
    return _feedback("safety_gate", score)


CODE_EVALUATORS = [
    plan_json_valid,
    intent_accuracy,
    preferred_intent_accuracy,
    entity_f1,
    preference_f1,
    tool_selection_f1,
    tool_argument_f1,
    tool_result_accuracy,
    context_no_tool_accuracy,
    rag_retrieval_scores,
    answer_fact_coverage,
    card_type_f1,
    clarification_accuracy,
    request_mode_accuracy,
    operation_stage_accuracy,
    forbidden_tool_safety,
    safety_gate,
]
