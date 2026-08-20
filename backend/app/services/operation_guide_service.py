from __future__ import annotations

import logging
import math
import re
from collections import Counter
from collections.abc import Iterable

import httpx
from openai import AsyncOpenAI, OpenAIError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.data.student_operation_guides import (
    GUIDES_BY_ID,
    STUDENT_OPERATION_GUIDES,
    OperationGuide,
)

logger = logging.getLogger(__name__)
_STATIC_EMBEDDING_CACHE: dict[str, list[float]] | None = None

_PLATFORM_TERMS = (
    "在线选课",
    "实验课表",
    "AI智能咨询",
    "推荐方案",
    "选择此方案",
    "换选做项目",
    "校验并准备确认",
    "确认执行",
    "取消全部选课",
    "调课",
    "调课申请",
    "换组",
    "换组申请",
    "补做",
    "补做申请",
    "任课教师初审",
    "管理员复审",
)
_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]")


def _normalize_question(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "", value).lower()


def _fallback_tokens(value: str) -> list[str]:
    compact = re.sub(r"\s+", "", value.lower())
    tokens = _TOKEN_PATTERN.findall(compact)
    chinese = "".join(item for item in tokens if "\u4e00" <= item <= "\u9fff")
    tokens.extend(chinese[index : index + 2] for index in range(len(chinese) - 1))
    tokens.extend(term.lower() for term in _PLATFORM_TERMS if term in value)
    return tokens


def tokenize_guide_text(value: str) -> list[str]:
    try:
        import jieba  # type: ignore[import-not-found]

        for term in _PLATFORM_TERMS:
            jieba.add_word(term, freq=100000)
        return [token.strip().lower() for token in jieba.lcut(value) if token.strip()]
    except ImportError:
        return _fallback_tokens(value)


def _guide_search_text(guide: OperationGuide) -> str:
    return " ".join(
        [
            *([guide["title"]] * 3),
            *guide["keywords"],
            *guide["keywords"],
            *guide["keywords"],
            *guide["questions"],
            *guide["questions"],
            *guide["steps"],
            *guide["notices"],
        ]
    )


def _embedding_text(guide: OperationGuide) -> str:
    return "\n".join(
        [
            guide["title"],
            *guide["questions"],
            *guide["keywords"],
            *guide["steps"],
            *guide["notices"],
        ]
    )


_DOCUMENT_TOKENS = {
    guide["guide_id"]: tokenize_guide_text(_guide_search_text(guide))
    for guide in STUDENT_OPERATION_GUIDES
}
_DOCUMENT_LENGTHS = {
    guide_id: len(tokens) for guide_id, tokens in _DOCUMENT_TOKENS.items()
}
_AVERAGE_DOCUMENT_LENGTH = sum(_DOCUMENT_LENGTHS.values()) / max(
    1, len(_DOCUMENT_LENGTHS)
)
_DOCUMENT_FREQUENCY = Counter(
    token
    for tokens in _DOCUMENT_TOKENS.values()
    for token in set(tokens)
)


def bm25_search(query: str, *, limit: int = 10) -> list[tuple[str, float]]:
    query_tokens = tokenize_guide_text(query)
    if not query_tokens:
        return []
    total_documents = len(_DOCUMENT_TOKENS)
    scores: list[tuple[str, float]] = []
    k1 = 1.5
    b = 0.75
    for guide_id, document_tokens in _DOCUMENT_TOKENS.items():
        frequencies = Counter(document_tokens)
        document_length = _DOCUMENT_LENGTHS[guide_id]
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if frequency == 0:
                continue
            document_frequency = _DOCUMENT_FREQUENCY[token]
            inverse_document_frequency = math.log(
                1 + (total_documents - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            denominator = frequency + k1 * (
                1 - b + b * document_length / _AVERAGE_DOCUMENT_LENGTH
            )
            score += inverse_document_frequency * frequency * (k1 + 1) / denominator
        if score > 0:
            scores.append((guide_id, score))
    return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]


async def create_embeddings(texts: Iterable[str]) -> list[list[float]]:
    settings = get_settings()
    if not settings.embedding_enabled:
        raise RuntimeError("操作指南Embedding尚未启用。")
    settings.validate_runtime_secrets()
    api_key = settings.effective_embedding_api_key
    assert api_key is not None
    client = AsyncOpenAI(
        api_key=api_key.get_secret_value(),
        base_url=settings.effective_embedding_base_url,
        # 忽略环境代理:代理不可达会导致 embedding 调用整体失败
        http_client=httpx.AsyncClient(trust_env=False),
    )
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=list(texts),
    )
    vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    if any(len(vector) != settings.embedding_dimensions for vector in vectors):
        raise ValueError("Embedding服务返回的向量维度与系统配置不一致。")
    return vectors


async def _vector_search(
    session: AsyncSession, query: str, *, limit: int = 10
) -> list[tuple[str, float]]:
    settings = get_settings()
    if not settings.embedding_enabled:
        return []
    try:
        vector = (await create_embeddings([query]))[0]
        vector_literal = "[" + ",".join(f"{value:.9g}" for value in vector) + "]"
        try:
            async with session.begin_nested():
                rows = (
                    await session.execute(
                        text(
                            "SELECT guide_id, 1 - (embedding <=> CAST(:embedding AS vector)) "
                            "AS similarity FROM operation_guide_index "
                            "WHERE audience = 'STUDENT' "
                            "AND knowledge_type = 'OPERATION_GUIDE' "
                            "AND status = 'PUBLISHED' AND locale = 'zh-CN' "
                            "AND platform_version = :platform_version "
                            "AND embedding IS NOT NULL "
                            "ORDER BY embedding <=> CAST(:embedding AS vector) LIMIT :limit"
                        ),
                        {
                            "embedding": vector_literal,
                            "platform_version": settings.app_version,
                            "limit": limit,
                        },
                    )
                ).all()
            if rows:
                return [
                    (
                        str(row.guide_id),
                        max(0.0, min(1.0, float(row.similarity))),
                    )
                    for row in rows
                ]
        except SQLAlchemyError:
            logger.info("pgvector指南索引尚未就绪，使用进程内向量索引。")
        return await _static_vector_search(vector, limit=limit)
    except (OpenAIError, RuntimeError, ValueError, OSError) as error:
        logger.warning("操作指南向量检索降级为BM25: %s", type(error).__name__)
        return []


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot_product / (left_norm * right_norm)


async def _static_vector_search(
    query_vector: list[float], *, limit: int
) -> list[tuple[str, float]]:
    global _STATIC_EMBEDDING_CACHE
    if _STATIC_EMBEDDING_CACHE is None:
        items = guide_embedding_texts()
        vectors = await create_embeddings(text_value for _, text_value in items)
        _STATIC_EMBEDDING_CACHE = {
            guide_id: vector
            for (guide_id, _), vector in zip(items, vectors, strict=True)
        }
    scores = [
        (guide_id, max(0.0, min(1.0, _cosine_similarity(query_vector, vector))))
        for guide_id, vector in _STATIC_EMBEDDING_CACHE.items()
    ]
    return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]


def _format_student_answer(guide: OperationGuide) -> str:
    lines = [guide["title"] + "："]
    lines.extend(f"{index}. {step}" for index, step in enumerate(guide["steps"], start=1))
    if guide["notices"]:
        lines.append("\n需要注意：" + "；".join(guide["notices"]))
    return "\n".join(lines)


async def search_operation_guides(
    session: AsyncSession, *, query: str
) -> dict[str, object]:
    bm25_results = bm25_search(query)
    vector_results = await _vector_search(session, query)
    bm25_max = bm25_results[0][1] if bm25_results else 0.0
    bm25_scores = {
        guide_id: score / bm25_max for guide_id, score in bm25_results if bm25_max > 0
    }
    vector_scores = dict(vector_results)
    candidate_ids = set(bm25_scores) | set(vector_scores)
    has_vector = bool(vector_scores)
    normalized_query = _normalize_question(query)
    exact_question_ids = {
        guide["guide_id"]
        for guide in STUDENT_OPERATION_GUIDES
        if normalized_query
        and any(
            _normalize_question(question) == normalized_query
            for question in guide["questions"]
        )
    }
    ranked = sorted(
        (
            {
                "guide_id": guide_id,
                "bm25_score": bm25_scores.get(guide_id, 0.0),
                "vector_score": vector_scores.get(guide_id, 0.0),
                "exact_question_match": guide_id in exact_question_ids,
                "score": (
                    bm25_scores.get(guide_id, 0.0) * 0.6
                    + vector_scores.get(guide_id, 0.0) * 0.4
                    if has_vector
                    else bm25_scores.get(guide_id, 0.0)
                ),
            }
            for guide_id in candidate_ids | exact_question_ids
            if guide_id in GUIDES_BY_ID
        ),
        key=lambda item: (bool(item["exact_question_match"]), float(item["score"])),
        reverse=True,
    )
    matches = ranked[:2]
    if not matches or float(matches[0]["score"]) < 0.25:
        return {
            "status": "NOT_FOUND",
            "matches": [],
            "answer": "当前操作指南中没有找到足够明确的说明。请告诉我你想使用哪个页面或完成什么操作。",
        }
    top_guide = GUIDES_BY_ID[str(matches[0]["guide_id"])]
    return {
        "status": "FOUND",
        "matches": [
            {
                **match,
                "title": GUIDES_BY_ID[str(match["guide_id"])]["title"],
                "topic": GUIDES_BY_ID[str(match["guide_id"])]["topic"],
            }
            for match in matches
        ],
        "guide": top_guide,
        "answer": _format_student_answer(top_guide),
        "source": f"学生端操作指南 · {top_guide['title']}",
        "retrieval_mode": "HYBRID" if has_vector else "BM25",
    }


def guide_embedding_texts() -> list[tuple[str, str]]:
    return [
        (guide["guide_id"], _embedding_text(guide))
        for guide in STUDENT_OPERATION_GUIDES
    ]
