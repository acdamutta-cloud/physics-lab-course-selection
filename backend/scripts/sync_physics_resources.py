"""同步通用高校物理实验的器材需求、实验室库存和能力映射。

默认仅输出差异。只有显式传入 ``--apply`` 才会在单个事务中写入数据库。
同步范围严格限定为内置目录中的 30 个 DEMO 项目和 11 间实验室。
"""

import argparse
import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.physics_resources import (
    EQUIPMENT_SPECS,
    LAB_INVENTORY_SPECS,
    PROJECT_LAB_CAPACITIES,
    PROJECT_RESOURCE_SPECS,
    build_reserved_inventory_specs,
    validate_catalog,
)
from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models import (
    EquipmentType,
    ExperimentProject,
    LabEquipmentInventory,
    Laboratory,
    LabProjectCapability,
    ProjectEquipmentRequirement,
)


@dataclass
class ChangeReport:
    items: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def add(self, category: str, message: str) -> None:
        self.items[category].append(message)

    @property
    def count(self) -> int:
        return sum(len(items) for items in self.items.values())

    def print(self, *, apply: bool) -> None:
        mode = "APPLY" if apply else "DRY-RUN"
        print(f"[{mode}] 物理实验资源同步差异：{self.count} 项")
        if not self.items:
            print("  当前数据已与目录一致。")
            return
        labels = {
            "equipment_create": "新增器材类型",
            "project_update": "更新项目备注",
            "project_grouping_update": "更新项目实验形式",
            "requirement_create": "新增器材需求",
            "requirement_update": "更新器材需求",
            "requirement_delete": "移除错误器材需求",
            "inventory_create": "新增实验室库存",
            "inventory_update": "更新实验室库存",
            "capability_create": "新增项目能力",
            "capability_update": "更新项目能力",
            "capability_delete": "移除错误项目能力",
        }
        for category, items in self.items.items():
            print(f"  {labels.get(category, category)}（{len(items)}）")
            for item in items:
                print(f"    - {item}")


async def _load_managed_objects(
    session: AsyncSession,
) -> tuple[
    dict[str, ExperimentProject],
    dict[str, Laboratory],
    list[EquipmentType],
]:
    projects = list(
        (
            await session.execute(
                select(ExperimentProject).where(
                    ExperimentProject.project_code.in_(
                        PROJECT_RESOURCE_SPECS
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    labs = list(
        (
            await session.execute(
                select(Laboratory).where(
                    Laboratory.lab_code.in_(LAB_INVENTORY_SPECS)
                )
            )
        )
        .scalars()
        .all()
    )
    equipment = list(
        (await session.execute(select(EquipmentType))).scalars().all()
    )
    projects_by_code = {item.project_code: item for item in projects}
    labs_by_code = {item.lab_code: item for item in labs}

    missing_projects = sorted(
        set(PROJECT_RESOURCE_SPECS) - set(projects_by_code)
    )
    missing_labs = sorted(set(LAB_INVENTORY_SPECS) - set(labs_by_code))
    if missing_projects or missing_labs:
        messages = []
        if missing_projects:
            messages.append("缺少项目：" + "、".join(missing_projects))
        if missing_labs:
            messages.append("缺少实验室：" + "、".join(missing_labs))
        raise RuntimeError(
            "同步前置数据不完整，未执行任何写入；" + "；".join(messages)
        )
    return projects_by_code, labs_by_code, equipment


async def sync_resources(
    session: AsyncSession,
    *,
    apply: bool,
) -> ChangeReport:
    catalog_errors = validate_catalog()
    if catalog_errors:
        raise RuntimeError("资源目录无效：" + "；".join(catalog_errors))

    report = ChangeReport()
    projects, labs, existing_equipment = await _load_managed_objects(session)
    equipment_by_code = {
        item.equipment_code: item for item in existing_equipment
    }
    equipment_by_identity = {
        (item.name, item.model or ""): item for item in existing_equipment
    }
    resolved_equipment: dict[str, EquipmentType] = {}

    for spec in EQUIPMENT_SPECS:
        equipment = equipment_by_code.get(spec.code)
        if equipment is None:
            equipment = equipment_by_identity.get((spec.name, spec.model))
        if equipment is None:
            report.add(
                "equipment_create",
                f"{spec.code} {spec.name}（{spec.model}）",
            )
            if not apply:
                continue
            equipment = EquipmentType(
                equipment_code=spec.code,
                name=spec.name,
                model=spec.model,
                unit=spec.unit,
                status="ACTIVE",
            )
            session.add(equipment)
            await session.flush()
        resolved_equipment[spec.code] = equipment

    project_ids = [item.id for item in projects.values()]
    project_code_by_id = {
        item.id: item.project_code for item in projects.values()
    }
    equipment_name_by_id = {
        item.id: item.name for item in existing_equipment
    }
    current_requirements = list(
        (
            await session.execute(
                select(ProjectEquipmentRequirement).where(
                    ProjectEquipmentRequirement.project_id.in_(project_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    requirement_by_key = {
        (item.project_id, item.equipment_type_id): item
        for item in current_requirements
    }
    desired_requirement_keys: set[tuple[UUID, UUID]] = set()

    for project_code, spec in PROJECT_RESOURCE_SPECS.items():
        project = projects[project_code]
        if (
            project.group_mode != "INDIVIDUAL"
            or project.default_group_size != 1
        ):
            report.add(
                "project_grouping_update",
                f"{project_code} -> 单人实验（1 人/组）",
            )
            if apply:
                project.group_mode = "INDIVIDUAL"
                project.default_group_size = 1
        if project.material_note != spec.material_note:
            report.add(
                "project_update",
                f"{project_code} material_note",
            )
            if apply:
                project.material_note = spec.material_note
        for desired in spec.requirements:
            equipment = resolved_equipment.get(desired.equipment_code)
            if equipment is None:
                report.add(
                    "requirement_create",
                    f"{project_code} <- {desired.equipment_code} "
                    f"x{desired.units_per_group}/组",
                )
                continue
            key = (project.id, equipment.id)
            desired_requirement_keys.add(key)
            current = requirement_by_key.get(key)
            if current is None:
                report.add(
                    "requirement_create",
                    f"{project_code} <- {equipment.name} "
                    f"x{desired.units_per_group}/组",
                )
                if apply:
                    session.add(
                        ProjectEquipmentRequirement(
                            project_id=project.id,
                            equipment_type_id=equipment.id,
                            units_per_group=desired.units_per_group,
                            required=desired.required,
                        )
                    )
            elif (
                current.units_per_group != desired.units_per_group
                or current.required != desired.required
            ):
                report.add(
                    "requirement_update",
                    f"{project_code} <- {equipment.name} "
                    f"x{desired.units_per_group}/组",
                )
                if apply:
                    current.units_per_group = desired.units_per_group
                    current.required = desired.required

    for key, current in requirement_by_key.items():
        if key in desired_requirement_keys:
            continue
        report.add(
            "requirement_delete",
            f"{project_code_by_id[current.project_id]} <- "
            f"{equipment_name_by_id.get(current.equipment_type_id, current.equipment_type_id)}",
        )
        if apply:
            await session.delete(current)

    lab_ids = [item.id for item in labs.values()]
    lab_code_by_id = {item.id: item.lab_code for item in labs.values()}
    current_inventories = list(
        (
            await session.execute(
                select(LabEquipmentInventory).where(
                    LabEquipmentInventory.laboratory_id.in_(lab_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    inventory_by_key = {
        (item.laboratory_id, item.equipment_type_id): item
        for item in current_inventories
    }
    desired_inventory_specs = build_reserved_inventory_specs(
        {project_code: 1 for project_code in PROJECT_RESOURCE_SPECS}
    )
    for lab_code, desired_inventory in desired_inventory_specs.items():
        lab = labs[lab_code]
        for equipment_code, (total, usable) in desired_inventory.items():
            equipment = resolved_equipment.get(equipment_code)
            if equipment is None:
                report.add(
                    "inventory_create",
                    f"{lab_code} <- {equipment_code} "
                    f"总数{total}/可用{usable}",
                )
                continue
            key = (lab.id, equipment.id)
            current = inventory_by_key.get(key)
            disabled = total - usable
            if current is None:
                report.add(
                    "inventory_create",
                    f"{lab_code} <- {equipment.name} "
                    f"总数{total}/可用{usable}",
                )
                if apply:
                    session.add(
                        LabEquipmentInventory(
                            laboratory_id=lab.id,
                            equipment_type_id=equipment.id,
                            total_quantity=total,
                            usable_quantity=usable,
                            disabled_quantity=disabled,
                        )
                    )
            elif current.usable_quantity < usable:
                increase = usable - current.usable_quantity
                target_total = max(
                    current.total_quantity + increase,
                    usable + current.disabled_quantity,
                )
                report.add(
                    "inventory_update",
                    f"{lab_code} <- {equipment.name} "
                    f"总数{target_total}/可用{usable}",
                )
                if apply:
                    current.total_quantity = target_total
                    current.usable_quantity = usable

    current_capabilities = list(
        (
            await session.execute(
                select(LabProjectCapability).where(
                    LabProjectCapability.project_id.in_(project_ids),
                    LabProjectCapability.laboratory_id.in_(lab_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    capability_by_key = {
        (item.project_id, item.laboratory_id): item
        for item in current_capabilities
    }
    desired_capability_keys: set[tuple[UUID, UUID]] = set()
    for project_code, spec in PROJECT_RESOURCE_SPECS.items():
        project = projects[project_code]
        for lab_code in spec.lab_codes:
            lab = labs[lab_code]
            key = (project.id, lab.id)
            desired_capability_keys.add(key)
            effective_capacity = PROJECT_LAB_CAPACITIES[
                project_code
            ][lab_code]
            current = capability_by_key.get(key)
            if current is None:
                report.add(
                    "capability_create",
                    f"{project_code} -> {lab_code} "
                    f"容量{effective_capacity}",
                )
                if apply:
                    session.add(
                        LabProjectCapability(
                            project_id=project.id,
                            laboratory_id=lab.id,
                            effective_capacity=effective_capacity,
                            status="ACTIVE",
                            note=spec.capability_note,
                        )
                    )
            elif (
                current.effective_capacity != effective_capacity
                or current.status != "ACTIVE"
                or current.note != spec.capability_note
            ):
                report.add(
                    "capability_update",
                    f"{project_code} -> {lab_code} "
                    f"容量{effective_capacity}",
                )
                if apply:
                    current.effective_capacity = effective_capacity
                    current.status = "ACTIVE"
                    current.note = spec.capability_note

    for key, current in capability_by_key.items():
        if key in desired_capability_keys:
            continue
        report.add(
            "capability_delete",
            f"{project_code_by_id[current.project_id]} -> "
            f"{lab_code_by_id[current.laboratory_id]}",
        )
        if apply:
            await session.delete(current)

    await session.flush()
    return report


async def run(*, apply: bool) -> int:
    try:
        async with AsyncSessionFactory() as session:
            try:
                report = await sync_resources(session, apply=apply)
                report.print(apply=apply)
                if apply:
                    await session.commit()
                    print("同步已在单个事务中提交。")
                else:
                    await session.rollback()
                    print("dry-run 完成，数据库未发生变更。")
            except Exception:
                await session.rollback()
                raise
        return 0
    finally:
        await dispose_database_engine()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入数据库；省略时仅输出差异",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(asyncio.run(run(apply=arguments.apply)))
