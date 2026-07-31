from uuid import uuid4

from app.services.resource_feasibility_service import (
    EquipmentRequirementInput,
    assess_inventory_thresholds,
    calculate_effective_capacity,
)


def required(name: str, units: int = 1) -> EquipmentRequirementInput:
    return EquipmentRequirementInput(
        equipment_type_id=uuid4(),
        equipment_name=name,
        units_per_group=units,
        required=True,
    )


def optional(name: str) -> EquipmentRequirementInput:
    return EquipmentRequirementInput(
        equipment_type_id=uuid4(),
        equipment_name=name,
        units_per_group=1,
        required=False,
    )


def test_capacity_is_minimum_of_room_capability_and_equipment() -> None:
    oscilloscope = required("示波器")
    generator = required("信号发生器")
    result = calculate_effective_capacity(
        group_size=2,
        safety_capacity=24,
        capability_capacity=20,
        requirements=[oscilloscope, generator],
        usable_inventory={
            oscilloscope.equipment_type_id: 10,
            generator.equipment_type_id: 6,
        },
    )
    assert result.feasible
    assert result.effective_capacity == 12
    assert result.limiting_equipment_ids == (
        generator.equipment_type_id,
    )


def test_missing_required_equipment_blocks_lab() -> None:
    track = required("气垫导轨")
    timer = required("光电计时器")
    result = calculate_effective_capacity(
        group_size=2,
        safety_capacity=24,
        capability_capacity=24,
        requirements=[track, timer],
        usable_inventory={track.equipment_type_id: 6},
    )
    assert not result.feasible
    assert result.effective_capacity == 0
    assert result.missing_equipment_names == ("光电计时器",)


def test_quantity_shortage_blocks_even_when_equipment_record_exists() -> None:
    meters = required("数字万用表", units=2)
    result = calculate_effective_capacity(
        group_size=2,
        safety_capacity=20,
        capability_capacity=20,
        requirements=[meters],
        usable_inventory={meters.equipment_type_id: 1},
    )
    assert not result.feasible
    assert result.missing_equipment_names == ("数字万用表",)


def test_optional_equipment_does_not_block_lab() -> None:
    apparatus = required("专用实验仪")
    spare = optional("备用示波器")
    result = calculate_effective_capacity(
        group_size=2,
        safety_capacity=16,
        capability_capacity=16,
        requirements=[apparatus, spare],
        usable_inventory={apparatus.equipment_type_id: 4},
    )
    assert result.feasible
    assert result.effective_capacity == 8


def test_partial_inventories_from_two_labs_cannot_be_combined() -> None:
    apparatus = required("实验仪")
    meter = required("测量仪")
    lab_a = calculate_effective_capacity(
        group_size=2,
        safety_capacity=20,
        capability_capacity=20,
        requirements=[apparatus, meter],
        usable_inventory={apparatus.equipment_type_id: 10},
    )
    lab_b = calculate_effective_capacity(
        group_size=2,
        safety_capacity=20,
        capability_capacity=20,
        requirements=[apparatus, meter],
        usable_inventory={meter.equipment_type_id: 10},
    )
    assert not lab_a.feasible
    assert not lab_b.feasible


def test_safety_capacity_is_rounded_down_to_complete_groups() -> None:
    apparatus = required("实验仪")
    result = calculate_effective_capacity(
        group_size=2,
        safety_capacity=15,
        capability_capacity=20,
        requirements=[apparatus],
        usable_inventory={apparatus.equipment_type_id: 10},
    )
    assert result.effective_capacity == 14


def test_single_person_inventory_keeps_two_spare_sets() -> None:
    apparatus = required("单人实验仪")
    blocking, warning = assess_inventory_thresholds(
        target_capacity=20,
        group_size=1,
        requirements=[apparatus],
        usable_inventory={apparatus.equipment_type_id: 21},
    )
    assert blocking == ()
    assert warning[0].base_required_quantity == 20
    assert warning[0].recommended_quantity == 22


def test_inventory_below_teaching_need_blocks_before_reserve_check() -> None:
    apparatus = required("单人实验仪")
    blocking, warning = assess_inventory_thresholds(
        target_capacity=20,
        group_size=1,
        requirements=[apparatus],
        usable_inventory={apparatus.equipment_type_id: 19},
    )
    assert blocking[0].base_required_quantity == 20
    assert warning == ()


def test_multi_person_group_reserve_uses_complete_groups() -> None:
    apparatus = required("分组实验仪", units=2)
    blocking, warning = assess_inventory_thresholds(
        target_capacity=21,
        group_size=4,
        requirements=[apparatus],
        usable_inventory={apparatus.equipment_type_id: 16},
    )
    assert blocking == ()
    assert warning == ()
