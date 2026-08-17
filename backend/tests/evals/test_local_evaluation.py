from __future__ import annotations

import asyncio

from evals import run_evaluation
from evals.dataset_manager import load_cases


def test_local_runner_executes_target_and_evaluators_without_langsmith(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(run_evaluation, "REPORT_DIR", tmp_path)
    cases = load_cases("smoke")[:2]

    async def target(inputs):
        del inputs
        return {"answer": "本地回答", "intent": "GENERAL_CHAT"}

    def evaluator(run, example):
        del run, example
        return {"key": "local_metric", "score": 1.0}

    rows = asyncio.run(
        run_evaluation._run_local_experiment(
            cases=cases,
            evaluators=[evaluator],
            max_concurrency=2,
            repetitions=1,
            target=target,
        )
    )

    assert len(rows) == 2
    assert all(row["run"]["outputs"]["answer"] == "本地回答" for row in rows)
    assert all(
        row["evaluation_results"]["results"][0]["key"] == "local_metric"
        for row in rows
    )
    progress = (tmp_path / "local-progress.jsonl").read_text(encoding="utf-8")
    assert progress.count("本地回答") == 2


def test_local_runner_records_evaluator_error_and_continues() -> None:
    case = load_cases("smoke")[0]

    async def target(inputs):
        del inputs
        return {"answer": "回答", "intent": "GENERAL_CHAT"}

    def broken_evaluator(run, example):
        del run, example
        raise RuntimeError("裁判暂不可用")

    row = asyncio.run(
        run_evaluation._run_local_case(
            case,
            evaluators=[broken_evaluator],
            target=target,
        )
    )

    feedback = row["evaluation_results"]["results"][0]
    assert feedback["key"] == "evaluator_error_broken_evaluator"
    assert "裁判暂不可用" in feedback["comment"]
