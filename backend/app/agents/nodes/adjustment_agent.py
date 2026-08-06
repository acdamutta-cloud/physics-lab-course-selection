from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from app.agents.model_provider import get_chat_model
from app.agents.states.adjustment import StudentAdjustmentState
from app.agents.tools.adjustment_tools import (
    get_student_adjustment_context,
    recommend_student_adjustments,
)
from app.schemas.student_adjustment import AdjustmentAgentPlan

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "student_adjustment"


def _emit(event: str, data: dict[str, object]) -> None:
    try:
        get_stream_writer()({"event": event, "data": data})
    except RuntimeError:
        pass


def _parse_plan(content: object) -> AdjustmentAgentPlan:
    text = str(content or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("planner response does not contain JSON")
    return AdjustmentAgentPlan.model_validate_json(text[start : end + 1])


async def normalize_adjustment_request(state: StudentAdjustmentState):
    trace_id = state.get("trace_id") or uuid4().hex
    _emit("meta", {"trace_id": trace_id, "intent": None})
    _emit("status", {"phase": "understanding", "message": "正在理解你的调整偏好…"})
    return {
        "trace_id": trace_id,
        "actor_type": "STUDENT",
        "change_scope": "INDIVIDUAL_ASSIGNMENT",
        "cards": [],
        "warnings": [],
    }


async def plan_with_llm(state: StudentAdjustmentState):
    model = state.get("model") or get_chat_model()
    if model is None:
        return {"model_error": "智能调整模型尚未配置。"}
    prompt = (PROMPT_DIR / "planner_v1.md").read_text(encoding="utf-8")
    payload = {
        "fixed_request_type": state["request_type"],
        "message": state["message"],
        "output_schema": AdjustmentAgentPlan.model_json_schema(),
    }
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
    ]
    try:
        response = await model.ainvoke(messages)
        return {"plan": _parse_plan(response.content)}
    except Exception as first_error:  # noqa: BLE001
        try:
            response = await model.ainvoke(
                [
                    *messages,
                    HumanMessage(
                        content=(
                            "上一次输出无法解析。只输出符合Schema的JSON，不要解释。"
                            f"错误类型：{type(first_error).__name__}"
                        )
                    ),
                ]
            )
            return {"plan": _parse_plan(response.content)}
        except Exception as error:  # noqa: BLE001
            return {"model_error": f"调整偏好规划失败：{type(error).__name__}"}


async def validate_plan(state: StudentAdjustmentState):
    if state.get("model_error"):
        return {}
    plan = state.get("plan")
    expected_intent = {
        "RESCHEDULE": "RECOMMEND_RESCHEDULE",
        "PROJECT_CHANGE": "RECOMMEND_PROJECT_CHANGE",
        "MAKEUP": "RECOMMEND_MAKEUP",
    }[state["request_type"]]
    if (
        plan is None
        or plan.request_type != state["request_type"]
        or plan.intent != expected_intent
    ):
        return {"model_error": "模型规划与当前申请类型不一致。"}
    if plan.needs_clarification:
        return {
            "clarification_question": plan.clarification_question
            or "你的时间偏好存在冲突，请明确希望优先满足的条件。"
        }
    return {}


async def load_student_adjustment_context(state: StudentAdjustmentState):
    context = await get_student_adjustment_context(
        state["session"],
        student_id=state["student_id"],
        term=state["term"],
        request_type=state["request_type"],
        source_record_id=state["source_record_id"],
    )
    return {"context": context}


async def resolve_source_record(state: StudentAdjustmentState):
    sources = state.get("context", {}).get("sources", [])
    found = any(
        str(item.get("record_id")) == str(state["source_record_id"])
        for item in sources
        if isinstance(item, dict)
    )
    return {} if found else {"model_error": "未找到可用于该类申请的原实验记录。"}


async def execute_adjustment_tool(state: StudentAdjustmentState):
    if state.get("model_error") or state.get("clarification_question"):
        return {"tool_results": []}
    _emit("status", {"phase": "tool", "message": "正在核验真实场次和时间冲突…"})
    options = await recommend_student_adjustments(
        state["session"],
        student_id=state["student_id"],
        term=state["term"],
        request_type=state["request_type"],
        source_record_id=state["source_record_id"],
        preferences=state["plan"].preferences,
        max_options=state.get("max_options", 3),
    )
    return {"tool_results": options}


async def build_grounding_bundle(state: StudentAdjustmentState):
    return {
        "grounding_bundle": {
            "request_type": state["request_type"],
            "approval_route": {
                "RESCHEDULE": "AUTO",
                "PROJECT_CHANGE": "ADMIN",
                "MAKEUP": "TEACHER",
            }[state["request_type"]],
            "options": [item.model_dump(mode="json") for item in state.get("tool_results", [])],
        }
    }


async def compose_answer_stream(state: StudentAdjustmentState):
    if state.get("model_error"):
        text = state["model_error"]
        _emit("error", {"code": "ADJUSTMENT_AGENT_ERROR", "message": text, "trace_id": state["trace_id"]})
        return {"answer": text}
    if state.get("clarification_question"):
        text = state["clarification_question"]
        _emit("delta", {"text": text})
        return {"answer": text}
    options = state.get("tool_results", [])
    if not options:
        text = "当前没有符合申请规则和时间条件的可选场次。你可以调整偏好，或联系管理员另行安排。"
        _emit("delta", {"text": text})
        return {"answer": text}
    model = state.get("model") or get_chat_model()
    prompt = (PROMPT_DIR / "composer_v1.md").read_text(encoding="utf-8")
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=json.dumps(state["grounding_bundle"], ensure_ascii=False)),
    ]
    _emit("status", {"phase": "answering", "message": "正在整理推荐结果…"})
    answer = ""
    async for chunk in model.astream(messages):
        text = str(chunk.content or "")
        if text:
            answer += text
            _emit("delta", {"text": text})
    return {"answer": answer}


async def validate_final_answer(state: StudentAdjustmentState):
    return {}


async def build_adjustment_cards(state: StudentAdjustmentState):
    cards = [
        {
            "type": "ADJUSTMENT_RECOMMENDATION",
            "title": f"推荐场次 {index}",
            "summary": "可带回申请表继续预览，提交时会再次实时校验。",
            "data": item.model_dump(mode="json"),
        }
        for index, item in enumerate(state.get("tool_results", []), 1)
    ]
    _emit(
        "final",
        {
            "intent": state.get("plan").intent if state.get("plan") else None,
            "cards": cards,
            "warnings": state.get("warnings", []),
        },
    )
    _emit("done", {"trace_id": state["trace_id"]})
    return {"cards": cards}
