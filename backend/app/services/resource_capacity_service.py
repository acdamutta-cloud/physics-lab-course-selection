from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import ExperimentProject
from app.models.resources import (
    EquipmentType,
    LabEquipmentInventory,
    Laboratory,
    LabProjectCapability,
    ProjectEquipmentRequirement,
    ResourceIssueReport,
)
from app.models.scheduling import ExperimentSession


def equipment_student_capacity(
    *, usable_quantity: int, units_per_group: int, students_per_unit: int
) -> int:
    return (
        max(0, usable_quantity) // max(1, units_per_group)
    ) * max(1, students_per_unit)


def minimum_resource_capacity(
    *,
    laboratory_capacity: int,
    capability_capacity: int,
    equipment_capacities: list[int],
    group_size: int = 1,
    group_mode: str = "INDIVIDUAL",
) -> int:
    effective = min(
        [laboratory_capacity, *equipment_capacities]
    )
    if group_mode == "GROUP":
        effective -= effective % max(1, group_size)
    return max(0, effective)


async def calculate_session_resource_capacity(
    session: AsyncSession,
    experiment_session: ExperimentSession,
    *,
    issue: ResourceIssueReport | None = None,
    project_pending_issue: bool = False,
) -> dict[str, Any]:
    """Calculate capacity as the minimum of lab, capability and every resource.

    A confirmed natural-language sharing rule (for example, ``2人一台``)
    controls the equipment multiplier.  Otherwise the project's established
    group size is used and a warning is returned.
    """

    project = await session.get(ExperimentProject, experiment_session.project_id)
    laboratory = await session.get(Laboratory, experiment_session.laboratory_id)
    capability = await session.scalar(
        select(LabProjectCapability).where(
            LabProjectCapability.project_id == experiment_session.project_id,
            LabProjectCapability.laboratory_id == experiment_session.laboratory_id,
            LabProjectCapability.status == "ACTIVE",
        )
    )
    if project is None or laboratory is None or capability is None:
        return {
            "known": False,
            "effective_capacity": 0,
            "warnings": ["实验室或项目能力配置不完整，无法确认有效容量。"],
            "equipment": [],
        }

    requirement_rows = (
        await session.execute(
            select(ProjectEquipmentRequirement, EquipmentType)
            .join(
                EquipmentType,
                EquipmentType.id == ProjectEquipmentRequirement.equipment_type_id,
            )
            .where(
                ProjectEquipmentRequirement.project_id == project.id,
                ProjectEquipmentRequirement.required.is_(True),
            )
        )
    ).all()
    inventories = {
        item.equipment_type_id: item
        for item in (
            await session.execute(
                select(LabEquipmentInventory).where(
                    LabEquipmentInventory.laboratory_id == laboratory.id
                )
            )
        ).scalars()
    }

    warnings: list[str] = []
    equipment_details: list[dict[str, Any]] = []
    capacities = [laboratory.safety_capacity, capability.effective_capacity]
    known = True
    for requirement, equipment_type in requirement_rows:
        inventory = inventories.get(requirement.equipment_type_id)
        if inventory is None:
            known = False
            capacity = 0
            multiplier = project.default_group_size
            usable = 0
            status = "MISSING"
            warnings.append(f"实验室未配置必需仪器“{equipment_type.name}”。")
        else:
            usable = inventory.usable_quantity
            if (
                issue is not None
                and project_pending_issue
                and issue.status in {"REPORTED", "PENDING_REVIEW"}
                and inventory.id == issue.inventory_id
            ):
                usable = max(0, usable - issue.affected_quantity)
            multiplier = (
                inventory.students_per_unit
                if inventory.sharing_rule_status == "CONFIRMED"
                and inventory.students_per_unit
                else 1
            )
            status = inventory.sharing_rule_status
            if inventory.usage_note and status != "CONFIRMED":
                known = False
                warnings.append(
                    f"仪器“{equipment_type.name}”的使用备注尚未确认，不能据此自动生成迁移方案。"
                )
            units = max(1, requirement.units_per_group)
            capacity = equipment_student_capacity(
                usable_quantity=usable,
                units_per_group=units,
                students_per_unit=multiplier,
            )
        capacities.append(capacity)
        equipment_details.append(
            {
                "equipment_type_id": str(requirement.equipment_type_id),
                "equipment_name": equipment_type.name,
                "usable_quantity": usable,
                "units_per_group": max(1, requirement.units_per_group),
                "students_per_unit": multiplier,
                "sharing_rule_status": status,
                "capacity": capacity,
            }
        )

    effective = minimum_resource_capacity(
        laboratory_capacity=laboratory.safety_capacity,
        capability_capacity=capability.effective_capacity,
        equipment_capacities=capacities[2:],
        group_size=project.default_group_size,
        group_mode=project.group_mode,
    )
    return {
        "known": known,
        "effective_capacity": max(0, effective),
        "laboratory_capacity": laboratory.safety_capacity,
        "project_capability_capacity": capability.effective_capacity,
        "equipment": equipment_details,
        "warnings": list(dict.fromkeys(warnings)),
    }


def relocation_requirement(selected_count: int, effective_capacity: int) -> dict[str, int]:
    required = max(0, selected_count - effective_capacity)
    return {
        "selected_count": selected_count,
        "effective_capacity": effective_capacity,
        "retained_count": selected_count - required,
        "required_relocation_count": required,
    }


# resource_impact 结果缓存：全量影响计算扫描数千场次（单次 0.3-0.9s），
# 列表页与审核/生成方案接口会反复调用，用短 TTL 缓存 + 写操作显式失效。
# key 带 issue.status——状态流转（审核/结案）自动换 key 失效。
# variant 区分 full（含 affected_sessions 明细，供生成方案接口）与
# lite（仅统计，供列表页，避免 2000+ 场次明细被序列化进响应）。
_IMPACT_CACHE_TTL_SECONDS = 120
_impact_logger = logging.getLogger(__name__)


def _impact_cache_key(issue_id: UUID, status: str, variant: str = "full") -> str:
    return f"resource-impact:{issue_id}:{status}:{variant}"


async def get_cached_resource_impact(
    issue_id: UUID, status: str, variant: str = "full"
) -> dict[str, Any] | None:
    """Return cached resource_impact result; None on miss or Redis failure."""
    try:
        from app.db.redis_client import get_redis_client

        raw = await get_redis_client().get(_impact_cache_key(issue_id, status, variant))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        _impact_logger.debug("resource_impact cache read failed", exc_info=True)
        return None


async def cache_resource_impact(
    issue_id: UUID, status: str, impact: dict[str, Any], variant: str = "full"
) -> None:
    """Store resource_impact result; Redis failure degrades gracefully."""
    try:
        from app.db.redis_client import get_redis_client

        await get_redis_client().setex(
            _impact_cache_key(issue_id, status, variant),
            _IMPACT_CACHE_TTL_SECONDS,
            json.dumps(impact, ensure_ascii=False),
        )
    except Exception:
        _impact_logger.debug("resource_impact cache write failed", exc_info=True)


async def invalidate_resource_impact_cache(
    issue_id: UUID, status: str
) -> None:
    """Drop cached impact after a write that changes capacity/shortage.

    Both variants are dropped: a write that changes selected_count or
    capacity invalidates the full detail and the list-page stats alike.
    """
    try:
        from app.db.redis_client import get_redis_client

        redis_client = get_redis_client()
        for variant in ("full", "lite"):
            await redis_client.delete(_impact_cache_key(issue_id, status, variant))
    except Exception:
        _impact_logger.debug("resource_impact cache invalidate failed", exc_info=True)
