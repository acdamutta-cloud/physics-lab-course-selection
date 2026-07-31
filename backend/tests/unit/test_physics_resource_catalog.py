from app.data.physics_resources import (
    LAB_INVENTORY_SPECS,
    PROJECT_RESOURCE_SPECS,
    validate_catalog,
)


def equipment_codes(project_code: str) -> set[str]:
    return {
        item.equipment_code
        for item in PROJECT_RESOURCE_SPECS[project_code].requirements
    }


def test_catalog_has_30_projects_and_is_self_consistent() -> None:
    assert len(PROJECT_RESOURCE_SPECS) == 30
    assert validate_catalog() == []


def test_every_assigned_lab_independently_contains_required_equipment() -> None:
    for project in PROJECT_RESOURCE_SPECS.values():
        for lab_code in project.lab_codes:
            inventory = LAB_INVENTORY_SPECS[lab_code]
            for requirement in project.requirements:
                if requirement.required:
                    assert (
                        inventory[requirement.equipment_code][1]
                        >= requirement.units_per_group
                    )


def test_mechanics_projects_no_longer_share_category_bundle() -> None:
    pendulum = equipment_codes("DEMO-PHY101-P02")
    young = equipment_codes("DEMO-PHY101-P04")
    assert "PHY-PENDULUM" in pendulum
    assert "PHY-AIR-TRACK" not in pendulum
    assert "PHY-YOUNG" in young
    assert "PHY-AIR-TRACK" not in young


def test_modern_projects_use_their_own_specialized_equipment() -> None:
    nmr = equipment_codes("DEMO-PHY301-P06")
    xray = equipment_codes("DEMO-PHY301-P10")
    assert "PHY-NMR" in nmr
    assert "PHY-PHOTOELECTRIC" not in nmr
    assert "PHY-XRAY" in xray
    assert "PHY-PHOTOELECTRIC" not in xray
