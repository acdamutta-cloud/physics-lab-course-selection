from collections.abc import AsyncIterator
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.teacher_adjustment_agent import (
    compose_teacher_answer,
    execute_teacher_recommendation,
    finalize_teacher_adjustment,
    normalize_teacher_adjustment,
    plan_teacher_preferences,
    validate_teacher_plan,
)
from app.agents.states.teacher_adjustment import TeacherAdjustmentState

GRAPH_NAME = "teacher_adjustment"
GRAPH_VERSION = "v1"
AGENT_CODE = "TEACHER_ADJUSTMENT_ADVISOR"
BUSINESS_TYPE = "TEACHER_ADJUSTMENT"


def build_teacher_adjustment_graph():
    graph = StateGraph(TeacherAdjustmentState)
    graph.add_node("normalize", normalize_teacher_adjustment)
    graph.add_node("plan_preferences", plan_teacher_preferences)
    graph.add_node("validate_plan", validate_teacher_plan)
    graph.add_node("recommend", execute_teacher_recommendation)
    graph.add_node("compose", compose_teacher_answer)
    graph.add_node("finalize", finalize_teacher_adjustment)
    graph.add_edge(START, "normalize")
    graph.add_edge("normalize", "plan_preferences")
    graph.add_edge("plan_preferences", "validate_plan")
    graph.add_edge("validate_plan", "recommend")
    graph.add_edge("recommend", "compose")
    graph.add_edge("compose", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


_GRAPH = build_teacher_adjustment_graph()


async def stream_teacher_adjustment(
    state: TeacherAdjustmentState,
) -> AsyncIterator[dict[str, Any]]:
    async for event in _GRAPH.astream(state, stream_mode="custom"):
        if isinstance(event, dict) and "event" in event:
            yield event
