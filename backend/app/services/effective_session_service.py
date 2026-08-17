from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scheduling import ExperimentSession
from app.models.teaching_adjustment import SessionExecutionOverride


async def effective_session_values(
    session: AsyncSession, items: Iterable[ExperimentSession]
) -> dict[UUID, dict[str, object]]:
    rows = list(items)
    result = {
        item.id: {
            "week_no": item.week_no,
            "day_of_week": item.day_of_week,
            "start_slot": item.start_slot,
            "end_slot": item.end_slot,
            "laboratory_id": item.laboratory_id,
            "teacher_id": item.teacher_id,
        }
        for item in rows
    }
    if not result:
        return result
    overrides = list(
        (
            await session.execute(
                select(SessionExecutionOverride)
                .where(
                    SessionExecutionOverride.session_id.in_(result),
                    SessionExecutionOverride.status == "ACTIVE",
                )
                .order_by(SessionExecutionOverride.created_at)
            )
        ).scalars()
    )
    for item in overrides:
        values = result[item.session_id]
        if item.override_type == "TIME":
            for field in ("week_no", "day_of_week", "start_slot", "end_slot"):
                if field in item.after_snapshot:
                    values[field] = int(item.after_snapshot[field])
        elif item.override_type == "LAB" and item.after_snapshot.get("laboratory_id"):
            values["laboratory_id"] = UUID(str(item.after_snapshot["laboratory_id"]))
        elif item.override_type == "TEACHER" and item.after_snapshot.get("teacher_id"):
            values["teacher_id"] = UUID(str(item.after_snapshot["teacher_id"]))
    return result
