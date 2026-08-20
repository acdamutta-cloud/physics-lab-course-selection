from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest

from app.services import resource_relocation_service as relocation_service
from app.services import teacher_adjustment_service as adjustment_service


@pytest.mark.asyncio
async def test_generate_scrap_completes_issue_to_scrapped(monkeypatch) -> None:
    """报废工单生成方案成功即置 SCRAPPED（对齐故障"生成方案即通过"）。"""
    issue = SimpleNamespace(
        id=uuid4(),
        status="RELOCATION_REQUIRED",
        remediation_status="REMEDIATION_REQUIRED",
        issue_type="EQUIPMENT_SCRAP",
        source_issue_id=None,
        resolved_at=None,
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=issue),
        execute=AsyncMock(return_value=SimpleNamespace(scalars=list)),
        delete=AsyncMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        relocation_service,
        "resource_impact",
        AsyncMock(
            return_value={
                "known": True,
                "shortage": True,
                "affected_sessions": [],
            }
        ),
    )
    complete = AsyncMock()
    monkeypatch.setattr(relocation_service, "_complete_scrap_issue", complete)

    plans = await relocation_service.generate_resource_relocation_plans(
        session,
        issue_id=issue.id,
        actor_id=uuid4(),
        preferences=relocation_service.SelectionPreferences(),
    )

    assert plans == []
    complete.assert_awaited_once_with(session, issue, actor_id=ANY)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_scrap_no_shortage_completes_issue(monkeypatch) -> None:
    """报废工单容量已恢复：无需分流也直接完成报废。"""
    issue = SimpleNamespace(
        id=uuid4(),
        status="RELOCATION_REQUIRED",
        remediation_status="REMEDIATION_REQUIRED",
        issue_type="EQUIPMENT_SCRAP",
        source_issue_id=None,
        resolved_at=None,
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=issue),
        execute=AsyncMock(return_value=SimpleNamespace(scalars=list)),
        delete=AsyncMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        relocation_service,
        "resource_impact",
        AsyncMock(
            return_value={
                "known": True,
                "shortage": False,
                "affected_sessions": [],
            }
        ),
    )
    complete = AsyncMock()
    monkeypatch.setattr(relocation_service, "_complete_scrap_issue", complete)

    plans = await relocation_service.generate_resource_relocation_plans(
        session,
        issue_id=issue.id,
        actor_id=uuid4(),
        preferences=relocation_service.SelectionPreferences(),
    )

    assert plans == []
    complete.assert_awaited_once_with(session, issue, actor_id=ANY)


@pytest.mark.asyncio
async def test_review_scrap_uses_pending_deduction(monkeypatch) -> None:
    """报废审批按"扣后容量"判断：impact 必须带 pending_deduction=True。"""
    issue = SimpleNamespace(
        id=uuid4(),
        status="PENDING_REVIEW",
        remediation_status="NOT_REQUIRED",
        issue_type="EQUIPMENT_SCRAP",
        reporter_teacher_id=None,
        report_no="RI-TEST-0001",
        affected_quantity=1,
    )
    asset = SimpleNamespace(instrument_no="B102-PHY-HALL-001")
    link = SimpleNamespace(previous_status="AVAILABLE")
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=issue),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        adjustment_service.asset_svc,
        "asset_for_issue",
        AsyncMock(return_value=(asset, link)),
    )
    impact = AsyncMock(
        return_value={"shortage": True, "available": 19, "required": 20}
    )
    monkeypatch.setattr(adjustment_service, "resource_impact", impact)

    returned, _ = await adjustment_service.review_resource_issue(
        session,
        issue_id=issue.id,
        actor_id=uuid4(),
        approved=True,
        approved_quantity=None,
    )

    assert returned.status == "RELOCATION_REQUIRED"
    assert returned.remediation_status == "REMEDIATION_REQUIRED"
    impact.assert_awaited_once_with(session, issue, pending_deduction=True)
