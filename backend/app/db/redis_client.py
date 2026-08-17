import asyncio

from redis.asyncio import BlockingConnectionPool, Redis

from app.core.config.settings import get_settings

_redis_client: Redis | None = None
_redis_pool: BlockingConnectionPool | None = None


def get_redis_client() -> Redis:
    """返回进程级连接池客户端，不在业务模块重复创建连接池。"""

    global _redis_client, _redis_pool
    if _redis_client is None:
        settings = get_settings()
        _redis_pool = BlockingConnectionPool.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            timeout=settings.redis_pool_wait_seconds,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
            socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        _redis_client = Redis(connection_pool=_redis_pool)
    return _redis_client


async def warm_redis_connections() -> None:
    """Create a bounded set of Redis connections before burst traffic arrives."""

    count = min(
        get_settings().redis_warm_connections,
        get_settings().redis_max_connections,
    )
    if count:
        client = get_redis_client()
        await asyncio.gather(*(client.ping() for _ in range(count)))


async def close_redis_client() -> None:
    global _redis_client, _redis_pool
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        _redis_pool = None
