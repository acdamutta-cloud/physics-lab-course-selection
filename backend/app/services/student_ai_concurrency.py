"""Concurrency isolation for student AI calls."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID, uuid4

from app.core.config.settings import get_settings
from app.db.redis_client import get_redis_client

logger = logging.getLogger(__name__)
_settings = get_settings()
_slots = asyncio.Semaphore(_settings.student_ai_max_concurrency)


class StudentAIConcurrencyError(RuntimeError):
    pass


class StudentAILease:
    def __init__(self, student_id: UUID) -> None:
        self.student_id = student_id
        self.key = f"ai:student:active:{student_id}"
        self.token = uuid4().hex
        self.slot_acquired = False
        self.student_acquired = False

    async def acquire(self) -> StudentAILease:
        settings = get_settings()
        try:
            await asyncio.wait_for(
                _slots.acquire(), timeout=settings.student_ai_acquire_timeout_seconds
            )
        except TimeoutError as error:
            raise StudentAIConcurrencyError("AI服务当前请求较多，请稍后重试。") from error
        self.slot_acquired = True
        try:
            self.student_acquired = bool(
                await get_redis_client().set(
                    self.key,
                    self.token,
                    nx=True,
                    ex=max(30, int(settings.deepseek_timeout_seconds) + 30),
                )
            )
        except Exception:  # Redis failure must not disable AI completely.
            logger.warning("Student AI lock unavailable; using process limit only", exc_info=True)
            self.student_acquired = True
        if not self.student_acquired:
            await self.release()
            raise StudentAIConcurrencyError("正在处理你的另一条咨询，请稍后重试。")
        return self

    async def release(self) -> None:
        if self.student_acquired:
            try:
                await get_redis_client().eval(
                    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                    "return redis.call('DEL', KEYS[1]) end return 0",
                    1,
                    self.key,
                    self.token,
                )
            except Exception:  # noqa: BLE001
                pass
            self.student_acquired = False
        if self.slot_acquired:
            _slots.release()
            self.slot_acquired = False
