"""AI 一键排课的 LangGraph 策略编排。

该图只负责将自然语言变成受控、可审计的运行时策略。确定性求解器和
数据库持久化由 schedule_service 执行，避免语言模型直接写数据库。
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.scheduling_agent import scheduling_agent_node
from app.agents.nodes.validation_agent import validation_agent_node
from app.agents.states.scheduling import SchedulingState


@lru_cache(maxsize=1)
def build_scheduling_graph():
    builder = StateGraph(SchedulingState)
    builder.add_node("scheduling_agent", scheduling_agent_node)
    builder.add_node("validation_agent", validation_agent_node)
    builder.add_edge(START, "scheduling_agent")
    builder.add_edge("scheduling_agent", "validation_agent")
    builder.add_edge("validation_agent", END)
    return builder.compile()


async def resolve_runtime_strategy(
    state: SchedulingState,
) -> SchedulingState:
    return await build_scheduling_graph().ainvoke(state)
