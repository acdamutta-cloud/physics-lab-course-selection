from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.model_provider import get_chat_model
from app.agents.states.student import StudentConsultationState
from app.agents.tools.student_tools import (
    execute_planned_tools,
    resolve_plan_entities,
    training_plan_tool,
)
from app.models.identity import Campus, Major, Student
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.schemas.student_consultation import (
    ConsultationCard,
    ConsultationMessage,
    StudentAgentPlan,
    weekday_name,
)

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "student_advisor"
PLANNER_PROMPT = PROMPT_DIR / "planner_v2.md"
COMPOSER_PROMPT = PROMPT_DIR / "composer_v2.md"
GENERAL_CHAT_PROMPT = PROMPT_DIR / "general_chat_v1.md"

REQUIRED_TOOL_BY_INTENT = {
    "BUSINESS_RULE_QUERY": {"lookup_student_rules"},
    "CHECK_ELIGIBILITY": {"check_selection_eligibility"},
    "EXPLAIN_CONFLICT": {"explain_selection_conflicts"},
    "QUERY_TRAINING_PLAN": {
        "get_training_plan_context",
        "get_remaining_projects",
    },
    "RECOMMEND_SELECTION": {"recommend_selection_plans"},
}


def _emit(event: str, data: dict[str, object]) -> None:
    """Emit a LangGraph custom stream event when running in stream mode."""

    try:
        get_stream_writer()({"event": event, "data": data})
    except RuntimeError:
        # Plain graph invocation intentionally has no custom stream consumer.
        pass


def _message_content(message: ConsultationMessage | dict[str, object]) -> str:
    if isinstance(message, dict):
        return str(message.get("content", "")).strip()
    return message.content.strip()


def _message_role(message: ConsultationMessage | dict[str, object]) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return message.role


async def normalize_request(
    state: StudentConsultationState,
) -> dict[str, object]:
    messages = list(state.get("messages", []))[-20:]
    current_question = next(
        (
            _message_content(message)
            for message in reversed(messages)
            if _message_role(message) == "user"
        ),
        "",
    )
    trace_id = state.get("trace_id") or uuid4().hex
    _emit("meta", {"trace_id": trace_id, "intent": None})
    _emit("status", {"phase": "understanding", "message": "正在理解你的问题…"})
    return {
        "trace_id": trace_id,
        "term_id": state["term"].id,
        "conversation_context": messages,
        "current_question": current_question,
        "warnings": [],
        "unknowns": [],
        "cards": [],
        "answer_buffer": "",
        "grounding_passed": True,
    }


async def load_base_context(
    state: StudentConsultationState,
) -> dict[str, object]:
    session: AsyncSession = state["session"]
    student = await session.get(Student, state["student_id"])
    if student is None:
        return {"model_error": "未找到当前学生信息。"}
    major = await session.get(Major, student.major_id)
    campus = await session.get(Campus, student.campus_id)
    plan = await training_plan_tool(
        session,
        student_id=state["student_id"],
        term=state["term"],
    )
    course_summaries: list[dict[str, object]] = []
    for course in plan.get("courses", []) if isinstance(plan, dict) else []:
        if not isinstance(course, dict):
            continue
        course_summaries.append(
            {
                "course_id": course.get("course_id"),
                "course_name": course.get("course_name"),
                "completion_status": course.get("completion_status"),
                "eligibility": course.get("eligibility"),
                "projects": [
                    {
                        "project_id": project.get("project_id"),
                        "project_name": project.get("project_name"),
                        "student_status": project.get("student_status"),
                    }
                    for project in course.get("projects", [])
                    if isinstance(project, dict)
                ],
            }
        )
    course_ids = {str(item["course_id"]) for item in course_summaries}
    project_ids = {
        str(project["project_id"])
        for course in course_summaries
        for project in course["projects"]
    }
    project_uuid_ids = [UUID(value) for value in project_ids]
    page = (
        state["page_context"].model_dump(mode="json")
        if state.get("page_context")
        else {"view": "ai", "course_id": None, "project_id": None, "session_id": None}
    )
    if page.get("course_id") and str(page["course_id"]) not in course_ids:
        page["course_id"] = None
    if page.get("project_id") and str(page["project_id"]) not in project_ids:
        page["project_id"] = None
    if page.get("session_id"):
        valid_session = await session.scalar(
            select(ExperimentSession.id)
            .join(
                ScheduleVersion,
                ScheduleVersion.id == ExperimentSession.schedule_version_id,
            )
            .where(
                ExperimentSession.id == page["session_id"],
                ExperimentSession.project_id.in_(project_uuid_ids),
                ScheduleVersion.term_id == state["term"].id,
                ScheduleVersion.status == "PUBLISHED",
            )
        )
        if valid_session is None:
            page["session_id"] = None
    base_context = {
        "student": {
            "display_name": student.name,
            "enrollment_year": student.enrollment_year,
            "major_name": major.name if major else None,
            "campus_name": campus.name if campus else None,
        },
        "term": {
            "academic_year": state["term"].academic_year,
            "semester_no": state["term"].semester_no,
        },
        "training_plan_summary": {
            "plan_code": plan.get("plan_code") if isinstance(plan, dict) else None,
            "courses": course_summaries,
        },
        "page": page,
    }
    return {"base_context": base_context}


def _planner_input(state: StudentConsultationState) -> list[object]:
    conversation = [
        {"role": _message_role(item), "content": _message_content(item)}
        for item in state.get("conversation_context", [])
    ]
    page = (
        state["page_context"].model_dump(mode="json")
        if state.get("page_context")
        else {"view": "ai"}
    )
    content = (
        "<output_schema>\n"
        f"{json.dumps(StudentAgentPlan.model_json_schema(), ensure_ascii=False)}\n"
        "</output_schema>\n"
        "<student_base_context>\n"
        f"{json.dumps(state.get('base_context', {}), ensure_ascii=False, default=str)}\n"
        "</student_base_context>\n"
        "<page_context>\n"
        f"{json.dumps(page, ensure_ascii=False, default=str)}\n"
        "</page_context>\n<conversation>\n"
        f"{json.dumps(conversation, ensure_ascii=False)}\n"
        "</conversation>\n<current_question>\n"
        f"{state.get('current_question', '')}\n</current_question>"
    )
    return [
        SystemMessage(content=PLANNER_PROMPT.read_text(encoding="utf-8")),
        HumanMessage(content=content),
    ]


def _extract_plan_json(content: object) -> StudentAgentPlan:
    """Parse a planner JSON response without provider-specific response_format."""

    if isinstance(content, list):
        text = "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    else:
        text = str(content or "")
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("planner response does not contain a JSON object")
    return StudentAgentPlan.model_validate(json.loads(text[start : end + 1]))


async def plan_with_llm(
    state: StudentConsultationState,
) -> dict[str, object]:
    if state.get("model_error"):
        return {}
    model = state.get("model") or get_chat_model()
    if model is None:
        return {"model_error": "智能咨询模型尚未配置。", "intent": "UNKNOWN"}
    messages = _planner_input(state)
    try:
        response = await model.ainvoke(messages)
        plan = _extract_plan_json(response.content)
        return {
            "plan": plan,
            "intent": plan.intent,
            "preferences": plan.preferences,
            "tool_requests": plan.tool_requests,
            "repaired_plan_attempted": False,
        }
    except Exception as first_error:  # noqa: BLE001 - provider/schema failures vary
        try:
            repair_messages = [
                *messages,
                HumanMessage(
                    content="上一次输出无法解析。请只输出一个符合Schema的JSON对象，"
                    f"不要解释。校验错误：{type(first_error).__name__}"
                ),
            ]
            response = await model.ainvoke(repair_messages)
            plan = _extract_plan_json(response.content)
            return {
                "plan": plan,
                "intent": plan.intent,
                "preferences": plan.preferences,
                "tool_requests": plan.tool_requests,
                "repaired_plan_attempted": True,
            }
        except Exception as repair_error:  # noqa: BLE001 - provider/schema failures vary
            return {
                "model_error": f"规划输出无法解析：{type(repair_error).__name__}",
                "intent": "UNKNOWN",
                "repaired_plan_attempted": True,
            }


async def validate_plan(
    state: StudentConsultationState,
) -> dict[str, object]:
    if state.get("model_error"):
        return {}
    plan = state.get("plan")
    if plan is None:
        return {"model_error": "模型没有生成执行计划。"}
    errors: list[str] = []
    names = {request.name for request in plan.tool_requests}
    allowed_for_intent = REQUIRED_TOOL_BY_INTENT.get(plan.intent, set())
    if plan.intent in REQUIRED_TOOL_BY_INTENT and not names.intersection(
        allowed_for_intent
    ):
        errors.append("当前意图缺少必须的只读工具调用。")
    if plan.intent == "UNKNOWN" and names:
        errors.append("UNKNOWN意图不应调用业务工具。")
    if plan.intent in {"GENERAL_CHAT", "OUT_OF_SCOPE"} and names:
        errors.append("普通交互和业务外问题不得调用业务工具。")
    if plan.intent == "GENERAL_CHAT" and not plan.direct_answer_allowed:
        errors.append("普通交互必须明确允许直接回答。")
    if plan.intent == "BUSINESS_RULE_QUERY" and not plan.rule_topics:
        errors.append("业务规则咨询必须提供受控规则主题。")
    for request in plan.tool_requests:
        forbidden = {"student_id", "term_id", "sql", "url"}.intersection(
            request.arguments
        )
        if forbidden:
            errors.append("工具参数包含模型无权提供的字段。")
    if errors:
        return {
            "plan_validation_errors": errors,
            "model_error": "模型执行计划未通过安全校验。",
        }
    return {"plan_validation_errors": []}


def route_after_plan(state: StudentConsultationState) -> str:
    if state.get("model_error"):
        return "emit_error"
    plan = state.get("plan")
    if plan is None:
        return "emit_error"
    if plan.needs_clarification:
        return "compose_clarification"
    if plan.intent == "GENERAL_CHAT":
        return "compose_general_chat_stream"
    if plan.intent == "OUT_OF_SCOPE":
        return "compose_boundary_notice"
    if plan.intent == "UNKNOWN":
        return "compose_clarification"
    if plan.intent == "BUSINESS_RULE_QUERY":
        return "execute_student_tools"
    return "resolve_entities"


async def resolve_entities(
    state: StudentConsultationState,
) -> dict[str, object]:
    plan = state["plan"]
    assert plan is not None
    page_context = state.get("page_context")
    resolved, clarification = await resolve_plan_entities(
        state["session"],
        student_id=state["student_id"],
        term=state["term"],
        plan=plan,
        page_session_id=page_context.session_id if page_context else None,
    )
    return {
        "resolved_entities": resolved,
        "clarification_question": clarification,
    }


def route_after_entities(state: StudentConsultationState) -> str:
    return (
        "compose_clarification"
        if state.get("clarification_question")
        else "execute_student_tools"
    )


async def execute_student_tools(
    state: StudentConsultationState,
) -> dict[str, object]:
    plan = state["plan"]
    assert plan is not None
    status_message = {
        "BUSINESS_RULE_QUERY": "正在查询已发布的选课规则…",
        "QUERY_TRAINING_PLAN": "正在查询你的培养方案…",
        "RECOMMEND_SELECTION": "正在筛选符合条件的实验场次…",
        "CHECK_ELIGIBILITY": "正在核验场次资格和时间冲突…",
        "EXPLAIN_CONFLICT": "正在分析不能选择的具体原因…",
    }.get(plan.intent, "正在查询相关规则…")
    _emit("status", {"phase": "tool", "message": status_message})
    results = await execute_planned_tools(
        state["session"],
        student_id=state["student_id"],
        term=state["term"],
        plan=plan,
        resolved=state.get("resolved_entities", {}),
    )
    return {"tool_results": results}


def route_after_tools(state: StudentConsultationState) -> str:
    if state.get("intent") != "BUSINESS_RULE_QUERY":
        return "build_grounding_bundle"
    for result in state.get("tool_results", []):
        if result.get("name") != "lookup_student_rules":
            continue
        data = result.get("data", {})
        if isinstance(data, dict) and data.get("status") == "NOT_FOUND":
            return "compose_rule_not_found"
    return "build_grounding_bundle"


async def build_grounding_bundle(
    state: StudentConsultationState,
) -> dict[str, object]:
    immutable_facts: dict[str, object] = {"tool_results": state.get("tool_results", [])}
    allowed_recommendations: list[object] = []
    unknowns: list[str] = []
    for result in state.get("tool_results", []):
        data = result.get("data", {})
        if not isinstance(data, dict):
            continue
        if data.get("unknown"):
            unknowns.append(str(data["unknown"]))
        if result.get("name") == "recommend_selection_plans":
            allowed_recommendations.extend(data.get("plans", []))
    bundle = {
        "immutable_facts": immutable_facts,
        "explainable_context": state.get("resolved_entities", {}),
        "allowed_recommendations": allowed_recommendations,
        "unknowns": unknowns,
    }
    return {"grounding_bundle": bundle, "unknowns": unknowns}


def _deterministic_answer(state: StudentConsultationState) -> str:
    if state.get("unknowns"):
        return "；".join(state["unknowns"])
    for result in state.get("tool_results", []):
        data = result.get("data", {})
        if not isinstance(data, dict):
            continue
        if "decision" in data:
            if data["decision"] == "ALLOW":
                warnings = [item["message"] for item in data.get("warnings", [])]
                suffix = f"需要注意：{'；'.join(warnings)}" if warnings else ""
                return f"该场次当前具备选择资格。{suffix}"
            reasons = [item["message"] for item in data.get("violations", [])]
            return "该场次当前不能选择，原因是：" + "；".join(reasons)
        if result.get("name") == "lookup_student_rules":
            return "当前已发布规则库中找到了与该问题直接相关的规定。"
        if result.get("name") == "recommend_selection_plans":
            plans = data.get("plans", [])
            return _format_recommendation_answer(plans)
        if result.get("name") == "get_training_plan_context":
            courses = data.get("courses", [])
            if isinstance(courses, list) and len(courses) == 1:
                course = courses[0]
                if isinstance(course, dict):
                    eligibility = course.get("eligibility", {})
                    if isinstance(eligibility, dict):
                        course_name = str(course.get("course_name", "该课程"))
                        if eligibility.get("decision") == "ALLOW":
                            return f"按照当前培养方案和个人修读状态，你本学期可以修读{course_name}。"
                        if eligibility.get("decision") == "BLOCK":
                            violations = eligibility.get("violations", [])
                            reasons = [
                                str(item.get("message"))
                                for item in violations
                                if isinstance(item, dict) and item.get("message")
                            ]
                            return (
                                f"按照当前培养方案和个人修读状态，你本学期暂不能修读{course_name}。"
                                + ("原因是：" + "；".join(reasons) if reasons else "")
                            )
            return "已查询你当前生效的培养方案，具体要求见下方结果。"
        if result.get("name") == "get_remaining_projects":
            return "已根据培养方案和当前资格整理剩余项目，具体分类见下方结果。"
    return "当前规则库中未找到相关说明。"


def _format_recommendation_answer(plans: object) -> str:
    """Render recommendation facts without asking the model to translate time data."""

    if not isinstance(plans, list) or not plans:
        return "目前没有找到同时满足资格、时间和顺序要求的场次。"

    lines = [f"已为你整理出{len(plans)}组推荐方案："]
    requirement_names = {"REQUIRED": "必做", "OPTIONAL": "选做"}
    for index, plan in enumerate(plans, start=1):
        if not isinstance(plan, dict):
            continue
        plan_name = str(plan.get("name") or f"推荐方案{index}")
        coverage = (
            "完整方案" if plan.get("coverage_status") == "COMPLETE" else "部分方案"
        )
        lines.append(f"\n{plan_name}（{coverage}）")

        retained = plan.get("retained_selections", [])
        sessions = plan.get("sessions", [])
        schedule_items: list[tuple[dict[str, object], bool]] = []
        if isinstance(retained, list):
            schedule_items.extend(
                (item, True) for item in retained if isinstance(item, dict)
            )
        if isinstance(sessions, list):
            schedule_items.extend(
                (item, False) for item in sessions if isinstance(item, dict)
            )

        for item, is_retained in schedule_items:
            day = int(item.get("day_of_week", 0))
            day_text = weekday_name(day)
            requirement = requirement_names.get(
                str(item.get("requirement_type", "")), "项目"
            )
            retained_text = "，已选" if is_retained else ""
            lines.append(
                f"- {item.get('project_name', '未命名项目')}（{requirement}{retained_text}）："
                f"第{item.get('week_no')}周{day_text} "
                f"第{item.get('start_slot')}—{item.get('end_slot')}节，"
                f"{item.get('laboratory_name', '实验室待定')}"
            )

        reasons = plan.get("reasons", [])
        if isinstance(reasons, list) and reasons:
            lines.append("推荐理由：" + "；".join(str(item) for item in reasons))
        warnings = plan.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            lines.append("注意：" + "；".join(str(item) for item in warnings))

        unmet = plan.get("unmet_requirements", [])
        if isinstance(unmet, list):
            unmet_reasons = [
                str(item.get("reason"))
                for item in unmet
                if isinstance(item, dict) and item.get("reason")
            ]
            if unmet_reasons:
                lines.append("尚未覆盖：" + "；".join(unmet_reasons))
    return "\n".join(lines)


def _recommendation_time_facts(
    state: StudentConsultationState,
) -> set[tuple[int, int, int, int]]:
    facts: set[tuple[int, int, int, int]] = set()
    for result in state.get("tool_results", []):
        if result.get("name") != "recommend_selection_plans":
            continue
        data = result.get("data", {})
        if not isinstance(data, dict):
            continue
        plans = data.get("plans", [])
        if not isinstance(plans, list):
            continue
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            for field in ("sessions", "retained_selections"):
                items = plan.get(field, [])
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    try:
                        facts.add(
                            (
                                int(item["week_no"]),
                                int(item["day_of_week"]),
                                int(item["start_slot"]),
                                int(item["end_slot"]),
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
    return facts


async def compose_clarification(
    state: StudentConsultationState,
) -> dict[str, object]:
    plan = state.get("plan")
    answer = (
        state.get("clarification_question")
        or (plan.clarification_question if plan else None)
        or "请补充具体课程、项目或场次信息。"
    )
    _emit("delta", {"text": answer})
    return {"answer_buffer": answer, "answer": answer}


async def compose_general_chat_stream(
    state: StudentConsultationState,
) -> dict[str, object]:
    _emit("status", {"phase": "answering", "message": "正在回复…"})
    model = state.get("model") or get_chat_model()
    fallback = "你好！我可以帮助你查询培养方案、核验选课资格、解释冲突并推荐实验场次。"
    if model is None:
        _emit("delta", {"text": fallback})
        return {"answer_buffer": fallback, "answer": fallback}
    conversation = [
        {"role": _message_role(item), "content": _message_content(item)}
        for item in state.get("conversation_context", [])
    ]
    messages = [
        SystemMessage(content=GENERAL_CHAT_PROMPT.read_text(encoding="utf-8")),
        HumanMessage(
            content=json.dumps(
                {
                    "conversation": conversation,
                    "current_question": state.get("current_question", ""),
                },
                ensure_ascii=False,
            )
        ),
    ]
    parts: list[str] = []
    async for chunk in model.astream(messages):
        text = chunk.content if isinstance(chunk.content, str) else ""
        if text:
            parts.append(text)
            _emit("delta", {"text": text})
    answer = "".join(parts).strip() or fallback
    return {"answer_buffer": answer, "answer": answer}


async def compose_boundary_notice(
    state: StudentConsultationState,
) -> dict[str, object]:
    answer = (
        "这个问题超出了当前助手的服务范围。"
        "我主要帮助处理物理实验培养方案、选课资格、冲突解释和场次推荐。"
    )
    _emit("delta", {"text": answer})
    return {"answer_buffer": answer, "answer": answer}


async def compose_rule_not_found(
    state: StudentConsultationState,
) -> dict[str, object]:
    answer = "这是物理实验相关问题，但当前规则库未明确此内容，我暂时无法给出确定答复。"
    _emit("delta", {"text": answer})
    return {"answer_buffer": answer, "answer": answer, "unknowns": [answer]}


async def compose_answer_stream(
    state: StudentConsultationState,
) -> dict[str, object]:
    _emit("status", {"phase": "answering", "message": "正在组织回答…"})
    model = state.get("model") or get_chat_model()
    deterministic = _deterministic_answer(state)
    if state.get("intent") == "RECOMMEND_SELECTION":
        # Exact schedule facts are rendered by the backend. The frontend still
        # animates this SSE delta character by character, while the LLM cannot
        # reinterpret the Sunday-first day_of_week value.
        _emit("delta", {"text": deterministic})
        return {"answer_buffer": deterministic, "answer": deterministic}
    if model is None:
        _emit("delta", {"text": deterministic})
        return {"answer_buffer": deterministic, "answer": deterministic}

    content = (
        f"<student_question>\n{state.get('current_question', '')}\n</student_question>\n"
        "<grounded_facts>\n"
        f"{json.dumps(state.get('grounding_bundle', {}), ensure_ascii=False, default=str)}\n"
        "</grounded_facts>\n"
        f"<required_conclusion>\n{deterministic}\n</required_conclusion>"
    )
    messages = [
        SystemMessage(content=COMPOSER_PROMPT.read_text(encoding="utf-8")),
        HumanMessage(content=content),
    ]
    parts: list[str] = []
    async for chunk in model.astream(messages):
        text = chunk.content if isinstance(chunk.content, str) else ""
        if not text:
            continue
        parts.append(text)
        _emit("delta", {"text": text})
    answer = "".join(parts).strip() or deterministic
    return {"answer_buffer": answer, "answer": answer}


async def validate_final_answer(
    state: StudentConsultationState,
) -> dict[str, object]:
    answer = state.get("answer_buffer", "")
    passed = True
    decision = None
    for result in state.get("tool_results", []):
        data = result.get("data", {})
        if isinstance(data, dict) and data.get("decision"):
            decision = data["decision"]
            break
        if result.get("name") == "get_training_plan_context" and isinstance(data, dict):
            courses = data.get("courses", [])
            if isinstance(courses, list) and len(courses) == 1:
                course = courses[0]
                if isinstance(course, dict) and isinstance(
                    course.get("eligibility"), dict
                ):
                    decision = course["eligibility"].get("decision")
                    break
    if decision == "BLOCK" and any(
        phrase in answer
        for phrase in (
            "可以选择",
            "具备选择资格",
            "允许选择",
            "可以修读",
            "具备修读资格",
        )
    ):
        passed = False
    if decision == "UNKNOWN" and not any(
        phrase in answer for phrase in ("无法确认", "信息不足", "未找到")
    ):
        passed = False
    if decision == "BLOCK" and not any(
        phrase in answer for phrase in ("不能修读", "不能选择", "暂不能", "不具备")
    ):
        passed = False
    if re.search(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
        r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b",
        answer,
    ):
        passed = False
    time_facts = _recommendation_time_facts(state)
    if time_facts:
        day_numbers = {"日": 1, "一": 2, "二": 3, "三": 4, "四": 5, "五": 6, "六": 7}
        for match in re.finditer(
            r"第\s*(\d+)\s*周\s*(?:周|星期)([日一二三四五六])\s*"
            r"(?:第\s*)?(\d+)\s*[—–－-]\s*(\d+)\s*节",
            answer,
        ):
            stated_fact = (
                int(match.group(1)),
                day_numbers[match.group(2)],
                int(match.group(3)),
                int(match.group(4)),
            )
            if stated_fact not in time_facts:
                passed = False
                break
    return {"grounding_passed": passed}


def route_after_grounding(state: StudentConsultationState) -> str:
    return (
        "build_cards"
        if state.get("grounding_passed", False)
        else "deterministic_fallback"
    )


async def deterministic_fallback(
    state: StudentConsultationState,
) -> dict[str, object]:
    answer = _deterministic_answer(state)
    _emit("delta", {"text": answer, "replace": True})
    return {"answer_buffer": answer, "answer": answer, "grounding_passed": True}


async def build_cards(
    state: StudentConsultationState,
) -> dict[str, object]:
    cards: list[ConsultationCard] = []
    for result in state.get("tool_results", []):
        name = result.get("name")
        data = result.get("data", {})
        if not isinstance(data, dict):
            continue
        if name in {"check_selection_eligibility", "explain_selection_conflicts"}:
            violations = data.get("violations", [])
            cards.append(
                ConsultationCard(
                    type="CONFLICT" if violations else "ELIGIBILITY",
                    title="选课资格检查",
                    summary=(
                        "可以选择"
                        if data.get("decision") == "ALLOW"
                        else "暂时不能选择"
                    ),
                    data=data,
                )
            )
        elif name == "recommend_selection_plans":
            cards.extend(
                ConsultationCard(
                    type="RECOMMENDATION",
                    title=str(plan.get("name", "推荐方案")),
                    summary=(
                        "完整覆盖当前可修课程要求"
                        if plan.get("coverage_status") == "COMPLETE"
                        else "当前最优部分方案，仍有要求未覆盖"
                    ),
                    data=plan,
                )
                for plan in data.get("plans", [])
                if isinstance(plan, dict)
            )
        elif name in {"get_training_plan_context", "get_remaining_projects"}:
            cards.append(
                ConsultationCard(
                    type="TRAINING_PLAN",
                    title="培养方案信息",
                    summary="以下内容来自当前生效的培养方案",
                    data=data,
                )
            )
    return {"cards": cards}


async def emit_error(
    state: StudentConsultationState,
) -> dict[str, object]:
    message = state.get("model_error") or "智能咨询服务暂时不可用，请稍后重试。"
    _emit(
        "error",
        {
            "code": "AI_PLANNER_UNAVAILABLE",
            "message": message,
            "trace_id": state.get("trace_id", ""),
        },
    )
    return {"answer": "", "answer_buffer": ""}


async def finalize_response(
    state: StudentConsultationState,
) -> dict[str, Any]:
    _emit(
        "final",
        {
            "intent": state.get("intent", "UNKNOWN"),
            "cards": [card.model_dump(mode="json") for card in state.get("cards", [])],
            "warnings": state.get("warnings", []),
            "unknowns": state.get("unknowns", []),
        },
    )
    _emit("done", {"trace_id": state.get("trace_id", "")})
    return {"answer": state.get("answer_buffer", "")}
