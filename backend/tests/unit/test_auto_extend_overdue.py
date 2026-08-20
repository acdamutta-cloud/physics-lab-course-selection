import asyncio
from datetime import UTC, datetime, timedelta
from json import loads
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import teacher_adjustment_service as service


class _Scalars:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


def _fake_issue() -> SimpleNamespace:
    return SimpleNamespace(
        id="issue-1",
        status="PROCESSING",
        impact_end=datetime.now(UTC) - timedelta(days=1),
        updated_by=None,
        report_no="RI-20260818-TEST",
        reporter_teacher_id="teacher-1",
        remediation_status="REMEDIATED",
    )


def _make_session(rows: list, teacher: SimpleNamespace | None) -> SimpleNamespace:
    return SimpleNamespace(
        execute=AsyncMock(return_value=_Result(rows)),
        get=AsyncMock(return_value=teacher),
        commit=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_auto_extend_overdue_extends_by_7_days_and_notifies(monkeypatch) -> None:
    issue = _fake_issue()
    teacher = SimpleNamespace(id="teacher-1", user_id="user-1")
    session = _make_session([issue], teacher)
    redis = SimpleNamespace(lpush=AsyncMock())
    monkeypatch.setattr("app.db.redis_client.get_redis_client", lambda: redis)
    monkeypatch.setattr(
        service,
        "resource_impact",
        AsyncMock(return_value={"shortage": True}),
    )

    count = await service.auto_extend_overdue_issues(session, actor_id=None)

    assert count == 1
    expected_end = datetime.now(UTC) + timedelta(days=7)
    assert issue.impact_end > expected_end - timedelta(seconds=30)
    assert issue.impact_end < expected_end + timedelta(seconds=30)
    assert issue.updated_by is None
    assert issue.remediation_status == "REMEDIATION_REQUIRED"
    session.commit.assert_awaited_once()
    # admin + teacher 各一条通知，含 title/type 字段
    assert redis.lpush.await_count == 2
    teacher_payload = loads(redis.lpush.await_args_list[1].args[1])
    assert redis.lpush.await_args_list[1].args[0] == "teacher:user-1:notifications"
    assert teacher_payload["title"] == "资源检修自动延期"
    assert teacher_payload["type"] == "资源异常"
    assert "已超期" in teacher_payload["msg"]
    admin_payload = loads(redis.lpush.await_args_list[0].args[1])
    assert redis.lpush.await_args_list[0].args[0] == "admin:notifications"
    assert admin_payload["title"] == "资源检修自动延期"


@pytest.mark.asyncio
async def test_auto_extend_overdue_no_issues_is_idempotent(monkeypatch) -> None:
    session = _make_session([], None)
    redis = SimpleNamespace(lpush=AsyncMock())
    monkeypatch.setattr("app.db.redis_client.get_redis_client", lambda: redis)
    monkeypatch.setattr(
        service,
        "resource_impact",
        AsyncMock(return_value={"shortage": False}),
    )

    count = await service.auto_extend_overdue_issues(session, actor_id=None)

    assert count == 0
    session.commit.assert_not_awaited()
    redis.lpush.assert_not_awaited()
    service.resource_impact.assert_not_awaited()


@pytest.mark.asyncio
async def test_periodic_scan_runs_and_stops_on_cancel(monkeypatch) -> None:
    auto_extend = AsyncMock(return_value=0)
    monkeypatch.setattr(service, "auto_extend_overdue_issues", auto_extend)
    # AsyncSessionFactory 是同步工厂（async with 内部 await __aenter__）
    monkeypatch.setattr(service, "AsyncSessionFactory", lambda: AsyncMock())
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr("asyncio.sleep", sleep)

    await service.periodic_resource_issue_overdue_scan()

    assert auto_extend.await_count == 2
    assert sleep.await_count == 2
