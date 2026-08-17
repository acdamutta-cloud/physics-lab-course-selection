from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.resources import EquipmentAsset, ResourceIssueAsset, ResourceIssueReport
from app.schemas.teacher_adjustment import (
    EquipmentScrapCreateRequest,
    ResourceIssueCreateRequest,
)


def test_equipment_failure_requires_exact_asset_id() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="具体仪器号"):
        ResourceIssueCreateRequest(
            laboratory_id=uuid4(),
            inventory_id=uuid4(),
            issue_type="EQUIPMENT_FAILURE",
            impact_start=now,
            impact_end=now + timedelta(days=1),
            description="示波器无法开机",
        )


def test_equipment_failure_accepts_asset_without_aggregate_inventory() -> None:
    now = datetime.now(UTC)
    request = ResourceIssueCreateRequest(
        laboratory_id=uuid4(),
        asset_id=uuid4(),
        issue_type="EQUIPMENT_FAILURE",
        impact_start=now,
        impact_end=now + timedelta(days=1),
        description="示波器无法开机",
    )
    assert request.affected_quantity == 1
    assert request.inventory_id is None


def test_scrap_request_requires_asset_and_reason() -> None:
    request = EquipmentScrapCreateRequest(asset_id=uuid4(), reason="主板损坏且无法维修")
    assert request.asset_id
    with pytest.raises(ValidationError):
        EquipmentScrapCreateRequest(asset_id=uuid4(), reason="坏")


def test_asset_tables_enforce_unique_code_and_one_active_chain() -> None:
    asset_constraints = {item.name for item in EquipmentAsset.__table__.constraints}
    assert "uq_equipment_asset_instrument_no" in asset_constraints
    active_indexes = {item.name: item for item in ResourceIssueAsset.__table__.indexes}
    assert active_indexes["uq_resource_issue_asset_active"].unique is True
    status_sql = " ".join(
        str(item.sqltext)
        for item in ResourceIssueReport.__table__.constraints
        if hasattr(item, "sqltext")
    )
    assert "SCRAP_REVIEW" in status_sql
    assert "RELOCATION_REQUIRED" in status_sql
    assert "SCRAPPED" in status_sql
