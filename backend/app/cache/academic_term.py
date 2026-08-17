"""Shared active-term cache used before student-specific cache lookups."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.teaching_tasks import get_or_create_active_term
from app.db.redis_client import get_redis_client

logger = logging.getLogger(__name__)
KEY = "academic-term:active:v1"
TTL_SECONDS = 3600
LOCAL_TTL_SECONDS = 30
_local_term_id: UUID | None = None
_local_expires_at = 0.0
_local_lock = asyncio.Lock()


async def get_active_term_id(session: AsyncSession) -> UUID:
    global _local_term_id, _local_expires_at
    now = time.monotonic()
    if _local_term_id is not None and now < _local_expires_at:
        return _local_term_id

    async with _local_lock:
        now = time.monotonic()
        if _local_term_id is not None and now < _local_expires_at:
            return _local_term_id
        value = await _load_active_term_id(session)
        _local_term_id = value
        _local_expires_at = now + LOCAL_TTL_SECONDS
        return value


async def _load_active_term_id(session: AsyncSession) -> UUID:
    redis = get_redis_client()
    try:
        raw = await redis.get(KEY)
        if raw:
            value = json.loads(raw)
            return UUID(value["id"])
    except Exception:
        logger.warning("Active-term cache read failed", exc_info=True)
    term = await get_or_create_active_term(session)
    try:
        await redis.set(KEY, json.dumps({"id": str(term.id)}), ex=TTL_SECONDS)
    except Exception:
        logger.warning("Active-term cache write failed", exc_info=True)
    return term.id


async def warm_active_term(session: AsyncSession) -> UUID:
    global _local_term_id, _local_expires_at
    term = await get_or_create_active_term(session)
    await get_redis_client().set(
        KEY, json.dumps({"id": str(term.id)}), ex=TTL_SECONDS
    )
    _local_term_id = term.id
    _local_expires_at = time.monotonic() + LOCAL_TTL_SECONDS
    return term.id
