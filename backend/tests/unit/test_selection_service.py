from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.schemas.student_consultation import SelectionEligibilityResult
from app.services import selection_service as service
from redis.exceptions import ConnectionError as RedisConnectionError


class _Redis:
    def __init__(self, preflight: list[object]):
        self.preflight = preflight
        self.set_calls: list[str] = []
        self.eval_calls: list[tuple[object, ...]] = []

    async def set(self, key, *_args, **_kwargs):
        self.set_calls.append(key)
        return True

    async def eval(self, *args, **_kwargs):
        self.eval_calls.append(args)
        if args[0] == service.LUA_PREFLIGHT_RESERVE:
            return self.preflight
        return 1


OPEN_GATE = {
    "open": True,
    "withdraw_open": True,
    "start": None,
    "end": None,
    "withdraw_end": None,
    "message": "",
    "withdraw_message": "",
}


def _patch_window_gate(monkeypatch, gate: dict[str, object] | None = None) -> None:
    async def _gate(*_args, **_kwargs):
        return dict(gate if gate is not None else OPEN_GATE)

    monkeypatch.setattr(service, "resolve_window_gate", _gate)


@pytest.mark.asyncio
async def test_redis_ineligible_request_never_opens_database_transaction(monkeypatch) -> None:
    _patch_window_gate(monkeypatch)
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    project_id, course_id = uuid4(), uuid4()
    redis = _Redis([-4, str(project_id), str(course_id), "TIME_CONFLICT"])
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=AssertionError("database was accessed")),
        execute=AsyncMock(side_effect=AssertionError("database was accessed")),
        rollback=AsyncMock(),
    )

    result = await service.select_session(
        redis,
        db,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
    )

    assert result.result == "ineligible"
    assert result.eligibility is not None
    assert result.eligibility.violations[0].code == "TIME_CONFLICT"
    assert result.message == "该场次当前不具备选择资格。"
    assert (
        result.eligibility.violations[0].message
        == "该场次与非实验课程或已有实验安排时间冲突。"
    )
    assert "Redis" not in result.message
    db.scalar.assert_not_awaited()
    db.execute.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_redis_context_uses_legacy_conflict_response(monkeypatch) -> None:
    _patch_window_gate(monkeypatch)
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    redis = _Redis([-3, "", "", "CONTEXT_MISSING"])
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=AssertionError("database was accessed")),
        execute=AsyncMock(side_effect=AssertionError("database was accessed")),
        rollback=AsyncMock(),
    )

    result = await service.select_session(
        redis,
        db,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
    )

    assert result.result == "conflict"
    assert result.message == "选课状态发生变化，请刷新后重试。"
    assert result.eligibility is None
    db.scalar.assert_not_awaited()
    db.execute.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_full_request_never_accesses_database(monkeypatch) -> None:
    _patch_window_gate(monkeypatch)
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    redis = _Redis([0, str(uuid4()), str(uuid4()), ""])
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=AssertionError("database was accessed")),
        execute=AsyncMock(side_effect=AssertionError("database was accessed")),
        rollback=AsyncMock(),
    )

    result = await service.select_session(
        redis,
        db,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
    )

    assert result.result == "full"
    db.scalar.assert_not_awaited()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_eligible_request_is_queued_without_database_access() -> None:
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    project_id, course_id = uuid4(), uuid4()
    redis = _Redis([1, str(project_id), str(course_id), "REQUIRED"])

    result = await service.enqueue_select_session(
        redis,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
    )

    assert result.result == "processing"
    assert result.message == "正在选课，请稍候……"
    assert isinstance(result.details.get("request_id"), str)
    preflight_call = redis.eval_calls[0]
    assert preflight_call[0] == service.LUA_PREFLIGHT_RESERVE
    assert preflight_call[-6] == "1"  # enqueue 标志位


@pytest.mark.asyncio
async def test_eligible_selection_uses_two_database_statements(monkeypatch) -> None:
    _patch_window_gate(monkeypatch)
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    project_id, course_id = uuid4(), uuid4()
    redis = _Redis([1, str(project_id), str(course_id), "REQUIRED"])
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=project_id),
        execute=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    refresh = AsyncMock()
    monkeypatch.setattr(service, "refresh_experiment_views_after_commit", refresh)

    result = await service.select_session(
        redis,
        db,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
    )

    assert result.result == "ok"
    db.scalar.assert_awaited_once()
    db.execute.assert_awaited_once()
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    refresh.assert_awaited_once_with(student_id, term_id)
    assert service.idempotency_key(student_id, session_id) in redis.set_calls


@pytest.mark.asyncio
async def test_database_failure_compensates_redis_reservation(monkeypatch) -> None:
    _patch_window_gate(monkeypatch)
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    project_id, course_id = uuid4(), uuid4()
    redis = _Redis([1, str(project_id), str(course_id), "OPTIONAL"])
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=RuntimeError("database unavailable")),
        execute=AsyncMock(),
        rollback=AsyncMock(),
    )

    result = await service.select_session(
        redis,
        db,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
    )

    assert result.result == "conflict"
    db.rollback.assert_awaited_once()
    assert any(call[0] == service.LUA_COMPENSATE for call in redis.eval_calls)


@pytest.mark.asyncio
async def test_deselect_falls_back_to_database_when_redis_is_unavailable(
    monkeypatch,
) -> None:
    _patch_window_gate(monkeypatch)
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    record = SimpleNamespace(status="SELECTED", withdrawn_at=None)
    target = SimpleNamespace(
        id=session_id,
        project_id=uuid4(),
        selected_count=1,
        capacity=20,
        status="OPEN",
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    redis = SimpleNamespace(
        set=AsyncMock(side_effect=ConnectionError("redis unavailable")),
        eval=AsyncMock(),
        delete=AsyncMock(),
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Result(record), _Result(target)]),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    refresh = AsyncMock(side_effect=ConnectionError("redis unavailable"))
    monkeypatch.setattr(service, "refresh_experiment_views_after_commit", refresh)

    result = await service.deselect_session(
        redis,
        db,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
    )

    assert result.result == "ok"
    assert result.message == "退选成功。"
    assert result.details == {"cache_sync": "deferred"}
    assert record.status == "WITHDRAWN"
    assert record.withdrawn_at is not None
    assert target.selected_count == 0
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_select_falls_back_to_locked_database_transaction_when_redis_is_down(
    monkeypatch,
) -> None:
    _patch_window_gate(monkeypatch)
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    project_id, course_id = uuid4(), uuid4()
    student = SimpleNamespace(major_id=uuid4(), enrollment_year=2024)
    target = SimpleNamespace(selected_count=1, capacity=20, status="OPEN")
    eligibility = SelectionEligibilityResult(
        decision="ALLOW",
        student_id=student_id,
        session_id=session_id,
        term_id=term_id,
        project_id=project_id,
        course_id=course_id,
    )
    redis = SimpleNamespace(
        eval=AsyncMock(side_effect=RedisConnectionError("redis unavailable"))
    )
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[student, "REQUIRED", target]),
        execute=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    eligibility_check = AsyncMock(return_value=eligibility)
    refresh = AsyncMock(side_effect=RedisConnectionError("redis unavailable"))
    monkeypatch.setattr(service, "check_selection_eligibility", eligibility_check)
    monkeypatch.setattr(service, "refresh_experiment_views_after_commit", refresh)

    result = await service.enqueue_select_session_with_fallback(
        redis,
        db,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
    )

    assert result.result == "ok"
    assert result.message == "选课成功。"
    assert result.details == {"admission_mode": "database_fallback"}
    assert target.selected_count == 2
    db.execute.assert_awaited_once()
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    eligibility_check.assert_awaited_once_with(
        db,
        student_id=student_id,
        session_id=session_id,
        lock_target=True,
    )


@pytest.mark.asyncio
async def test_selection_consumer_group_retries_until_redis_recovers(
    monkeypatch,
) -> None:
    redis = SimpleNamespace(
        xgroup_create=AsyncMock(
            side_effect=[
                RedisConnectionError("redis unavailable"),
                service.ResponseError("BUSYGROUP Consumer Group name already exists"),
            ]
        )
    )
    sleep = AsyncMock()
    monkeypatch.setattr(service.asyncio, "sleep", sleep)

    await service._ensure_selection_consumer_group(redis)

    assert redis.xgroup_create.await_count == 2
    sleep.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_closed_window_rejects_before_redis_and_database(
    monkeypatch,
) -> None:
    _patch_window_gate(
        monkeypatch,
        {
            "open": False,
            "withdraw_open": False,
            "message": "选课已结束。",
            "withdraw_message": "退选时间已截止。",
        },
    )
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    redis = _Redis([1, str(uuid4()), str(uuid4()), "REQUIRED"])
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=AssertionError("database was accessed")),
        execute=AsyncMock(side_effect=AssertionError("database was accessed")),
        rollback=AsyncMock(),
    )

    result = await service.select_session(
        redis,
        db,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
    )

    assert result.result == "ineligible"
    assert result.message == "选课已结束。"
    assert redis.eval_calls == []
    db.scalar.assert_not_awaited()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_window_rejects_enqueue_with_fallback(monkeypatch) -> None:
    _patch_window_gate(
        monkeypatch,
        {
            "open": False,
            "withdraw_open": False,
            "message": "选课尚未开始。",
            "withdraw_message": "选课尚未开始。",
        },
    )
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    redis = _Redis([1, str(uuid4()), str(uuid4()), "REQUIRED"])
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=AssertionError("database was accessed"))
    )

    result = await service.enqueue_select_session_with_fallback(
        redis,
        db,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
    )

    assert result.result == "ineligible"
    assert result.message == "选课尚未开始。"
    assert redis.eval_calls == []
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_deselect_blocked_outside_withdraw_window(monkeypatch) -> None:
    _patch_window_gate(
        monkeypatch,
        {
            "open": False,
            "withdraw_open": False,
            "message": "选课已结束。",
            "withdraw_message": "退选时间已截止。",
        },
    )
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    redis = SimpleNamespace(set=AsyncMock(), eval=AsyncMock(), delete=AsyncMock())
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=AssertionError("database was accessed"))
    )

    result = await service.deselect_session(
        redis,
        db,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
    )

    assert result.result == "ineligible"
    assert result.message == "退选时间已截止。"
    redis.set.assert_not_awaited()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_window_flags_passed_into_preflight_lua(monkeypatch) -> None:
    _patch_window_gate(monkeypatch)
    student_id, term_id, session_id = uuid4(), uuid4(), uuid4()
    project_id, course_id = uuid4(), uuid4()
    redis = _Redis([1, str(project_id), str(course_id), "REQUIRED"])

    await service.enqueue_select_session(
        redis,
        student_id=student_id,
        term_id=term_id,
        session_id=session_id,
        window_gate=OPEN_GATE,
    )

    preflight_call = redis.eval_calls[0]
    assert preflight_call[-2] == "1"  # 窗口开放标志
    assert preflight_call[-1] == "当前不在选课时间范围内。"  # 开放时兜底文案（不会被 Lua 使用）


def test_preflight_window_closed_code_maps_to_ineligible() -> None:
    result = service._preflight_failure_result(
        code=-8,
        student_id=uuid4(),
        term_id=uuid4(),
        session_id=uuid4(),
        project_id=None,
        course_id=None,
        detail="选课已结束。",
    )

    assert result is not None
    assert result.result == "ineligible"
    assert result.message == "选课已结束。"
    assert result.eligibility is None
