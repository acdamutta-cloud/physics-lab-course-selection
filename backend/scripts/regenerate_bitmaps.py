"""重新生成学生忙闲位图：18周，2节连排，30-60%工作日白天占用。"""
import asyncio
import random
from uuid import uuid4

from sqlalchemy import delete as sql_delete, select

from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models.curriculum import AcademicTerm
from app.models.identity import Student, StudentBusyBitmap

WEEKS = 18
DAYS = 7    # 0=Sun ... 6=Sat
SLOTS = 12
TOTAL_BITS = WEEKS * DAYS * SLOTS
TOTAL_BYTES = (TOTAL_BITS + 7) // 8


def set_bit(bitmap: bytearray, week: int, day: int, slot: int) -> None:
    """week:1-18, day:0-6, slot:0-11"""
    idx = (week - 1) * DAYS * SLOTS + day * SLOTS + slot
    bitmap[idx // 8] |= (1 << (7 - (idx % 8)))


def bit_is_set(bitmap: bytearray, week: int, day: int, slot: int) -> bool:
    idx = (week - 1) * DAYS * SLOTS + day * SLOTS + slot
    return bool(bitmap[idx // 8] & (1 << (7 - (idx % 8))))


def set_consecutive(bitmap: bytearray, week: int, day: int, start_slot: int, count: int) -> bool:
    """放置连续 count 节课，成功返回 True，冲突返回 False"""
    for s in range(count):
        if bit_is_set(bitmap, week, day, start_slot + s):
            return False
    for s in range(count):
        set_bit(bitmap, week, day, start_slot + s)
    return True


async def main():
    async with AsyncSessionFactory() as session:
        term = (await session.execute(
            select(AcademicTerm).where(AcademicTerm.status == "ACTIVE")
        )).scalar_one_or_none()
        if term is None:
            term = (await session.execute(
                select(AcademicTerm).order_by(AcademicTerm.created_at.desc()).limit(1)
            )).scalar_one()

        await session.execute(sql_delete(StudentBusyBitmap))
        await session.flush()

        students = (await session.execute(
            select(Student).where(Student.academic_status == "ACTIVE")
        )).scalars().all()

        rng = random.Random(20260730)
        created = 0

        for student in students:
            bitmap = bytearray(TOTAL_BYTES)

            # 每个学生随机：白天 30-50%，晚间 10-20%，周末 3-7%
            day_pct = rng.uniform(0.30, 0.50)
            eve_pct = rng.uniform(0.10, 0.20)
            wkd_pct = rng.uniform(0.03, 0.07)

            # 1. 工作日白天：2节连排，直到达到目标占用率
            weekday_day_slots = 5 * 8 * WEEKS
            target_day = int(weekday_day_slots * day_pct)
            placed = 0
            while placed * 2 < target_day:
                w = rng.randint(1, WEEKS)
                d = rng.randint(1, 5)
                ss = rng.choice([0, 2, 4, 6])
                if set_consecutive(bitmap, w, d, ss, 2):
                    placed += 1

            # 2. 工作日晚上：2节连排
            eve_count = int(5 * 4 * WEEKS * eve_pct / 2)
            for _ in range(eve_count):
                w = rng.randint(1, WEEKS)
                d = rng.randint(1, 5)
                ss = rng.choice([8, 10])  # 晚9-10节 或 晚11-12节
                set_consecutive(bitmap, w, d, ss, 2)

            # 3. 周末：2节连排
            wkd_count = int(2 * 12 * WEEKS * wkd_pct / 2)
            for _ in range(wkd_count):
                w = rng.randint(1, WEEKS)
                d = rng.choice([0, 6])
                ss = rng.choice([0, 2, 4, 6, 8, 10])
                set_consecutive(bitmap, w, d, ss, 2)

            session.add(StudentBusyBitmap(
                student_id=student.id, term_id=term.id,
                start_week=1, end_week=WEEKS,
                days_per_week=DAYS, slots_per_day=SLOTS,
                bitmap=bytes(bitmap), mapping_version=1,
            ))
            created += 1

        await session.commit()
        print(f"Created {created} bitmaps ({TOTAL_BYTES} bytes each)")

    await dispose_database_engine()


if __name__ == "__main__":
    asyncio.run(main())
