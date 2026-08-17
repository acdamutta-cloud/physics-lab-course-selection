from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routers import students
from app.schemas.auth import UserProfile
from app.schemas.student_consultation import ConsultationRequest
from app.services.student_ai_concurrency import StudentAIConcurrencyError


def _profile() -> UserProfile:
    return UserProfile(
        id=uuid4(),
        login_name="D2024010001",
        user_type="STUDENT",
        student_id=uuid4(),
    )


def _request() -> ConsultationRequest:
    return ConsultationRequest(
        messages=[{"role": "user", "content": "请查询我的实验课表"}]
    )


@pytest.mark.asyncio
async def test_ai_limit_rejects_before_active_term_database_query(monkeypatch):
    events: list[str] = []

    class RejectingLease:
        def __init__(self, _student_id):
            events.append("lease-created")

        async def acquire(self):
            events.append("lease-rejected")
            raise StudentAIConcurrencyError("busy")

    term_query = AsyncMock()
    monkeypatch.setattr(students, "StudentAILease", RejectingLease)
    monkeypatch.setattr(students, "get_or_create_active_term", term_query)

    with pytest.raises(HTTPException) as captured:
        await students.consult_student_agent(_request(), object(), _profile())

    assert captured.value.status_code == 429
    assert events == ["lease-created", "lease-rejected"]
    term_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_success_releases_lease(monkeypatch):
    events: list[str] = []

    class AcceptedLease:
        def __init__(self, _student_id):
            events.append("lease-created")

        async def acquire(self):
            events.append("lease-acquired")

        async def release(self):
            events.append("lease-released")

    async def active_term(_session):
        events.append("term-loaded")
        return SimpleNamespace(id=uuid4())

    async def invoke(**_kwargs):
        events.append("graph-invoked")
        return {
            "intent": "BASIC_INFO_QUERY",
            "answer": "测试回答",
            "cards": [],
            "warnings": [],
            "unknowns": [],
        }

    monkeypatch.setattr(students, "StudentAILease", AcceptedLease)
    monkeypatch.setattr(students, "get_or_create_active_term", active_term)
    monkeypatch.setattr(students, "invoke_registered_graph", invoke)

    response = await students.consult_student_agent(_request(), object(), _profile())

    assert response.answer == "测试回答"
    assert events == [
        "lease-created",
        "lease-acquired",
        "term-loaded",
        "graph-invoked",
        "lease-released",
    ]
