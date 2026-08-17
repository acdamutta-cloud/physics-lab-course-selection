"""High-concurrency selection with Redis admission and queued DB commits."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import case, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.selection_precheck import (
    applications_key,
    idempotency_key,
    selected_projects_key,
    student_context_key,
)
from app.core.config.settings import get_settings
from app.db.redis_client import get_redis_client
from app.db.session import AsyncSessionFactory
from app.models.curriculum import (
    ExperimentProject,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingPlanProject,
)
from app.models.enrollment import StudentProjectRecord
from app.models.identity import Student
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.schemas.student_consultation import SelectionEligibilityResult
from app.services.student_cache_service import refresh_experiment_views_after_commit
from app.services.selection_window_service import resolve_window_gate
from app.services.student_consultation_service import check_selection_eligibility

logger = logging.getLogger(__name__)

PENDING_ZSET = "selection:pending"
SELECTION_STREAM = "selection:requests:v1"
SELECTION_CONSUMER_GROUP = "selection-db-writers:v1"
LOCK_TTL_MS = 30_000
RESERVATION_TTL_MS = 120_000
RESERVATION_REVIEW_SECONDS = 30
SESSION_META_VERSION = "v1"


@dataclass
class SelectionOperationResult:
    result: str
    message: str
    eligibility: SelectionEligibilityResult | None = None
    details: dict[str, object] = field(default_factory=dict)


_PRECHECK_REASON: dict[str, tuple[str, str]] = {
    "STUDENT_INACTIVE": ("COURSE", "当前学籍状态不允许选课。"),
    "TRAINING_PLAN_RULE_MISSING": (
        "DATA",
        "当前培养方案中未找到该实验课程或项目的修读规则。",
    ),
    "STUDY_PERIOD_NOT_REACHED": ("COURSE", "尚未达到该课程要求的修读学年或学期。"),
    "COURSE_ALREADY_PASSED": ("COURSE", "该实验课程已经通过，不能重复修读。"),
    "PREREQUISITE_COURSE_NOT_PASSED": ("COURSE", "该课程要求的先修课程尚未通过。"),
    "SESSION_TERM_MISMATCH": ("SESSION", "该实验场次不属于当前学期。"),
    "SCHEDULE_NOT_PUBLISHED": ("SESSION", "该场次所属课表尚未发布。"),
    "SESSION_NOT_OPEN": ("SESSION", "该实验场次当前未开放选课。"),
    "BUSY_BITMAP_MISSING": ("DATA", "当前学期忙闲数据缺失或格式不兼容。"),
    "PROJECT_ALREADY_SELECTED": ("PROJECT", "同一实验项目只能选择一个场次。"),
    "PROJECT_OCCUPIED_BY_APPLICATION": (
        "PROJECT",
        "该项目存在调课、换组或补做申请，暂时不能重复选择。",
    ),
    "TIME_CONFLICT": ("SESSION", "该场次与非实验课程或已有实验安排时间冲突。"),
    "PROJECT_ORDER_VIOLATION": ("PROJECT", "该场次不符合实验项目修读顺序要求。"),
}


def _precheck_eligibility(
    *,
    student_id: UUID,
    term_id: UUID,
    session_id: UUID,
    project_id: UUID | None,
    course_id: UUID | None,
    detail: str,
) -> SelectionEligibilityResult:
    try:
        parsed = json.loads(detail) if detail.startswith("[") else [detail]
    except json.JSONDecodeError:
        parsed = [detail]
    codes = [str(value) for value in parsed if value] or ["TRAINING_PLAN_RULE_MISSING"]
    violations = []
    for code in dict.fromkeys(codes):
        scope, message = _PRECHECK_REASON.get(
            code, ("DATA", "选课资格缓存内容不完整，请稍后重试。")
        )
        violations.append({"code": code, "scope": scope, "message": message})
    return SelectionEligibilityResult(
        decision="UNKNOWN" if any(item["scope"] == "DATA" for item in violations) else "BLOCK",
        student_id=student_id,
        session_id=session_id,
        term_id=term_id,
        project_id=project_id,
        course_id=course_id,
        violations=violations,
    )


def _stock_key(session_id: UUID) -> str:
    return f"session:stock:{session_id}"


def _session_meta_key(session_id: UUID) -> str:
    return f"selection:session-meta:{session_id}:{SESSION_META_VERSION}"


def _student_lock_key(student_id: UUID, term_id: UUID) -> str:
    return f"selection:lock:{student_id}:{term_id}"


def _request_status_key(student_id: UUID, request_id: str) -> str:
    return f"selection:request-status:{student_id}:{request_id}"


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

LUA_PREFLIGHT_RESERVE = """
local meta_key = KEYS[1]
local context_key = KEYS[2]
local selected_key = KEYS[3]
local applications_key = KEYS[4]
local stock_key = KEYS[5]
local pending_key = KEYS[6]
local idempotency_key = KEYS[7]
local lock_key = KEYS[8]
local stream_key = KEYS[9]
local status_key = KEYS[10]
local expected_term = ARGV[1]
local student_id = ARGV[2]
local session_id = ARGV[3]
local token = ARGV[4]
local review_at = tonumber(ARGV[5])
local ttl_ms = tonumber(ARGV[6])
local lock_ttl_ms = tonumber(ARGV[7])
local enqueue = ARGV[8]
local max_queue_length = tonumber(ARGV[9])
local status_ttl_seconds = tonumber(ARGV[10])

if not redis.call('SET', lock_key, token, 'NX', 'PX', lock_ttl_ms) then
  return {-6, '', '', 'STUDENT_OPERATION_BUSY'}
end
local function reject(result)
  if redis.call('GET', lock_key) == token then redis.call('DEL', lock_key) end
  return result
end

if enqueue == '1' and redis.call('XLEN', stream_key) >= max_queue_length then
  return reject({-7, '', '', 'SELECTION_QUEUE_FULL'})
end

if ARGV[12] ~= '1' then
  return reject({-8, '', '', ARGV[13]})
end

local meta = redis.call(
  'HMGET', meta_key, 'term_id', 'project_id', 'course_id', 'schedule_status',
  'session_status', 'week_no', 'day_of_week', 'start_slot', 'end_slot'
)
local raw_context = redis.call('GET', context_key)
if not meta[1] or not meta[2] or not meta[3] or not raw_context then
  return reject({-3, '', '', 'SELECTION_CACHE_MISSING'})
end
if meta[1] ~= expected_term then return reject({-4, meta[2], meta[3], 'SESSION_TERM_MISMATCH'}) end
if meta[4] ~= 'PUBLISHED' then return reject({-4, meta[2], meta[3], 'SCHEDULE_NOT_PUBLISHED'}) end
if meta[5] ~= 'DRAFT' and meta[5] ~= 'OPEN' and meta[5] ~= 'FULL' then
  return reject({-4, meta[2], meta[3], 'SESSION_NOT_OPEN'})
end

if redis.call('EXISTS', idempotency_key) == 1 then
  return reject({-5, meta[2], meta[3], 'SESSION_ALREADY_SELECTED'})
end
local context = cjson.decode(raw_context)
local rule = context['projects'][meta[2]]
if not context['academic_active'] or not rule then
  local reason = not context['academic_active'] and 'STUDENT_INACTIVE'
    or 'TRAINING_PLAN_RULE_MISSING'
  return reject({-4, meta[2], meta[3], reason})
end
if not context['bitmap_valid'] then
  return reject({-4, meta[2], meta[3], 'BUSY_BITMAP_MISSING'})
end
if rule['course_id'] ~= meta[3] then
  return reject({-4, meta[2], meta[3], 'TRAINING_PLAN_RULE_MISSING'})
end
if rule['violations'] and #rule['violations'] > 0 then
  return reject({-4, meta[2], meta[3], cjson.encode(rule['violations'])})
end
if redis.call('SISMEMBER', selected_key, meta[2]) == 1 then
  return reject({-2, meta[2], meta[3], 'PROJECT_ALREADY_SELECTED'})
end
if redis.call('SISMEMBER', applications_key, meta[2]) == 1 then
  return reject({-4, meta[2], meta[3], 'PROJECT_OCCUPIED_BY_APPLICATION'})
end
for slot = tonumber(meta[8]), tonumber(meta[9]) do
  local slot_key = meta[6] .. ':' .. meta[7] .. ':' .. slot
  if context['busy_slots'][slot_key] then
    return reject({-4, meta[2], meta[3], 'TIME_CONFLICT'})
  end
end

local target_start = ((tonumber(meta[6]) - 1) * 7 + tonumber(meta[7]) - 1) * 12
  + tonumber(meta[8])
local target_end = ((tonumber(meta[6]) - 1) * 7 + tonumber(meta[7]) - 1) * 12
  + tonumber(meta[9])
for _, constraint in ipairs(context['order_constraints']) do
  if constraint['before'] == meta[2] then
    local after_time = context['selected_times'][constraint['after']]
    if after_time and target_end >= tonumber(after_time['start']) then
      return reject({-4, meta[2], meta[3], 'PROJECT_ORDER_VIOLATION'})
    end
  elseif constraint['after'] == meta[2] then
    local before_time = context['selected_times'][constraint['before']]
    if before_time and tonumber(before_time['end']) >= target_start then
      return reject({-4, meta[2], meta[3], 'PROJECT_ORDER_VIOLATION'})
    end
  end
end

local project_key = 'selection:project:' .. student_id .. ':' .. expected_term .. ':' .. meta[2]
local existing = redis.call('GET', project_key)
if existing then return reject({-2, meta[2], meta[3], 'PROJECT_ALREADY_SELECTED'}) end
local stock = tonumber(redis.call('GET', stock_key) or '-1')
if stock < 0 then return reject({-3, meta[2], meta[3], 'STOCK_CACHE_MISSING'}) end
if stock <= 0 then return reject({0, meta[2], meta[3], 'SESSION_FULL'}) end

local reservation_value = session_id .. ':' .. token
local pending_member = student_id .. ':' .. expected_term .. ':' .. meta[2]
  .. ':' .. session_id .. ':' .. token
redis.call('DECR', stock_key)
redis.call('SET', project_key, reservation_value, 'PX', ttl_ms)
redis.call('ZADD', pending_key, review_at, pending_member)
if enqueue == '1' then
  redis.call(
    'XADD', stream_key, '*',
    'request_id', token,
    'student_id', student_id,
    'term_id', expected_term,
    'session_id', session_id,
    'project_id', meta[2],
    'course_id', meta[3],
    'requirement_type', rule['requirement_type']
  )
  redis.call(
    'HSET', status_key,
    'student_id', student_id,
    'result', 'processing',
    'message', ARGV[11],
    'eligibility', ''
  )
  redis.call('EXPIRE', status_key, status_ttl_seconds)
end
return {1, meta[2], meta[3], rule['requirement_type']}
"""

LUA_FINALIZE = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[2]))
if redis.call('GET', KEYS[3]) == ARGV[3] then redis.call('DEL', KEYS[3]) end
return 1
"""

LUA_COMPENSATE = """
local removed = redis.call('ZREM', KEYS[1], ARGV[1])
if removed == 1 then
  redis.call('INCR', KEYS[2])
  if redis.call('GET', KEYS[3]) == ARGV[2] then redis.call('DEL', KEYS[3]) end
end
if redis.call('GET', KEYS[4]) == ARGV[3] then redis.call('DEL', KEYS[4]) end
return removed
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


async def _preflight_reserve(
    redis: Redis,
    *,
    student_id: UUID,
    term_id: UUID,
    session_id: UUID,
    enqueue: bool = False,
    window_gate: dict[str, object] | None = None,
) -> tuple[int, UUID | None, UUID | None, str, str]:
    """Atomically validate hot session data and reserve stock in one round trip."""

    settings = get_settings()
    gate = window_gate or {}
    token = uuid4().hex
    reservation_seconds = (
        settings.selection_queue_reservation_seconds
        if enqueue
        else RESERVATION_TTL_MS // 1000
    )
    review_at = int(datetime.now(UTC).timestamp()) + (
        reservation_seconds if enqueue else RESERVATION_REVIEW_SECONDS
    )
    raw = await redis.eval(
        LUA_PREFLIGHT_RESERVE,
        10,
        _session_meta_key(session_id),
        student_context_key(student_id, term_id),
        selected_projects_key(student_id, term_id),
        applications_key(student_id, term_id),
        _stock_key(session_id),
        PENDING_ZSET,
        idempotency_key(student_id, session_id),
        _student_lock_key(student_id, term_id),
        SELECTION_STREAM,
        _request_status_key(student_id, token),
        str(term_id),
        str(student_id),
        str(session_id),
        token,
        review_at,
        reservation_seconds * 1000,
        reservation_seconds * 1000 if enqueue else LOCK_TTL_MS,
        "1" if enqueue else "0",
        settings.selection_queue_max_length,
        settings.selection_request_status_ttl_seconds,
        "正在选课，请稍候……",
        "1" if gate.get("open") else "0",
        str(gate.get("message") or "当前不在选课时间范围内。"),
    )
    code = int(raw[0])

    def parse_uuid(value: object) -> UUID | None:
        if isinstance(value, bytes):
            value = value.decode()
        try:
            return UUID(str(value)) if value else None
        except ValueError:
            return None

    detail = raw[3].decode() if isinstance(raw[3], bytes) else str(raw[3] or "")
    return code, parse_uuid(raw[1]), parse_uuid(raw[2]), token, detail


def _preflight_failure_result(
    *,
    code: int,
    student_id: UUID,
    term_id: UUID,
    session_id: UUID,
    project_id: UUID | None,
    course_id: UUID | None,
    detail: str,
) -> SelectionOperationResult | None:
    if code == 1:
        return None
    if code == -6:
        return SelectionOperationResult(
            result="busy", message="正在处理你的另一笔选课请求，请稍后重试。"
        )
    if code == -7:
        return SelectionOperationResult(
            result="busy", message="当前选课人数较多，请稍后重试。"
        )
    if code == -8:
        return SelectionOperationResult(
            result="ineligible", message=detail or "当前不在选课时间范围内。"
        )
    if code == -3:
        return SelectionOperationResult(
            result="conflict", message="选课状态发生变化，请刷新后重试。"
        )
    if code == -5:
        return SelectionOperationResult(
            result="already_selected", message="该场次已经选择，无需重复提交。"
        )
    if code == 0:
        return SelectionOperationResult(result="full", message="该场次名额已满。")
    if code == -2:
        return SelectionOperationResult(
            result="duplicate", message="同一实验项目只能选择一个场次。"
        )
    if code == -4:
        eligibility = _precheck_eligibility(
            student_id=student_id,
            term_id=term_id,
            session_id=session_id,
            project_id=project_id,
            course_id=course_id,
            detail=detail,
        )
        return SelectionOperationResult(
            result="ineligible",
            message="该场次当前不具备选择资格。",
            eligibility=eligibility,
        )
    return SelectionOperationResult(
        result="conflict", message="选课状态发生变化，请刷新后重试。"
    )


async def enqueue_select_session(
    redis: Redis,
    *,
    student_id: UUID,
    term_id: UUID,
    session_id: UUID,
    window_gate: dict[str, object] | None = None,
) -> SelectionOperationResult:
    """Finish admission in Redis and return without waiting for PostgreSQL."""

    code, project_id, course_id, request_id, detail = await _preflight_reserve(
        redis,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
        enqueue=True,
        window_gate=window_gate,
    )
    rejected = _preflight_failure_result(
        code=code,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
        project_id=project_id,
        course_id=course_id,
        detail=detail,
    )
    if rejected is not None:
        return rejected
    if project_id is None or course_id is None or detail not in {"REQUIRED", "OPTIONAL"}:
        return SelectionOperationResult(
            result="conflict", message="选课状态发生变化，请刷新后重试。"
        )
    return SelectionOperationResult(
        result="processing",
        message="正在选课，请稍候……",
        details={"request_id": request_id},
    )


async def _select_session_database_fallback(
    db: AsyncSession,
    *,
    student_id: UUID,
    term_id: UUID,
    session_id: UUID,
    window_gate: dict[str, object] | None = None,
) -> SelectionOperationResult:
    """Select synchronously when Redis is unavailable.

    The student row serializes eligibility checks for one student, while the
    session row lock serializes capacity changes across students.
    """

    gate = window_gate or {}
    if not gate.get("open", True):
        return SelectionOperationResult(
            result="ineligible",
            message=str(gate.get("message") or "当前不在选课时间范围内。"),
        )
    project_id: UUID | None = None
    try:
        student = await db.scalar(
            select(Student).where(Student.id == student_id).with_for_update()
        )
        if student is None:
            await db.rollback()
            return SelectionOperationResult(
                result="conflict", message="学生信息不存在，请刷新后重试。"
            )

        eligibility = await check_selection_eligibility(
            db,
            student_id=student_id,
            session_id=session_id,
            lock_target=True,
        )
        project_id = eligibility.project_id
        if eligibility.term_id != term_id:
            eligibility = _precheck_eligibility(
                student_id=student_id,
                term_id=term_id,
                session_id=session_id,
                project_id=eligibility.project_id,
                course_id=eligibility.course_id,
                detail="SESSION_TERM_MISMATCH",
            )
            await db.rollback()
            return SelectionOperationResult(
                result="ineligible",
                message="该场次当前不具备选择资格。",
                eligibility=eligibility,
            )
        if any(
            warning.code == "SESSION_ALREADY_SELECTED"
            for warning in eligibility.warnings
        ):
            await db.rollback()
            return SelectionOperationResult(
                result="already_selected", message="该场次已经选择，无需重复提交。"
            )
        if not eligibility.eligible:
            await db.rollback()
            return SelectionOperationResult(
                result="ineligible",
                message="该场次当前不具备选择资格。",
                eligibility=eligibility,
            )
        if eligibility.project_id is None or eligibility.course_id is None:
            await db.rollback()
            return SelectionOperationResult(
                result="conflict", message="选课规则数据不完整，请刷新后重试。"
            )

        requirement_type = await db.scalar(
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
                TrainingPlanCourse.course_id == eligibility.course_id,
                TrainingPlanProject.project_id == eligibility.project_id,
            )
            .order_by(TrainingPlan.version_no.desc())
            .limit(1)
        )
        if requirement_type not in {"REQUIRED", "OPTIONAL"}:
            await db.rollback()
            return SelectionOperationResult(
                result="conflict", message="培养方案中的项目类型缺失，请联系管理员。"
            )

        target = await db.scalar(
            select(ExperimentSession)
            .where(ExperimentSession.id == session_id)
            .with_for_update()
        )
        if (
            target is None
            or target.status not in {"DRAFT", "OPEN"}
            or target.selected_count >= target.capacity
        ):
            await db.rollback()
            return SelectionOperationResult(result="full", message="该场次名额已满。")

        target.selected_count += 1
        if target.selected_count >= target.capacity:
            target.status = "FULL"
        await db.execute(
            insert(StudentProjectRecord).values(
                id=uuid4(),
                student_id=student_id,
                term_id=term_id,
                course_id=eligibility.course_id,
                project_id=eligibility.project_id,
                session_id=session_id,
                requirement_type=requirement_type,
                status="SELECTED",
                selected_at=datetime.now(UTC),
                report_status="NOT_REQUIRED",
                version_no=1,
            )
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing_session = await db.scalar(
            select(StudentProjectRecord.session_id).where(
                StudentProjectRecord.student_id == student_id,
                StudentProjectRecord.term_id == term_id,
                StudentProjectRecord.project_id == project_id,
                StudentProjectRecord.status.in_(
                    ["SELECTED", "COMPLETED", "ABSENT", "MAKEUP_PENDING"]
                ),
            )
        )
        if existing_session == session_id:
            return SelectionOperationResult(
                result="already_selected", message="该场次已经选择，无需重复提交。"
            )
        return SelectionOperationResult(
            result="duplicate", message="同一实验项目只能选择一个场次。"
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "Database fallback selection failed student=%s session=%s",
            student_id,
            session_id,
        )
        return SelectionOperationResult(
            result="error", message="选课失败，请稍后重试。"
        )

    try:
        await refresh_experiment_views_after_commit(student_id, term_id)
    except Exception:  # noqa: BLE001 -- cache failure cannot reverse a committed selection
        logger.warning(
            "Selection committed but cache refresh was deferred student=%s session=%s",
            student_id,
            session_id,
        )
    return SelectionOperationResult(
        result="ok",
        message="选课成功。",
        details={"admission_mode": "database_fallback"},
    )


async def enqueue_select_session_with_fallback(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    term_id: UUID,
    session_id: UUID,
) -> SelectionOperationResult:
    gate = await resolve_window_gate(db, term_id)
    if not gate["open"]:
        return SelectionOperationResult(
            result="ineligible", message=gate["message"]
        )
    try:
        return await enqueue_select_session(
            redis,
            student_id=student_id,
            term_id=term_id,
            session_id=session_id,
            window_gate=gate,
        )
    except (RedisError, OSError):
        logger.warning(
            "Redis admission unavailable; using database fallback student=%s session=%s",
            student_id,
            session_id,
        )
        return await _select_session_database_fallback(
            db,
            student_id=student_id,
            term_id=term_id,
            session_id=session_id,
            window_gate=gate,
        )


async def get_selection_request_status(
    redis: Redis, *, student_id: UUID, request_id: str
) -> SelectionOperationResult | None:
    values = await redis.hgetall(_request_status_key(student_id, request_id))
    if not values or values.get("student_id") != str(student_id):
        return None
    eligibility = None
    if values.get("eligibility"):
        eligibility = SelectionEligibilityResult.model_validate_json(
            values["eligibility"]
        )
    return SelectionOperationResult(
        result=values.get("result", "processing"),
        message=values.get("message", "正在选课，请稍候……"),
        eligibility=eligibility,
        details={"request_id": request_id},
    )


async def _store_selection_request_result(
    redis: Redis,
    *,
    student_id: UUID,
    request_id: str,
    result: SelectionOperationResult,
) -> None:
    settings = get_settings()
    key = _request_status_key(student_id, request_id)
    await redis.hset(
        key,
        mapping={
            "student_id": str(student_id),
            "result": result.result,
            "message": result.message,
            "eligibility": (
                result.eligibility.model_dump_json() if result.eligibility else ""
            ),
        },
    )
    await redis.expire(key, settings.selection_request_status_ttl_seconds)


async def _commit_reserved_selection(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    term_id: UUID,
    session_id: UUID,
    project_id: UUID,
    course_id: UUID,
    requirement_type: str,
    token: str,
) -> SelectionOperationResult:
    pending_member = _reservation_member(
        student_id, term_id, project_id, session_id, token
    )
    reservation_value = f"{session_id}:{token}"
    project_key = _project_key(student_id, term_id, project_id)
    database_committed = False
    try:
        next_count = ExperimentSession.selected_count + 1
        updated_project_id = await db.scalar(
            update(ExperimentSession)
            .where(
                ExperimentSession.id == session_id,
                ExperimentSession.project_id == project_id,
                ExperimentSession.status.in_(["DRAFT", "OPEN"]),
                ExperimentSession.selected_count < ExperimentSession.capacity,
            )
            .values(
                selected_count=next_count,
                status=case(
                    (next_count >= ExperimentSession.capacity, "FULL"),
                    else_=ExperimentSession.status,
                ),
                updated_at=datetime.now(UTC),
            )
            .returning(ExperimentSession.project_id)
        )
        if updated_project_id is None:
            raise ValueError("SESSION_FULL_OR_CHANGED")
        await db.execute(
            insert(StudentProjectRecord).values(
                id=uuid4(),
                student_id=student_id,
                term_id=term_id,
                course_id=course_id,
                project_id=project_id,
                session_id=session_id,
                requirement_type=requirement_type,
                status="SELECTED",
                selected_at=datetime.now(UTC),
                report_status="NOT_REQUIRED",
                version_no=1,
            )
        )
        await db.commit()
        database_committed = True
        try:
            await redis.set(
                idempotency_key(student_id, session_id), str(project_id), ex=86_400
            )
            await redis.eval(
                LUA_FINALIZE,
                3,
                PENDING_ZSET,
                project_key,
                _student_lock_key(student_id, term_id),
                pending_member,
                86_400_000,
                token,
            )
        except Exception:
            logger.exception(
                "选课已提交但 Redis 确认失败 student=%s session=%s",
                student_id,
                session_id,
            )
        await refresh_experiment_views_after_commit(student_id, term_id)
        return SelectionOperationResult(result="ok", message="选课成功。")
    except IntegrityError:
        await db.rollback()
        existing_session = await db.scalar(
            select(StudentProjectRecord.session_id).where(
                StudentProjectRecord.student_id == student_id,
                StudentProjectRecord.term_id == term_id,
                StudentProjectRecord.session_id == session_id,
                StudentProjectRecord.status.in_(
                    ["SELECTED", "COMPLETED", "ABSENT", "MAKEUP_PENDING"]
                ),
            )
        )
        if existing_session is not None:
            database_committed = True
            await redis.set(
                idempotency_key(student_id, session_id), str(project_id), ex=86_400
            )
            await redis.eval(
                LUA_FINALIZE,
                3,
                PENDING_ZSET,
                project_key,
                _student_lock_key(student_id, term_id),
                pending_member,
                86_400_000,
                token,
            )
            return SelectionOperationResult(result="ok", message="选课成功。")
        logger.info(
            "Selection unique constraint rejected student=%s session=%s",
            student_id,
            session_id,
        )
        return SelectionOperationResult(
            result="duplicate", message="同一实验项目只能选择一个场次。"
        )
    except Exception:
        if database_committed:
            logger.exception(
                "Selection post-commit work failed student=%s session=%s",
                student_id,
                session_id,
            )
            return SelectionOperationResult(result="ok", message="选课成功。")
        await db.rollback()
        logger.exception(
            "选课异步提交失败 student=%s session=%s", student_id, session_id
        )
        return SelectionOperationResult(
            result="conflict", message="选课状态发生变化，请刷新后重试。"
        )
    finally:
        if not database_committed:
            try:
                await redis.eval(
                    LUA_COMPENSATE,
                    4,
                    PENDING_ZSET,
                    _stock_key(session_id),
                    project_key,
                    _student_lock_key(student_id, term_id),
                    pending_member,
                    reservation_value,
                    token,
                )
            except Exception:
                logger.exception(
                    "选课失败后的 Redis 补偿失败 reservation=%s", pending_member
                )


async def select_session(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    term_id: UUID,
    session_id: UUID,
) -> SelectionOperationResult:
    """Compatibility path for internal callers that still need a final result."""

    gate = await resolve_window_gate(db, term_id)
    if not gate["open"]:
        return SelectionOperationResult(
            result="ineligible", message=gate["message"]
        )
    code, project_id, course_id, token, detail = await _preflight_reserve(
        redis,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
        window_gate=gate,
    )
    rejected = _preflight_failure_result(
        code=code,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
        project_id=project_id,
        course_id=course_id,
        detail=detail,
    )
    if rejected is not None:
        return rejected
    if project_id is None or course_id is None or detail not in {"REQUIRED", "OPTIONAL"}:
        return SelectionOperationResult(
            result="conflict", message="选课状态发生变化，请刷新后重试。"
        )
    return await _commit_reserved_selection(
        redis,
        db,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
        project_id=project_id,
        course_id=course_id,
        requirement_type=detail,
        token=token,
    )


async def deselect_session(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    term_id: UUID,
    session_id: UUID,
) -> SelectionOperationResult:
    gate = await resolve_window_gate(db, term_id)
    if not gate["withdraw_open"]:
        return SelectionOperationResult(
            result="ineligible", message=gate["withdraw_message"]
        )
    lock: tuple[str, str] | None = None
    redis_available = True
    try:
        lock = await _acquire_student_lock(redis, student_id, term_id)
    except Exception:
        # Redis 只承担加速和快速互斥；不可用时由数据库行锁保证退选安全。
        redis_available = False
        logger.warning(
            "Redis unavailable while acquiring deselection lock; falling back to DB row locks "
            "student=%s session=%s",
            student_id,
            session_id,
            exc_info=True,
        )
    if redis_available and lock is None:
        return SelectionOperationResult(
            result="busy", message="正在处理你的另一笔选课请求，请稍后重试。"
        )
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
            if lock is not None:
                await _release_lock(redis, lock[0], lock[1])
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
    except Exception:
        await db.rollback()
        logger.exception(
            "退选同步提交失败 student=%s session=%s", student_id, session_id
        )
        if lock is not None:
            await _release_lock(redis, lock[0], lock[1])
        return SelectionOperationResult(
            result="error", message="退选失败，请稍后重试。"
        )

    cache_sync_ok = True
    try:
        await refresh_experiment_views_after_commit(student_id, term_id)
        if target is not None:
            await redis.eval(
                LUA_DESELECT,
                2,
                _stock_key(session_id),
                _project_key(student_id, term_id, target.project_id),
                str(session_id),
                max(0, target.capacity - target.selected_count),
            )
        await redis.delete(idempotency_key(student_id, session_id))
    except Exception:
        # 数据库已经提交成功，缓存故障不能再把成功退选误报成失败。
        cache_sync_ok = False
        logger.warning(
            "Deselection committed but cache synchronization was deferred "
            "student=%s session=%s",
            student_id,
            session_id,
            exc_info=True,
        )

    if lock is not None:
        await _release_lock(redis, lock[0], lock[1])
    return SelectionOperationResult(
        result="ok",
        message="退选成功。",
        details={"cache_sync": "ok" if cache_sync_ok else "deferred"},
    )


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
                3,
                PENDING_ZSET,
                project_key,
                _student_lock_key(student_id, term_id),
                member,
                86_400_000,
                token,
            )
        else:
            await redis.eval(
                LUA_COMPENSATE,
                4,
                PENDING_ZSET,
                _stock_key(session_id),
                project_key,
                _student_lock_key(student_id, term_id),
                member,
                f"{session_id}:{token}",
                token,
            )


async def reconcile_session_stocks(redis: Redis) -> int:
    """Repair idle stock keys from PostgreSQL without touching active holds."""

    active_members = await redis.zrange(PENDING_ZSET, 0, -1)
    active_session_ids: set[UUID] = set()
    for member in active_members:
        try:
            active_session_ids.add(UUID(member.split(":")[3]))
        except (ValueError, IndexError, AttributeError):
            continue
    async with AsyncSessionFactory() as db:
        rows = (
            await db.execute(
                select(
                    ExperimentSession.id,
                    ExperimentSession.capacity,
                    ExperimentSession.selected_count,
                    ScheduleVersion.term_id,
                    ScheduleVersion.status,
                    ExperimentSession.status,
                    ExperimentSession.project_id,
                    ExperimentProject.course_id,
                    ExperimentSession.week_no,
                    ExperimentSession.day_of_week,
                    ExperimentSession.start_slot,
                    ExperimentSession.end_slot,
                )
                .join(
                    ScheduleVersion,
                    ScheduleVersion.id == ExperimentSession.schedule_version_id,
                )
                .join(
                    ExperimentProject,
                    ExperimentProject.id == ExperimentSession.project_id,
                )
                .where(
                    ScheduleVersion.status == "PUBLISHED",
                    ExperimentSession.status.in_(["DRAFT", "OPEN", "FULL"]),
                )
            )
        ).all()
    pipe = redis.pipeline(transaction=False)
    repaired = 0
    for (
        session_id,
        capacity,
        selected_count,
        term_id,
        schedule_status,
        session_status,
        project_id,
        course_id,
        week_no,
        day_of_week,
        start_slot,
        end_slot,
    ) in rows:
        pipe.hset(
            _session_meta_key(session_id),
            mapping={
                "term_id": str(term_id),
                "project_id": str(project_id),
                "course_id": str(course_id),
                "schedule_status": schedule_status,
                "session_status": session_status,
                "week_no": week_no,
                "day_of_week": day_of_week,
                "start_slot": start_slot,
                "end_slot": end_slot,
            },
        )
        if session_id not in active_session_ids:
            pipe.set(
                _stock_key(session_id),
                max(0, capacity - selected_count),
            )
            repaired += 1
    if rows:
        await pipe.execute()
    return repaired


async def _ensure_selection_consumer_group(redis: Redis) -> None:
    """Wait for Redis and create the queue group once it becomes reachable."""

    attempts = 0
    while True:
        try:
            await redis.xgroup_create(
                SELECTION_STREAM,
                SELECTION_CONSUMER_GROUP,
                id="0-0",
                mkstream=True,
            )
            return
        except asyncio.CancelledError:
            raise
        except ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                return
            attempts += 1
        except (RedisError, OSError):
            attempts += 1
        if attempts == 1 or attempts % 30 == 0:
            logger.warning(
                "Selection queue is waiting for Redis; retrying automatically attempts=%s",
                attempts,
            )
        await asyncio.sleep(2)


async def consume_selection_queue() -> None:
    """Run bounded PostgreSQL writers for Redis-admitted selection requests."""

    redis = get_redis_client()
    await _ensure_selection_consumer_group(redis)
    try:
        await warm_open_session_stocks()
    except Exception:
        logger.warning("Selection stock warm-up after Redis recovery failed", exc_info=True)

    settings = get_settings()
    workers = [
        asyncio.create_task(
            _selection_stream_worker(redis, f"writer-{index}-{uuid4().hex[:8]}")
        )
        for index in range(settings.selection_queue_worker_count)
    ]
    recovery = asyncio.create_task(_selection_stream_recovery(redis))
    reconciliation = asyncio.create_task(_selection_reconciliation_loop(redis))
    tasks = [*workers, recovery, reconciliation]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _process_selection_stream_entry(
    redis: Redis, message_id: str, values: dict[str, str]
) -> None:
    try:
        request_id = values["request_id"]
        student_id = UUID(values["student_id"])
        term_id = UUID(values["term_id"])
        session_id = UUID(values["session_id"])
        project_id = UUID(values["project_id"])
        course_id = UUID(values["course_id"])
        requirement_type = values["requirement_type"]
        if request_id == "" or requirement_type not in {"REQUIRED", "OPTIONAL"}:
            raise ValueError("invalid selection task")
    except (KeyError, TypeError, ValueError):
        logger.error("丢弃格式错误的选课队列消息 id=%s values=%s", message_id, values)
        await redis.xack(SELECTION_STREAM, SELECTION_CONSUMER_GROUP, message_id)
        await redis.xdel(SELECTION_STREAM, message_id)
        return

    async with AsyncSessionFactory() as db:
        result = await _commit_reserved_selection(
            redis,
            db,
            student_id=student_id,
            term_id=term_id,
            session_id=session_id,
            project_id=project_id,
            course_id=course_id,
            requirement_type=requirement_type,
            token=request_id,
        )
    await _store_selection_request_result(
        redis,
        student_id=student_id,
        request_id=request_id,
        result=result,
    )
    await redis.xack(SELECTION_STREAM, SELECTION_CONSUMER_GROUP, message_id)
    await redis.xdel(SELECTION_STREAM, message_id)


async def _selection_stream_worker(redis: Redis, consumer_name: str) -> None:
    settings = get_settings()
    while True:
        try:
            batches = await redis.xreadgroup(
                SELECTION_CONSUMER_GROUP,
                consumer_name,
                {SELECTION_STREAM: ">"},
                count=settings.selection_queue_batch_size,
                block=settings.selection_queue_block_ms,
            )
            for _stream, messages in batches:
                for message_id, values in messages:
                    await _process_selection_stream_entry(redis, message_id, values)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("选课数据库Worker异常 consumer=%s", consumer_name)
            await asyncio.sleep(1)


async def _selection_stream_recovery(redis: Redis) -> None:
    """Claim unacknowledged tasks left by an interrupted worker."""

    settings = get_settings()
    consumer_name = f"recovery-{uuid4().hex[:8]}"
    while True:
        try:
            await asyncio.sleep(5)
            claimed = await redis.xautoclaim(
                SELECTION_STREAM,
                SELECTION_CONSUMER_GROUP,
                consumer_name,
                min_idle_time=5000,
                start_id="0-0",
                count=settings.selection_queue_batch_size,
            )
            messages = claimed[1] if len(claimed) > 1 else []
            for message_id, values in messages:
                await _process_selection_stream_entry(redis, message_id, values)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("恢复未确认选课任务失败")
            await asyncio.sleep(2)


async def _selection_reconciliation_loop(redis: Redis) -> None:
    cycles = 0
    while True:
        try:
            await _reconcile_pending(redis)
            cycles += 1
            if cycles % 12 == 0:
                await reconcile_session_stocks(redis)
            await asyncio.sleep(get_settings().selection_reconcile_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("选课预留对账失败")
            await asyncio.sleep(2)


async def warm_open_session_stocks() -> int:
    """Initialize missing stock keys without overwriting live reservations."""

    redis = get_redis_client()
    async with AsyncSessionFactory() as db:
        rows = (
            await db.execute(
                select(
                    ExperimentSession.id,
                    ExperimentSession.capacity,
                    ExperimentSession.selected_count,
                    ScheduleVersion.term_id,
                    ScheduleVersion.status,
                    ExperimentSession.status,
                    ExperimentSession.project_id,
                    ExperimentProject.course_id,
                    ExperimentSession.week_no,
                    ExperimentSession.day_of_week,
                    ExperimentSession.start_slot,
                    ExperimentSession.end_slot,
                )
                .join(
                    ScheduleVersion,
                    ScheduleVersion.id == ExperimentSession.schedule_version_id,
                )
                .join(
                    ExperimentProject,
                    ExperimentProject.id == ExperimentSession.project_id,
                )
                .where(
                    ScheduleVersion.status == "PUBLISHED",
                    ExperimentSession.status.in_(["DRAFT", "OPEN", "FULL"]),
                )
            )
        ).all()
    if not rows:
        return 0
    pipe = redis.pipeline(transaction=False)
    for (
        session_id,
        capacity,
        selected_count,
        term_id,
        schedule_status,
        session_status,
        project_id,
        course_id,
        week_no,
        day_of_week,
        start_slot,
        end_slot,
    ) in rows:
        pipe.hset(
            _session_meta_key(session_id),
            mapping={
                "term_id": str(term_id),
                "project_id": str(project_id),
                "course_id": str(course_id),
                "schedule_status": schedule_status,
                "session_status": session_status,
                "week_no": week_no,
                "day_of_week": day_of_week,
                "start_slot": start_slot,
                "end_slot": end_slot,
            },
        )
        pipe.set(
            _stock_key(session_id),
            max(0, capacity - selected_count),
            nx=True,
        )
    await pipe.execute()
    return len(rows)
