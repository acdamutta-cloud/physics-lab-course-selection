from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Student, StudentBusyBitmap, TeacherBusyBitmap


async def count_students_by_major_year(
    session: AsyncSession,
) -> dict[tuple[UUID, int], int]:
    """返回 {(major_id, enrollment_year): student_count}"""
    stmt = (
        select(
            Student.major_id,
            Student.enrollment_year,
            func.count(Student.id).label("cnt"),
        )
        .where(Student.academic_status == "ACTIVE")
        .group_by(Student.major_id, Student.enrollment_year)
    )
    result = await session.execute(stmt)
    return {(row[0], row[1]): row[2] for row in result.all()}


async def count_students_by_major_year_list(
    session: AsyncSession, cohorts: list[tuple[UUID, int]]
) -> dict[tuple[UUID, int], int]:
    """批量统计指定 (major_id, enrollment_year) 组合的学生数。"""
    if not cohorts:
        return {}
    all_counts = await count_students_by_major_year(session)
    return {k: all_counts.get(k, 0) for k in cohorts}


async def count_total_active_students(session: AsyncSession) -> int:
    stmt = select(func.count(Student.id)).where(Student.academic_status == "ACTIVE")
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_student_bitmap(
    session: AsyncSession, student_id: UUID, term_id: UUID
) -> StudentBusyBitmap | None:
    stmt = select(StudentBusyBitmap).where(
        StudentBusyBitmap.student_id == student_id,
        StudentBusyBitmap.term_id == term_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_teacher_bitmap(
    session: AsyncSession, teacher_id: UUID, term_id: UUID
) -> TeacherBusyBitmap | None:
    stmt = select(TeacherBusyBitmap).where(
        TeacherBusyBitmap.teacher_id == teacher_id,
        TeacherBusyBitmap.term_id == term_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
