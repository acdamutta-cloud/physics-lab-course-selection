from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.resources import (
    EquipmentType,
    LabEquipmentInventory,
    Laboratory,
)
from app.services.equipment_usage_note_service import interpret_equipment_usage_note


# ── Labs ──

async def get_labs(session: AsyncSession) -> list[Laboratory]:
    stmt = (
        select(Laboratory)
        .options(selectinload(Laboratory.equipment_inventory).selectinload(LabEquipmentInventory.equipment_type))
        .order_by(Laboratory.name)
    )
    result = await session.execute(stmt)
    return list(result.scalars().unique())


async def get_lab_by_id(session: AsyncSession, lab_id: UUID) -> Laboratory | None:
    stmt = (
        select(Laboratory)
        .options(selectinload(Laboratory.equipment_inventory).selectinload(LabEquipmentInventory.equipment_type))
        .where(Laboratory.id == lab_id)
    )
    result = await session.execute(stmt)
    return result.scalars().unique().one_or_none()


async def create_lab(
    session: AsyncSession, data: dict, created_by: UUID
) -> Laboratory:
    lab = Laboratory(
        lab_code=data["lab_code"],
        name=data["name"],
        campus_id=data.get("campus_id"),
        room_type=data.get("room_type"),
        safety_capacity=data.get("safety_capacity", 24),
        manager_teacher_id=data.get("manager_teacher_id"),
        description=data.get("description"),
        status="ACTIVE",
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(lab)
    await session.flush()
    return lab


async def update_lab(
    session: AsyncSession, lab_id: UUID, data: dict
) -> Laboratory | None:
    lab = await session.get(Laboratory, lab_id)
    if lab is None:
        return None
    for key in ("name", "room_type", "safety_capacity", "description", "status"):
        if key in data:
            setattr(lab, key, data[key])
    if "manager_teacher_id" in data:
        lab.manager_teacher_id = data["manager_teacher_id"]
    await session.flush()
    return lab


async def delete_lab(session: AsyncSession, lab_id: UUID) -> bool:
    lab = await session.get(Laboratory, lab_id)
    if lab is None:
        return False
    await session.delete(lab)
    await session.flush()
    return True


# ── Equipment Inventory ──

async def add_equipment_to_lab(
    session: AsyncSession, lab_id: UUID, data: dict
) -> LabEquipmentInventory:
    rule = interpret_equipment_usage_note(
        data.get("usage_note", data.get("note"))
    )
    inv = LabEquipmentInventory(
        laboratory_id=lab_id,
        equipment_type_id=UUID(data["equipment_type_id"]),
        total_quantity=data.get("total_quantity", 1),
        usable_quantity=data.get("usable_quantity", 1),
        disabled_quantity=data.get("disabled_quantity", 0),
        usage_note=rule.usage_note or None,
        students_per_unit=rule.students_per_unit,
        sharing_rule_status=rule.sharing_rule_status,
        sharing_rule_source="SEMANTIC_PARSER" if rule.usage_note else None,
        sharing_rule_evidence=rule.evidence,
    )
    session.add(inv)
    await session.flush()
    return inv


async def update_equipment_inventory(
    session: AsyncSession, inv_id: UUID, data: dict
) -> LabEquipmentInventory | None:
    inv = await session.get(LabEquipmentInventory, inv_id)
    if inv is None:
        return None

    total = data.get("total_quantity", inv.total_quantity)
    usable = data.get("usable_quantity", inv.usable_quantity)
    disabled = data.get("disabled_quantity", inv.disabled_quantity)

    # 校验与自动修正
    if usable < 0:
        usable = 0
    if usable > total:
        usable = total
    # disabled 自动计算为差额
    inv.total_quantity = total
    inv.usable_quantity = usable
    inv.disabled_quantity = max(0, total - usable)

    if "usage_note" in data or "note" in data:
        rule = interpret_equipment_usage_note(
            data.get("usage_note", data.get("note"))
        )
        inv.usage_note = rule.usage_note or None
        inv.students_per_unit = rule.students_per_unit
        inv.sharing_rule_status = rule.sharing_rule_status
        inv.sharing_rule_source = "SEMANTIC_PARSER" if rule.usage_note else None
        inv.sharing_rule_evidence = rule.evidence

    await session.flush()
    return inv


async def delete_equipment_inventory(
    session: AsyncSession, inv_id: UUID
) -> bool:
    inv = await session.get(LabEquipmentInventory, inv_id)
    if inv is None:
        return False
    await session.delete(inv)
    await session.flush()
    return True


async def get_active_equipment_types(
    session: AsyncSession,
) -> list[EquipmentType]:
    stmt = select(EquipmentType).where(
        EquipmentType.status == "ACTIVE"
    ).order_by(EquipmentType.name)
    result = await session.execute(stmt)
    return list(result.scalars())
