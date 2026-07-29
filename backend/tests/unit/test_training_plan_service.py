from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.schemas.training_plan import CreateTrainingPlanRequest
from app.services import training_plan_service as service


def payload() -> CreateTrainingPlanRequest:
    return CreateTrainingPlanRequest(
        major_id=uuid4(),
        enrollment_year=2024,
        courses=[
            {
                "course_id": uuid4(),
                "study_year": 1,
                "semester_no": 1,
                "projects": [],
            }
        ],
    )


@pytest.mark.asyncio
async def test_update_rejects_published_plan(monkeypatch) -> None:
    session = AsyncMock()
    plan = SimpleNamespace(status="PUBLISHED")
    monkeypatch.setattr(service, "_validate_payload", AsyncMock())
    monkeypatch.setattr(
        service.tp_crud, "get_plan_detail", AsyncMock(return_value=plan)
    )

    with pytest.raises(service.TrainingPlanError, match="不能原地修改"):
        await service.update_plan(session, uuid4(), payload(), uuid4())


@pytest.mark.asyncio
async def test_copy_rejects_existing_draft(monkeypatch) -> None:
    session = AsyncMock()
    plan = SimpleNamespace(status="DRAFT")
    monkeypatch.setattr(
        service.tp_crud, "get_plan_detail", AsyncMock(return_value=plan)
    )

    with pytest.raises(service.TrainingPlanError, match="已经是草稿"):
        await service.copy_plan_to_draft(session, uuid4(), uuid4())


def test_completeness_requires_projects_counts_and_order_rule() -> None:
    complete_course = SimpleNamespace(
        projects=[
            SimpleNamespace(requirement_type="REQUIRED"),
            SimpleNamespace(requirement_type="OPTIONAL"),
        ],
        required_project_count=1,
        optional_project_min_count=1,
        order_rule_text="先完成必做项目",
    )
    assert service._is_complete(SimpleNamespace(courses=[complete_course]))

    incomplete_course = SimpleNamespace(
        projects=complete_course.projects,
        required_project_count=1,
        optional_project_min_count=1,
        order_rule_text="",
    )
    assert not service._is_complete(
        SimpleNamespace(courses=[incomplete_course])
    )
