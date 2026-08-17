from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.agents.graphs.student_graph import run_student_consultation
from app.db.session import AsyncSessionFactory
from app.models.curriculum import AcademicTerm
from app.models.identity import Student
from app.schemas.student_consultation import (
    ConsultationMessage,
    StudentPageContext,
)
from evals.config import FIXTURE_DIR, EvalSettings
from evals.schemas import EvalInputs, TargetOutput
from evals.tool_database_fixture import (
    apply_tool_database_fixture,
    context_with_database_fixture,
)

_DATABASE_FIXTURE_LOCK = asyncio.Lock()


def _serialize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def load_fixture_context(fixture_id: str) -> dict[str, Any]:
    path = Path(FIXTURE_DIR) / "student_context.json"
    fixtures = json.loads(path.read_text(encoding="utf-8"))
    if fixture_id not in fixtures:
        raise KeyError(f"未知学生评测fixture：{fixture_id}")
    return fixtures[fixture_id]


async def run_student_consultation_case(inputs: dict[str, Any]) -> dict[str, Any]:
    parsed = EvalInputs.model_validate(inputs)
    settings = EvalSettings.from_env()
    if parsed.database_fixture_id:
        async with _DATABASE_FIXTURE_LOCK:
            return await _run_student_consultation_case(parsed, settings)
    return await _run_student_consultation_case(parsed, settings)


async def _run_student_consultation_case(
    parsed: EvalInputs,
    settings: EvalSettings,
) -> dict[str, Any]:
    async with AsyncSessionFactory() as session:
        student = await session.scalar(
            select(Student).where(Student.student_no == settings.student_no)
        )
        if student is None:
            raise RuntimeError(
                "评测学生不存在，请通过 EVAL_STUDENT_NO 指定已初始化的测试学生。"
            )
        term = await session.scalar(
            select(AcademicTerm)
            .where(AcademicTerm.status == "ACTIVE")
            .order_by(AcademicTerm.start_date.desc())
            .limit(1)
        )
        if term is None:
            raise RuntimeError("评测数据库中没有ACTIVE学期。")
        base_context = load_fixture_context(parsed.student_fixture_id)
        if parsed.database_fixture_id:
            selections = await apply_tool_database_fixture(
                session,
                fixture_id=parsed.database_fixture_id,
                student_id=student.id,
                term=term,
            )
            base_context = context_with_database_fixture(base_context, selections)
        state = await run_student_consultation(
            {
                "session": session,
                "student_id": student.id,
                "term": term,
                "messages": [
                    ConsultationMessage.model_validate(message.model_dump())
                    for message in parsed.messages
                ],
                "page_context": StudentPageContext.model_validate(parsed.page_context),
                "base_context": base_context,
            }
        )
        # 即使未来误把写操作接入咨询图，评测会话也不提交数据库事务。
        await session.rollback()
    plan = _serialize(state.get("plan"))
    output = TargetOutput(
        answer=str(state.get("answer") or ""),
        intent=str(state.get("intent") or "UNKNOWN"),
        plan=plan if isinstance(plan, dict) else None,
        resolved_entities=_serialize(state.get("resolved_entities", {})),
        preferences=_serialize(state.get("preferences", {})),
        tool_requests=_serialize(state.get("tool_requests", [])),
        tool_results=_serialize(state.get("tool_results", [])),
        cards=_serialize(state.get("cards", [])),
        grounding_passed=bool(state.get("grounding_passed", False)),
        repaired_plan_attempted=bool(state.get("repaired_plan_attempted", False)),
        model_error=state.get("model_error"),
        trace_id=state.get("trace_id"),
        database_fixture_id=parsed.database_fixture_id,
        evaluation_context=_serialize(base_context),
    )
    return output.model_dump(mode="json")


async def student_consultation_target(inputs: dict[str, Any]) -> dict[str, Any]:
    """LangSmith原生异步target，避免asyncpg连接池跨事件循环复用。"""

    return await run_student_consultation_case(inputs)
