from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.model_provider import get_chat_model, provider_failure_message
from app.agents.states.student import StudentConsultationState
from app.agents.tools.student_tools import (
    _canonical_name_matches,
    execute_planned_tools,
    resolve_plan_entities,
    training_plan_tool,
)
from app.cache.student_views import ai_context_key, get_or_build
from app.core.config.settings import get_settings
from app.models.identity import Campus, Major, Student
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.schemas.student_consultation import (
    ConsultationCard,
    ConsultationMessage,
    SelectionPreferences,
    StudentAgentPlan,
    weekday_name,
)
from app.services.selection_window_service import get_term_window
from app.services.student_adjustment_service import get_adjustment_context

SHANGHAI = ZoneInfo("Asia/Shanghai")

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "student_advisor"
PLANNER_PROMPT = PROMPT_DIR / "planner_v2.md"
COMPOSER_PROMPT = PROMPT_DIR / "composer_v2.md"
GENERAL_CHAT_PROMPT = PROMPT_DIR / "general_chat_v1.md"

logger = logging.getLogger(__name__)

REQUIRED_TOOL_BY_INTENT = {
    "BASIC_INFO_QUERY": {
        "lookup_student_rules",
        "get_training_plan_context",
        "get_remaining_projects",
    },
    "CHECK_ELIGIBILITY": {"check_selection_eligibility"},
    "EXPLAIN_CONFLICT": {"explain_selection_conflicts"},
    "RECOMMEND_SELECTION": {"recommend_selection_plans"},
    "DESELECT_SELECTION": {"preview_deselection"},
    "SYSTEM_GUIDE": {"lookup_operation_guide"},
    "START_ADJUSTMENT": {"prepare_adjustment_entry"},
}


async def _release_read_transaction(state: StudentConsultationState) -> None:
    """Return a read-only DB connection before waiting on the model provider."""

    session = state.get("session")
    if isinstance(session, AsyncSession) and session.in_transaction():
        await session.commit()


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


# AI 回答里的"推荐理由/注意"段由后端生成、含"符合XX偏好"等字样；若原样
# 进入下一轮 planner 的对话上下文，会被模型当作学生偏好来源，造成偏好漂移
# （如历史里出现"早上、下午偏好"）。裁剪到该段之前，保留场次清单等中性内容。
_PREFERENCE_POLLUTION_PATTERN = re.compile(r"(推荐理由|注意)[:：]")


def _planner_message_content(message: ConsultationMessage | dict[str, object]) -> str:
    content = _message_content(message)
    if _message_role(message) == "assistant":
        content = _PREFERENCE_POLLUTION_PATTERN.split(content, 1)[0]
    return content


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
    if "base_context" in state:
        return {}
    settings = get_settings()
    base_context = await get_or_build(
        ai_context_key(state["student_id"], state["term"].id),
        ttl=settings.student_ai_context_cache_ttl_seconds,
        builder=lambda: _build_base_context(state),
    )
    return {"base_context": base_context}


async def _build_base_context(state: StudentConsultationState) -> dict[str, object]:
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
    selection_context = await get_adjustment_context(
        session,
        student_id=state["student_id"],
        term=state["term"],
        request_type=None,
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
    current_selections: list[dict[str, object]] = []
    for source in selection_context.get("sources", []):
        if not isinstance(source, dict):
            continue
        session_fact = source.get("session")
        if not isinstance(session_fact, dict):
            continue
        current_selections.append(
            {
                "status": source.get("status", "SELECTED"),
                "course_name": session_fact.get("course_name"),
                "project_name": session_fact.get("project_name"),
                "requirement_type": session_fact.get("requirement_type"),
                "week_no": session_fact.get("week_no"),
                "day_of_week": session_fact.get("day_of_week"),
                "day_name": session_fact.get("day_name"),
                "start_slot": session_fact.get("start_slot"),
                "end_slot": session_fact.get("end_slot"),
                "teacher_name": session_fact.get("teacher_name"),
                "laboratory_name": session_fact.get("laboratory_name"),
            }
        )
    today = datetime.now(UTC).date()
    if today < state["term"].start_date:
        current_week = 0
    else:
        current_week = (today - state["term"].start_date).days // 7 + 1
    window = await get_term_window(session, state["term"].id)
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
            "current_week": current_week,
            "total_weeks": state["term"].total_weeks,
        },
        "selection_window": (
            {
                "start_at": window.start_at.isoformat(),
                "end_at": window.end_at.isoformat(),
                "withdraw_end_at": (
                    window.withdraw_end_at.isoformat()
                    if window.withdraw_end_at
                    else None
                ),
                "status": window.status,
            }
            if window is not None
            else None
        ),
        "training_plan_summary": {
            "plan_code": plan.get("plan_code") if isinstance(plan, dict) else None,
            "courses": course_summaries,
        },
        "current_selections": current_selections,
        "page": page,
    }
    return base_context


def _planner_input(state: StudentConsultationState) -> list[object]:
    conversation = [
        {"role": _message_role(item), "content": _planner_message_content(item)}
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


def _provider_failure_message(error: Exception) -> str | None:
    """See model_provider.provider_failure_message (kept for back-compat)."""

    return provider_failure_message(error)


async def plan_with_llm(
    state: StudentConsultationState,
) -> dict[str, object]:
    if state.get("model_error"):
        return {}
    await _release_read_transaction(state)
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
    except Exception as first_error:
        provider_message = _provider_failure_message(first_error)
        if provider_message:
            logger.warning(
                "Student AI planner provider failure: %s",
                provider_message,
                exc_info=True,
            )
            return {
                "model_error": provider_message,
                "intent": "UNKNOWN",
                "repaired_plan_attempted": False,
            }
        try:
            if isinstance(first_error, ValidationError):
                error_detail = json.dumps(
                    first_error.errors(), ensure_ascii=False, default=str
                )
            else:
                error_detail = str(first_error)
            logger.warning(
                "Student AI planner output invalid, repairing: %s",
                error_detail,
                exc_info=True,
            )
            repair_messages = [
                *messages,
                HumanMessage(
                    content="上一次输出无法解析。请只输出一个符合Schema的JSON对象，"
                    f"不要解释。校验错误：{error_detail}"
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
        except Exception as repair_error:
            provider_message = _provider_failure_message(repair_error)
            if provider_message:
                logger.warning(
                    "Student AI planner provider failure during repair: %s",
                    provider_message,
                    exc_info=True,
                )
                return {
                    "model_error": provider_message,
                    "intent": "UNKNOWN",
                    "repaired_plan_attempted": True,
                }
            return {
                "model_error": f"规划输出无法解析：{type(repair_error).__name__}",
                "intent": "UNKNOWN",
                "repaired_plan_attempted": True,
            }


def _merge_tool_preference_arguments(plan: StudentAgentPlan) -> StudentAgentPlan:
    """模型有时会把偏好写进 recommend_selection_plans 的参数而非顶层 preferences，
    导致教师/模块偏好在后端评分时丢失。此处合并兜底：顶层已填字段优先，
    未填字段用工具参数补齐。

    lookup_student_rules 同理：模型偶尔把 rule_topics 写进 arguments 而非顶层
    rule_topics 字段，会导致安全校验拒绝计划（"业务规则咨询必须提供受控规则主题"），
    并让执行阶段拿不到主题。此处归一化兜底。"""
    for request in plan.tool_requests:
        if request.name == "lookup_student_rules" and not plan.rule_topics:
            arg_topics = request.arguments.get("rule_topics")
            if isinstance(arg_topics, list) and arg_topics:
                valid = [t for t in arg_topics if isinstance(t, str)]
                if valid:
                    plan = plan.model_copy(update={"rule_topics": valid})
        if request.name != "recommend_selection_plans":
            continue
        raw = request.arguments.get("preference")
        if isinstance(raw, str):
            raw = json.loads(raw) if raw.strip() else None
        if not isinstance(raw, dict):
            continue
        try:
            arg_preferences = SelectionPreferences.model_validate(raw)
        except ValidationError:
            continue
        updates: dict[str, object] = {}
        for field in SelectionPreferences.model_fields:
            current_value = getattr(plan.preferences, field)
            fallback_value = getattr(arg_preferences, field)
            if (
                current_value in (None, False, [])
                and fallback_value not in (None, False, [])
            ):
                updates[field] = fallback_value
        if not updates:
            continue
        plan = plan.model_copy(
            update={
                "preferences": plan.preferences.model_copy(update=updates)
            }
        )
    return plan


async def validate_plan(
    state: StudentConsultationState,
) -> dict[str, object]:
    if state.get("model_error"):
        return {}
    plan = state.get("plan")
    if plan is None:
        return {"model_error": "模型没有生成执行计划。"}
    plan = _merge_tool_preference_arguments(plan)
    reference = plan.entity_reference
    if (
        plan.operation_stage == "PLAN_DRAFT"
        and reference is not None
        and reference.project_name is not None
        and re.fullmatch(r"(?:AI)?(?:推荐)?方案(?:草稿|[一二三123])?", reference.project_name.strip(), re.IGNORECASE)
    ):
        # “方案1/推荐方案”是草稿标识，不是实验项目实体。
        plan = plan.model_copy(
            update={"entity_reference": reference.model_copy(update={"project_name": None})}
        )
    # 需要澄清时不会执行任何工具。弱模型有时会一边提问一边附带工具，
    # 服务端在安全边界统一去除，避免旧对话中的实体被误用于个人业务查询。
    plan_sanitized = bool(plan.needs_clarification and plan.tool_requests)
    if plan_sanitized:
        plan = plan.model_copy(update={"tool_requests": []})
    errors: list[str] = []
    names = {request.name for request in plan.tool_requests}
    action_tools = {"preview_deselection", "prepare_adjustment_entry"}
    allowed_for_intent = REQUIRED_TOOL_BY_INTENT.get(plan.intent, set())
    if (
        not plan.needs_clarification
        and plan.intent in REQUIRED_TOOL_BY_INTENT
        and not names.intersection(allowed_for_intent)
        and plan.term_fact_query == "NONE"
    ):
        # 学期事实(周次/选课窗口)由上下文确定性回答,无需工具调用。
        errors.append("当前意图缺少必须的只读工具调用。")
    if plan.intent == "UNKNOWN" and names:
        errors.append("UNKNOWN意图不应调用业务工具。")
    if plan.intent in {"GENERAL_CHAT", "OUT_OF_SCOPE"} and names:
        errors.append("普通交互和业务外问题不得调用业务工具。")
    if plan.intent == "GENERAL_CHAT" and not plan.direct_answer_allowed:
        errors.append("普通交互必须明确允许直接回答。")
    if plan.request_mode in {"ASK_CAPABILITY", "ASK_STEPS"} and names.intersection(
        action_tools
    ):
        errors.append("询问能力或步骤时不得执行退选预览或个人调整工具。")
    if plan.operation_stage == "PLAN_DRAFT" and names.intersection(action_tools):
        errors.append("推荐方案草稿不得调用已选课退选或调整工具。")
    if plan.request_mode == "SAFETY_REFUSAL" and names:
        errors.append("安全拒绝场景不得调用业务工具。")
    if plan.term_fact_query != "NONE":
        # 学期事实(周次/选课窗口)由上下文确定性回答,不允许模型附加工具或规则主题。
        if names:
            errors.append("学期事实查询不得调用业务工具。")
        if plan.rule_topics:
            errors.append("学期事实查询不应携带公共规则主题。")
        if plan.intent != "BASIC_INFO_QUERY":
            errors.append("学期事实查询的意图应为基本信息查询。")
    if plan.intent == "QUERY_CURRENT_SELECTION" and names.difference(
        {"get_training_plan_context"}
    ):
        errors.append("当前选课项目状态查询包含不相关的工具调用。")
    if plan.intent == "BASIC_INFO_QUERY" and "lookup_student_rules" in names and not plan.rule_topics:
        errors.append("业务规则咨询必须提供受控规则主题。")
    if plan.intent == "BASIC_INFO_QUERY" and names.difference(allowed_for_intent):
        errors.append("基本信息查询包含不相关的工具调用。")
    if (
        plan.intent == "BASIC_INFO_QUERY"
        and "lookup_student_rules" not in names
        and plan.rule_topics
    ):
        errors.append("培养方案查询不应提供公共规则主题。")
    if plan.requested_application_type is not None and plan.intent not in {
        "SYSTEM_GUIDE",
        "START_ADJUSTMENT",
    }:
        errors.append("打开申请界面的动作只能用于系统操作指南意图。")
    for request in plan.tool_requests:
        forbidden = {"student_id", "term_id", "sql", "url"}.intersection(
            request.arguments
        )
        if forbidden:
            errors.append("工具参数包含模型无权提供的字段。")
    if errors:
        logger.warning(
            "Planner plan rejected errors=%s plan=%s",
            errors,
            plan.model_dump(mode="json"),
        )
        return {
            "plan_validation_errors": errors,
            "model_error": "模型执行计划未通过安全校验。",
        }
    if plan_sanitized:
        return {
            "plan": plan,
            "tool_requests": plan.tool_requests,
            "plan_validation_errors": [],
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
    if plan.intent == "SYSTEM_GUIDE":
        return "execute_student_tools"
    if plan.intent == "BASIC_INFO_QUERY" and {
        request.name for request in plan.tool_requests
    } == {"lookup_student_rules"}:
        return "execute_student_tools"
    return "resolve_entities"


async def resolve_entities(
    state: StudentConsultationState,
) -> dict[str, object]:
    plan = state["plan"]
    assert plan is not None
    if plan.intent == "QUERY_CURRENT_SELECTION":
        reference = plan.entity_reference
        selection_scope = (
            "FILTERED"
            if reference
            and any(
                (
                    reference.course_name,
                    reference.project_name,
                    reference.teacher_name,
                    reference.week_no,
                    reference.day_name,
                    reference.start_slot,
                    reference.end_slot,
                )
            )
            else "ALL"
        )
        project_name = reference.project_name if reference else None
        if project_name and not (reference and reference.conversation_reference):
            compact_name = "".join(project_name.split())
            compact_question = "".join(state.get("current_question", "").split())
            if compact_name not in compact_question:
                project_name = None
        courses = (
            state.get("base_context", {})
            .get("training_plan_summary", {})
            .get("courses", [])
        )
        selection_items: list[dict[str, object]] = []
        current_selections = state.get("base_context", {}).get("current_selections")
        if isinstance(current_selections, list):
            for source in current_selections:
                if not isinstance(source, dict):
                    continue
                if project_name and not _canonical_name_matches(
                    str(source.get("project_name") or ""), project_name
                ):
                    continue
                if reference and reference.course_name and not _canonical_name_matches(
                    str(source.get("course_name") or ""), reference.course_name
                ):
                    continue
                if reference and reference.teacher_name and not _canonical_name_matches(
                    str(source.get("teacher_name") or ""), reference.teacher_name
                ):
                    continue
                if reference and reference.week_no is not None and (
                    source.get("week_no") != reference.week_no
                ):
                    continue
                if reference and reference.day_name is not None and (
                    source.get("day_name") != reference.day_name
                ):
                    continue
                if reference and reference.start_slot is not None and (
                    source.get("start_slot") != reference.start_slot
                ):
                    continue
                if reference and reference.end_slot is not None and (
                    source.get("end_slot") != reference.end_slot
                ):
                    continue
                selection_items.append(
                    {
                        **source,
                        "student_status": source.get("status", "SELECTED"),
                    }
                )
            if selection_items or not project_name:
                return {
                    "resolved_entities": {
                        "selection_items": selection_items,
                        "selection_scope": selection_scope,
                    },
                    "clarification_question": None,
                }
        if not project_name:
            matched_course = False
            for course in courses if isinstance(courses, list) else []:
                if not isinstance(course, dict):
                    continue
                if (
                    reference
                    and reference.course_name
                    and not _canonical_name_matches(
                        str(course.get("course_name") or ""),
                        reference.course_name,
                    )
                ):
                    continue
                matched_course = True
                for project in course.get("projects", []):
                    if not isinstance(project, dict):
                        continue
                    if str(project.get("student_status") or "NOT_SELECTED") not in {
                        "SELECTED",
                        "COMPLETED",
                        "ABSENT",
                        "MAKEUP_PENDING",
                    }:
                        continue
                    selection_items.append(
                        {
                            **project,
                            "course_id": course.get("course_id"),
                            "course_name": course.get("course_name"),
                        }
                    )
            if reference and reference.course_name and not matched_course:
                return {
                    "resolved_entities": {},
                    "clarification_question": "当前培养方案中未找到这门实验课程，请确认课程名称。",
                }
            return {
                "resolved_entities": {
                    "selection_items": selection_items,
                    "selection_scope": selection_scope,
                },
                "clarification_question": None,
            }
        matches: list[dict[str, object]] = []
        for course in courses if isinstance(courses, list) else []:
            if not isinstance(course, dict):
                continue
            if reference.course_name and not _canonical_name_matches(
                str(course.get("course_name") or ""), reference.course_name
            ):
                continue
            for project in course.get("projects", []):
                if isinstance(project, dict) and _canonical_name_matches(
                    str(project.get("project_name") or ""), project_name
                ):
                    matches.append(
                        {
                            **project,
                            "course_id": course.get("course_id"),
                            "course_name": course.get("course_name"),
                        }
                    )
        if len(matches) == 1:
            return {
                "resolved_entities": matches[0],
                "clarification_question": None,
            }
        if len(matches) > 1:
            return {
                "resolved_entities": {},
                "clarification_question": "找到多个名称相近的实验项目，请补充所属课程。",
            }
        return {
            "resolved_entities": {},
            "clarification_question": "当前培养方案中未找到这个实验项目，请确认项目名称。",
        }
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
    if state.get("clarification_question"):
        return "compose_clarification"
    if state.get("intent") == "QUERY_CURRENT_SELECTION":
        return "build_grounding_bundle"
    return "execute_student_tools"


def _format_current_selection_answer(resolved: dict[str, object]) -> str:
    selection_items = resolved.get("selection_items")
    if isinstance(selection_items, list):
        if not selection_items:
            if resolved.get("selection_scope") == "FILTERED":
                return "你本学期已选课表中没有找到符合这些条件的实验场次。"
            return "你本学期当前还没有已选择或已完成的实验项目。"
        status_labels = {
            "SELECTED": "已选",
            "COMPLETED": "已完成",
            "ABSENT": "缺席",
            "MAKEUP_PENDING": "补做处理中",
        }
        grouped: dict[str, list[str]] = {}
        for item in selection_items:
            if not isinstance(item, dict):
                continue
            course_name = str(item.get("course_name") or "未标明课程")
            project_name = str(item.get("project_name") or "未命名项目")
            status = str(item.get("student_status") or "SELECTED")
            details: list[str] = []
            if item.get("week_no") is not None and item.get("day_name"):
                details.append(f"第{item['week_no']}周{item['day_name']}")
            if item.get("start_slot") is not None and item.get("end_slot") is not None:
                details.append(f"第{item['start_slot']}—{item['end_slot']}节")
            if item.get("teacher_name"):
                details.append(f"{item['teacher_name']}老师")
            if item.get("laboratory_name"):
                details.append(str(item["laboratory_name"]))
            detail_text = f"，{'，'.join(details)}" if details else ""
            grouped.setdefault(course_name, []).append(
                f"{project_name}（{status_labels.get(status, status)}{detail_text}）"
            )
        if resolved.get("selection_scope") == "FILTERED":
            lines = [f"在你本学期的已选课表中，找到 {len(selection_items)} 个匹配场次："]
        else:
            lines = [f"你本学期共选择过 {len(selection_items)} 个实验项目："]
        lines.extend(
            f"- {course_name}：{'、'.join(projects)}"
            for course_name, projects in grouped.items()
        )
        return "\n".join(lines)
    project_name = str(resolved.get("project_name") or "该实验项目")
    course_name = str(resolved.get("course_name") or "")
    course_text = f"（{course_name}）" if course_name else ""
    status = str(resolved.get("student_status") or "NOT_SELECTED")
    if status == "SELECTED":
        return f"是的，你当前已经选择了“{project_name}”{course_text}。"
    if status == "COMPLETED":
        return f"是的，你已经完成了“{project_name}”{course_text}。"
    if status == "ABSENT":
        return f"你选择过“{project_name}”{course_text}，但当前记录状态为缺席，可以查看是否符合补做申请条件。"
    if status == "MAKEUP_PENDING":
        return f"你选择过“{project_name}”{course_text}，当前正在等待补做安排完成。"
    return f"没有，你当前尚未选择“{project_name}”{course_text}。"


async def execute_student_tools(
    state: StudentConsultationState,
) -> dict[str, object]:
    plan = state["plan"]
    assert plan is not None
    status_message = {
        "BASIC_INFO_QUERY": "正在查询相关规则或培养方案信息…",
        "RECOMMEND_SELECTION": "正在筛选符合条件的实验场次…",
        "DESELECT_SELECTION": "正在匹配你当前已选的实验场次…",
        "SYSTEM_GUIDE": "正在查找学生端操作指南…",
        "START_ADJUSTMENT": "正在匹配你当前已选的实验…",
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
        question=state.get("current_question", ""),
    )
    return {"tool_results": results}


def route_after_tools(state: StudentConsultationState) -> str:
    if state.get("intent") != "BASIC_INFO_QUERY":
        return "build_grounding_bundle"
    if any(
        result.get("name") != "lookup_student_rules"
        for result in state.get("tool_results", [])
    ):
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
    if state.get("intent") == "QUERY_CURRENT_SELECTION":
        immutable_facts["base_context_selection"] = state.get(
            "resolved_entities", {}
        )
    base = state.get("base_context") or {}
    if isinstance(base, dict):
        # 周次与选课窗口是可信上下文事实,随 grounding bundle 交给 composer,
        # 使"现在是第几周""选课截止时间"类问题无需工具调用也能有据回答。
        immutable_facts["term_schedule"] = {
            "current_week": base.get("term", {}).get("current_week"),
            "total_weeks": base.get("term", {}).get("total_weeks"),
            "selection_window": base.get("selection_window"),
        }
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
    plan = state.get("plan")
    if plan is not None and plan.term_fact_query != "NONE":
        return _format_term_fact_answer(state, plan.term_fact_query)
    if state.get("intent") == "QUERY_CURRENT_SELECTION":
        return _format_current_selection_answer(state.get("resolved_entities", {}))
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
            return _format_remaining_projects_answer(data)
        if result.get("name") == "preview_deselection":
            sessions = data.get("sessions", [])
            if not isinstance(sessions, list) or not sessions:
                return str(data.get("message") or "没有匹配到当前可取消的已选场次。")
            return (
                f"已根据你的描述匹配到 {len(sessions)} 个已选实验场次。"
                "请核对下方清单；确认无误后回复“确认取消”。"
            )
        if result.get("name") == "lookup_operation_guide":
            return str(data.get("answer") or "当前操作指南中没有找到明确说明。")
        if result.get("name") == "prepare_adjustment_entry":
            return str(data.get("message") or "请核对下方匹配到的原实验。")
    return "当前规则库中未找到相关说明。"


def _format_term_fact_answer(
    state: StudentConsultationState, kind: str
) -> str:
    """学期事实(周次/选课窗口)的确定性回答,不经过 LLM。

    数据来自 base_context(Redis 缓存 30 分钟),窗口为低频配置,
    滞后窗口可接受;时间统一转北京时间呈现。
    """

    base = state.get("base_context") or {}
    term = base.get("term") or {}
    if kind == "CURRENT_WEEK":
        current_week = term.get("current_week")
        total_weeks = term.get("total_weeks")
        if current_week is None or total_weeks is None:
            return "当前学期信息尚未就绪，请稍后再试。"
        return f"当前是第{current_week}教学周（本学期共{total_weeks}周）。"

    window = base.get("selection_window")
    if not isinstance(window, dict) or not window.get("start_at"):
        return "当前尚未配置选课窗口，暂时无法选课。"

    def _beijing(value: str) -> str:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(SHANGHAI).strftime("%m月%d日 %H:%M")

    start_at = _beijing(window["start_at"])
    end_at = _beijing(window["end_at"])
    withdraw_raw = window.get("withdraw_end_at")
    withdraw_end = _beijing(withdraw_raw) if withdraw_raw else None
    now = datetime.now(SHANGHAI)
    if window.get("start_at") and now < datetime.fromisoformat(str(window["start_at"])):
        state_label = "选课尚未开始"
    elif window.get("end_at") and now <= datetime.fromisoformat(str(window["end_at"])):
        state_label = "当前正在选课开放时间内"
    elif withdraw_raw and now <= datetime.fromisoformat(str(withdraw_raw)):
        state_label = "选课已截止，但仍可退选"
    else:
        state_label = "选课与退选均已结束"
    parts = [
        f"选课开放时间：{start_at} 至 {end_at}。",
    ]
    if withdraw_end:
        parts.append(f"退选截止时间：{withdraw_end}。")
    parts.append(f"当前状态：{state_label}。")
    return "".join(parts)


def _format_remaining_projects_answer(data: dict[str, object]) -> str:
    progress_items = data.get("course_progress", [])
    if not isinstance(progress_items, list) or not progress_items:
        return "当前没有查到可用于计算必做、选做进度的培养方案信息。"

    lines = ["根据当前培养方案和选课记录，你的实验项目进度如下："]
    eligible_items: list[dict[str, object]] = []
    for item in progress_items:
        if not isinstance(item, dict):
            continue
        course_name = str(item.get("course_name") or "未命名课程")
        required = item.get("required", {})
        optional = item.get("optional", {})
        if not isinstance(required, dict) or not isinstance(optional, dict):
            continue
        if not item.get("eligible"):
            reasons = [
                str(violation.get("message"))
                for violation in item.get("eligibility_violations", [])
                if isinstance(violation, dict) and violation.get("message")
            ]
            suffix = f"，原因：{'；'.join(reasons)}" if reasons else ""
            lines.append(f"\n{course_name}：本学期暂不具备修读资格，不计入当前待选数量{suffix}。")
            continue

        eligible_items.append(item)
        lines.extend(
            [
                f"\n{course_name}",
                (
                    "- 必做："
                    f"已选择 {required.get('selected', 0)}/{required.get('total', 0)}，"
                    f"还需选择 {required.get('remaining_to_select', 0)} 个。"
                ),
                (
                    "- 选做："
                    f"已选择 {optional.get('selected', 0)}/{optional.get('minimum', 0)}，"
                    f"还需选择 {optional.get('remaining_to_select', 0)} 个。"
                ),
            ]
        )

    summary = data.get("summary", {})
    total_remaining = (
        int(summary.get("total_remaining_to_select", 0))
        if isinstance(summary, dict)
        else 0
    )
    if total_remaining <= 0:
        lines.append("\n当前无需再选择新的实验项目。")
        return "\n".join(lines)

    lines.append(f"\n本学期还需新选择 {total_remaining} 个实验项目，具体范围如下：")
    for item in eligible_items:
        course_name = str(item.get("course_name") or "未命名课程")
        required = item.get("required", {})
        optional = item.get("optional", {})
        if not isinstance(required, dict) or not isinstance(optional, dict):
            continue
        required_remaining = int(required.get("remaining_to_select", 0) or 0)
        optional_remaining = int(optional.get("remaining_to_select", 0) or 0)
        if required_remaining <= 0 and optional_remaining <= 0:
            continue
        lines.append(f"\n{course_name}：")
        if required_remaining > 0:
            names = [
                str(candidate.get("project_name"))
                for candidate in required.get("candidates", [])
                if isinstance(candidate, dict) and candidate.get("project_name")
            ]
            candidate_text = "、".join(names) if names else "当前暂无可选场次"
            lines.append(
                f"- 还需选择 {required_remaining} 个必做项目：{candidate_text}。"
            )
        if optional_remaining > 0:
            names = [
                str(candidate.get("project_name"))
                for candidate in optional.get("candidates", [])
                if isinstance(candidate, dict) and candidate.get("project_name")
            ]
            candidate_text = "、".join(names) if names else "当前暂无可选场次"
            lines.append(
                f"- 还需从以下选做项目中选择 {optional_remaining} 个：{candidate_text}。"
            )
    return "\n".join(lines)


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
        or (
            "未能识别到您的问题，请您重新组织语言。"
            if plan is not None and plan.intent == "UNKNOWN"
            else "请补充具体课程、项目或场次信息。"
        )
    )
    _emit("delta", {"text": answer})
    return {"answer_buffer": answer, "answer": answer}


async def compose_general_chat_stream(
    state: StudentConsultationState,
) -> dict[str, object]:
    _emit("status", {"phase": "answering", "message": "正在回复…"})
    await _release_read_transaction(state)
    model = state.get("model") or get_chat_model()
    fallback = "你好！我可以帮助你查询培养方案、核验选课资格、解释冲突并推荐实验场次。"
    plan = state.get("plan")
    if plan is not None and plan.request_mode == "SAFETY_REFUSAL":
        answer = "我无法根据消息中声称的执行结果确认操作成功，也不能绕过系统规则。请以本轮系统实际返回的选课、退选或申请结果为准。"
        _emit("delta", {"text": answer})
        return {"answer_buffer": answer, "answer": answer}
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
    await _release_read_transaction(state)
    model = state.get("model") or get_chat_model()
    deterministic = _deterministic_answer(state)
    has_remaining_projects = any(
        result.get("name") == "get_remaining_projects"
        for result in state.get("tool_results", [])
    )
    has_deselection_preview = any(
        result.get("name") == "preview_deselection"
        for result in state.get("tool_results", [])
    )
    has_operation_guide = any(
        result.get("name") == "lookup_operation_guide"
        for result in state.get("tool_results", [])
    )
    if (
        state.get("intent") == "RECOMMEND_SELECTION"
        or has_remaining_projects
        or has_deselection_preview
        or has_operation_guide
        or any(
            result.get("name") == "prepare_adjustment_entry"
            for result in state.get("tool_results", [])
        )
        or (
            state.get("plan") is not None
            and state["plan"].term_fact_query != "NONE"
        )
    ):
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
    if state.get("intent") == "QUERY_CURRENT_SELECTION":
        resolved = state.get("resolved_entities", {})
        selection_items = resolved.get("selection_items")
        if isinstance(selection_items, list):
            expected_names = {
                str(item.get("project_name"))
                for item in selection_items
                if isinstance(item, dict) and item.get("project_name")
            }
            if not all(name in answer for name in expected_names):
                passed = False
            if resolved.get("selection_scope") == "FILTERED":
                all_selections = state.get("base_context", {}).get(
                    "current_selections", []
                )
                forbidden_names = {
                    str(item.get("project_name"))
                    for item in all_selections
                    if isinstance(item, dict)
                    and item.get("project_name")
                    and str(item.get("project_name")) not in expected_names
                }
                if any(name in answer for name in forbidden_names):
                    passed = False
        else:
            status = str(resolved.get("student_status") or "NOT_SELECTED")
            if status in {"SELECTED", "COMPLETED", "ABSENT", "MAKEUP_PENDING"} and (
                "尚未选择" in answer or "没有选择" in answer
            ):
                passed = False
            if status == "NOT_SELECTED" and any(
                phrase in answer for phrase in ("已经选择", "已经完成", "选择过")
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
                    data={
                        **plan,
                        "preferences": (
                            state.get("preferences").model_dump(mode="json")
                            if state.get("preferences") is not None
                            else {}
                        ),
                    },
                )
                for plan in data.get("plans", [])
                if isinstance(plan, dict)
            )
        elif name == "preview_deselection":
            sessions = data.get("sessions", [])
            count = len(sessions) if isinstance(sessions, list) else 0
            cards.append(
                ConsultationCard(
                    type="DESELECTION",
                    title="取消选课确认",
                    summary=f"将取消 {count} 个已选实验场次",
                    data=data,
                )
            )
        elif name == "lookup_operation_guide":
            matches = data.get("matches", [])
            title = "学生端操作指南"
            if isinstance(matches, list) and matches and isinstance(matches[0], dict):
                title = str(matches[0].get("title") or title)
            guide = data.get("guide")
            guide_topic = str(guide.get("topic") or "") if isinstance(guide, dict) else ""
            application_topics = {
                "ADJUSTMENT_APPLICATION",
                "RESCHEDULE_APPLICATION",
                "PROJECT_CHANGE_APPLICATION",
                "MAKEUP_APPLICATION",
            }
            requested_application_type = None
            if guide_topic in application_topics and state.get("plan") is not None:
                requested_application_type = state["plan"].requested_application_type
            cards.append(
                ConsultationCard(
                    type="GUIDE",
                    title=title,
                    summary=str(data.get("source") or "学生端操作指南"),
                    data={
                        **data,
                        "requested_application_type": requested_application_type,
                    },
                )
            )
        elif name == "prepare_adjustment_entry":
            cards.append(
                ConsultationCard(
                    type="APPLICATION_ENTRY",
                    title=str(data.get("title") or "确认需要调整的原实验"),
                    summary=str(data.get("message") or "请先核对原实验"),
                    data=data,
                )
            )
        elif name == "get_remaining_projects":
            summary = data.get("summary", {})
            total = (
                summary.get("total_remaining_to_select", 0)
                if isinstance(summary, dict)
                else 0
            )
            cards.append(
                ConsultationCard(
                    type="TRAINING_PLAN",
                    title="实验项目进度",
                    summary=f"当前还需新选择 {total} 个实验项目",
                    data=data,
                )
            )
        elif name == "get_training_plan_context":
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
