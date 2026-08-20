"""端到端验证:重放方案 108371bc 的执行。

模拟前端"校验并确认":prepare_plan 重新生成 confirmation_token 后
execute_plan 顺序执行全部场次。验证竞态修复(提交后同步重建准入
context + -3 自动重试)后不再出现"选课状态发生变化"。
"""

import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.crud.teaching_tasks import get_or_create_active_term
from app.db.redis_client import get_redis_client
from app.db.session import AsyncSessionFactory
from app.services import selection_plan_service

PLAN_ID = sys.argv[1] if len(sys.argv) > 1 else "108371bc-f5bb-4d80-8710-56fdcf3c0cac"


async def main() -> None:
    redis = get_redis_client()
    student_ids: list[str] = []
    async for key in redis.scan_iter(match="student:selection-plan:*"):
        if str(key).endswith(PLAN_ID):
            student_ids.append(str(key).split(":")[2])
    if not student_ids:
        print("PLAN DRAFT NOT FOUND")
        return
    student_id = UUID(student_ids[0])
    print(f"student_id: {student_id}")

    async with AsyncSessionFactory() as db:
        term = await get_or_create_active_term(db)
        print(f"term: {term.id} ({term.academic_year} S{term.semester_no})")

        draft = await selection_plan_service.get_plan(
            redis, student_id=student_id, plan_id=UUID(PLAN_ID)
        )
        print(f"draft status: {draft.status}, version: {draft.version}, items: {len(draft.items)}")
        for item in draft.items:
            print(
                f"  - {item.selected.project_name or item.project_id} | {item.selected.session_id} | {item.status} | {item.result_message or ''}"
            )

        if draft.status == "READY":
            print("draft already READY (token pending) -> skipping prepare")
        else:
            draft, preview = await selection_plan_service.prepare_plan(
                redis,
                db,
                student_id=student_id,
                plan_id=UUID(PLAN_ID),
                version=draft.version,
            )
            print(f"prepared: valid={preview.valid} new={preview.new_count} violations={preview.violations}")

        result = await selection_plan_service.execute_plan(
            redis,
            db,
            student_id=student_id,
            term=term,
            plan_id=UUID(PLAN_ID),
            confirmation_token=draft.confirmation_token or "",
        )
        print(f"EXECUTED: succeeded={result.succeeded} failed={result.failed}")
        for item in result.plan.items:
            print(
                f"  - {item.selected.project_name or item.project_id} | {item.selected.session_id} | {item.status} | {item.result_message or ''}"
            )


if __name__ == "__main__":
    asyncio.run(main())
