"""Encrypted, short-lived cache for authenticated user profiles.

The cache is an availability aid only. PostgreSQL remains authoritative.  No
password, phone number, JWT, or password hash is stored in Redis.  Redis keys
use an HMAC rather than the raw user id and values are encrypted at rest.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import random
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config.settings import get_settings
from app.db.redis_client import get_redis_client
from app.db.session import AsyncSessionFactory
from app.models.identity import Major, Student, Teacher, UserAccount
from app.schemas.auth import UserProfile

logger = logging.getLogger(__name__)
VERSION = "v2"


def _secret() -> bytes:
    return get_settings().jwt_secret_key.get_secret_value().encode("utf-8")


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(b"auth-cache:" + _secret()).digest())
    return Fernet(key)


def auth_profile_key(user_id: UUID) -> str:
    digest = hmac.new(_secret(), str(user_id).encode(), hashlib.sha256).hexdigest()[:32]
    return f"auth:principal:{VERSION}:{digest}"


async def _read(redis: Redis, key: str) -> UserProfile | None:
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        decrypted = _fernet().decrypt(raw.encode() if isinstance(raw, str) else raw)
        envelope = json.loads(decrypted)
        if envelope.get("version") != VERSION or envelope.get("active") is not True:
            raise ValueError("unsupported authentication cache envelope")
        profile = dict(envelope["profile"])
        profile["student_id"] = envelope.get("student_id")
        profile["teacher_id"] = envelope.get("teacher_id")
        return UserProfile.model_validate(profile)
    except (InvalidToken, ValueError, TypeError, KeyError, json.JSONDecodeError):
        logger.warning("Invalid authentication cache entry removed key=%s", key)
        try:
            await redis.delete(key)
        except Exception:  # best-effort cleanup only
            logger.debug("Invalid authentication cache cleanup failed", exc_info=True)
        return None
    except Exception:  # Redis must not make authentication unavailable
        logger.warning("Authentication cache read failed; using PostgreSQL", exc_info=True)
        return None


async def _write(redis: Redis, key: str, profile: UserProfile) -> None:
    settings = get_settings()
    envelope = {
        "version": VERSION,
        "active": True,
        "profile": profile.model_dump(mode="json"),
        "student_id": str(profile.student_id) if profile.student_id else None,
        "teacher_id": str(profile.teacher_id) if profile.teacher_id else None,
    }
    encrypted = _fernet().encrypt(
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    ttl = settings.auth_profile_cache_ttl_seconds + random.randint(
        0, settings.auth_profile_cache_ttl_jitter_seconds
    )
    try:
        await redis.set(key, encrypted, ex=ttl)
    except Exception:
        logger.warning("Authentication cache write failed key=%s", key, exc_info=True)


async def get_or_build_profile(
    user_id: UUID,
    builder: Callable[[], Awaitable[UserProfile | None]],
    *,
    redis: Redis | None = None,
) -> UserProfile | None:
    client = redis or get_redis_client()
    key = auth_profile_key(user_id)
    cached = await _read(client, key)
    if cached is not None:
        return cached

    settings = get_settings()
    lock_key = f"cache:rebuild:{key}"
    token = uuid4().hex
    try:
        acquired = bool(
            await client.set(
                lock_key, token, nx=True, px=settings.auth_profile_cache_rebuild_lock_ms
            )
        )
    except Exception:  # noqa: BLE001 - Redis degradation falls back to PostgreSQL
        return await builder()

    if not acquired:
        for _ in range(settings.auth_profile_cache_wait_attempts):
            await asyncio.sleep(settings.auth_profile_cache_wait_ms / 1000)
            cached = await _read(client, key)
            if cached is not None:
                return cached
        return await builder()

    try:
        profile = await builder()
        if profile is not None:
            await _write(client, key, profile)
        return profile
    finally:
        try:
            await client.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                "return redis.call('DEL', KEYS[1]) end return 0",
                1,
                lock_key,
                token,
            )
        except Exception:  # an expiring lock is safe to leave behind
            logger.debug("Authentication rebuild lock release failed", exc_info=True)


async def invalidate_auth_profile(user_id: UUID) -> None:
    try:
        await asyncio.wait_for(
            get_redis_client().delete(auth_profile_key(user_id)), timeout=1.0
        )
    except Exception:  # noqa: BLE001 - invalidation is best effort after commit
        logger.warning("Authentication cache invalidation failed user=%s", user_id)


async def warm_active_auth_profiles() -> int:
    """Bulk-preload active profiles once across all application workers."""

    redis = get_redis_client()
    lock_key = f"auth:principal:warm:{VERSION}"
    try:
        if not await redis.set(lock_key, uuid4().hex, nx=True, ex=300):
            return 0
    except Exception:  # noqa: BLE001 - warm-up is an optional optimization
        logger.warning("Authentication cache warm-up skipped: Redis unavailable")
        return 0

    async with AsyncSessionFactory() as session:
        rows = (
            await session.execute(
                select(
                    UserAccount.id,
                    UserAccount.login_name,
                    UserAccount.user_type,
                    Student.name,
                    Student.id.label("student_id"),
                    Student.student_no,
                    Student.enrollment_year,
                    Major.name.label("major_name"),
                    Teacher.name.label("teacher_name"),
                    Teacher.id.label("teacher_id"),
                    Teacher.employee_no,
                    Teacher.department,
                    Teacher.title,
                )
                .outerjoin(Student, Student.user_id == UserAccount.id)
                .outerjoin(Major, Major.id == Student.major_id)
                .outerjoin(Teacher, Teacher.user_id == UserAccount.id)
                .where(UserAccount.status == "ACTIVE")
            )
        ).all()

    warmed = 0
    for row in rows:
        profile = UserProfile(
            id=row.id,
            login_name=row.login_name,
            user_type=row.user_type,
            name=row.name if row.user_type == "STUDENT" else row.teacher_name,
            student_id=row.student_id,
            teacher_id=row.teacher_id,
            student_no=row.student_no,
            enrollment_year=row.enrollment_year,
            major_name=row.major_name,
            employee_no=row.employee_no,
            department=row.department,
            title=row.title,
        )
        await _write(redis, auth_profile_key(row.id), profile)
        warmed += 1
    logger.info("Warmed %d encrypted authentication profiles", warmed)
    return warmed


async def periodic_auth_profile_warmup() -> None:
    """Refresh active principals before their 30-minute base TTL expires."""

    while True:
        try:
            await warm_active_auth_profiles()
            await asyncio.sleep(20 * 60)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("Periodic authentication warm-up failed", exc_info=True)
            await asyncio.sleep(60)
