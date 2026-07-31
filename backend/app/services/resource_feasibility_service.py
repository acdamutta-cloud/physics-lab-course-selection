"""实验项目在单间实验室内的器材可行性计算。"""

from dataclasses import dataclass
from math import ceil
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
)


@dataclass(frozen=True)
class EquipmentRequirementInput:
    equipment_type_id: UUID
    equipment_name: str
    units_per_group: int
    required: bool = True


@dataclass(frozen=True)
class CapacityCalculation:
    effective_capacity: int
    missing_equipment_ids: tuple[UUID, ...]
    missing_equipment_names: tuple[str, ...]
    limiting_equipment_ids: tuple[UUID, ...]
    invalid_requirement_ids: tuple[UUID, ...]

    @property
    def feasible(self) -> bool:
        return self.effective_capacity > 0 and not (
            self.missing_equipment_ids or self.invalid_requirement_ids
        )


@dataclass(frozen=True)
class ProjectLabOption:
    project_id: UUID
    laboratory_id: UUID
    laboratory_code: str
    effective_capacity: int
    limiting_equipment_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class EquipmentReserveShortage:
    equipment_type_id: UUID
    equipment_name: str
    usable_quantity: int
    base_required_quantity: int
    recommended_quantity: int


@dataclass(frozen=True)
class ProjectLabIssue:
    project_id: UUID
    project_name: str
    laboratory_id: UUID | None
    laboratory_code: str | None
    message: str
    missing_equipment_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectLabWarning:
    project_id: UUID
    project_name: str
    laboratory_id: UUID
    laboratory_code: str
    code: str
    message: str
    shortages: tuple[EquipmentReserveShortage, ...]


@dataclass(frozen=True)
class ProjectResourceFeasibility:
    options: dict[UUID, list[ProjectLabOption]]
    issues: list[ProjectLabIssue]
    warnings: list[ProjectLabWarning]


def assess_inventory_thresholds(
    *,
    target_capacity: int,
    group_size: int,
    requirements: list[EquipmentRequirementInput],
    usable_inventory: dict[UUID, int],
    reserve_groups: int = 2,
) -> tuple[
    tuple[EquipmentReserveShortage, ...],
    tuple[EquipmentReserveShortage, ...],
]:
    """分别返回基础器材不足和备用器材不足。"""

    groups = ceil(target_capacity / group_size)
    blocking: list[EquipmentReserveShortage] = []
    reserve_shortages: list[EquipmentReserveShortage] = []
    for item in requirements:
        if not item.required or item.units_per_group < 1:
            continue
        usable = usable_inventory.get(item.equipment_type_id, 0)
        base_required = groups * item.units_per_group
        recommended = (groups + reserve_groups) * item.units_per_group
        shortage = EquipmentReserveShortage(
            equipment_type_id=item.equipment_type_id,
            equipment_name=item.equipment_name,
            usable_quantity=usable,
            base_required_quantity=base_required,
            recommended_quantity=recommended,
        )
        if usable < base_required:
            blocking.append(shortage)
        elif usable < recommended:
            reserve_shortages.append(shortage)
    return tuple(blocking), tuple(reserve_shortages)


def calculate_effective_capacity(
    *,
    group_size: int,
    safety_capacity: int,
    capability_capacity: int,
    requirements: list[EquipmentRequirementInput],
    usable_inventory: dict[UUID, int],
) -> CapacityCalculation:
    """计算一间实验室对一个项目的有效容量。

    所有必需器材都从同一个 ``usable_inventory`` 读取，因此不会把不同
    实验室的部分库存拼接在一起。
    """

    required_items = [item for item in requirements if item.required]
    invalid = tuple(
        item.equipment_type_id
        for item in required_items
        if item.units_per_group < 1
    )
    missing_items = [
        item
        for item in required_items
        if usable_inventory.get(item.equipment_type_id, 0)
        < max(item.units_per_group, 1)
    ]
    if (
        group_size < 1
        or safety_capacity < group_size
        or capability_capacity < group_size
        or not required_items
        or invalid
        or missing_items
    ):
        return CapacityCalculation(
            effective_capacity=0,
            missing_equipment_ids=tuple(
                item.equipment_type_id for item in missing_items
            ),
            missing_equipment_names=tuple(
                item.equipment_name for item in missing_items
            ),
            limiting_equipment_ids=(),
            invalid_requirement_ids=invalid,
        )

    equipment_capacities = {
        item.equipment_type_id: (
            usable_inventory.get(item.equipment_type_id, 0)
            // item.units_per_group
        )
        * group_size
        for item in required_items
    }
    equipment_capacity = min(equipment_capacities.values())
    effective_capacity = min(
        safety_capacity,
        capability_capacity,
        equipment_capacity,
    )
    # 场次只能容纳完整实验组。
    effective_capacity -= effective_capacity % group_size
    limiting = tuple(
        equipment_id
        for equipment_id, capacity in equipment_capacities.items()
        if capacity == equipment_capacity
        and equipment_capacity == effective_capacity
    )
    return CapacityCalculation(
        effective_capacity=effective_capacity,
        missing_equipment_ids=(),
        missing_equipment_names=(),
        limiting_equipment_ids=limiting,
        invalid_requirement_ids=(),
    )


async def evaluate_project_lab_resources(
    session: AsyncSession,
    project_ids: list[UUID] | tuple[UUID, ...] | set[UUID],
) -> ProjectResourceFeasibility:
    """返回项目的可行实验室和不可行原因。"""

    ids = list(dict.fromkeys(project_ids))
    if not ids:
        return ProjectResourceFeasibility(
            options={}, issues=[], warnings=[]
        )

    projects = list(
        (
            await session.execute(
                select(ExperimentProject).where(
                    ExperimentProject.id.in_(ids),
                    ExperimentProject.status == "ACTIVE",
                )
            )
        )
        .scalars()
        .all()
    )
    projects_by_id = {project.id: project for project in projects}
    options: dict[UUID, list[ProjectLabOption]] = {
        project_id: [] for project_id in ids
    }
    issues: list[ProjectLabIssue] = []
    warnings: list[ProjectLabWarning] = []

    requirement_rows = (
        await session.execute(
            select(ProjectEquipmentRequirement, EquipmentType)
            .join(
                EquipmentType,
                EquipmentType.id
                == ProjectEquipmentRequirement.equipment_type_id,
            )
            .where(ProjectEquipmentRequirement.project_id.in_(ids))
        )
    ).all()
    requirements_by_project: dict[
        UUID, list[EquipmentRequirementInput]
    ] = {project_id: [] for project_id in ids}
    for requirement, equipment in requirement_rows:
        # 停用器材不能满足必需需求，按零库存处理。
        name = equipment.name
        requirements_by_project[requirement.project_id].append(
            EquipmentRequirementInput(
                equipment_type_id=requirement.equipment_type_id,
                equipment_name=name,
                units_per_group=requirement.units_per_group,
                required=requirement.required,
            )
        )

    capability_rows = (
        await session.execute(
            select(LabProjectCapability, Laboratory)
            .join(
                Laboratory,
                Laboratory.id == LabProjectCapability.laboratory_id,
            )
            .where(
                LabProjectCapability.project_id.in_(ids),
                LabProjectCapability.status == "ACTIVE",
                Laboratory.status == "ACTIVE",
            )
        )
    ).all()
    lab_ids = {
        laboratory.id for _, laboratory in capability_rows
    }
    inventory_rows = []
    if lab_ids:
        inventory_rows = (
            await session.execute(
                select(LabEquipmentInventory, EquipmentType)
                .join(
                    EquipmentType,
                    EquipmentType.id
                    == LabEquipmentInventory.equipment_type_id,
                )
                .where(LabEquipmentInventory.laboratory_id.in_(lab_ids))
            )
        ).all()
    inventory_by_lab: dict[UUID, dict[UUID, int]] = {}
    for inventory, equipment in inventory_rows:
        usable = (
            inventory.usable_quantity
            if equipment.status == "ACTIVE"
            else 0
        )
        inventory_by_lab.setdefault(
            inventory.laboratory_id, {}
        )[inventory.equipment_type_id] = usable

    capabilities_by_project: dict[
        UUID, list[tuple[LabProjectCapability, Laboratory]]
    ] = {project_id: [] for project_id in ids}
    for capability, laboratory in capability_rows:
        capabilities_by_project[capability.project_id].append(
            (capability, laboratory)
        )

    for project_id in ids:
        project = projects_by_id.get(project_id)
        if project is None:
            issues.append(
                ProjectLabIssue(
                    project_id=project_id,
                    project_name="未知或停用项目",
                    laboratory_id=None,
                    laboratory_code=None,
                    message="实验项目不存在或已停用",
                )
            )
            continue
        requirements = requirements_by_project[project_id]
        if not any(item.required for item in requirements):
            issues.append(
                ProjectLabIssue(
                    project_id=project_id,
                    project_name=project.project_name,
                    laboratory_id=None,
                    laboratory_code=None,
                    message="未配置必需器材",
                )
            )
            continue
        capabilities = capabilities_by_project[project_id]
        if not capabilities:
            issues.append(
                ProjectLabIssue(
                    project_id=project_id,
                    project_name=project.project_name,
                    laboratory_id=None,
                    laboratory_code=None,
                    message="没有启用的项目—实验室能力记录",
                )
            )
            continue
        for capability, laboratory in capabilities:
            target_capacity = min(
                laboratory.safety_capacity,
                capability.effective_capacity,
            )
            target_capacity -= (
                target_capacity % project.default_group_size
            )
            blocking, reserve_shortages = assess_inventory_thresholds(
                target_capacity=target_capacity,
                group_size=project.default_group_size,
                requirements=requirements,
                usable_inventory=inventory_by_lab.get(laboratory.id, {}),
            )
            if blocking:
                missing_names = tuple(
                    item.equipment_name for item in blocking
                )
                issues.append(
                    ProjectLabIssue(
                        project_id=project_id,
                        project_name=project.project_name,
                        laboratory_id=laboratory.id,
                        laboratory_code=laboratory.lab_code,
                        message=(
                            "器材数量不足以满足项目核定容量："
                            + "、".join(missing_names)
                        ),
                        missing_equipment_names=missing_names,
                    )
                )
                continue
            calculation = calculate_effective_capacity(
                group_size=project.default_group_size,
                safety_capacity=laboratory.safety_capacity,
                capability_capacity=capability.effective_capacity,
                requirements=requirements,
                usable_inventory=inventory_by_lab.get(laboratory.id, {}),
            )
            if calculation.feasible:
                options[project_id].append(
                    ProjectLabOption(
                        project_id=project_id,
                        laboratory_id=laboratory.id,
                        laboratory_code=laboratory.lab_code,
                        effective_capacity=target_capacity,
                        limiting_equipment_ids=(
                            calculation.limiting_equipment_ids
                        ),
                    )
                )
                if reserve_shortages:
                    warnings.append(
                        ProjectLabWarning(
                            project_id=project_id,
                            project_name=project.project_name,
                            laboratory_id=laboratory.id,
                            laboratory_code=laboratory.lab_code,
                            code="RESERVE_SHORTAGE",
                            message=(
                                "器材够正常开课，但不足 2 个备用实验组"
                            ),
                            shortages=reserve_shortages,
                        )
                    )
                continue
            if calculation.invalid_requirement_ids:
                message = "器材需求的每组数量必须大于零"
            elif calculation.missing_equipment_names:
                message = (
                    "同一实验室缺少或可用数量不足的必需器材："
                    + "、".join(calculation.missing_equipment_names)
                )
            else:
                message = "安全容量或项目能力容量不足一组"
            issues.append(
                ProjectLabIssue(
                    project_id=project_id,
                    project_name=project.project_name,
                    laboratory_id=laboratory.id,
                    laboratory_code=laboratory.lab_code,
                    message=message,
                    missing_equipment_names=(
                        calculation.missing_equipment_names
                    ),
                )
            )

    for project_id, project_options in options.items():
        project_options.sort(
            key=lambda item: (-item.effective_capacity, item.laboratory_code)
        )
        if project_id in projects_by_id and not project_options:
            project = projects_by_id[project_id]
            issues.append(
                ProjectLabIssue(
                    project_id=project_id,
                    project_name=project.project_name,
                    laboratory_id=None,
                    laboratory_code=None,
                    message="没有一间实验室能独立满足全部必需器材",
                )
            )
    return ProjectResourceFeasibility(
        options=options,
        issues=issues,
        warnings=warnings,
    )


async def get_project_lab_options(
    session: AsyncSession,
    project_ids: list[UUID] | tuple[UUID, ...] | set[UUID],
) -> dict[UUID, list[ProjectLabOption]]:
    """排课使用的内部接口：返回每个项目的同室齐套候选实验室。"""

    result = await evaluate_project_lab_resources(session, project_ids)
    return result.options
