from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import AcademicTerm
from app.models.resources import (
    EquipmentAsset,
    EquipmentAssetEvent,
    EquipmentType,
    LabEquipmentInventory,
    Laboratory,
    ProjectEquipmentRequirement,
    ResourceIssueAsset,
    ResourceIssueObservation,
    ResourceIssueReport,
)
from app.models.scheduling import ExperimentSession, ScheduleVersion


ACTIVE_ASSET_STATUSES = {"AVAILABLE", "QUARANTINED", "UNDER_REPAIR", "DISABLED", "LOST"}


async def sync_inventory_counts(session: AsyncSession, inventory_id: UUID) -> None:
    inventory = await session.scalar(
        select(LabEquipmentInventory)
        .where(LabEquipmentInventory.id == inventory_id)
        .with_for_update()
    )
    if inventory is None:
        raise LookupError("设备汇总库存不存在。")
    statuses = list(
        (
            await session.execute(
                select(EquipmentAsset.status).where(
                    EquipmentAsset.current_inventory_id == inventory_id
                )
            )
        ).scalars()
    )
    inventory.total_quantity = sum(value != "SCRAPPED" for value in statuses)
    inventory.usable_quantity = statuses.count("AVAILABLE")
    inventory.disabled_quantity = sum(
        value not in {"AVAILABLE", "SCRAPPED"} for value in statuses
    )


def asset_dict(asset: EquipmentAsset, equipment: EquipmentType, lab: Laboratory, active_issue: ResourceIssueReport | None = None) -> dict[str, object]:
    return {
        "id": str(asset.id), "instrument_no": asset.instrument_no,
        "equipment_type_id": str(asset.equipment_type_id),
        "equipment_name": equipment.name, "model": equipment.model or "",
        "laboratory_id": str(lab.id), "laboratory_name": lab.name,
        "inventory_id": str(asset.current_inventory_id), "status": asset.status,
        "note": asset.note or "",
        "active_issue": ({"id": str(active_issue.id), "report_no": active_issue.report_no, "status": active_issue.status, "issue_type": active_issue.issue_type} if active_issue else None),
    }


async def list_lab_assets(
    session: AsyncSession, lab_id: UUID, *, equipment_type_id: UUID | None = None,
    status: str | None = None, query: str = "", page: int = 1, page_size: int = 50,
) -> dict[str, object]:
    statement = (
        select(EquipmentAsset, EquipmentType, Laboratory, ResourceIssueReport)
        .join(LabEquipmentInventory, LabEquipmentInventory.id == EquipmentAsset.current_inventory_id)
        .join(EquipmentType, EquipmentType.id == EquipmentAsset.equipment_type_id)
        .join(Laboratory, Laboratory.id == LabEquipmentInventory.laboratory_id)
        .outerjoin(ResourceIssueAsset, (ResourceIssueAsset.asset_id == EquipmentAsset.id) & ResourceIssueAsset.active.is_(True))
        .outerjoin(ResourceIssueReport, ResourceIssueReport.id == ResourceIssueAsset.resource_issue_id)
        .where(LabEquipmentInventory.laboratory_id == lab_id)
        .order_by(EquipmentAsset.instrument_no)
    )
    if equipment_type_id:
        statement = statement.where(EquipmentAsset.equipment_type_id == equipment_type_id)
    if status:
        statement = statement.where(EquipmentAsset.status == status)
    if query.strip():
        statement = statement.where(EquipmentAsset.instrument_no.ilike(f"%{query.strip()}%"))
    total = int(
        await session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
    )
    rows = (await session.execute(
        statement.offset((page - 1) * page_size).limit(page_size)
    )).all()
    return {
        "items": [asset_dict(*row) for row in rows],
        "total": total, "page": page, "page_size": page_size,
    }


async def batch_create_assets(
    session: AsyncSession, *, lab_id: UUID, equipment_type_id: UUID,
    quantity: int, actor_id: UUID, available_quantity: int | None = None,
    commit: bool = True,
) -> list[EquipmentAsset]:
    inventory = await session.scalar(
        select(LabEquipmentInventory).where(
            LabEquipmentInventory.laboratory_id == lab_id,
            LabEquipmentInventory.equipment_type_id == equipment_type_id,
        ).with_for_update()
    )
    if inventory is None:
        inventory = LabEquipmentInventory(
            laboratory_id=lab_id, equipment_type_id=equipment_type_id,
            total_quantity=0, usable_quantity=0, disabled_quantity=0,
            sharing_rule_status="UNPARSED", created_by=actor_id, updated_by=actor_id,
        )
        session.add(inventory)
        await session.flush()
    lab = await session.get(Laboratory, lab_id)
    equipment = await session.get(EquipmentType, equipment_type_id)
    if lab is None or equipment is None:
        raise LookupError("实验室或设备类型不存在。")
    prefix = f"{lab.lab_code}-{equipment.equipment_code}-"
    codes = list((await session.execute(select(EquipmentAsset.instrument_no).where(EquipmentAsset.instrument_no.like(f"{prefix}%")))).scalars())
    numbers = [int(code[len(prefix):]) for code in codes if code[len(prefix):].isdigit()]
    start = max(numbers, default=0) + 1
    assets: list[EquipmentAsset] = []
    available = quantity if available_quantity is None else available_quantity
    if available < 0 or available > quantity:
        raise ValueError("可用数量必须在 0 到登记数量之间。")
    for offset in range(quantity):
        status = "AVAILABLE" if offset < available else "DISABLED"
        asset = EquipmentAsset(
            instrument_no=f"{prefix}{start + offset:03d}", equipment_type_id=equipment_type_id,
            current_inventory_id=inventory.id, origin_laboratory_id=lab_id,
            status=status, created_by=actor_id, updated_by=actor_id,
        )
        session.add(asset); await session.flush(); assets.append(asset)
        session.add(EquipmentAssetEvent(asset_id=asset.id, event_type="REGISTER", to_status=status, to_inventory_id=inventory.id, created_by=actor_id, updated_by=actor_id))
    await sync_inventory_counts(session, inventory.id)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return assets


async def reportable_assets(session: AsyncSession, teacher_id: UUID) -> list[dict[str, object]]:
    statement = (
        select(EquipmentAsset, EquipmentType, Laboratory, ResourceIssueReport)
        .join(LabEquipmentInventory, LabEquipmentInventory.id == EquipmentAsset.current_inventory_id)
        .join(EquipmentType, EquipmentType.id == EquipmentAsset.equipment_type_id)
        .join(Laboratory, Laboratory.id == LabEquipmentInventory.laboratory_id)
        .join(ProjectEquipmentRequirement, ProjectEquipmentRequirement.equipment_type_id == EquipmentAsset.equipment_type_id)
        .join(ExperimentSession, (ExperimentSession.project_id == ProjectEquipmentRequirement.project_id) & (ExperimentSession.laboratory_id == Laboratory.id))
        .join(ScheduleVersion, ScheduleVersion.id == ExperimentSession.schedule_version_id)
        .outerjoin(ResourceIssueAsset, (ResourceIssueAsset.asset_id == EquipmentAsset.id) & ResourceIssueAsset.active.is_(True))
        .outerjoin(ResourceIssueReport, ResourceIssueReport.id == ResourceIssueAsset.resource_issue_id)
        .where(ScheduleVersion.status == "PUBLISHED", ExperimentSession.teacher_id == teacher_id, EquipmentAsset.status != "SCRAPPED")
        .distinct()
        .order_by(EquipmentAsset.instrument_no)
    )
    return [asset_dict(*row) for row in (await session.execute(statement)).all()]


async def create_asset_issue(session: AsyncSession, *, teacher_id: UUID, actor_id: UUID, asset_id: UUID, issue_type: str, severity: str, description: str, impact_start: datetime, impact_end: datetime) -> tuple[ResourceIssueReport, bool]:
    asset = await session.scalar(select(EquipmentAsset).where(EquipmentAsset.id == asset_id).with_for_update())
    if asset is None or asset.status == "SCRAPPED":
        raise ValueError("仪器不存在或已经报废。")
    eligible_ids = {UUID(item["id"]) for item in await reportable_assets(session, teacher_id)}
    if asset.id not in eligible_ids:
        raise ValueError("只能报备本人课程相关仪器。")
    active_link = await session.scalar(select(ResourceIssueAsset).where(ResourceIssueAsset.asset_id == asset.id, ResourceIssueAsset.active.is_(True)).with_for_update())
    active_issue = await session.get(ResourceIssueReport, active_link.resource_issue_id) if active_link else None
    if active_issue and issue_type == "EQUIPMENT_FAILURE" and active_issue.issue_type == "EQUIPMENT_FAILURE":
        session.add(ResourceIssueObservation(resource_issue_id=active_issue.id, reporter_teacher_id=teacher_id, severity=severity, description=description, created_by=actor_id, updated_by=actor_id))
        await session.commit()
        return active_issue, True
    if active_issue and active_issue.issue_type == "EQUIPMENT_SCRAP":
        raise ValueError(f"该仪器已有报废申请 {active_issue.report_no} 正在审批。")
    source_issue_id = active_issue.id if active_issue else None
    if active_link:
        active_link.active = False
        active_issue.status = "SCRAP_REVIEW" if issue_type == "EQUIPMENT_SCRAP" else "CLOSED"
    inventory = await session.get(LabEquipmentInventory, asset.current_inventory_id)
    item = ResourceIssueReport(
        report_no=f"RI-{datetime.now(UTC):%Y%m%d}-{uuid4().hex[:8].upper()}", reporter_teacher_id=teacher_id,
        issue_type=issue_type, laboratory_id=inventory.laboratory_id, equipment_type_id=asset.equipment_type_id,
        inventory_id=inventory.id, affected_quantity=1, impact_start=impact_start, impact_end=impact_end,
        severity=severity, description=description, status="PENDING_REVIEW", source_issue_id=source_issue_id,
        created_by=actor_id, updated_by=actor_id,
    )
    session.add(item); await session.flush()
    previous_status = asset.status
    session.add(ResourceIssueAsset(resource_issue_id=item.id, asset_id=asset.id, active=True, previous_status=previous_status, created_by=actor_id, updated_by=actor_id))
    session.add(ResourceIssueObservation(resource_issue_id=item.id, reporter_teacher_id=teacher_id, severity=severity, description=description, created_by=actor_id, updated_by=actor_id))
    asset.status = "QUARANTINED"; asset.updated_by = actor_id
    session.add(EquipmentAssetEvent(asset_id=asset.id, event_type="SCRAP_REQUEST" if issue_type == "EQUIPMENT_SCRAP" else "ISSUE_REPORT", from_status=previous_status, to_status="QUARANTINED", to_inventory_id=asset.current_inventory_id, resource_issue_id=item.id, note=description, created_by=actor_id, updated_by=actor_id))
    await sync_inventory_counts(session, asset.current_inventory_id)
    await session.commit(); await session.refresh(item)
    return item, False


async def asset_for_issue(session: AsyncSession, issue_id: UUID) -> tuple[EquipmentAsset, ResourceIssueAsset] | None:
    row = (await session.execute(select(EquipmentAsset, ResourceIssueAsset).join(ResourceIssueAsset, ResourceIssueAsset.asset_id == EquipmentAsset.id).where(ResourceIssueAsset.resource_issue_id == issue_id).order_by(ResourceIssueAsset.created_at.desc()))).first()
    return row if row else None


async def assets_for_issue(
    session: AsyncSession, issue_id: UUID
) -> list[tuple[EquipmentAsset, ResourceIssueAsset]]:
    return list(
        (
            await session.execute(
                select(EquipmentAsset, ResourceIssueAsset)
                .join(ResourceIssueAsset, ResourceIssueAsset.asset_id == EquipmentAsset.id)
                .where(ResourceIssueAsset.resource_issue_id == issue_id)
                .order_by(EquipmentAsset.instrument_no)
            )
        ).all()
    )


async def restore_or_transition_issue_asset(session: AsyncSession, issue: ResourceIssueReport, *, actor_id: UUID, target_status: str, close_link: bool) -> None:
    rows = await assets_for_issue(session, issue.id)
    if not rows:
        return
    inventory_ids: set[UUID] = set()
    for asset, link in rows:
        before = asset.status; asset.status = target_status; asset.updated_by = actor_id
        if close_link: link.active = False
        inventory_ids.add(asset.current_inventory_id)
        session.add(EquipmentAssetEvent(asset_id=asset.id, event_type="STATUS_CHANGE", from_status=before, to_status=target_status, to_inventory_id=asset.current_inventory_id, resource_issue_id=issue.id, created_by=actor_id, updated_by=actor_id))
    for inventory_id in inventory_ids:
        await sync_inventory_counts(session, inventory_id)


async def issue_asset_payload(session: AsyncSession, issue_id: UUID) -> dict[str, object] | None:
    rows = await assets_for_issue(session, issue_id)
    if not rows: return None
    asset, _ = rows[0]
    inventory = await session.get(LabEquipmentInventory, asset.current_inventory_id)
    equipment = await session.get(EquipmentType, asset.equipment_type_id)
    lab = await session.get(Laboratory, inventory.laboratory_id)
    result = asset_dict(asset, equipment, lab)
    result["linked_asset_count"] = len(rows)
    return result


async def transfer_asset(session: AsyncSession, *, asset_id: UUID, target_lab_id: UUID, actor_id: UUID) -> EquipmentAsset:
    asset = await session.scalar(select(EquipmentAsset).where(EquipmentAsset.id == asset_id).with_for_update())
    if asset is None or asset.status == "SCRAPPED":
        raise ValueError("仪器不存在或已经报废。")
    active = await session.scalar(select(ResourceIssueAsset.id).where(ResourceIssueAsset.asset_id == asset.id, ResourceIssueAsset.active.is_(True)))
    if active:
        raise ValueError("仪器存在活动异常或报废申请，不能调拨。")
    old_inventory_id = asset.current_inventory_id
    target = await session.scalar(select(LabEquipmentInventory).where(LabEquipmentInventory.laboratory_id == target_lab_id, LabEquipmentInventory.equipment_type_id == asset.equipment_type_id).with_for_update())
    if target is None:
        target = LabEquipmentInventory(laboratory_id=target_lab_id, equipment_type_id=asset.equipment_type_id, total_quantity=0, usable_quantity=0, disabled_quantity=0, sharing_rule_status="UNPARSED", created_by=actor_id, updated_by=actor_id)
        session.add(target); await session.flush()
    asset.current_inventory_id = target.id; asset.updated_by = actor_id
    session.add(EquipmentAssetEvent(asset_id=asset.id, event_type="TRANSFER", from_status=asset.status, to_status=asset.status, from_inventory_id=old_inventory_id, to_inventory_id=target.id, created_by=actor_id, updated_by=actor_id))
    await sync_inventory_counts(session, old_inventory_id); await sync_inventory_counts(session, target.id)
    await session.commit(); await session.refresh(asset)
    return asset


async def asset_events(session: AsyncSession, asset_id: UUID) -> list[dict[str, object]]:
    rows = list((await session.execute(select(EquipmentAssetEvent).where(EquipmentAssetEvent.asset_id == asset_id).order_by(EquipmentAssetEvent.created_at.desc()))).scalars())
    return [{"id": str(item.id), "event_type": item.event_type, "from_status": item.from_status, "to_status": item.to_status, "note": item.note or "", "created_at": item.created_at} for item in rows]
