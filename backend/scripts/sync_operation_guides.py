from __future__ import annotations

import asyncio
import hashlib
import json

from sqlalchemy import text

from app.core.config.settings import get_settings
from app.data.student_operation_guides import GUIDES_BY_ID
from app.db.session import AsyncSessionFactory
from app.services.operation_guide_service import (
    create_embeddings,
    guide_embedding_texts,
)


async def sync_guides() -> None:
    settings = get_settings()
    if not settings.embedding_enabled:
        raise RuntimeError("请先配置 EMBEDDING_API_KEY 或 SILICONFLOW_API_KEY。")
    guide_texts = guide_embedding_texts()
    vectors = await create_embeddings(text_value for _, text_value in guide_texts)
    async with AsyncSessionFactory() as session:
        for (guide_id, _), vector in zip(guide_texts, vectors, strict=True):
            guide = GUIDES_BY_ID[guide_id]
            content_json = json.dumps(guide, ensure_ascii=False, sort_keys=True)
            content_hash = hashlib.sha256(content_json.encode("utf-8")).hexdigest()
            vector_literal = "[" + ",".join(f"{value:.9g}" for value in vector) + "]"
            await session.execute(
                text(
                    "INSERT INTO operation_guide_index "
                    "(guide_id, title, audience, knowledge_type, topic, locale, status, "
                    "platform_version, content_hash, content, embedding) VALUES "
                    "(:guide_id, :title, 'STUDENT', 'OPERATION_GUIDE', :topic, "
                    "'zh-CN', 'PUBLISHED', :platform_version, :content_hash, "
                    "CAST(:content AS jsonb), CAST(:embedding AS vector)) "
                    "ON CONFLICT (guide_id) DO UPDATE SET "
                    "title = EXCLUDED.title, topic = EXCLUDED.topic, "
                    "platform_version = EXCLUDED.platform_version, "
                    "content_hash = EXCLUDED.content_hash, content = EXCLUDED.content, "
                    "embedding = EXCLUDED.embedding"
                ),
                {
                    "guide_id": guide_id,
                    "title": guide["title"],
                    "topic": guide["topic"],
                    "platform_version": settings.app_version,
                    "content_hash": content_hash,
                    "content": content_json,
                    "embedding": vector_literal,
                },
            )
        await session.commit()
    print(f"已同步 {len(guide_texts)} 条学生操作指南。")


if __name__ == "__main__":
    asyncio.run(sync_guides())
