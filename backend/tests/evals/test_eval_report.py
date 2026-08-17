from __future__ import annotations

import csv
from types import SimpleNamespace

from evals import report


def test_review_report_places_question_reference_and_answer_together(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(report, "REPORT_DIR", tmp_path)
    result = SimpleNamespace(
        example=SimpleNamespace(
            inputs={"messages": [{"role": "user", "content": "我选了哪些实验？"}]},
            outputs={
                "expected_intent": "QUERY_CURRENT_SELECTION",
                "expected_facts": ["交流电桥"],
                "expected_tools": [],
            },
            metadata={"case_id": "context-001", "category": "CONTEXT"},
        ),
        run=SimpleNamespace(
            outputs={
                "intent": "QUERY_CURRENT_SELECTION",
                "answer": "你已选择交流电桥。",
                "tool_requests": [],
            },
            error=None,
        ),
        evaluation_results={
            "results": [{"key": "intent_accuracy", "score": 1.0}]
        },
    )

    paths = report.write_local_reports([result], experiment_name="unit-test")

    with paths["review"].open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["case_id"] == "context-001"
    assert row["question"] == "我选了哪些实验？"
    assert row["model_answer"] == "你已选择交流电桥。"
    assert "交流电桥" in row["expected_facts"]
    assert "intent_accuracy" in row["feedback_details"]
    assert paths["html"].exists()
    html_text = paths["html"].read_text(encoding="utf-8")
    assert "我选了哪些实验？" in html_text
    assert "你已选择交流电桥。" in html_text
