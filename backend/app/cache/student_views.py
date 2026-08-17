"""Best-effort caches for read-heavy student views.

PostgreSQL remains authoritative.  Every helper in this module deliberately
fails open so a Redis incident cannot make read-only student pages unavailable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis

from app.core.config.settings import get_settings
from app.db.redis_client import get_redis_client

logger = logging.getLogger(__name__)
CACHE_VERSION = "v2"


def dashboard_key(student_id: UUID, term_id: UUID) -> str:
    return f"student:dashboard:{student_id}:{term_id}:{CACHE_VERSION}"


def dashboard_static_key(student_id: UUID, term_id: UUID) -> str:
    return f"student:dashboard-static:{student_id}:{term_id}:{CACHE_VERSION}"


def dashboard_dynamic_key(student_id: UUID, term_id: UUID) -> str:
    return f"student:dashboard-dynamic:{student_id}:{term_id}:{CACHE_VERSION}"


def dashboard_summary_key(student_id: UUID, term_id: UUID) -> str:
    return f"student:dashboard-summary:{student_id}:{term_id}:{CACHE_VERSION}"


def timetable_key(student_id: UUID, term_id: UUID) -> str:
    return f"student:timetable:{student_id}:{term_id}:{CACHE_VERSION}"


def dashboard_shared_key(content_hash: str) -> str:
    return f"student:dashboard-shared:{CACHE_VERSION}:{content_hash}"


def bitmap_key(student_id: UUID, term_id: UUID) -> str:
    return f"student:bitmap:{student_id}:{term_id}:{CACHE_VERSION}"


def ai_context_key(student_id: UUID, term_id: UUID) -> str:
    return f"ai:student-context:{student_id}:{term_id}:{CACHE_VERSION}"


async def _read(redis: Redis, key: str) -> Any | None:
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except (OSError, ValueError, TypeError):
        logger.warning("Student cache read failed; using PostgreSQL key=%s", key)
        try:
            await redis.delete(key)
        except Exception:  # cache cleanup is best effort
            logger.debug("Bad student cache cleanup failed key=%s", key, exc_info=True)
        return None
    except Exception:
        logger.exception("Unexpected student cache read failure key=%s", key)
        return None


async def _write(redis: Redis, key: str, value: Any, ttl: int) -> None:
    settings = get_settings()
    jitter = random.randint(0, settings.student_cache_ttl_jitter_seconds)
    try:
        await redis.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl + jitter)
    except Exception:
        logger.warning("Student cache write failed key=%s", key, exc_info=True)


async def write_cache(key: str, value: Any, ttl: int, redis: Redis | None = None) -> None:
    """Public best-effort writer used by controlled warm-up jobs."""

    await _write(redis or get_redis_client(), key, value, ttl)


async def write_dashboard_fragments(
    student_id: UUID,
    term_id: UUID,
    value: dict[str, Any],
    *,
    redis: Redis | None = None,
) -> None:
    """Atomically replace each dashboard fragment after a fresh value is built."""

    client = redis or get_redis_client()
    settings = get_settings()
    stable, changing = split_dashboard(value)
    shared = {"courses": stable.pop("courses", [])}
    shared_json = json.dumps(
        shared, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    shared_key = dashboard_shared_key(
        hashlib.sha256(shared_json.encode("utf-8")).hexdigest()[:24]
    )
    student_static = {**stable, "shared_ref": shared_key}
    summary = dashboard_summary(value)
    timetable = dashboard_timetable(value)
    await asyncio.gather(
        _write(
            client,
            dashboard_static_key(student_id, term_id),
            student_static,
            settings.student_dashboard_static_cache_ttl_seconds,
        ),
        _write(
            client,
            shared_key,
            shared,
            settings.student_dashboard_static_cache_ttl_seconds,
        ),
        _write(
            client,
            dashboard_dynamic_key(student_id, term_id),
            changing,
            settings.student_dashboard_cache_ttl_seconds,
        ),
        _write(
            client,
            dashboard_summary_key(student_id, term_id),
            summary,
            settings.student_dashboard_cache_ttl_seconds,
        ),
        _write(
            client,
            timetable_key(student_id, term_id),
            timetable,
            settings.student_dashboard_cache_ttl_seconds,
        ),
    )


async def ensure_dashboard_derived_views(
    student_id: UUID,
    term_id: UUID,
    value: dict[str, Any],
    *,
    redis: Redis | None = None,
) -> None:
    """Populate lightweight views from an already cached Dashboard value."""

    client = redis or get_redis_client()
    settings = get_settings()
    summary_key = dashboard_summary_key(student_id, term_id)
    schedule_key = timetable_key(student_id, term_id)
    cached_summary, cached_timetable = await asyncio.gather(
        _read(client, summary_key), _read(client, schedule_key)
    )
    writes = []
    if cached_summary is None:
        writes.append(
            _write(
                client,
                summary_key,
                dashboard_summary(value),
                settings.student_dashboard_cache_ttl_seconds,
            )
        )
    if cached_timetable is None:
        writes.append(
            _write(
                client,
                schedule_key,
                dashboard_timetable(value),
                settings.student_dashboard_cache_ttl_seconds,
            )
        )
    if writes:
        await asyncio.gather(*writes)


async def get_or_build(
    key: str,
    *,
    ttl: int,
    builder: Callable[[], Awaitable[Any]],
    redis: Redis | None = None,
) -> Any:
    """Return a cached view with short single-flight protection.

    If Redis, locking, JSON decoding, or cache writes fail, ``builder`` is used.
    This preserves the original database-backed behaviour.
    """

    client = redis or get_redis_client()
    cached = await _read(client, key)
    if cached is not None:
        return cached

    settings = get_settings()
    lock_key = f"cache:rebuild:{key}"
    lock_token = uuid4().hex
    acquired = False
    try:
        acquired = bool(
            await client.set(
                lock_key,
                lock_token,
                nx=True,
                px=settings.student_cache_rebuild_lock_ms,
            )
        )
    except Exception:  # noqa: BLE001 - Redis degradation falls back to PostgreSQL
        return await builder()

    if not acquired:
        for _ in range(settings.student_cache_wait_attempts):
            await asyncio.sleep(settings.student_cache_wait_ms / 1000)
            cached = await _read(client, key)
            if cached is not None:
                return cached
        return await builder()

    try:
        value = await builder()
        await _write(client, key, value, ttl)
        return value
    finally:
        try:
            await client.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                "return redis.call('DEL', KEYS[1]) end return 0",
                1,
                lock_key,
                lock_token,
            )
        except Exception:
            logger.debug("Student cache lock release failed key=%s", key, exc_info=True)


async def invalidate_student_views(student_id: UUID, term_id: UUID) -> None:
    """Invalidate experiment-related views; the base bitmap is unchanged."""

    keys = (
        dashboard_key(student_id, term_id),
        dashboard_dynamic_key(student_id, term_id),
        dashboard_summary_key(student_id, term_id),
        timetable_key(student_id, term_id),
        ai_context_key(student_id, term_id),
    )
    try:
        await asyncio.wait_for(get_redis_client().delete(*keys), timeout=1.0)
    except Exception:
        logger.warning(
            "Student cache invalidation failed student=%s term=%s",
            student_id,
            term_id,
            exc_info=True,
        )


async def invalidate_ai_context(student_id: UUID, term_id: UUID) -> None:
    """Invalidate only AI data when an application changes but timetable does not."""

    try:
        await asyncio.wait_for(
            get_redis_client().delete(ai_context_key(student_id, term_id)), timeout=1.0
        )
    except Exception:  # noqa: BLE001 - invalidation is best effort after commit
        logger.warning("AI context invalidation failed student=%s", student_id)


async def invalidate_base_bitmap(student_id: UUID, term_id: UUID) -> None:
    """Use only when the non-experiment timetable itself changes."""

    try:
        await asyncio.wait_for(
            get_redis_client().delete(
                bitmap_key(student_id, term_id),
                dashboard_key(student_id, term_id),
                dashboard_static_key(student_id, term_id),
                dashboard_dynamic_key(student_id, term_id),
                dashboard_summary_key(student_id, term_id),
                timetable_key(student_id, term_id),
                ai_context_key(student_id, term_id),
            ),
            timeout=1.0,
        )
    except Exception:  # noqa: BLE001 - invalidation is best effort after commit
        logger.warning("Base bitmap cache invalidation failed student=%s", student_id)


_DYNAMIC_TOP_LEVEL = {
    "selection",
    "next_lab",
    "selected_sessions",
    "prerequisites",
}


def dashboard_summary(value: dict[str, Any]) -> dict[str, Any]:
    """Return the fields used by the home page without unselected session cards."""

    selected_ids = {
        item.get("session_id")
        for item in value.get("selected_sessions", [])
        if item.get("session_id")
    }
    courses = []
    for source_course in value.get("courses", []):
        course = dict(source_course)
        projects = []
        for source_project in source_course.get("projects", []):
            project = dict(source_project)
            project["available_sessions"] = [
                item
                for item in source_project.get("available_sessions", [])
                if item.get("id") in selected_ids
            ]
            projects.append(project)
        course["projects"] = projects
        courses.append(course)
    return {
        "profile": value.get("profile"),
        "term": value.get("term"),
        "courses": courses,
        "prerequisites": value.get("prerequisites"),
        "selection": value.get("selection"),
        "next_lab": value.get("next_lab"),
        "selected_sessions": value.get("selected_sessions", []),
    }


def dashboard_timetable(value: dict[str, Any]) -> dict[str, Any]:
    """Return the exact schedule fields already rendered by StudentPortal."""

    return {
        "term": value.get("term"),
        "selected_sessions": value.get("selected_sessions", []),
    }


def split_dashboard(value: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a legacy dashboard response without changing its public shape."""

    static = {k: v for k, v in value.items() if k not in _DYNAMIC_TOP_LEVEL}
    dynamic = {k: value.get(k) for k in _DYNAMIC_TOP_LEVEL}
    static_courses = []
    dynamic_courses = []
    for course in value.get("courses", []):
        stable_course = dict(course)
        dynamic_courses.append(
            {
                "completion_status": stable_course.pop("completion_status", None),
                "prerequisites_passed": stable_course.pop("prerequisites_passed", []),
                "prerequisites_failed": stable_course.pop("prerequisites_failed", []),
            }
        )
        static_courses.append(stable_course)
    static["courses"] = static_courses
    dynamic["courses"] = dynamic_courses
    return static, dynamic


def merge_dashboard(static: dict[str, Any], dynamic: dict[str, Any]) -> dict[str, Any]:
    """Recreate the exact legacy dashboard dictionary from cache fragments."""

    value = dict(static)
    dynamic_courses = dynamic.get("courses", [])
    courses = []
    for index, stable_course in enumerate(static.get("courses", [])):
        course = dict(stable_course)
        if index < len(dynamic_courses):
            course.update(dynamic_courses[index])
        courses.append(course)
    value["courses"] = courses
    for key in _DYNAMIC_TOP_LEVEL:
        value[key] = dynamic.get(key)
    return value


async def get_or_build_dashboard(
    student_id: UUID,
    term_id: UUID,
    *,
    builder: Callable[[], Awaitable[dict[str, Any]]],
    redis: Redis | None = None,
) -> dict[str, Any]:
    """Read split dashboard caches, building them from the legacy query on miss."""

    client = redis or get_redis_client()
    static_key = dashboard_static_key(student_id, term_id)
    dynamic_key = dashboard_dynamic_key(student_id, term_id)
    static, dynamic = await asyncio.gather(
        _read(client, static_key), _read(client, dynamic_key)
    )
    if isinstance(static, dict) and isinstance(dynamic, dict):
        shared_ref = static.get("shared_ref")
        if isinstance(shared_ref, str):
            shared = await _read(client, shared_ref)
            if isinstance(shared, dict):
                static = {**static, **shared}
                static.pop("shared_ref", None)
                return merge_dashboard(static, dynamic)
        elif "courses" in static:  # backward-compatible fragment format
            return merge_dashboard(static, dynamic)

    settings = get_settings()
    lock_key = f"cache:rebuild:{dashboard_key(student_id, term_id)}"
    lock_token = uuid4().hex
    try:
        acquired = bool(
            await client.set(
                lock_key,
                lock_token,
                nx=True,
                px=settings.student_cache_rebuild_lock_ms,
            )
        )
    except Exception:  # noqa: BLE001 - Redis degradation falls back to PostgreSQL
        return await builder()

    if not acquired:
        for _ in range(settings.student_cache_wait_attempts):
            await asyncio.sleep(settings.student_cache_wait_ms / 1000)
            static, dynamic = await asyncio.gather(
                _read(client, static_key), _read(client, dynamic_key)
            )
            if isinstance(static, dict) and isinstance(dynamic, dict):
                shared_ref = static.get("shared_ref")
                if isinstance(shared_ref, str):
                    shared = await _read(client, shared_ref)
                    if isinstance(shared, dict):
                        merged_static = {**static, **shared}
                        merged_static.pop("shared_ref", None)
                        return merge_dashboard(merged_static, dynamic)
        return await builder()

    try:
        full = await builder()
        await write_dashboard_fragments(student_id, term_id, full, redis=client)
        return full
    finally:
        try:
            await client.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                "return redis.call('DEL', KEYS[1]) end return 0",
                1,
                lock_key,
                lock_token,
            )
        except Exception:
            logger.debug("Dashboard cache lock release failed", exc_info=True)
