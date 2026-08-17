"""选课时间窗口：配置与门控语义。

DB 的 selection_window 表为权威数据源，Redis 缓存供选课准入 Lua 热路径
直读。门控语义（与用户确认）：
- 未配置窗口或 status != OPEN → 禁止选课/退选；
- 选课允许区间 [start_at, end_at]；
- 退选允许区间 [start_at, withdraw_end_at]（withdraw_end_at 缺省按 end_at）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.selection_window import (
    invalidate_window_cache,
    read_window_cache,
    write_window_cache,
)
from app.models.enrollment import SelectionWindow

WINDOW_NOT_CONFIGURED_MESSAGE = "选课暂未开放，请等待管理员配置选课时间。"


def _epoch(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.astimezone(UTC).timestamp())


def _row_cache_dict(window: SelectionWindow) -> dict[str, Any]:
    """时间无关的缓存结构；open 判定在使用时按当前时间计算。"""

    return {
        "start": _epoch(window.start_at),
        "end": _epoch(window.end_at),
        "withdraw_end": _epoch(window.withdraw_end_at),
        "status": window.status,
    }


async def get_term_window(
    session: AsyncSession, term_id: UUID
) -> SelectionWindow | None:
    """整学期窗口（course_id IS NULL）。"""

    return await session.scalar(
        select(SelectionWindow).where(
            SelectionWindow.term_id == term_id,
            SelectionWindow.course_id.is_(None),
        )
    )


async def configure_term_window(
    session: AsyncSession,
    *,
    term_id: UUID,
    start_at: datetime,
    end_at: datetime,
    withdraw_end_at: datetime | None,
) -> SelectionWindow:
    """配置整学期选课窗口（upsert），保存后显式失效 Redis 缓存。"""

    if end_at <= start_at:
        raise ValueError("选课结束时间必须晚于开始时间。")
    if withdraw_end_at is not None and withdraw_end_at < end_at:
        raise ValueError("退选截止时间不得早于选课结束时间。")
    window = await session.scalar(
        select(SelectionWindow)
        .where(
            SelectionWindow.term_id == term_id,
            SelectionWindow.course_id.is_(None),
        )
        .with_for_update()
    )
    if window is None:
        window = SelectionWindow(
            term_id=term_id,
            course_id=None,
            selection_rule_set_id=None,
            start_at=start_at,
            end_at=end_at,
            withdraw_end_at=withdraw_end_at,
            status="OPEN",
        )
        session.add(window)
    else:
        window.start_at = start_at
        window.end_at = end_at
        window.withdraw_end_at = withdraw_end_at
        window.status = "OPEN"
    await session.commit()
    await session.refresh(window)
    await invalidate_window_cache(term_id)
    return window


def _gate_from_row(
    row: dict[str, Any] | None, *, now_epoch: int
) -> dict[str, Any]:
    if row is None:
        return {
            "open": False,
            "withdraw_open": False,
            "start": None,
            "end": None,
            "withdraw_end": None,
            "message": WINDOW_NOT_CONFIGURED_MESSAGE,
            "withdraw_message": WINDOW_NOT_CONFIGURED_MESSAGE,
        }
    start = row.get("start")
    end = row.get("end")
    withdraw_end = row.get("withdraw_end") or end
    status_open = row.get("status") == "OPEN"
    if not status_open:
        select_message = "选课窗口未开放。"
    elif start is None or end is None:
        select_message = WINDOW_NOT_CONFIGURED_MESSAGE
    elif now_epoch < start:
        select_message = "选课尚未开始。"
    elif now_epoch > end:
        select_message = "选课已结束。"
    else:
        select_message = ""
    if not status_open:
        withdraw_message = "选课窗口未开放。"
    elif start is None:
        withdraw_message = WINDOW_NOT_CONFIGURED_MESSAGE
    elif now_epoch < start:
        withdraw_message = "选课尚未开始。"
    elif withdraw_end is not None and now_epoch > withdraw_end:
        withdraw_message = "退选时间已截止。"
    else:
        withdraw_message = ""
    return {
        "open": bool(select_message == ""),
        "withdraw_open": bool(withdraw_message == ""),
        "start": start,
        "end": end,
        "withdraw_end": withdraw_end,
        "message": select_message,
        "withdraw_message": withdraw_message,
    }


async def resolve_window_gate(
    session: AsyncSession, term_id: UUID
) -> dict[str, Any]:
    """门控判定：先读 Redis 缓存，未命中查 DB 回写；Redis 故障降级 DB。"""

    cached = await read_window_cache(term_id)
    if cached is None:
        window = await get_term_window(session, term_id)
        if window is not None:
            cached = _row_cache_dict(window)
            await write_window_cache(term_id, cached)
    return _gate_from_row(cached, now_epoch=int(datetime.now(UTC).timestamp()))
