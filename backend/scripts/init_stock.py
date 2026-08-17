"""初始化 Redis 选课库存。"""
import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.services.selection_service import init_session_stock


async def main():
    async with AsyncSessionFactory() as s:
        sessions = (
            await s.execute(
                select(ExperimentSession).join(ScheduleVersion).where(
                    ScheduleVersion.status.in_(["PUBLISHED", "DRAFT"])
                )
            )
        ).scalars().all()
        for x in sessions:
            await init_session_stock(x.id, x.capacity, x.selected_count)
        print(f"{len(sessions)} stocks ready")
    await dispose_database_engine()


if __name__ == "__main__":
    asyncio.run(main())
