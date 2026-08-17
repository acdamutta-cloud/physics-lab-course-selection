"""Preload Redis selection contexts without modifying PostgreSQL business rows."""

from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path

from sqlalchemy import select

from app.cache.academic_term import warm_active_term
from app.cache.selection_precheck import refresh_selection_context
from app.db.redis_client import close_redis_client
from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models.identity import Student, UserAccount


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--student-csv", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=10)
    return parser.parse_args()


def _login_names(path: Path | None, limit: int) -> list[str] | None:
    if path is None:
        return None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        values = [
            row["login_name"].strip()
            for row in csv.DictReader(handle)
            if row.get("login_name")
        ]
    return values[:limit] if limit > 0 else values


async def main() -> None:
    args = _arguments()
    names = _login_names(args.student_csv, args.limit)
    try:
        async with AsyncSessionFactory() as session:
            term_id = await warm_active_term(session)
            statement = (
                select(Student.id)
                .join(UserAccount, UserAccount.id == Student.user_id)
                .where(
                    Student.academic_status == "ACTIVE",
                    UserAccount.status == "ACTIVE",
                )
                .order_by(UserAccount.login_name)
            )
            if names is not None:
                statement = statement.where(UserAccount.login_name.in_(names))
            student_ids = list(await session.scalars(statement))

        semaphore = asyncio.Semaphore(max(1, args.concurrency))
        warmed = 0

        async def warm_one(student_id) -> None:
            nonlocal warmed
            async with semaphore, AsyncSessionFactory() as session:
                if await refresh_selection_context(
                    session, student_id=student_id, term_id=term_id
                ):
                    warmed += 1

        await asyncio.gather(*(warm_one(student_id) for student_id in student_ids))
        print(f"selection_contexts_warmed={warmed}")
        print(f"students_requested={len(student_ids)}")
        print("database_modified=false")
    finally:
        await dispose_database_engine()
        await close_redis_client()


if __name__ == "__main__":
    asyncio.run(main())
