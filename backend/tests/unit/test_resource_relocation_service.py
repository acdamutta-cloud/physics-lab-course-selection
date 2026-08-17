from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.services import resource_relocation_service as service


@pytest.mark.asyncio
async def test_scrap_issue_can_generate_relocation_after_review(monkeypatch) -> None:
    issue = SimpleNamespace(
        id=uuid4(),
        status="RELOCATION_REQUIRED",
        remediation_status="REMEDIATION_REQUIRED",
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=issue),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        service,
        "resource_impact",
        AsyncMock(
            return_value={
                "known": True,
                "shortage": False,
                "affected_sessions": [],
            }
        ),
    )

    plans = await service.generate_resource_relocation_plans(
        session,
        issue_id=issue.id,
        actor_id=uuid4(),
        preferences=service.SelectionPreferences(),
    )

    assert plans == []
    assert issue.remediation_status == "NOT_REQUIRED"
    session.commit.assert_awaited_once()
