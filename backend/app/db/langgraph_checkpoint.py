from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config.settings import get_settings


@asynccontextmanager
async def create_langgraph_checkpointer(
    *,
    setup: bool = False,
) -> AsyncIterator[AsyncPostgresSaver]:
    """创建 LangGraph PostgreSQL Checkpointer。

    `setup=True` 只应在初始化或迁移阶段使用，不应在每次请求中执行。
    """

    settings = get_settings()
    async with AsyncPostgresSaver.from_conn_string(
        settings.langgraph_database_url
    ) as checkpointer:
        if setup:
            await checkpointer.setup()
        yield checkpointer
