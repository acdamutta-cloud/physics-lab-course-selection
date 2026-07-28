import asyncio
import json

from sqlalchemy import func, select, text

from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models import (
    ExperimentCourse,
    ExperimentProject,
    ExperimentSession,
    Major,
    Student,
    StudentClass,
    Teacher,
)


async def collect_counts() -> dict[str, int]:
    async with AsyncSessionFactory() as session:
        statements = {
            "majors": select(func.count())
            .select_from(Major)
            .where(Major.code.like("DEMO-%")),
            "classes": select(func.count())
            .select_from(StudentClass)
            .where(StudentClass.code.like("DEMO-%")),
            "students": select(func.count())
            .select_from(Student)
            .where(Student.student_no.like("D2024%")),
            "teachers": select(func.count())
            .select_from(Teacher)
            .where(Teacher.employee_no.like("DEMO-%")),
            "courses": select(func.count())
            .select_from(ExperimentCourse)
            .where(ExperimentCourse.course_code.like("DEMO-%")),
            "projects": select(func.count())
            .select_from(ExperimentProject)
            .where(ExperimentProject.project_code.like("DEMO-%")),
            "sessions": select(func.count())
            .select_from(ExperimentSession)
            .where(ExperimentSession.session_code.like("DEMO-%")),
        }
        result: dict[str, int] = {}
        for name, statement in statements.items():
            result[name] = int(await session.scalar(statement) or 0)
        return result


async def collect_students_per_major() -> list[dict[str, str | int]]:
    async with AsyncSessionFactory() as session:
        rows = (
            await session.execute(
                select(
                    Major.code,
                    Major.name,
                    func.count(Student.id).label("student_count"),
                )
                .join(Student, Student.major_id == Major.id)
                .where(
                    Major.code.like("DEMO-%"),
                    Student.student_no.like("D2024%"),
                )
                .group_by(Major.code, Major.name)
                .order_by(Major.code)
            )
        ).all()
        return [
            {
                "major_code": row.code,
                "major_name": row.name,
                "student_count": int(row.student_count),
            }
            for row in rows
        ]


async def collect_schema_state() -> dict[str, str | int]:
    async with AsyncSessionFactory() as session:
        business_table_count = await session.scalar(
            text(
                """
                SELECT count(*)
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                  AND table_name != 'alembic_version'
                """
            )
        )
        alembic_version = await session.scalar(
            text("SELECT version_num FROM alembic_version")
        )
        seed_audit_rows = await session.scalar(
            text(
                """
                SELECT count(*)
                FROM operation_log
                WHERE request_id = 'DEMO-SEED-V1'
                  AND result = 'SUCCEEDED'
                """
            )
        )
        return {
            "business_tables": int(business_table_count or 0),
            "alembic_version": str(alembic_version or ""),
            "seed_audit_rows": int(seed_audit_rows or 0),
        }


async def main() -> None:
    try:
        counts = await collect_counts()
        students_per_major = await collect_students_per_major()
        schema_state = await collect_schema_state()
        print(
            json.dumps(
                {
                    "schema": schema_state,
                    "counts": counts,
                    "students_per_major": students_per_major,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if counts["majors"] != 10:
            raise RuntimeError("模拟专业数量应为 10")
        if counts["students"] != 2000:
            raise RuntimeError("模拟学生数量应为 2000")
        if counts["classes"] != 50:
            raise RuntimeError("模拟班级数量应为 50")
        if len(students_per_major) != 10 or any(
            item["student_count"] != 200 for item in students_per_major
        ):
            raise RuntimeError("每个模拟专业都应恰好包含 200 名学生")
        if schema_state["business_tables"] != 41:
            raise RuntimeError("业务表数量应为 41")
        if schema_state["seed_audit_rows"] != 1:
            raise RuntimeError("模拟数据写入审计记录应为 1 条")
        print("模拟数据数量校验通过。")
    finally:
        await dispose_database_engine()


if __name__ == "__main__":
    asyncio.run(main())
