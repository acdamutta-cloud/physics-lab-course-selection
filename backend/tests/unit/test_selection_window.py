from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services import selection_window_service as svc

START = int(datetime(2026, 8, 17, 8, 0, tzinfo=UTC).timestamp())
END = int(datetime(2026, 8, 24, 16, 0, tzinfo=UTC).timestamp())
WITHDRAW_END = int(datetime(2026, 8, 28, 16, 0, tzinfo=UTC).timestamp())
NOW_INSIDE = int(datetime(2026, 8, 20, 12, 0, tzinfo=UTC).timestamp())
NOW_AFTER_END = int(datetime(2026, 8, 26, 12, 0, tzinfo=UTC).timestamp())
NOW_AFTER_WITHDRAW = int(datetime(2026, 8, 29, 12, 0, tzinfo=UTC).timestamp())
NOW_BEFORE = int(datetime(2026, 8, 10, 12, 0, tzinfo=UTC).timestamp())


def _row(**overrides) -> dict:
    base = {
        "start": START,
        "end": END,
        "withdraw_end": None,
        "status": "OPEN",
    }
    base.update(overrides)
    return base


def test_gate_unconfigured_blocks_selection() -> None:
    gate = svc._gate_from_row(None, now_epoch=NOW_INSIDE)

    assert gate["open"] is False
    assert gate["withdraw_open"] is False
    assert gate["message"] == svc.WINDOW_NOT_CONFIGURED_MESSAGE


def test_gate_closed_status_blocks() -> None:
    gate = svc._gate_from_row(
        _row(status="CLOSED"), now_epoch=NOW_INSIDE
    )

    assert gate["open"] is False
    assert gate["message"] == "选课窗口未开放。"


def test_gate_before_start_blocks() -> None:
    gate = svc._gate_from_row(_row(), now_epoch=NOW_BEFORE)

    assert gate["open"] is False
    assert gate["message"] == "选课尚未开始。"


def test_gate_inside_window_allows() -> None:
    gate = svc._gate_from_row(_row(), now_epoch=NOW_INSIDE)

    assert gate["open"] is True
    assert gate["withdraw_open"] is True
    assert gate["message"] == ""
    assert gate["withdraw_message"] == ""


def test_gate_after_end_blocks_selection_but_allows_withdraw() -> None:
    gate = svc._gate_from_row(
        _row(withdraw_end=WITHDRAW_END), now_epoch=NOW_AFTER_END
    )

    assert gate["open"] is False
    assert gate["message"] == "选课已结束。"
    assert gate["withdraw_open"] is True


def test_gate_withdraw_end_falls_back_to_end_at() -> None:
    gate = svc._gate_from_row(_row(), now_epoch=NOW_AFTER_END)

    assert gate["open"] is False
    assert gate["withdraw_open"] is False
    assert gate["withdraw_message"] == "退选时间已截止。"


def test_gate_after_withdraw_end_blocks_withdraw() -> None:
    gate = svc._gate_from_row(
        _row(withdraw_end=WITHDRAW_END), now_epoch=NOW_AFTER_WITHDRAW
    )

    assert gate["withdraw_open"] is False
    assert gate["withdraw_message"] == "退选时间已截止。"


@pytest.mark.asyncio
async def test_resolve_gate_uses_cache_without_database(monkeypatch) -> None:
    term_id = uuid4()
    now = datetime.now(UTC)
    row = {
        "start": int((now - timedelta(hours=1)).timestamp()),
        "end": int((now + timedelta(hours=1)).timestamp()),
        "withdraw_end": None,
        "status": "OPEN",
    }
    read = AsyncMock(return_value=row)
    write = AsyncMock()
    monkeypatch.setattr(svc, "read_window_cache", read)
    monkeypatch.setattr(svc, "write_window_cache", write)
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=AssertionError("database was accessed"))
    )

    gate = await svc.resolve_window_gate(db, term_id)

    assert gate["open"] is True
    read.assert_awaited_once_with(term_id)
    write.assert_not_awaited()
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_gate_falls_back_to_database_and_writes_cache(
    monkeypatch,
) -> None:
    term_id = uuid4()
    read = AsyncMock(return_value=None)
    write = AsyncMock()
    monkeypatch.setattr(svc, "read_window_cache", read)
    monkeypatch.setattr(svc, "write_window_cache", write)
    window = SimpleNamespace(
        start_at=datetime(2026, 8, 17, 8, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 24, 16, 0, tzinfo=UTC),
        withdraw_end_at=None,
        status="OPEN",
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=window))

    gate = await svc.resolve_window_gate(db, term_id)

    assert gate["start"] == START
    assert gate["end"] == END
    write.assert_awaited_once_with(
        term_id,
        {
            "start": START,
            "end": END,
            "withdraw_end": None,
            "status": "OPEN",
        },
    )


@pytest.mark.asyncio
async def test_configure_creates_window_and_invalidates_cache(monkeypatch) -> None:
    term_id = uuid4()
    invalidate = AsyncMock()
    monkeypatch.setattr(svc, "invalidate_window_cache", invalidate)
    window = SimpleNamespace(term_id=term_id, status="OPEN")
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=None),
        add=MagicMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(side_effect=lambda item: item),
    )
    start_at = datetime(2026, 8, 17, 8, 0, tzinfo=UTC)
    end_at = datetime(2026, 8, 24, 16, 0, tzinfo=UTC)

    result = await svc.configure_term_window(
        db,
        term_id=term_id,
        start_at=start_at,
        end_at=end_at,
        withdraw_end_at=None,
    )

    assert result.status == "OPEN"
    db.add.assert_called_once()
    db.commit.assert_awaited_once()
    invalidate.assert_awaited_once_with(term_id)


@pytest.mark.asyncio
async def test_configure_rejects_invalid_ranges(monkeypatch) -> None:
    invalidate = AsyncMock()
    monkeypatch.setattr(svc, "invalidate_window_cache", invalidate)
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=None),
        add=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    start_at = datetime(2026, 8, 24, 16, 0, tzinfo=UTC)
    end_at = datetime(2026, 8, 17, 8, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="晚于开始时间"):
        await svc.configure_term_window(
            db,
            term_id=uuid4(),
            start_at=start_at,
            end_at=end_at,
            withdraw_end_at=None,
        )
    with pytest.raises(ValueError, match="退选截止"):
        await svc.configure_term_window(
            db,
            term_id=uuid4(),
            start_at=datetime(2026, 8, 17, 8, 0, tzinfo=UTC),
            end_at=datetime(2026, 8, 24, 16, 0, tzinfo=UTC),
            withdraw_end_at=datetime(2026, 8, 20, 16, 0, tzinfo=UTC),
        )
    db.commit.assert_not_awaited()
    invalidate.assert_not_awaited()
