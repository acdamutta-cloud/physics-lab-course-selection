from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
load_dotenv(BACKEND_ROOT / ".env")

from evals.anonymizer import anonymize_payload
from evals.config import REPORT_DIR, EvalSettings
from evals.dataset_manager import (
    dump_validation_report,
    load_cases,
    sync_cases_to_langsmith,
    validate_dataset,
)
from evals.evaluators.code import CODE_EVALUATORS
from evals.evaluators.judge import deepseek_quality_judge
from evals.report import write_local_reports
from evals.target import student_consultation_target


def _configured_model_name() -> str:
    provider = os.getenv("MODEL_PROVIDER", "mock").lower()
    variables = {
        "dashscope": "DASHSCOPE_MODEL",
        "huggingface": "HUGGINGFACE_MODEL",
        "deepseek": "DEEPSEEK_MODEL",
    }
    variable = variables.get(provider)
    return os.getenv(variable, provider) if variable else provider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行学生端AI智能咨询本地或LangSmith评测")
    parser.add_argument(
        "--suite",
        choices=("full", "smoke", "rag", "context", "tool", "routing"),
        default="smoke",
    )
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--sync-only", action="store_true")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="兼容参数；当前默认即为纯本地评测，不连接或上传LangSmith",
    )
    parser.add_argument(
        "--langsmith",
        action="store_true",
        help="显式启用LangSmith云端评测和Trace上传",
    )
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--experiment-prefix")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument(
        "--max-concurrency",
        type=int,
        help="本次评测最大并发数；未指定时读取 EVAL_MAX_CONCURRENCY（默认4）",
    )
    parser.add_argument("--limit", type=int, help="仅运行前N条，用于连通性验证")
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="仅运行指定case ID；可重复传入，用于复测失败样本",
    )
    return parser.parse_args()


def _flatten_feedback(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else [value]
    return [dict(item) for item in items if isinstance(item, dict)]


async def _run_local_case(
    case: Any,
    *,
    evaluators: list[Any],
    target: Any = student_consultation_target,
) -> dict[str, Any]:
    inputs = case.inputs.model_dump(mode="json")
    reference = case.reference_outputs.model_dump(mode="json")
    metadata = case.metadata.model_dump(mode="json")
    output: dict[str, Any] = {}
    target_error = ""
    try:
        output = await target(inputs)
    except Exception as error:  # noqa: BLE001 - 单条失败不能中断整批
        target_error = f"{type(error).__name__}: {error}"
    run = SimpleNamespace(outputs=output, error=target_error or None)
    example = SimpleNamespace(inputs=inputs, outputs=reference, metadata=metadata)
    feedback: list[dict[str, Any]] = []
    if target_error:
        feedback.append(
            {"key": "target_execution", "score": 0.0, "comment": target_error}
        )
    else:
        for evaluator in evaluators:
            try:
                value = await asyncio.to_thread(evaluator, run, example)
                feedback.extend(_flatten_feedback(value))
            except Exception as error:  # noqa: BLE001 - 记录裁判错误后继续
                feedback.append(
                    {
                        "key": f"evaluator_error_{evaluator.__name__}",
                        "score": None,
                        "comment": f"{type(error).__name__}: {error}",
                    }
                )
    return {
        "run": {"outputs": output, "error": target_error or None},
        "example": {
            "inputs": inputs,
            "outputs": reference,
            "metadata": metadata,
        },
        "evaluation_results": {"results": feedback},
    }


async def _run_local_experiment(
    *,
    cases: list[Any],
    evaluators: list[Any],
    max_concurrency: int,
    repetitions: int,
    target: Any = student_consultation_target,
) -> list[dict[str, Any]]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    progress_path = REPORT_DIR / "local-progress.jsonl"
    progress_path.write_text("", encoding="utf-8")
    semaphore = asyncio.Semaphore(max_concurrency)
    jobs = [(case, repetition) for repetition in range(repetitions) for case in cases]

    async def execute(case: Any, repetition: int) -> dict[str, Any]:
        async with semaphore:
            row = await _run_local_case(case, evaluators=evaluators, target=target)
            row["example"]["metadata"]["repetition"] = repetition + 1
            return row

    tasks = [asyncio.create_task(execute(case, repetition)) for case, repetition in jobs]
    rows: list[dict[str, Any]] = []
    try:
        for completed, task in enumerate(asyncio.as_completed(tasks), start=1):
            row = await task
            rows.append(row)
            with progress_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            metadata = row["example"]["metadata"]
            output = row["run"]["outputs"]
            print(
                f"[{completed}/{len(tasks)}] {metadata['case_id']} "
                f"intent={output.get('intent', 'ERROR')}",
                flush=True,
            )
    except BaseException:
        for task in tasks:
            task.cancel()
        raise
    return rows


def _build_client() -> Any:
    try:
        from langsmith import Client
        from langsmith.anonymizer import create_anonymizer
    except ImportError as error:
        raise RuntimeError(
            "缺少评测依赖，请先执行 pip install -r requirements-eval.txt"
        ) from error
    return Client(anonymizer=create_anonymizer(lambda text: anonymize_payload(text)))


async def _run_experiment(
    *,
    client: Any,
    dataset_name: str,
    evaluators: list[Any],
    experiment_prefix: str,
    settings: EvalSettings,
    suite: str,
    repetitions: int,
    max_concurrency: int,
) -> tuple[str, list[Any]]:
    from langsmith import aevaluate

    results = await aevaluate(
        student_consultation_target,
        data=dataset_name,
        evaluators=evaluators,
        experiment_prefix=experiment_prefix,
        max_concurrency=max_concurrency,
        num_repetitions=repetitions,
        client=client,
        metadata={
            "models": [_configured_model_name()],
            "model_provider": os.getenv("MODEL_PROVIDER", "mock"),
            "prompts": ["student-planner-v2", "student-composer-v2"],
            "tools": ["student-consultation-readonly-tools"],
            "suite": suite,
        },
    )
    rows = [row async for row in results]
    return results.experiment_name, rows


def main() -> int:
    args = parse_args()
    settings = EvalSettings.from_env()
    max_concurrency = args.max_concurrency or settings.max_concurrency
    if max_concurrency < 1:
        raise ValueError("--max-concurrency 必须大于0")
    cases = load_cases(args.suite)
    if args.case_ids:
        requested = set(args.case_ids)
        cases = [case for case in cases if case.metadata.case_id in requested]
        missing = sorted(requested - {case.metadata.case_id for case in cases})
        if missing:
            raise ValueError(f"没有找到case ID: {', '.join(missing)}")
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit 必须大于0")
        cases = cases[: args.limit]
    validation = validate_dataset(cases)
    print(dump_validation_report(validation))
    if args.suite == "full" and validation["missing_pairs"]:
        raise RuntimeError("完整评测集存在缺失鲁棒性配对样本。")
    if args.validate_only:
        return 0

    if args.local_only and (args.langsmith or args.sync_only):
        raise ValueError("--local-only 不能与 --langsmith 或 --sync-only 同时使用")

    evaluators = list(CODE_EVALUATORS)
    if not args.no_judge:
        evaluators.append(deepseek_quality_judge)
    experiment_prefix = args.experiment_prefix or settings.experiment_prefix
    local_mode = args.local_only or not (args.langsmith or args.sync_only)
    if local_mode:
        # .env可能默认开启业务Trace，本地模式必须显式关闭，避免继续消耗LangSmith额度。
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGCHAIN_TRACING"] = "false"
        experiment_name = (
            f"{experiment_prefix}-local-"
            f"{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}"
        )
        rows = asyncio.run(
            _run_local_experiment(
                cases=cases,
                evaluators=evaluators,
                max_concurrency=max_concurrency,
                repetitions=args.repetitions or settings.repetitions,
            )
        )
        paths = write_local_reports(rows, experiment_name=experiment_name)
        print(
            json.dumps(
                {key: str(path.resolve()) for key, path in paths.items()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    settings.validate_langsmith()
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_PROJECT", settings.project_name)
    client = _build_client()
    dataset_name = (
        settings.dataset_name
        if args.suite == "full"
        else f"{settings.dataset_name}-{args.suite}"
    )
    dataset_id = sync_cases_to_langsmith(
        client,
        dataset_name=dataset_name,
        cases=cases,
    )
    print(
        json.dumps(
            {"dataset": dataset_name, "dataset_id": dataset_id}, ensure_ascii=False
        )
    )
    if args.sync_only:
        return 0
    experiment_name, rows = asyncio.run(
        _run_experiment(
            client=client,
            dataset_name=dataset_name,
            evaluators=evaluators,
            experiment_prefix=experiment_prefix,
            settings=settings,
            suite=args.suite,
            repetitions=args.repetitions or settings.repetitions,
            max_concurrency=max_concurrency,
        )
    )
    paths = write_local_reports(rows, experiment_name=experiment_name)
    print(
        json.dumps(
            {key: str(path.resolve()) for key, path in paths.items()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
