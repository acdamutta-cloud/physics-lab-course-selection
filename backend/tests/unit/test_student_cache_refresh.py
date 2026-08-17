import asyncio
from uuid import uuid4

import pytest

from app.services import student_cache_service


@pytest.mark.asyncio
async def test_after_commit_invalidates_then_rebuilds_dashboard_and_ai(monkeypatch):
    student_id, term_id = uuid4(), uuid4()
    events = []

    async def invalidate(_student_id, _term_id):
        events.append("invalidated")

    async def refresh(_student_id, _term_id, **kwargs):
        events.append(("refreshed", kwargs))

    monkeypatch.setattr(student_cache_service, "invalidate_student_views", invalidate)
    monkeypatch.setattr(student_cache_service, "refresh_student_caches", refresh)

    await student_cache_service.refresh_experiment_views_after_commit(
        student_id, term_id
    )
    for _ in range(20):
        if (student_id, term_id) not in student_cache_service._PENDING_REFRESHES:
            break
        await asyncio.sleep(0)

    assert events[0] == "invalidated"
    assert events[1][0] == "refreshed"
    assert events[1][1]["dashboard"] is True
    assert events[1][1]["ai_context"] is True
    assert events[1][1]["bitmap"] is False
    assert events[1][1]["force_refresh"] is True


@pytest.mark.asyncio
async def test_application_status_refresh_does_not_touch_dashboard(monkeypatch):
    student_id, term_id = uuid4(), uuid4()
    invalidated = []
    refreshed = []

    async def invalidate_ai(_student_id, _term_id):
        invalidated.append("ai")

    async def refresh(_student_id, _term_id, **kwargs):
        refreshed.append(kwargs)

    monkeypatch.setattr(student_cache_service, "invalidate_ai_context", invalidate_ai)
    monkeypatch.setattr(student_cache_service, "refresh_student_caches", refresh)

    await student_cache_service.refresh_experiment_views_after_commit(
        student_id, term_id, dashboard=False
    )
    for _ in range(20):
        if (student_id, term_id) not in student_cache_service._PENDING_REFRESHES:
            break
        await asyncio.sleep(0)

    assert invalidated == ["ai"]
    assert refreshed[0]["dashboard"] is False
    assert refreshed[0]["ai_context"] is True
    assert refreshed[0]["bitmap"] is False


@pytest.mark.asyncio
async def test_bitmap_force_refresh_builds_before_atomic_write(monkeypatch):
    student_id, term_id = uuid4(), uuid4()
    events = []

    class FakeResult:
        def one_or_none(self):
            return (object(), object())

    class FakeSession:
        async def execute(self, _statement):
            return FakeResult()

        async def get(self, _model, _identity):
            return object()

    class FakeFactory:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, *_args):
            return None

    async def profile(_student, _user):
        return object()

    async def bitmap_builder(_session, _profile):
        events.append("built")
        return {"data": "new"}

    async def write(key, value, ttl):
        events.append(("written", key, value, ttl))

    monkeypatch.setattr(student_cache_service, "AsyncSessionFactory", FakeFactory)
    monkeypatch.setattr(student_cache_service, "_profile", profile)
    monkeypatch.setattr(student_cache_service, "write_cache", write)
    monkeypatch.setattr(
        "app.api.routers.students._get_my_bitmap_uncached", bitmap_builder
    )

    await student_cache_service.refresh_student_caches(
        student_id,
        term_id,
        dashboard=False,
        ai_context=False,
        bitmap=True,
        force_bitmap=True,
    )

    assert events[0] == "built"
    assert events[1][0] == "written"
    assert events[1][2] == {"data": "new"}
