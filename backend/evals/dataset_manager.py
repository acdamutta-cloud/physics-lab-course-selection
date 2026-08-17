from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from evals.config import DATASET_DIR
from evals.schemas import EvalCase

DATASET_FILES = {
    "rag": DATASET_DIR / "rag_cases.jsonl",
    "context": DATASET_DIR / "context_cases.jsonl",
    "tool": DATASET_DIR / "tool_cases.jsonl",
    "routing": DATASET_DIR / "routing_safety_cases.jsonl",
}


def read_jsonl(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                cases.append(EvalCase.model_validate_json(line))
            except Exception as error:
                raise ValueError(
                    f"{path.name} 第{line_number}行无效：{error}"
                ) from error
    return cases


def load_cases(suite: str = "full") -> list[EvalCase]:
    if suite == "full" or suite == "smoke":
        paths = list(DATASET_FILES.values())
    elif suite in DATASET_FILES:
        paths = [DATASET_FILES[suite]]
    else:
        raise ValueError(f"未知评测集：{suite}")
    cases = [case for path in paths for case in read_jsonl(path)]
    if suite == "smoke":
        cases = [case for case in cases if case.metadata.split == "smoke"]
    return cases


def validate_dataset(cases: Iterable[EvalCase]) -> dict[str, Any]:
    items = list(cases)
    ids = [item.metadata.case_id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("评测集中存在重复 case_id")
    id_set = set(ids)
    missing_pairs = sorted(
        {
            item.metadata.pair_id
            for item in items
            if item.metadata.pair_id and item.metadata.pair_id not in id_set
        }
    )
    # 子集运行可能不包含配对原始样本，完整集必须由调用方检查 missing_pairs。
    categories = Counter(item.metadata.category for item in items)
    robustness = Counter(item.metadata.robustness_type for item in items)
    return {
        "total": len(items),
        "categories": dict(categories),
        "robustness": dict(robustness),
        "missing_pairs": missing_pairs,
    }


def _example_id(dataset_name: str, case_id: str):
    return uuid5(NAMESPACE_URL, f"langsmith:{dataset_name}:{case_id}")


def sync_cases_to_langsmith(
    client: Any,
    *,
    dataset_name: str,
    cases: list[EvalCase],
) -> str:
    """幂等同步数据集；调用方负责在外部写入前取得授权。"""

    try:
        dataset = client.read_dataset(dataset_name=dataset_name)
    except Exception:  # noqa: BLE001 - SDK各版本NotFound类型不同
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="物理实验学生端AI智能咨询分层评测集",
        )
    examples = [
        {
            "id": _example_id(dataset_name, case.metadata.case_id),
            "inputs": case.inputs.model_dump(mode="json"),
            "outputs": case.reference_outputs.model_dump(mode="json"),
            "metadata": case.metadata.model_dump(mode="json"),
            "split": case.metadata.split,
        }
        for case in cases
    ]
    batch_size = 100
    for attempt in range(3):
        existing_ids = {
            str(example.id)
            for example in client.list_examples(dataset_id=dataset.id, limit=None)
        }
        creates = [
            item for item in examples if str(item["id"]) not in existing_ids
        ]
        if not creates:
            break
        try:
            for start in range(0, len(creates), batch_size):
                client.create_examples(
                    dataset_id=dataset.id,
                    examples=creates[start : start + batch_size],
                    max_concurrency=3,
                )
        except Exception as error:
            if "Conflict" not in type(error).__name__ or attempt == 2:
                raise
            continue
        break
    for start in range(0, len(examples), batch_size):
        client.update_examples(
            dataset_id=dataset.id,
            updates=examples[start : start + batch_size],
        )
    return str(dataset.id)


def dump_validation_report(summary: dict[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
