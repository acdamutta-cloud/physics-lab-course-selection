from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Major, Student, Teacher, UserAccount


async def get_user_by_login_name(
    session: AsyncSession, login_name: str
) -> UserAccount | None:
    stmt = select(UserAccount).where(UserAccount.login_name == login_name)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_id(
    session: AsyncSession, user_id: UUID
) -> UserAccount | None:
    stmt = select(UserAccount).where(UserAccount.id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_student_by_user_id(
    session: AsyncSession, user_id: UUID
) -> Student | None:
    stmt = select(Student).where(Student.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_teacher_by_user_id(
    session: AsyncSession, user_id: UUID
) -> Teacher | None:
    stmt = select(Teacher).where(Teacher.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_phone(
    session: AsyncSession, phone: str
) -> UserAccount | None:
    stmt = select(UserAccount).where(UserAccount.phone == phone)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_major_name(session: AsyncSession, major_id: UUID) -> str | None:
    major = await session.get(Major, major_id)
    return major.name if major else None
