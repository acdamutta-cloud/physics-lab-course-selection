"""High-concurrency selection with Redis reservations and synchronous DB commit."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.redis_client import get_redis_client
from app.db.session import AsyncSessionFactory
from app.models.curriculum import TrainingPlan, TrainingPlanCourse, TrainingPlanProject
from app.models.enrollment import StudentProjectRecord
from app.models.scheduling import ExperimentSession
from app.schemas.student_consultation import SelectionEligibilityResult
from app.services.student_consultation_service import check_selection_eligibility

logger = logging.getLogger(__name__)

PENDING_ZSET = "selection:pending"
LOCK_TTL_MS = 30_000
RESERVATION_TTL_MS = 120_000
RESERVATION_REVIEW_SECONDS = 30


@dataclass
class SelectionOperationResult:
    result: str
    message: str
    eligibility: SelectionEligibilityResult | None = None
    details: dict[str, object] = field(default_factory=dict)


def _stock_key(session_id: UUID) -> str:
    return f"session:stock:{session_id}"


def _student_lock_key(student_id: UUID, term_id: UUID) -> str:
    return f"selection:lock:{student_id}:{term_id}"


def _project_key(student_id: UUID, term_id: UUID, project_id: UUID) -> str:
    return f"selection:project:{student_id}:{term_id}:{project_id}"


def _reservation_member(
    student_id: UUID,
    term_id: UUID,
    project_id: UUID,
    session_id: UUID,
    token: str,
) -> str:
    return ":".join(map(str, (student_id, term_id, project_id, session_id, token)))


LUA_RESERVE = """
local stock_key = KEYS[1]
local project_key = KEYS[2]
local pending_key = KEYS[3]
local reservation_value = ARGV[1]
local pending_member = ARGV[2]
local review_at = tonumber(ARGV[3])
local ttl_ms = tonumber(ARGV[4])

local existing = redis.call('GET', project_key)
if existing then
  if string.sub(existing, 1, string.len(ARGV[5])) == ARGV[5] then return -1 end
  return -2
end
local stock = tonumber(redis.call('GET', stock_key) or '-1')
if stock <= 0 then return 0 end
redis.call('DECR', stock_key)
redis.call('SET', project_key, reservation_value, 'PX', ttl_ms)
redis.call('ZADD', pending_key, review_at, pending_member)
return 1
"""

LUA_FINALIZE = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[2]))
return 1
"""

LUA_COMPENSATE = """
if redis.call('ZREM', KEYS[1], ARGV[1]) == 0 then return 0 end
redis.call('INCR', KEYS[2])
if redis.call('GET', KEYS[3]) == ARGV[2] then redis.call('DEL', KEYS[3]) end
return 1
"""

LUA_DESELECT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  redis.call('INCR', KEYS[1])
else
  redis.call('SET', KEYS[1], ARGV[2])
end
local current = redis.call('GET', KEYS[2])
if current and string.sub(current, 1, string.len(ARGV[1])) == ARGV[1] then
  redis.call('DEL', KEYS[2])
end
return 1
"""


async def init_session_stock(
    session_id: UUID, capacity: int, selected_count: int = 0
) -> None:
    redis = get_redis_client()
    remaining = max(0, capacity - selected_count)
    await redis.set(_stock_key(session_id), remaining)


async def _acquire_student_lock(
    redis: Redis, student_id: UUID, term_id: UUID
) -> tuple[str, str] | None:
    key = _student_lock_key(student_id, term_id)
    token = uuid4().hex
    acquired = await redis.set(key, token, nx=True, px=LOCK_TTL_MS)
    return (key, token) if acquired else None


async def _release_lock(redis: Redis, key: str, token: str) -> None:
    try:
        await redis.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('DEL', KEYS[1]) end return 0",
            1,
            key,
            token,
        )
    except Exception:
        logger.exception("释放学生选课锁失败 key=%s", key)


async def _requirement_type(
    session: AsyncSession,
    *,
    student_id: UUID,
    project_id: UUID,
) -> str:
    from app.models.identity import Student

    student = await session.get(Student, student_id)
    if student is None:
        return "OPTIONAL"
    value = await session.scalar(
        select(TrainingPlanProject.requirement_type)
        .join(
            TrainingPlanCourse,
            TrainingPlanCourse.id == TrainingPlanProject.plan_course_id,
        )
        .join(TrainingPlan, TrainingPlan.id == TrainingPlanCourse.plan_id)
        .where(
            TrainingPlan.major_id == student.major_id,
            TrainingPlan.enrollment_year == student.enrollment_year,
            TrainingPlan.status == "PUBLISHED",
            TrainingPlanProject.project_id == project_id,
        )
        .limit(1)
    )
    return value or "OPTIONAL"


async def select_session(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    term_id: UUID,
    session_id: UUID,
) -> SelectionOperationResult:
    lock = await _acquire_student_lock(redis, student_id, term_id)
    if lock is None:
        return SelectionOperationResult(
            result="busy", message="正在处理你的另一笔选课请求，请稍后重试。"
        )
    lock_key, lock_token = lock
    pending_member = ""
    reservation_value = ""
    target: ExperimentSession | None = None
    try:
        existing = (
            await db.execute(
                select(StudentProjectRecord).where(
                    StudentProjectRecord.student_id == student_id,
                    StudentProjectRecord.term_id == term_id,
                    StudentProjectRecord.session_id == session_id,
                    StudentProjectRecord.status.in_(
                        ["SELECTED", "COMPLETED", "ABSENT", "MAKEUP_PENDING"]
                    ),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return SelectionOperationResult(
                result="already_selected", message="该场次已经选择，无需重复提交。"
            )

        eligibility = await check_selection_eligibility(
            db,
            student_id=student_id,
            session_id=session_id,
            lock_target=True,
        )
        if not eligibility.eligible:
            return SelectionOperationResult(
                result="ineligible",
                message="该场次当前不具备选择资格。",
                eligibility=eligibility,
            )
        if eligibility.term_id != term_id:
            return SelectionOperationResult(
                result="ineligible",
                message="该场次不属于当前学期。",
                eligibility=SelectionEligibilityResult(
                    decision="BLOCK",
                    student_id=student_id,
                    session_id=session_id,
                    term_id=eligibility.term_id,
                    project_id=eligibility.project_id,
                    course_id=eligibility.course_id,
                    violations=[
                        {
                            "code": "SESSION_TERM_MISMATCH",
                            "scope": "SESSION",
                            "message": "该场次不属于当前学期。",
                        }
                    ],
                ),
            )
        target = await db.get(ExperimentSession, session_id)
        if target is None:
            return SelectionOperationResult(
                result="not_found", message="实验场次不存在。"
            )

        stock_key = _stock_key(session_id)
        await redis.set(
            stock_key,
            max(0, target.capacity - target.selected_count),
            nx=True,
        )
        project_key = _project_key(student_id, term_id, target.project_id)
        token = uuid4().hex
        reservation_value = f"{session_id}:{token}"
        pending_member = _reservation_member(
            student_id, term_id, target.project_id, session_id, token
        )
        review_at = int(datetime.now(UTC).timestamp()) + RESERVATION_REVIEW_SECONDS
        reserve_result = int(
            await redis.eval(
                LUA_RESERVE,
                3,
                stock_key,
                project_key,
                PENDING_ZSET,
                reservation_value,
                pending_member,
                review_at,
                RESERVATION_TTL_MS,
                str(session_id),
            )
        )
        if reserve_result == 0:
            return SelectionOperationResult(result="full", message="该场次名额已满。")
        if reserve_result in {-1, -2}:
            return SelectionOperationResult(
                result="duplicate", message="同一实验项目只能选择一个场次。"
            )

        # The target row was locked by eligibility; recheck the DB source of truth.
        if target.selected_count >= target.capacity:
            raise ValueError("SESSION_FULL_AFTER_RESERVATION")
        target.selected_count += 1
        if target.selected_count >= target.capacity:
            target.status = "FULL"
        db.add(
            StudentProjectRecord(
                student_id=student_id,
                term_id=term_id,
                course_id=eligibility.course_id,
                project_id=target.project_id,
                session_id=target.id,
                requirement_type=await _requirement_type(
                    db, student_id=student_id, project_id=target.project_id
                ),
                status="SELECTED",
                selected_at=datetime.now(UTC),
            )
        )
        await db.commit()
        try:
            await redis.eval(
                LUA_FINALIZE,
                2,
                PENDING_ZSET,
                project_key,
                pending_member,
                86_400_000,
            )
        except Exception:
            # PostgreSQL is authoritative. The pending reservation remains for
            # the reconciliation worker, and an idempotent retry sees the row.
            logger.exception(
                "选课已提交但 Redis 确认失败 student=%s session=%s",
                student_id,
                session_id,
            )
        return SelectionOperationResult(result="ok", message="选课成功。")
    except Exception:
        await db.rollback()
        logger.exception(
            "选课同步提交失败 student=%s session=%s", student_id, session_id
        )
        if pending_member and target is not None:
            try:
                await redis.eval(
                    LUA_COMPENSATE,
                    3,
                    PENDING_ZSET,
                    _stock_key(session_id),
                    _project_key(student_id, term_id, target.project_id),
                    pending_member,
                    reservation_value,
                )
            except Exception:
                logger.exception(
                    "选课失败后的 Redis 补偿失败 reservation=%s", pending_member
                )
        return SelectionOperationResult(
            result="conflict", message="选课状态发生变化，请刷新后重试。"
        )
    finally:
        await _release_lock(redis, lock_key, lock_token)


async def deselect_session(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    term_id: UUID,
    session_id: UUID,
) -> SelectionOperationResult:
    lock = await _acquire_student_lock(redis, student_id, term_id)
    if lock is None:
        return SelectionOperationResult(
            result="busy", message="正在处理你的另一笔选课请求，请稍后重试。"
        )
    lock_key, lock_token = lock
    try:
        record = (
            await db.execute(
                select(StudentProjectRecord)
                .with_for_update()
                .where(
                    StudentProjectRecord.student_id == student_id,
                    StudentProjectRecord.term_id == term_id,
                    StudentProjectRecord.session_id == session_id,
                    StudentProjectRecord.status == "SELECTED",
                )
            )
        ).scalar_one_or_none()
        if record is None:
            return SelectionOperationResult(
                result="not_enrolled", message="未选择该实验场次。"
            )
        target = (
            await db.execute(
                select(ExperimentSession)
                .with_for_update()
                .where(ExperimentSession.id == session_id)
            )
        ).scalar_one_or_none()
        record.status = "WITHDRAWN"
        record.withdrawn_at = datetime.now(UTC)
        if target is not None:
            target.selected_count = max(0, target.selected_count - 1)
            if target.status == "FULL":
                target.status = "OPEN"
        await db.commit()
        if target is not None:
            await redis.eval(
                LUA_DESELECT,
                2,
                _stock_key(session_id),
                _project_key(student_id, term_id, target.project_id),
                str(session_id),
                max(0, target.capacity - target.selected_count),
            )
        return SelectionOperationResult(result="ok", message="退选成功。")
    except Exception:
        await db.rollback()
        logger.exception(
            "退选同步提交失败 student=%s session=%s", student_id, session_id
        )
        return SelectionOperationResult(
            result="error", message="退选失败，请稍后重试。"
        )
    finally:
        await _release_lock(redis, lock_key, lock_token)


async def _reconcile_pending(redis: Redis) -> None:
    now = int(datetime.now(UTC).timestamp())
    members = await redis.zrangebyscore(PENDING_ZSET, 0, now, start=0, num=100)
    for member in members:
        try:
            student_raw, term_raw, project_raw, session_raw, token = member.split(":")
            student_id = UUID(student_raw)
            term_id = UUID(term_raw)
            project_id = UUID(project_raw)
            session_id = UUID(session_raw)
        except (ValueError, TypeError):
            await redis.zrem(PENDING_ZSET, member)
            continue
        async with AsyncSessionFactory() as db:
            exists = await db.scalar(
                select(StudentProjectRecord.id).where(
                    StudentProjectRecord.student_id == student_id,
                    StudentProjectRecord.term_id == term_id,
                    StudentProjectRecord.session_id == session_id,
                    StudentProjectRecord.status.in_(
                        ["SELECTED", "COMPLETED", "ABSENT", "MAKEUP_PENDING"]
                    ),
                )
            )
        project_key = _project_key(student_id, term_id, project_id)
        if exists:
            await redis.eval(
                LUA_FINALIZE,
                2,
                PENDING_ZSET,
                project_key,
                member,
                86_400_000,
            )
        else:
            await redis.eval(
                LUA_COMPENSATE,
                3,
                PENDING_ZSET,
                _stock_key(session_id),
                project_key,
                member,
                f"{session_id}:{token}",
            )


async def consume_selection_queue() -> None:
    """Compatibility entrypoint: reconcile abandoned Redis reservations."""

    redis = get_redis_client()
    while True:
        try:
            await _reconcile_pending(redis)
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("选课预留对账失败")
            await asyncio.sleep(2)
