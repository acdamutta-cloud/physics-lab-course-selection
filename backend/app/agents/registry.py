from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.agents.graphs.adjustment_graph import (
    _ADJUSTMENT_GRAPH,
    stream_student_adjustment,
)
from app.agents.graphs.scheduling_graph import build_scheduling_graph
from app.agents.graphs.student_graph import _STUDENT_GRAPH, stream_student_consultation
from app.agents.graphs.teacher_adjustment_graph import (
    _GRAPH as _TEACHER_ADJUSTMENT_GRAPH,
)
from app.agents.graphs.teacher_adjustment_graph import (
    stream_teacher_adjustment,
)

InvokeHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
StreamHandler = Callable[[dict[str, Any]], AsyncIterator[dict[str, Any]]]


@dataclass(frozen=True)
class GraphRegistration:
    business_type: str
    graph_name: str
    graph_version: str
    allowed_actors: frozenset[str]
    invoke: InvokeHandler
    stream: StreamHandler | None = None


async def _invoke_scheduling(payload: dict[str, Any]) -> dict[str, Any]:
    return await build_scheduling_graph().ainvoke(payload)


async def _invoke_student(payload: dict[str, Any]) -> dict[str, Any]:
    return await _STUDENT_GRAPH.ainvoke(payload)


async def _invoke_student_adjustment(payload: dict[str, Any]) -> dict[str, Any]:
    return await _ADJUSTMENT_GRAPH.ainvoke(payload)


async def _invoke_teacher_adjustment(payload: dict[str, Any]) -> dict[str, Any]:
    return await _TEACHER_ADJUSTMENT_GRAPH.ainvoke(payload)


GRAPH_REGISTRY: dict[str, GraphRegistration] = {
    "SYSTEM_SCHEDULING": GraphRegistration(
        business_type="SYSTEM_SCHEDULING",
        graph_name="scheduling_graph",
        graph_version="v1",
        allowed_actors=frozenset({"ADMIN"}),
        invoke=_invoke_scheduling,
    ),
    "STUDENT_CONSULTATION": GraphRegistration(
        business_type="STUDENT_CONSULTATION",
        graph_name="student_consultation",
        graph_version="v2",
        allowed_actors=frozenset({"STUDENT"}),
        invoke=_invoke_student,
        stream=stream_student_consultation,
    ),
    "STUDENT_ADJUSTMENT": GraphRegistration(
        business_type="STUDENT_ADJUSTMENT",
        graph_name="student_adjustment",
        graph_version="v1",
        allowed_actors=frozenset({"STUDENT"}),
        invoke=_invoke_student_adjustment,
        stream=stream_student_adjustment,
    ),
    "TEACHER_ADJUSTMENT": GraphRegistration(
        business_type="TEACHER_ADJUSTMENT",
        graph_name="teacher_adjustment",
        graph_version="v1",
        allowed_actors=frozenset({"TEACHER", "ADMIN"}),
        invoke=_invoke_teacher_adjustment,
        stream=stream_teacher_adjustment,
    ),
}


def resolve_graph(business_type: str, actor_type: str) -> GraphRegistration:
    registration = GRAPH_REGISTRY.get(business_type)
    if registration is None:
        raise ValueError(f"不支持的Agent业务类型：{business_type}")
    if actor_type not in registration.allowed_actors:
        raise PermissionError(f"{actor_type}无权调用{business_type}。")
    return registration


async def invoke_registered_graph(
    *, business_type: str, actor_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    registration = resolve_graph(business_type, actor_type)
    return await registration.invoke(payload)


async def stream_registered_graph(
    *, business_type: str, actor_type: str, payload: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    registration = resolve_graph(business_type, actor_type)
    if registration.stream is None:
        raise ValueError(f"{business_type}不支持流式调用。")
    async for event in registration.stream(payload):
        yield event
