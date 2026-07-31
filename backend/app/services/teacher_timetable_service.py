from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scheduling import (
    ExperimentSession,
    ScheduleVersion,
    TeacherTimetableEntry,
)


async def rebuild_teacher_timetable(
    session: AsyncSession,
    published_version_id: UUID,
) -> int:
    """Rebuild the teacher-facing index from one published schedule."""

    version = await session.get(ScheduleVersion, published_version_id)
    if version is None:
        raise ValueError("课表版本不存在")
    if version.status != "PUBLISHED":
        raise ValueError("教师课表只能从已发布的课表版本重建")

    rows = (
        await session.execute(
            select(
                ExperimentSession.id,
                ExperimentSession.teacher_id,
            )
            .where(
                ExperimentSession.schedule_version_id
                == published_version_id
            )
            .order_by(ExperimentSession.id)
        )
    ).all()

    await session.execute(
        delete(TeacherTimetableEntry).where(
            TeacherTimetableEntry.term_id == version.term_id
        )
    )
    session.add_all(
        [
            TeacherTimetableEntry(
                teacher_id=teacher_id,
                term_id=version.term_id,
                schedule_version_id=version.id,
                experiment_session_id=session_id,
            )
            for session_id, teacher_id in rows
        ]
    )
    await session.flush()
    return len(rows)
