import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_engine
from app.models.audit import OperationLog
from app.models.curriculum import ExperimentCourse, ExperimentProject
from app.models.identity import Major, UserAccount
from app.schemas.training_plan import (
    CreateTrainingPlanRequest,
    UpdateTrainingPlanRequest,
)
from app.services import training_plan_service as service


@pytest.mark.asyncio
async def test_training_plan_full_lifecycle_rolls_back(monkeypatch) -> None:
    try:
        connection = await async_engine.connect()
    except (OSError, SQLAlchemyError) as error:
        pytest.skip(f"PostgreSQL 不可用：{error}")

    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False)

    async def flush_instead_of_commit() -> None:
        await session.flush()

    monkeypatch.setattr(session, "commit", flush_instead_of_commit)
    try:
        major = (
            await session.execute(
                select(Major).where(Major.status == "ACTIVE").limit(1)
            )
        ).scalar_one()
        course = (
            await session.execute(
                select(ExperimentCourse)
                .where(ExperimentCourse.status == "ACTIVE")
                .limit(1)
            )
        ).scalar_one()
        project = (
            await session.execute(
                select(ExperimentProject)
                .where(
                    ExperimentProject.course_id == course.id,
                    ExperimentProject.status == "ACTIVE",
                )
                .limit(1)
            )
        ).scalar_one()
        actor_id = await session.scalar(
            select(UserAccount.id)
                .where(
                    UserAccount.user_type == "ADMIN",
                    UserAccount.status == "ACTIVE",
                )
                .limit(1)
        )
        if actor_id is None:
            pytest.skip("数据库中没有可用管理员账号")
        request_data = {
            "major_id": major.id,
            "enrollment_year": 2099,
            "courses": [
                {
                    "course_id": course.id,
                    "study_year": 1,
                    "semester_no": 1,
                    "required_project_count": 1,
                    "optional_project_min_count": 0,
                    "order_rule_text": "完成必做项目后方可结课",
                    "projects": [
                        {
                            "project_id": project.id,
                            "requirement_type": "REQUIRED",
                            "display_order": 1,
                        }
                    ],
                }
            ],
        }

        created = await service.create_plan(
            session,
            CreateTrainingPlanRequest.model_validate(request_data),
            actor_id,
        )
        updated = await service.update_plan(
            session,
            created.id,
            UpdateTrainingPlanRequest.model_validate(request_data),
            actor_id,
        )
        first_publish = await service.publish_plan(
            session, updated.id, actor_id
        )
        copied = await service.copy_plan_to_draft(
            session, first_publish.id, actor_id
        )
        second_publish = await service.publish_plan(
            session, copied.id, actor_id
        )

        assert second_publish.status == "PUBLISHED"
        assert second_publish.version_no == first_publish.version_no + 1
        audit_count = await session.scalar(
            select(func.count(OperationLog.id)).where(
                OperationLog.object_id.in_([created.id, copied.id])
            )
        )
        assert int(audit_count or 0) >= 5
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
