"""Per-term selection window cache.

DB 的 selection_window 表是权威数据源；Redis 缓存供选课准入 Lua 热路径
直接读取。管理员更新窗口后必须显式失效，短 TTL 仅作兜底。
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from app.core.config.settings import get_settings
from app.db.redis_client import get_redis_client

logger = logging.getLogger(__name__)
CACHE_VERSION = "v1"


def window_key(term_id: UUID) -> str:
    return f"selection:window:{term_id}:{CACHE_VERSION}"


async def read_window_cache(term_id: UUID) -> dict[str, Any] | None:
    try:
        raw = await get_redis_client().get(window_key(term_id))
        if raw:
            return json.loads(raw)
    except Exception:
        logger.warning("Selection window cache read failed", exc_info=True)
    return None


async def write_window_cache(term_id: UUID, window: dict[str, Any]) -> None:
    settings = get_settings()
    try:
        await get_redis_client().set(
            window_key(term_id),
            json.dumps(window, ensure_ascii=False),
            ex=settings.selection_window_cache_ttl_seconds,
        )
    except Exception:
        logger.warning("Selection window cache write failed", exc_info=True)


async def invalidate_window_cache(term_id: UUID) -> None:
    try:
        await get_redis_client().delete(window_key(term_id))
    except Exception:
        logger.warning("Selection window cache invalidate failed", exc_info=True)
