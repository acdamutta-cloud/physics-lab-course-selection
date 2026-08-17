from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from app.agents.model_provider import get_chat_model
from app.agents.states.teacher_adjustment import TeacherAdjustmentState
from app.schemas.student_consultation import SelectionPreferences
from app.schemas.teacher_adjustment import TeacherRescheduleAgentPlan
from app.services.teacher_adjustment_service import recommend_reschedules

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "teacher_adjustment"


def _emit(event: str, data: dict[str, object]) -> None:
    try:
        get_stream_writer()({"event": event, "data": data})
    except RuntimeError:
        pass


async def normalize_teacher_adjustment(state: TeacherAdjustmentState):
    trace_id = state.get("trace_id") or uuid4().hex
    _emit("meta", {"trace_id": trace_id, "intent": "RECOMMEND_TEACHER_RESCHEDULE"})
    _emit("status", {"phase": "understanding", "message": "正在理解你的调课偏好…"})
    return {"trace_id": trace_id, "operation": "RECOMMEND_TEACHER_RESCHEDULE"}


def _parse_plan(content: object) -> TeacherRescheduleAgentPlan:
    text = str(content or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("planner response does not contain JSON")
    return TeacherRescheduleAgentPlan.model_validate_json(text[start : end + 1])


async def extract_teacher_preferences(message: str, *, model=None) -> dict[str, object]:
    """Shared LLM preference extraction for adjustment recommendation tools."""

    message = message.strip()
    if not message:
        return {"plan": TeacherRescheduleAgentPlan(preferences=SelectionPreferences())}
    model = model or get_chat_model()
    if model is None:
        return {"model_error": "智能调整模型尚未配置。"}
    prompt = (PROMPT_DIR / "planner_v1.md").read_text(encoding="utf-8")
    payload = {
        "message": message,
        "schema": TeacherRescheduleAgentPlan.model_json_schema(),
    }
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
    ]
    try:
        response = await model.ainvoke(messages)
        return {"plan": _parse_plan(response.content)}
    except Exception as error:  # noqa: BLE001
        return {"model_error": f"调整偏好无法解析：{type(error).__name__}"}


async def plan_teacher_preferences(state: TeacherAdjustmentState):
    message = state.get("message", "").strip()
    if not message:
        return {"plan": TeacherRescheduleAgentPlan(preferences=SelectionPreferences())}
    model = state.get("model") or get_chat_model()
    if model is None:
        return {"model_error": "智能调课模型尚未配置。"}
    prompt = (PROMPT_DIR / "planner_v1.md").read_text(encoding="utf-8")
    payload = {
        "message": message,
        "schema": TeacherRescheduleAgentPlan.model_json_schema(),
    }
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
    ]
    try:
        response = await model.ainvoke(messages)
        return {"plan": _parse_plan(response.content)}
    except Exception as error:  # noqa: BLE001
        return {"model_error": f"调课偏好无法解析：{type(error).__name__}"}


async def validate_teacher_plan(state: TeacherAdjustmentState):
    plan = state.get("plan")
    if state.get("model_error") or plan is None:
        return {}
    if plan.needs_clarification:
        return {
            "clarification_question": plan.clarification_question
            or "调课偏好存在冲突，请明确优先条件。"
        }
    return {}


async def execute_teacher_recommendation(state: TeacherAdjustmentState):
    if state.get("model_error") or state.get("clarification_question"):
        return {"options": []}
    _emit("status", {"phase": "tool", "message": "正在核验教师、实验室和学生时间冲突…"})
    options = await recommend_reschedules(
        state["session"],
        teacher_id=state["teacher_id"],
        term=state["term"],
        original_session_id=state["original_session_id"],
        preferences=state["plan"].preferences,
        max_options=state.get("max_options", 3),
    )
    return {"options": options}


async def compose_teacher_answer(state: TeacherAdjustmentState):
    if state.get("model_error"):
        text = state["model_error"]
        _emit(
            "error",
            {
                "code": "TEACHER_ADJUSTMENT_AGENT_ERROR",
                "message": text,
                "trace_id": state["trace_id"],
            },
        )
        return {"answer": text}
    if state.get("clarification_question"):
        text = state["clarification_question"]
        _emit("delta", {"text": text})
        return {"answer": text}
    options = state.get("options", [])
    if not options:
        text = "当前没有满足硬性约束的调课方案，请调整偏好或联系管理员进一步协调。"
        _emit("delta", {"text": text})
        return {"answer": text}
    conflict_count = sum(
        1 for item in options if (item.affected_student_count or 0) > 0
    )
    safe_count = len(options) - conflict_count
    if conflict_count == 0:
        text = f"已找到{len(options)}组候选调课方案，均无学生时间冲突。"
    elif safe_count == 0:
        text = (
            f"已找到{len(options)}组候选调课方案，均涉及学生时间冲突，"
            "审批时需要安排受影响学生。"
        )
    else:
        text = (
            f"已找到{len(options)}组候选调课方案，其中{safe_count}组无冲突，"
            f"{conflict_count}组涉及学生时间冲突。"
        )
    _emit("status", {"phase": "answering", "message": "正在整理调课方案…"})
    _emit("delta", {"text": text})
    return {"answer": text}


async def finalize_teacher_adjustment(state: TeacherAdjustmentState):
    cards = [
        {
            "type": "TEACHER_RESCHEDULE",
            "title": f"调课方案{index}",
            "data": item.model_dump(mode="json"),
        }
        for index, item in enumerate(state.get("options", []), 1)
    ]
    _emit(
        "final",
        {"intent": "RECOMMEND_TEACHER_RESCHEDULE", "cards": cards, "warnings": []},
    )
    _emit("done", {"trace_id": state["trace_id"]})
    return {"cards": cards}
