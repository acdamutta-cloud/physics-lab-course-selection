"""诊断:检查学生最新方案草稿中每个场次的准入缓存状态(meta/stock/context/idempotency)。"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.redis_client import get_redis_client

STUDENT = "8611ff9b-f74d-599d-9e2a-01b493742a13"
TERM = "0a975078-8fe1-4ef0-a785-74b04eab5dca"
CONTEXT_KEY = f"selection:student-context:{STUDENT}:{TERM}:v1"


async def main() -> None:
    redis = get_redis_client()
    # 读取最新的 PARTIAL 草稿
    drafts = []
    async for key in redis.scan_iter(match=f"student:selection-plan:{STUDENT}:*"):
        drafts.append(str(key))
    drafts.sort()
    target = None
    for key in drafts:
        raw = await redis.get(key)
        data = json.loads(raw)
        if data.get("status") == "PARTIAL":
            target = data
            print(f"draft: {key.split(':')[-1][:8]} status=PARTIAL version={data.get('version')}")
            break
    if target is None:
        print("NO PARTIAL DRAFT")
        return

    print(f"\nstudent context key: {CONTEXT_KEY}")
    context_exists = await redis.exists(CONTEXT_KEY)
    print(f"context exists: {context_exists == 1}")
    if context_exists:
        ctx = json.loads(await redis.get(CONTEXT_KEY))
        print(
            f"  projects: {len(ctx.get('projects', {}))}, busy_slots: {len(ctx.get('busy_slots', {}))}, "
            f"academic_active: {ctx.get('academic_active')}, bitmap_valid: {ctx.get('bitmap_valid')}"
        )

    print("\n--- per-session admission keys (latest PARTIAL draft) ---")
    for item in target.get("items", []):
        s = item["selected"]
        sid = s["session_id"]
        name = s.get("project_name") or "?"
        meta_key = f"selection:session-meta:{sid}:v1"
        stock_key = f"session:stock:{sid}"
        meta = await redis.hmget(
            meta_key, "term_id", "project_id", "course_id", "schedule_status",
            "session_status", "week_no", "day_of_week", "start_slot", "end_slot",
        )
        stock = await redis.get(stock_key)
        idem = await redis.get(f"selection:idempotency:{STUDENT}:{sid}")
        missing = [i for i, v in enumerate(meta) if not v]
        sel_project = await redis.sismember(
            f"selection:selected-projects:{STUDENT}:{TERM}", str(s.get("project_id") or "")
        )
        print(
            f"  {name} | session={sid[:8]} | meta={len(meta)-len(missing)}/9 {missing} | "
            f"stock={stock} | idem={'YES' if idem else 'no'} | project_in_selected_set={sel_project == 1}"
        )


if __name__ == "__main__":
    asyncio.run(main())
