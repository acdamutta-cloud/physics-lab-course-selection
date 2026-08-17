from __future__ import annotations

from typing import Any

from app.agents.registry import resolve_graph


def authorize_and_route(state: dict[str, Any]) -> dict[str, Any]:
    """Deterministic high-level routing; an LLM never chooses privileged graphs."""

    registration = resolve_graph(state["business_type"], state["actor_type"])
    return {
        "selected_graph": registration.graph_name,
        "graph_version": registration.graph_version,
    }


async def invoke_selected_graph(state: dict[str, Any]) -> dict[str, Any]:
    registration = resolve_graph(state["business_type"], state["actor_type"])
    result = await registration.invoke(state.get("payload", {}))
    return {"result": result}
