from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parent
DATASET_DIR = EVAL_ROOT / "datasets"
FIXTURE_DIR = EVAL_ROOT / "fixtures"
REPORT_DIR = EVAL_ROOT / "reports"


@dataclass(frozen=True)
class EvalSettings:
    dataset_name: str = "student-ai-consultation-e2e-v1"
    project_name: str = "physics-lab-student-consultation"
    experiment_prefix: str = "qwen14b-student-consultation"
    judge_provider: str = "deepseek"
    max_concurrency: int = 4
    repetitions: int = 1
    student_no: str = "D2024010001"

    @classmethod
    def from_env(cls) -> EvalSettings:
        return cls(
            dataset_name=os.getenv(
                "LANGSMITH_EVAL_DATASET", "student-ai-consultation-e2e-v1"
            ),
            project_name=os.getenv(
                "LANGSMITH_PROJECT", "physics-lab-student-consultation"
            ),
            experiment_prefix=os.getenv(
                "EVAL_EXPERIMENT_PREFIX", "qwen14b-student-consultation"
            ),
            judge_provider=os.getenv("EVAL_JUDGE_PROVIDER", "deepseek"),
            max_concurrency=max(1, int(os.getenv("EVAL_MAX_CONCURRENCY", "4"))),
            repetitions=max(1, int(os.getenv("EVAL_REPETITIONS", "1"))),
            student_no=os.getenv("EVAL_STUDENT_NO", "D2024010001"),
        )

    def validate_langsmith(self) -> None:
        if not os.getenv("LANGSMITH_API_KEY", "").strip():
            raise RuntimeError(
                "缺少 LANGSMITH_API_KEY；请写入 backend/.env 后再运行云端评测。"
            )


QUALITY_THRESHOLDS = {
    "plan_json_valid": 0.99,
    "intent_accuracy": 0.95,
    "entity_f1": 0.94,
    "tool_selection_f1": 0.95,
    "tool_result_accuracy": 0.95,
    "card_type_f1": 0.95,
    "clarification_accuracy": 0.95,
    "context_no_tool_accuracy": 0.98,
    "rag_top1_accuracy": 0.90,
    "rag_recall_at_2": 0.97,
    "judge_task_completion": 7.5,
    "safety_gate": 1.0,
}
