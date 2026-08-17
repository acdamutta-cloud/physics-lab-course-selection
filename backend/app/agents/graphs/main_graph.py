from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.supervisor import authorize_and_route, invoke_selected_graph


class MainAgentState(TypedDict, total=False):
    trace_id: str
    business_type: str
    actor_type: str
    actor_id: str
    operation: str
    payload: dict[str, Any]
    selected_graph: str
    graph_version: str
    result: dict[str, Any]


def build_main_graph():
    graph = StateGraph(MainAgentState)
    graph.add_node("authorize_and_route", authorize_and_route)
    graph.add_node("invoke_domain_graph", invoke_selected_graph)
    graph.add_edge(START, "authorize_and_route")
    graph.add_edge("authorize_and_route", "invoke_domain_graph")
    graph.add_edge("invoke_domain_graph", END)
    return graph.compile()


_MAIN_GRAPH = build_main_graph()


async def run_agent_task(state: MainAgentState) -> MainAgentState:
    return await _MAIN_GRAPH.ainvoke(state)
