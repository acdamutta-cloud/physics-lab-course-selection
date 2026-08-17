from __future__ import annotations

import asyncio

from app.db.session import AsyncSessionFactory
from app.services.operation_guide_service import search_operation_guides

QUERIES = (
    "这张实验课表怎么保存成PDF",
    "方案里的选做项目怎么换成其他实验",
    "已经选课后时间不合适怎么调整",
    "补做是不是任课老师同意就可以了",
    "怎么一次取消本学期全部选课",
    "如果我想调课应该怎么操作",
    "如果我想换组应该怎么操作",
    "如果我想补做应该怎么操作",
)


async def main() -> None:
    async with AsyncSessionFactory() as session:
        for query in QUERIES:
            result = await search_operation_guides(session, query=query)
            guide = result.get("guide", {})
            title = guide.get("title", "未命中") if isinstance(guide, dict) else "未命中"
            print(f"{query} -> {title} [{result.get('retrieval_mode', 'NONE')}]")


if __name__ == "__main__":
    asyncio.run(main())
