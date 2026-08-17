from collections.abc import AsyncIterator
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.student_advisor import (
    build_cards,
    build_grounding_bundle,
    compose_answer_stream,
    compose_boundary_notice,
    compose_clarification,
    compose_general_chat_stream,
    compose_rule_not_found,
    deterministic_fallback,
    emit_error,
    execute_student_tools,
    finalize_response,
    load_base_context,
    normalize_request,
    plan_with_llm,
    resolve_entities,
    route_after_entities,
    route_after_grounding,
    route_after_plan,
    route_after_tools,
    validate_final_answer,
    validate_plan,
)
from app.agents.states.student import StudentConsultationState

GRAPH_NAME = "student_consultation"
GRAPH_VERSION = "v2"
AGENT_CODE = "STUDENT_SELECTION_ADVISOR"
BUSINESS_TYPE = "STUDENT_CONSULTATION"


def build_student_graph():
    builder = StateGraph(StudentConsultationState)
    builder.add_node("normalize_request", normalize_request)
    builder.add_node("load_base_context", load_base_context)
    builder.add_node("plan_with_llm", plan_with_llm)
    builder.add_node("validate_plan", validate_plan)
    builder.add_node("emit_error", emit_error)
    builder.add_node("compose_clarification", compose_clarification)
    builder.add_node("compose_general_chat_stream", compose_general_chat_stream)
    builder.add_node("compose_boundary_notice", compose_boundary_notice)
    builder.add_node("compose_rule_not_found", compose_rule_not_found)
    builder.add_node("resolve_entities", resolve_entities)
    builder.add_node("execute_student_tools", execute_student_tools)
    builder.add_node("build_grounding_bundle", build_grounding_bundle)
    builder.add_node("compose_answer_stream", compose_answer_stream)
    builder.add_node("validate_final_answer", validate_final_answer)
    builder.add_node("deterministic_fallback", deterministic_fallback)
    builder.add_node("build_cards", build_cards)
    builder.add_node("finalize_response", finalize_response)

    builder.add_edge(START, "normalize_request")
    builder.add_edge("normalize_request", "load_base_context")
    builder.add_edge("load_base_context", "plan_with_llm")
    builder.add_edge("plan_with_llm", "validate_plan")
    builder.add_conditional_edges(
        "validate_plan",
        route_after_plan,
        {
            "emit_error": "emit_error",
            "compose_clarification": "compose_clarification",
            "compose_general_chat_stream": "compose_general_chat_stream",
            "compose_boundary_notice": "compose_boundary_notice",
            "execute_student_tools": "execute_student_tools",
            "resolve_entities": "resolve_entities",
        },
    )
    builder.add_conditional_edges(
        "resolve_entities",
        route_after_entities,
        {
            "compose_clarification": "compose_clarification",
            "build_grounding_bundle": "build_grounding_bundle",
            "execute_student_tools": "execute_student_tools",
        },
    )
    builder.add_conditional_edges(
        "execute_student_tools",
        route_after_tools,
        {
            "build_grounding_bundle": "build_grounding_bundle",
            "compose_rule_not_found": "compose_rule_not_found",
        },
    )
    builder.add_edge("build_grounding_bundle", "compose_answer_stream")
    builder.add_edge("compose_answer_stream", "validate_final_answer")
    builder.add_conditional_edges(
        "validate_final_answer",
        route_after_grounding,
        {
            "build_cards": "build_cards",
            "deterministic_fallback": "deterministic_fallback",
        },
    )
    builder.add_edge("deterministic_fallback", "build_cards")
    builder.add_edge("compose_clarification", "build_cards")
    builder.add_edge("compose_general_chat_stream", "build_cards")
    builder.add_edge("compose_boundary_notice", "build_cards")
    builder.add_edge("compose_rule_not_found", "build_cards")
    builder.add_edge("emit_error", END)
    builder.add_edge("build_cards", "finalize_response")
    builder.add_edge("finalize_response", END)
    return builder.compile()


_STUDENT_GRAPH = build_student_graph()


async def run_student_consultation(
    state: StudentConsultationState,
) -> StudentConsultationState:
    return await _STUDENT_GRAPH.ainvoke(state)


async def stream_student_consultation(
    state: StudentConsultationState,
) -> AsyncIterator[dict[str, Any]]:
    async for event in _STUDENT_GRAPH.astream(state, stream_mode="custom"):
        if isinstance(event, dict) and "event" in event:
            yield event
