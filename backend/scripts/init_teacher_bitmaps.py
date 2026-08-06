"""初始化教师忙闲位图：18周，全部空闲。"""
import asyncio
from sqlalchemy import delete as sql_delete, select

from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models.curriculum import AcademicTerm
from app.models.identity import Teacher, TeacherBusyBitmap

WEEKS = 18
DAYS = 7
SLOTS = 12
TOTAL_BYTES = (WEEKS * DAYS * SLOTS + 7) // 8  # 189 bytes


async def main():
    async with AsyncSessionFactory() as session:
        term = (await session.execute(
            select(AcademicTerm).where(AcademicTerm.status == "ACTIVE")
        )).scalar_one_or_none()
        if term is None:
            term = (await session.execute(
                select(AcademicTerm).order_by(AcademicTerm.created_at.desc()).limit(1)
            )).scalar_one()

        teachers = (await session.execute(
            select(Teacher).where(Teacher.status == "ACTIVE")
        )).scalars().all()

        # 清空旧数据
        await session.execute(sql_delete(TeacherBusyBitmap))
        await session.flush()

        created = 0
        for teacher in teachers:
            session.add(TeacherBusyBitmap(
                teacher_id=teacher.id,
                term_id=term.id,
                start_week=1,
                end_week=WEEKS,
                days_per_week=DAYS,
                slots_per_day=SLOTS,
                bitmap=bytes(TOTAL_BYTES),  # 全零 = 全空闲
                mapping_version=1,
            ))
            created += 1

        await session.commit()
        print(f"Initialized {created} teacher bitmaps (all free, {TOTAL_BYTES} bytes each)")

    await dispose_database_engine()


if __name__ == "__main__":
    asyncio.run(main())
