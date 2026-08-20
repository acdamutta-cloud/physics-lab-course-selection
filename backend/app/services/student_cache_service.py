"""Controlled warm-up and refresh for student read caches.

All builders call the existing database-backed aggregation functions.  This
module changes only when those results are cached, not how they are computed.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID, uuid4

from sqlalchemy import select

from app.cache.academic_term import warm_active_term
from app.cache.selection_precheck import (
    invalidate_selection_context,
    refresh_selection_context,
    student_context_key,
)
from app.cache.student_views import (
    ai_context_key,
    bitmap_key,
    ensure_dashboard_derived_views,
    get_or_build,
    get_or_build_dashboard,
    invalidate_ai_context,
    invalidate_student_views,
    write_cache,
    write_dashboard_fragments,
)
from app.core.config.settings import get_settings
from app.db.redis_client import get_redis_client
from app.db.session import AsyncSessionFactory
from app.models.curriculum import AcademicTerm
from app.models.identity import Student, UserAccount
from app.schemas.auth import UserProfile

logger = logging.getLogger(__name__)
WARM_LOCK = "student:cache-warm:v2"
REFRESH_LOCK = "student:cache-refresh:v2"
SELECTION_CONTEXT_WARM_LOCK = "selection:context-missing-warm:v1"
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()
_PENDING_REFRESHES: dict[tuple[UUID, UUID], bool] = {}
_REFRESH_SEMAPHORE = asyncio.Semaphore(get_settings().student_cache_warm_concurrency)


def _start_background(coro) -> None:
    async def guarded() -> None:
        try:
            await coro
        except Exception:
            logger.warning("Student cache background refresh failed", exc_info=True)

    task = asyncio.create_task(guarded())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def refresh_experiment_views_after_commit(
    student_id: UUID,
    term_id: UUID,
    *,
    dashboard: bool = True,
) -> None:
    """Invalidate stale fragments, then rebuild without delaying the commit."""

    if dashboard:
        await invalidate_student_views(student_id, term_id)
    else:
        await invalidate_ai_context(student_id, term_id)
    await invalidate_selection_context(student_id, term_id)
    # 准入上下文必须立即可用:提交成功即删除 context,而批量方案执行
    # (execute_plan)会顺序连续处理多个场次;若重建放后台,下一个场次的
    # LUA 预检会在重建完成前命中 SELECTION_CACHE_MISSING(-3),表现为
    # "选课状态发生变化,请刷新后重试。"。这里同步重建准入 context,
    # 后台 rebuild 只负责 dashboard/ai 视图。
    try:
        async with AsyncSessionFactory() as session:
            await refresh_selection_context(
                session, student_id=student_id, term_id=term_id
            )
    except Exception:
        logger.warning(
            "Selection context rebuild after commit failed student=%s term=%s",
            student_id,
            term_id,
            exc_info=True,
        )
    identity = (student_id, term_id)
    if identity in _PENDING_REFRESHES:
        _PENDING_REFRESHES[identity] = _PENDING_REFRESHES[identity] or dashboard
        return
    _PENDING_REFRESHES[identity] = dashboard

    async def rebuild() -> None:
        try:
            while identity in _PENDING_REFRESHES:
                refresh_dashboard = _PENDING_REFRESHES.pop(identity)
                async with _REFRESH_SEMAPHORE:
                    await refresh_student_caches(
                        student_id,
                        term_id,
                        dashboard=refresh_dashboard,
                        ai_context=True,
                        bitmap=False,
                        force_refresh=True,
                    )
        finally:
            _PENDING_REFRESHES.pop(identity, None)

    _start_background(rebuild())


async def _unlink_batch(redis: object, keys: list[str]) -> int:
    pipe = redis.pipeline(transaction=False)
    for key in keys:
        pipe.unlink(key)
    results = await pipe.execute()
    return sum(int(value) for value in results)


async def invalidate_term_selection_caches(term_id: UUID) -> int:
    """课表发布批量退选后,清理该学期全部学生的选课相关缓存。

    发布新课表会把全学期学生的选课记录批量置为 WITHDRAWN(见
    schedule_service.publish_selected_schedule)。若不清理 Redis,残留的
    已选项目集合会在 TTL 内把学生挡在"同一实验项目只能选择一个场次"上。
    缓存清理必须 fail-open:Redis 故障不能阻塞课表发布。
    """

    term = str(term_id)
    patterns = [
        f"selection:student-context:*:{term}:v1",
        f"selection:selected-projects:*:{term}",
        f"selection:applications:*:{term}",
        f"selection:project:*:{term}:*",
        f"student:dashboard:*:{term}:v2",
        f"student:dashboard-static:*:{term}:v2",
        f"student:dashboard-dynamic:*:{term}:v2",
        f"student:dashboard-summary:*:{term}:v2",
        f"student:timetable:*:{term}:v2",
        f"ai:student-context:*:{term}:v2",
    ]
    redis = get_redis_client()
    deleted = 0
    try:
        for pattern in patterns:
            batch: list[str] = []
            async for key in redis.scan_iter(match=pattern, count=500):
                batch.append(key)
                if len(batch) >= 500:
                    deleted += await _unlink_batch(redis, batch)
                    batch = []
            if batch:
                deleted += await _unlink_batch(redis, batch)
    except Exception:  # noqa: BLE001 - cache cleanup must not block publishing
        logger.warning(
            "Term-wide selection cache cleanup failed term=%s", term, exc_info=True
        )
        return 0
    logger.info(
        "Term-wide selection cache cleanup deleted=%s term=%s", deleted, term
    )
    return deleted


async def _profile(student: Student, user: UserAccount) -> UserProfile:
    return UserProfile(
        id=user.id,
        login_name=user.login_name,
        user_type=user.user_type,
        student_id=student.id,
        name=student.name,
        student_no=student.student_no,
        enrollment_year=student.enrollment_year,
    )


async def refresh_student_caches(
    student_id: UUID,
    term_id: UUID,
    *,
    dashboard: bool = True,
    ai_context: bool = True,
    bitmap: bool = False,
    force_bitmap: bool = False,
    force_refresh: bool = False,
) -> None:
    """Rebuild selected cache fragments using their existing builders."""

    # Runtime imports avoid an application-router import cycle.
    from app.agents.nodes.student_advisor import _build_base_context
    from app.api.routers.students import (
        _get_dashboard_uncached,
        _get_my_bitmap_uncached,
    )

    async with AsyncSessionFactory() as session:
        row = (
            await session.execute(
                select(Student, UserAccount)
                .join(UserAccount, UserAccount.id == Student.user_id)
                .where(Student.id == student_id, UserAccount.status == "ACTIVE")
            )
        ).one_or_none()
        term = await session.get(AcademicTerm, term_id)
        if row is None or term is None:
            return
        student, user = row
        profile = await _profile(student, user)
        settings = get_settings()
        if bitmap:
            if force_bitmap:
                fresh_bitmap = await _get_my_bitmap_uncached(session, profile)
                await write_cache(
                    bitmap_key(student_id, term_id),
                    fresh_bitmap,
                    settings.student_bitmap_cache_ttl_seconds,
                )
            else:
                await get_or_build(
                    bitmap_key(student_id, term_id),
                    ttl=settings.student_bitmap_cache_ttl_seconds,
                    builder=lambda: _get_my_bitmap_uncached(session, profile),
                )
        if dashboard:
            if force_refresh:
                fresh_dashboard = await _get_dashboard_uncached(session, profile)
                await write_dashboard_fragments(
                    student_id, term_id, fresh_dashboard
                )
            else:
                cached_dashboard = await get_or_build_dashboard(
                    student_id,
                    term_id,
                    builder=lambda: _get_dashboard_uncached(session, profile),
                )
                await ensure_dashboard_derived_views(
                    student_id, term_id, cached_dashboard
                )
        if ai_context:
            state = {"session": session, "student_id": student_id, "term": term}
            if force_refresh:
                fresh_ai = await _build_base_context(state)
                await write_cache(
                    ai_context_key(student_id, term_id),
                    fresh_ai,
                    settings.student_ai_context_cache_ttl_seconds,
                )
            else:
                await get_or_build(
                    ai_context_key(student_id, term_id),
                    ttl=settings.student_ai_context_cache_ttl_seconds,
                    builder=lambda: _build_base_context(state),
                )
        try:
            await refresh_selection_context(
                session, student_id=student_id, term_id=term_id
            )
        except Exception:
            logger.warning(
                "Selection context refresh failed student=%s term=%s",
                student_id,
                term_id,
                exc_info=True,
            )


async def warm_all_student_caches(*, force_bitmap: bool = False) -> int:
    """Preload all active students with bounded database concurrency."""

    redis = get_redis_client()
    lock_token = uuid4().hex
    try:
        acquired = await redis.set(WARM_LOCK, lock_token, nx=True, ex=7200)
        if not acquired:
            return 0
    except Exception:  # noqa: BLE001
        logger.warning("Student cache warm-up skipped: Redis unavailable")
        return 0

    async with AsyncSessionFactory() as session:
        term_id = await warm_active_term(session)
        student_ids = list(
            await session.scalars(
                select(Student.id)
                .join(UserAccount, UserAccount.id == Student.user_id)
                .where(
                    Student.academic_status == "ACTIVE",
                    UserAccount.status == "ACTIVE",
                )
                .order_by(Student.id)
            )
        )

    semaphore = asyncio.Semaphore(get_settings().student_cache_warm_concurrency)
    warmed = 0

    async def warm_one(student_id: UUID) -> None:
        nonlocal warmed
        async with semaphore:
            try:
                await refresh_student_caches(
                    student_id,
                    term_id,
                    dashboard=True,
                    ai_context=True,
                    bitmap=True,
                    force_bitmap=force_bitmap,
                )
                warmed += 1
            except Exception:  # noqa: BLE001 - one student must not stop the batch
                logger.warning("Student cache warm-up failed student=%s", student_id)

    try:
        await asyncio.gather(*(warm_one(student_id) for student_id in student_ids))
        logger.info(
            "Warmed student read caches count=%d total=%d", warmed, len(student_ids)
        )
        return warmed
    finally:
        try:
            await redis.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                "return redis.call('DEL', KEYS[1]) end return 0",
                1,
                WARM_LOCK,
                lock_token,
            )
        except Exception:
            logger.debug("Student warm-up lock release failed", exc_info=True)


async def periodic_student_cache_warmup() -> None:
    """Keep all student caches populated with bounded rolling refreshes."""

    settings = get_settings()
    await asyncio.sleep(settings.student_cache_initial_refresh_delay_seconds)
    bitmap_refresh_due = asyncio.get_running_loop().time() + 23 * 60 * 60
    while True:
        try:
            force_bitmap = asyncio.get_running_loop().time() >= bitmap_refresh_due
            await refresh_all_student_caches(force_bitmap=force_bitmap)
            if force_bitmap:
                bitmap_refresh_due = asyncio.get_running_loop().time() + 23 * 60 * 60
            await asyncio.sleep(settings.student_cache_periodic_refresh_seconds)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("Periodic student cache warm-up failed", exc_info=True)
            await asyncio.sleep(60)


async def warm_missing_selection_contexts() -> int:
    """Populate only absent admission contexts without rebuilding dashboards."""

    redis = get_redis_client()
    lock_token = uuid4().hex
    acquired = await redis.set(
        SELECTION_CONTEXT_WARM_LOCK,
        lock_token,
        nx=True,
        ex=3600,
    )
    if not acquired:
        return 0
    try:
        async with AsyncSessionFactory() as session:
            term_id = await warm_active_term(session)
            student_ids = list(
                await session.scalars(
                    select(Student.id)
                    .join(UserAccount, UserAccount.id == Student.user_id)
                    .where(
                        Student.academic_status == "ACTIVE",
                        UserAccount.status == "ACTIVE",
                    )
                    .order_by(Student.id)
                )
            )

        missing: list[UUID] = []
        for offset in range(0, len(student_ids), 500):
            batch = student_ids[offset : offset + 500]
            pipe = redis.pipeline(transaction=False)
            for student_id in batch:
                pipe.exists(student_context_key(student_id, term_id))
            present = await pipe.execute()
            missing.extend(
                student_id
                for student_id, exists in zip(batch, present, strict=True)
                if not exists
            )
        if not missing:
            return 0

        semaphore = asyncio.Semaphore(
            get_settings().selection_context_warm_concurrency
        )
        warmed = 0

        async def warm_one(student_id: UUID) -> None:
            nonlocal warmed
            async with semaphore, AsyncSessionFactory() as session:
                if await refresh_selection_context(
                    session, student_id=student_id, term_id=term_id
                ):
                    warmed += 1

        results = await asyncio.gather(
            *(warm_one(student_id) for student_id in missing),
            return_exceptions=True,
        )
        failures = sum(isinstance(result, Exception) for result in results)
        logger.info(
            "Warmed missing selection contexts count=%d missing=%d failures=%d",
            warmed,
            len(missing),
            failures,
        )
        return warmed
    finally:
        await redis.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('DEL', KEYS[1]) end return 0",
            1,
            SELECTION_CONTEXT_WARM_LOCK,
            lock_token,
        )


async def periodic_selection_context_warmup() -> None:
    """Quickly fill missing selection contexts and keep expiry gaps bounded."""

    settings = get_settings()
    await asyncio.sleep(settings.selection_context_initial_warm_delay_seconds)
    while True:
        try:
            await warm_missing_selection_contexts()
            await asyncio.sleep(settings.selection_context_missing_scan_seconds)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("Selection context warm-up failed", exc_info=True)
            await asyncio.sleep(30)


async def refresh_all_student_caches(*, force_bitmap: bool = False) -> int:
    """Rolling overwrite used before dynamic/AI TTLs and bitmap TTL expire."""

    redis = get_redis_client()
    lock_token = uuid4().hex
    try:
        acquired = await redis.set(REFRESH_LOCK, lock_token, nx=True, ex=7200)
        if not acquired:
            return 0
    except Exception:  # noqa: BLE001
        logger.warning("Student cache refresh skipped: Redis unavailable")
        return 0

    try:
        async with AsyncSessionFactory() as session:
            term_id = await warm_active_term(session)
            student_ids = list(
                await session.scalars(
                    select(Student.id)
                    .join(UserAccount, UserAccount.id == Student.user_id)
                    .where(
                        Student.academic_status == "ACTIVE",
                        UserAccount.status == "ACTIVE",
                    )
                )
            )
        semaphore = asyncio.Semaphore(get_settings().student_cache_warm_concurrency)
        refreshed = 0

        async def refresh_one(student_id: UUID) -> None:
            nonlocal refreshed
            async with semaphore:
                await refresh_student_caches(
                    student_id,
                    term_id,
                    dashboard=True,
                    ai_context=True,
                    bitmap=force_bitmap,
                    force_bitmap=force_bitmap,
                    force_refresh=True,
                )
                refreshed += 1

        results = await asyncio.gather(
            *(refresh_one(student_id) for student_id in student_ids),
            return_exceptions=True,
        )
        failures = sum(isinstance(result, Exception) for result in results)
        logger.info(
            "Refreshed student caches count=%d failures=%d", refreshed, failures
        )
        return refreshed
    finally:
        try:
            await redis.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                "return redis.call('DEL', KEYS[1]) end return 0",
                1,
                REFRESH_LOCK,
                lock_token,
            )
        except Exception:
            logger.debug("Student refresh lock release failed", exc_info=True)
