from collections.abc import AsyncIterator
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.adjustment_agent import (
    build_adjustment_cards,
    build_grounding_bundle,
    compose_answer_stream,
    execute_adjustment_tool,
    load_student_adjustment_context,
    normalize_adjustment_request,
    plan_with_llm,
    resolve_source_record,
    validate_final_answer,
    validate_plan,
)
from app.agents.states.adjustment import StudentAdjustmentState

GRAPH_NAME = "student_adjustment"
GRAPH_VERSION = "v1"
AGENT_CODE = "STUDENT_ADJUSTMENT_ADVISOR"
BUSINESS_TYPE = "STUDENT_ADJUSTMENT"


def build_adjustment_graph():
    graph = StateGraph(StudentAdjustmentState)
    graph.add_node("normalize_adjustment_request", normalize_adjustment_request)
    graph.add_node("plan_with_llm", plan_with_llm)
    graph.add_node("validate_plan", validate_plan)
    graph.add_node("load_student_adjustment_context", load_student_adjustment_context)
    graph.add_node("resolve_source_record", resolve_source_record)
    graph.add_node("execute_adjustment_tool", execute_adjustment_tool)
    graph.add_node("build_grounding_bundle", build_grounding_bundle)
    graph.add_node("compose_answer_stream", compose_answer_stream)
    graph.add_node("validate_final_answer", validate_final_answer)
    graph.add_node("build_adjustment_cards", build_adjustment_cards)
    graph.add_edge(START, "normalize_adjustment_request")
    graph.add_edge("normalize_adjustment_request", "plan_with_llm")
    graph.add_edge("plan_with_llm", "validate_plan")
    graph.add_edge("validate_plan", "load_student_adjustment_context")
    graph.add_edge("load_student_adjustment_context", "resolve_source_record")
    graph.add_edge("resolve_source_record", "execute_adjustment_tool")
    graph.add_edge("execute_adjustment_tool", "build_grounding_bundle")
    graph.add_edge("build_grounding_bundle", "compose_answer_stream")
    graph.add_edge("compose_answer_stream", "validate_final_answer")
    graph.add_edge("validate_final_answer", "build_adjustment_cards")
    graph.add_edge("build_adjustment_cards", END)
    return graph.compile()


_ADJUSTMENT_GRAPH = build_adjustment_graph()


async def stream_student_adjustment(
    state: StudentAdjustmentState,
) -> AsyncIterator[dict[str, Any]]:
    async for event in _ADJUSTMENT_GRAPH.astream(state, stream_mode="custom"):
        if isinstance(event, dict) and "event" in event:
            yield event
