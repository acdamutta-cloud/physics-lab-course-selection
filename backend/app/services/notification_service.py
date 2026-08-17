"""Redis 通知队列的公共读写辅助。"""

from __future__ import annotations

import json
from typing import Any

from app.db.redis_client import get_redis_client


async def remove_notification_by_value(key: str, value: Any) -> bool:
    """按值删除一条通知。

    前端回传的是 JSON.stringify 的结果，与存储时 json.dumps 的序列化格式
    可能不同（冒号空格、转义等），直接 lrem 原始字符串会匹配失败导致
    「已读后刷新又出现」。因此先按解析后的对象比对，再用存储时的原始
    字符串执行 lrem。
    """

    if isinstance(value, str):
        try:
            target: Any = json.loads(value)
        except (TypeError, ValueError):
            target = value
    else:
        target = value
    redis = get_redis_client()
    for stored in await redis.lrange(key, 0, -1):
        try:
            parsed: Any = json.loads(stored)
        except (TypeError, ValueError):
            parsed = stored
        if parsed == target:
            await redis.lrem(key, 1, stored)
            return True
    return False
