from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.schemas.training_plan import (
    CreateCourseRequest,
    CreateTrainingPlanRequest,
)
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


@pytest.mark.asyncio
async def test_create_course_persists_and_returns_course_info(monkeypatch) -> None:
    session = AsyncMock()
    actor_id = uuid4()
    course = SimpleNamespace(
        id=uuid4(),
        course_code="PHYS-EXP-401",
        course_name="综合物理实验",
        course_type="EXPERIMENT",
    )
    monkeypatch.setattr(
        service.tp_crud,
        "create_experiment_course",
        AsyncMock(return_value=course),
    )
    monkeypatch.setattr(
        service.tp_crud,
        "add_operation_log",
        AsyncMock(),
    )

    result = await service.create_course(
        session,
        CreateCourseRequest(
            course_name="综合物理实验",
            credits="1.5",
            default_slots=4,
        ),
        actor_id,
    )

    assert result.id == course.id
    assert result.course_type == "EXPERIMENT"
    create_call = service.tp_crud.create_experiment_course.await_args.kwargs
    assert create_call["course_code"].startswith("EXP-")
    assert len(create_call["course_code"]) == 16
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_course_project_removes_unreferenced_project(
    monkeypatch,
) -> None:
    session = AsyncMock()
    project = SimpleNamespace(
        id=uuid4(),
        course_id=uuid4(),
        project_code="PHYS-EXP-401-P01",
        project_name="综合测量",
        status="ACTIVE",
    )
    session.scalar.side_effect = [project, 0, 0, 0, 0]
    monkeypatch.setattr(
        service.tp_crud,
        "add_operation_log",
        AsyncMock(),
    )

    await service.delete_course_project(
        session,
        project.course_id,
        project.id,
        uuid4(),
    )

    session.delete.assert_awaited_once_with(project)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_course_project_rejects_referenced_project() -> None:
    session = AsyncMock()
    project = SimpleNamespace(
        id=uuid4(),
        course_id=uuid4(),
        project_code="PHYS-EXP-401-P01",
        project_name="综合测量",
        status="ACTIVE",
    )
    session.scalar.side_effect = [project, 1, 0, 0, 0]

    with pytest.raises(service.TrainingPlanError, match="不能直接删除"):
        await service.delete_course_project(
            session,
            project.course_id,
            project.id,
            uuid4(),
        )

    session.delete.assert_not_awaited()
