"""Exercise the selection Lua scripts against Redis using isolated random keys."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from app.cache.selection_precheck import (
    applications_key,
    idempotency_key,
    selected_projects_key,
    student_context_key,
)
from app.db.redis_client import close_redis_client, get_redis_client
from app.services import selection_service as service


async def main() -> None:
    redis = get_redis_client()
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    project_id, course_id = uuid4(), uuid4()
    meta_key = service._session_meta_key(session_id)
    context_key = student_context_key(student_id, term_id)
    selected_key = selected_projects_key(student_id, term_id)
    application_key = applications_key(student_id, term_id)
    stock_key = service._stock_key(session_id)
    idem_key = idempotency_key(student_id, session_id)
    lock_key = service._student_lock_key(student_id, term_id)
    project_key = service._project_key(student_id, term_id, project_id)
    stream_key = f"selection:test-stream:{uuid4().hex}"
    original_stream = service.SELECTION_STREAM
    service.SELECTION_STREAM = stream_key
    keys = [
        meta_key,
        context_key,
        selected_key,
        application_key,
        stock_key,
        idem_key,
        lock_key,
        project_key,
    ]
    try:
        await redis.hset(
            meta_key,
            mapping={
                "term_id": str(term_id),
                "project_id": str(project_id),
                "course_id": str(course_id),
                "schedule_status": "PUBLISHED",
                "session_status": "OPEN",
                "week_no": 1,
                "day_of_week": 1,
                "start_slot": 1,
                "end_slot": 2,
            },
        )
        context = {
            "academic_active": True,
            "bitmap_valid": True,
            "projects": {
                str(project_id): {
                    "course_id": str(course_id),
                    "requirement_type": "REQUIRED",
                    "violations": [],
                }
            },
            "busy_slots": {},
            "selected_times": {},
            "order_constraints": [],
        }
        await redis.set(context_key, json.dumps(context), ex=60)
        await redis.set(stock_key, 1, ex=60)
        code, reserved_project, _, token, _ = await service._preflight_reserve(
            redis,
            student_id=student_id,
            term_id=term_id,
            session_id=session_id,
        )
        assert code == 1 and reserved_project == project_id
        member = service._reservation_member(
            student_id, term_id, project_id, session_id, token
        )
        value = f"{session_id}:{token}"
        await redis.eval(
            service.LUA_COMPENSATE,
            4,
            service.PENDING_ZSET,
            stock_key,
            project_key,
            lock_key,
            member,
            value,
            token,
        )
        assert int(await redis.get(stock_key)) == 1
        assert not await redis.exists(lock_key)

        code, _, _, request_id, _ = await service._preflight_reserve(
            redis,
            student_id=student_id,
            term_id=term_id,
            session_id=session_id,
            enqueue=True,
        )
        assert code == 1
        assert await redis.xlen(stream_key) == 1
        status_key = service._request_status_key(student_id, request_id)
        status = await redis.hgetall(status_key)
        assert status["result"] == "processing"
        member = service._reservation_member(
            student_id, term_id, project_id, session_id, request_id
        )
        await redis.eval(
            service.LUA_COMPENSATE,
            4,
            service.PENDING_ZSET,
            stock_key,
            project_key,
            lock_key,
            member,
            f"{session_id}:{request_id}",
            request_id,
        )
        await redis.delete(status_key)

        context["busy_slots"] = {"1:1:1": True}
        await redis.set(context_key, json.dumps(context), ex=60)
        code, _, _, _, detail = await service._preflight_reserve(
            redis,
            student_id=student_id,
            term_id=term_id,
            session_id=session_id,
        )
        assert code == -4 and detail == "TIME_CONFLICT"
        assert int(await redis.get(stock_key)) == 1
        assert not await redis.exists(lock_key)
        print("redis_preflight=passed")
        print("redis_atomic_enqueue=passed")
        print("redis_compensation=passed")
        print("concrete_reason=TIME_CONFLICT")
    finally:
        service.SELECTION_STREAM = original_stream
        await redis.delete(*keys, stream_key)
        await close_redis_client()


if __name__ == "__main__":
    asyncio.run(main())
