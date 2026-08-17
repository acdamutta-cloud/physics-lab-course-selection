from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config.settings import get_settings

JUDGE_PROMPT = """你是高校学生端AI咨询的独立评测员。只评价给定回答，不执行任务。
按0到10分评价：task_completion、correctness、completeness、relevance、student_clarity、groundedness。
10=完全满足；8-9=正确且仅有轻微瑕疵；6-7=主体正确但有明显遗漏；
3-5=部分完成或存在较大错误；1-2=严重偏离；0=完全错误或不安全。
reference 是评分要点而非要求逐字复述；同义表达、等价步骤和基于可信上下文得出的正确答案均应得分。
评价工具类回答时必须结合 actual.tool_results；评价RAG回答时必须结合检索结果；
判断课程是必修还是选修时，只能依据 actual.tool_results 中明确的 course_nature：REQUIRED 表示必修，ELECTIVE 表示选修。不得根据 required_project_count、optional_project_min_count 或项目必做/选做数量推断课程性质。缺少 course_nature 时，即使答案碰巧正确，correctness 和 groundedness 也不得给满分。
评价上下文类回答时必须结合 actual.evaluation_context，不得因为答案没有复述无关信息而扣分。
必须只输出JSON：
{"task_completion":0,"correctness":0,"completeness":0,"relevance":0,"student_clarity":0,"groundedness":0,"comment":"简短原因"}
不得输出思考过程。"""


@lru_cache
def get_eval_judge_model() -> ChatOpenAI:
    settings = get_settings()
    if (
        settings.deepseek_api_key is None
        or not settings.deepseek_api_key.get_secret_value().strip()
    ):
        raise RuntimeError("缺少 DEEPSEEK_API_KEY，无法运行独立LLM裁判。")
    return ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_base_url,
        temperature=0,
        max_tokens=1024,
        timeout=settings.deepseek_timeout_seconds,
        max_retries=settings.deepseek_max_retries,
    )


def _extract_json(content: Any) -> dict[str, Any]:
    text = str(content or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("裁判模型未返回JSON")
    return json.loads(text[start : end + 1])


def deepseek_quality_judge(run: Any, example: Any) -> list[dict[str, Any]]:
    output = dict(getattr(run, "outputs", None) or {})
    reference = dict(getattr(example, "outputs", None) or {})
    inputs = dict(getattr(example, "inputs", None) or {})
    payload = {
        "student_question": (inputs.get("messages") or [{}])[-1].get("content", ""),
        "reference": reference,
        "actual": {
            "answer": output.get("answer", ""),
            "intent": output.get("intent"),
            "tool_requests": output.get("tool_requests", []),
            "tool_results": output.get("tool_results", []),
            "cards": output.get("cards", []),
            "resolved_entities": output.get("resolved_entities", {}),
            "evaluation_context": output.get("evaluation_context", {}),
            "grounding_passed": output.get("grounding_passed"),
        },
    }
    response = get_eval_judge_model().invoke(
        [
            SystemMessage(content=JUDGE_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
        ]
    )
    result = _extract_json(response.content)
    comment = str(result.get("comment") or "")
    keys = (
        "task_completion",
        "correctness",
        "completeness",
        "relevance",
        "student_clarity",
        "groundedness",
    )
    return [
        {
            "key": f"judge_{key}",
            "score": max(0.0, min(10.0, float(result.get(key, 0)))),
            "comment": comment,
        }
        for key in keys
    ]
