"""Preload student read caches without modifying business tables."""

from __future__ import annotations

import asyncio

from app.cache.auth_principals import warm_active_auth_profiles
from app.db.redis_client import close_redis_client
from app.db.session import dispose_database_engine
from app.services.student_cache_service import warm_all_student_caches


async def main() -> None:
    try:
        principals = await warm_active_auth_profiles()
        warmed = await warm_all_student_caches()
        print(f"warmed_auth_principals={principals}")
        print(f"warmed_students={warmed}")
        print("database_modified=false")
    finally:
        await dispose_database_engine()
        await close_redis_client()


if __name__ == "__main__":
    asyncio.run(main())
