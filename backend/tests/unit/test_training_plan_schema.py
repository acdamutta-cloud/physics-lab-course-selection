from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.training_plan import (
    CreateProjectRequest,
    CreateTrainingPlanRequest,
    TrainingPlanCourseIn,
    TrainingPlanProjectIn,
)


def make_course(**overrides) -> TrainingPlanCourseIn:
    project_id = uuid4()
    data = {
        "course_id": uuid4(),
        "study_year": 1,
        "semester_no": 1,
        "required_project_count": 1,
        "optional_project_min_count": 0,
        "projects": [
            {
                "project_id": project_id,
                "requirement_type": "REQUIRED",
                "display_order": 1,
            }
        ],
    }
    data.update(overrides)
    return TrainingPlanCourseIn.model_validate(data)


def test_plan_requires_at_least_one_course() -> None:
    with pytest.raises(ValidationError, match="List should have at least 1 item"):
        CreateTrainingPlanRequest(
            major_id=uuid4(), enrollment_year=2024, courses=[]
        )


def test_plan_rejects_duplicate_courses() -> None:
    course_id = uuid4()
    first = make_course(course_id=course_id)
    second = make_course(course_id=course_id)
    with pytest.raises(ValidationError, match="不能重复配置课程"):
        CreateTrainingPlanRequest(
            major_id=uuid4(),
            enrollment_year=2024,
            courses=[first, second],
        )


def test_course_rejects_duplicate_projects() -> None:
    project_id = uuid4()
    with pytest.raises(ValidationError, match="不能重复配置实验项目"):
        make_course(
            required_project_count=1,
            projects=[
                TrainingPlanProjectIn(project_id=project_id),
                TrainingPlanProjectIn(project_id=project_id),
            ],
        )


def test_course_rejects_required_count_over_configured_projects() -> None:
    with pytest.raises(ValidationError, match="必做项目数量不能超过"):
        make_course(required_project_count=2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("study_year", 0),
        ("study_year", 7),
        ("semester_no", 0),
        ("semester_no", 4),
        ("course_nature", "UNKNOWN"),
    ],
)
def test_course_rejects_invalid_ranges_and_enum(field: str, value) -> None:
    with pytest.raises(ValidationError):
        make_course(**{field: value})


def test_custom_project_requires_confirmed_business_fields() -> None:
    request = CreateProjectRequest(
        project_code="PHYS-EXP-001",
        project_name="测试实验项目",
        category="OTHER",
        required_slots=4,
        default_group_size=2,
        historical_selection_ratio=Decimal("0.5000"),
    )
    assert request.historical_selection_ratio == Decimal("0.5000")
    assert request.group_mode == "GROUP"

    with pytest.raises(ValidationError):
        CreateProjectRequest(
            project_code="PHYS-EXP-002",
            project_name="比例错误",
            category="OTHER",
            required_slots=4,
            default_group_size=2,
            historical_selection_ratio=Decimal("1.1000"),
        )


def test_project_group_mode_controls_group_size() -> None:
    individual = CreateProjectRequest(
        project_code="PHYS-INDIVIDUAL",
        project_name="单人实验",
        category="OTHER",
        required_slots=4,
        group_mode="INDIVIDUAL",
        default_group_size=8,
        historical_selection_ratio=Decimal("0.5000"),
    )
    assert individual.default_group_size == 1

    with pytest.raises(ValidationError):
        CreateProjectRequest(
            project_code="PHYS-GROUP",
            project_name="多人实验",
            category="OTHER",
            required_slots=4,
            group_mode="GROUP",
            default_group_size=1,
            historical_selection_ratio=Decimal("0.5000"),
        )
