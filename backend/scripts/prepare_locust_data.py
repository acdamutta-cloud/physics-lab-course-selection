"""Prepare local, read-only Locust credentials from existing student accounts.

The generated access tokens are sensitive and are written below ``load-data/``,
which is ignored by Git.  This script never updates database rows.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from sqlalchemy import select

from app.core.config.settings import get_settings
from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models.identity import Student, UserAccount
from app.models.scheduling import ExperimentSession, ScheduleVersion


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-year", type=int, default=1000)
    parser.add_argument("--expires-hours", type=int, default=4)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("load-data/student_tokens_4000.csv"),
    )
    parser.add_argument(
        "--session-output",
        type=Path,
        default=Path("load-data/session_pool.csv"),
    )
    return parser.parse_args()


async def prepare(args: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    secret = settings.jwt_secret_key.get_secret_value().strip()
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY 未配置，不能生成可验证的压测令牌")
    selected: list[tuple[Student, UserAccount]] = []
    counts: dict[int, int] = {}
    async with AsyncSessionFactory() as session:
        years = (
            await session.scalars(
                select(Student.enrollment_year)
                .distinct()
                .order_by(Student.enrollment_year)
            )
        ).all()
        for year in years:
            rows = (
                await session.execute(
                    select(Student, UserAccount)
                    .join(UserAccount, UserAccount.id == Student.user_id)
                    .where(
                        Student.enrollment_year == year,
                        UserAccount.status == "ACTIVE",
                    )
                    .order_by(Student.student_no)
                    .limit(args.per_year)
                )
            ).all()
            if len(rows) < args.per_year:
                raise RuntimeError(
                    f"{year} 级只有 {len(rows)} 个激活学生账号，少于 {args.per_year}"
                )
            selected.extend(rows)
            counts[int(year)] = len(rows)

    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=args.expires_hours)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["token", "student_no", "enrollment_year"]
        )
        writer.writeheader()
        for student, user in selected:
            token = jwt.encode(
                {
                    "sub": str(user.id),
                    "type": "STUDENT",
                    "iat": now,
                    "exp": expires_at,
                    "purpose": "access",
                    "load_test": True,
                },
                secret,
                algorithm=settings.jwt_algorithm,
            )
            writer.writerow(
                {
                    "token": token,
                    "student_no": student.student_no,
                    "enrollment_year": student.enrollment_year,
                }
            )

    async with AsyncSessionFactory() as session:
        session_rows = (
            await session.execute(
                select(
                    ExperimentSession.id,
                    ExperimentSession.session_code,
                    ExperimentSession.capacity,
                    ExperimentSession.selected_count,
                    ExperimentSession.status,
                )
                .join(
                    ScheduleVersion,
                    ScheduleVersion.id == ExperimentSession.schedule_version_id,
                )
                .where(
                    ScheduleVersion.status == "PUBLISHED",
                    ExperimentSession.status.in_(["DRAFT", "OPEN", "FULL"]),
                    ExperimentSession.selected_count < ExperimentSession.capacity,
                )
                .order_by(ExperimentSession.session_code)
            )
        ).all()
    args.session_output.parent.mkdir(parents=True, exist_ok=True)
    with args.session_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "session_id",
                "session_code",
                "capacity",
                "selected_count",
                "remaining",
                "status",
            ],
        )
        writer.writeheader()
        for session_id, code, capacity, selected_count, status in session_rows:
            writer.writerow(
                {
                    "session_id": session_id,
                    "session_code": code,
                    "capacity": capacity,
                    "selected_count": selected_count,
                    "remaining": capacity - selected_count,
                    "status": status,
                }
            )

    summary = {
        "output": str(args.output.resolve()),
        "total": len(selected),
        "by_year": counts,
        "expires_at": expires_at.isoformat(),
        "session_output": str(args.session_output.resolve()),
        "available_sessions": len(session_rows),
        "database_modified": False,
    }
    summary_path = args.output.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


async def main() -> None:
    args = _arguments()
    try:
        print(json.dumps(await prepare(args), ensure_ascii=False, indent=2))
    finally:
        await dispose_database_engine()


if __name__ == "__main__":
    asyncio.run(main())
