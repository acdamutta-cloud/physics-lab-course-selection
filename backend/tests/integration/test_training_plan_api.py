from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.db.session import get_db_session
from app.main import app
from app.schemas.auth import UserProfile

client = TestClient(app)


def test_training_plan_list_requires_login() -> None:
    response = client.get("/api/v1/training-plans")
    assert response.status_code == 401


def test_non_admin_cannot_create_training_plan() -> None:
    async def fake_user() -> UserProfile:
        return UserProfile(
            id=uuid4(),
            login_name="teacher",
            user_type="TEACHER",
            name="测试教师",
        )

    async def fake_session():
        yield AsyncMock()

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db_session] = fake_session
    try:
        response = client.post(
            "/api/v1/training-plans",
            json={
                "major_id": str(uuid4()),
                "enrollment_year": 2024,
                "courses": [
                    {
                        "course_id": str(uuid4()),
                        "study_year": 1,
                        "semester_no": 1,
                        "projects": [],
                    }
                ],
            },
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
