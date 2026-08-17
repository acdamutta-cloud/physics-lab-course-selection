from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enrollment import StudentProjectRecord
from app.models.identity import Student, StudentBusyBitmap
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.schemas.teacher_adjustment import AffectedStudent, TimeTarget
from app.services.course_availability_service import bitmap_slot_is_busy


def time_overlaps(
    *,
    week_a: int,
    day_a: int,
    start_a: int,
    end_a: int,
    week_b: int,
    day_b: int,
    start_b: int,
    end_b: int,
) -> bool:
    return week_a == week_b and day_a == day_b and start_a <= end_b and start_b <= end_a


async def validate_teacher_and_lab_time(
    session: AsyncSession,
    *,
    schedule_version_id: UUID,
    original_session_id: UUID,
    teacher_id: UUID,
    laboratory_id: UUID,
    target: TimeTarget,
) -> list[str]:
    rows = list(
        (
            await session.execute(
                select(ExperimentSession).where(
                    ExperimentSession.schedule_version_id == schedule_version_id,
                    ExperimentSession.id != original_session_id,
                    ExperimentSession.week_no == target.week_no,
                    ExperimentSession.day_of_week == target.day_of_week,
                    ExperimentSession.start_slot <= target.end_slot,
                    ExperimentSession.end_slot >= target.start_slot,
                    ExperimentSession.status.notin_(["CANCELLED", "COMPLETED"]),
                )
            )
        ).scalars()
    )
    errors: list[str] = []
    if any(item.teacher_id == teacher_id for item in rows):
        errors.append("目标时间与教师已有实验场次冲突。")
    if any(item.laboratory_id == laboratory_id for item in rows):
        errors.append("目标时间实验室已被其他场次占用。")
    return errors


async def affected_students_for_time(
    session: AsyncSession,
    *,
    term_id: UUID,
    original_session_id: UUID,
    target: TimeTarget,
) -> list[AffectedStudent]:
    rows = list(
        (
            await session.execute(
                select(StudentProjectRecord, Student)
                .join(Student, Student.id == StudentProjectRecord.student_id)
                .where(
                    StudentProjectRecord.session_id == original_session_id,
                    StudentProjectRecord.status.in_(["SELECTED", "MAKEUP_PENDING"]),
                )
            )
        ).all()
    )
    if not rows:
        return []
    student_ids = [student.id for _, student in rows]
    bitmaps = {
        item.student_id: item
        for item in (
            await session.execute(
                select(StudentBusyBitmap).where(
                    StudentBusyBitmap.term_id == term_id,
                    StudentBusyBitmap.student_id.in_(student_ids),
                )
            )
        ).scalars()
    }
    other_records = list(
        (
            await session.execute(
                select(StudentProjectRecord)
                .options(selectinload(StudentProjectRecord.session))
                .where(
                    StudentProjectRecord.student_id.in_(student_ids),
                    StudentProjectRecord.session_id != original_session_id,
                    StudentProjectRecord.status.in_(["SELECTED", "MAKEUP_PENDING"]),
                )
            )
        ).scalars()
    )
    by_student: dict[UUID, list[ExperimentSession]] = {}
    for record in other_records:
        if record.session is not None:
            by_student.setdefault(record.student_id, []).append(record.session)

    affected: list[AffectedStudent] = []
    for _, student in rows:
        reasons: list[str] = []
        bitmap = bitmaps.get(student.id)
        if bitmap is not None and any(
            bitmap_slot_is_busy(
                bitmap.bitmap,
                week_no=target.week_no,
                day_of_week=target.day_of_week,
                slot_no=slot,
                start_week=bitmap.start_week,
                days_per_week=bitmap.days_per_week,
                slots_per_day=bitmap.slots_per_day,
            )
            for slot in range(target.start_slot, target.end_slot + 1)
        ):
            reasons.append("目标时间与已有课程冲突")
        if any(
            time_overlaps(
                week_a=target.week_no,
                day_a=target.day_of_week,
                start_a=target.start_slot,
                end_a=target.end_slot,
                week_b=item.week_no,
                day_b=item.day_of_week,
                start_b=item.start_slot,
                end_b=item.end_slot,
            )
            for item in by_student.get(student.id, [])
        ):
            reasons.append("目标时间与已选实验冲突")
        if reasons:
            affected.append(
                AffectedStudent(
                    student_id=student.id,
                    student_no=student.student_no,
                    name=student.name,
                    reasons=reasons,
                )
            )
    return affected


async def published_session(
    session: AsyncSession, session_id: UUID
) -> ExperimentSession | None:
    return (
        await session.execute(
            select(ExperimentSession)
            .join(
                ScheduleVersion,
                ScheduleVersion.id == ExperimentSession.schedule_version_id,
            )
            .where(
                ExperimentSession.id == session_id,
                ScheduleVersion.status == "PUBLISHED",
            )
        )
    ).scalar_one_or_none()
