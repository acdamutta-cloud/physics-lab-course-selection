from app.services.equipment_usage_note_service import interpret_equipment_usage_note
from app.services.resource_capacity_service import (
    equipment_student_capacity,
    minimum_resource_capacity,
    relocation_requirement,
)


def test_interprets_two_students_per_device() -> None:
    for note in ("2人一台", "两人一台", "每台可供2名学生", "2人/台"):
        result = interpret_equipment_usage_note(note)
        assert result.sharing_rule_status == "CONFIRMED"
        assert result.students_per_unit == 2


def test_ambiguous_note_cannot_drive_automatic_capacity() -> None:
    result = interpret_equipment_usage_note("多人共用，轮流使用")
    assert result.sharing_rule_status == "AMBIGUOUS"
    assert result.students_per_unit is None


def test_equipment_capacity_applies_students_per_unit() -> None:
    assert equipment_student_capacity(
        usable_quantity=8,
        units_per_group=1,
        students_per_unit=2,
    ) == 16


def test_effective_capacity_uses_minimum_constraint() -> None:
    assert minimum_resource_capacity(
        laboratory_capacity=24,
        capability_capacity=20,
        equipment_capacities=[16, 18],
    ) == 16


def test_only_overflow_students_need_relocation() -> None:
    enough = relocation_requirement(selected_count=16, effective_capacity=16)
    assert enough["required_relocation_count"] == 0

    shortage = relocation_requirement(selected_count=20, effective_capacity=16)
    assert shortage == {
        "selected_count": 20,
        "effective_capacity": 16,
        "retained_count": 16,
        "required_relocation_count": 4,
    }
