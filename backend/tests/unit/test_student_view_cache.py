import asyncio
from uuid import uuid4

import pytest

from app.cache import student_views


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.fail_reads = False

    async def get(self, key):
        if self.fail_reads:
            raise OSError("redis unavailable")
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    async def eval(self, script, count, key, token):
        if self.values.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0


def test_keys_isolate_students_and_terms():
    student_a, student_b, term_a, term_b = (uuid4() for _ in range(4))
    assert student_views.dashboard_key(student_a, term_a) != student_views.dashboard_key(
        student_b, term_a
    )
    assert student_views.bitmap_key(student_a, term_a) != student_views.bitmap_key(
        student_a, term_b
    )


@pytest.mark.asyncio
async def test_cache_hit_matches_built_value():
    redis = FakeRedis()
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        return {"courses": [{"name": "大学物理实验"}], "bitmap_data": None}

    first = await student_views.get_or_build("student:test", ttl=60, builder=build, redis=redis)
    second = await student_views.get_or_build("student:test", ttl=60, builder=build, redis=redis)
    assert first == second
    assert calls == 1


@pytest.mark.asyncio
async def test_bad_cache_falls_back_to_builder():
    redis = FakeRedis()
    redis.values["student:test"] = "{broken"

    async def build():
        return {"ok": True}

    assert await student_views.get_or_build(
        "student:test", ttl=60, builder=build, redis=redis
    ) == {"ok": True}


@pytest.mark.asyncio
async def test_redis_failure_falls_back_to_builder():
    redis = FakeRedis()
    redis.fail_reads = True

    async def build():
        return {"source": "postgres"}

    assert await student_views.get_or_build(
        "student:test", ttl=60, builder=build, redis=redis
    ) == {"source": "postgres"}


@pytest.mark.asyncio
async def test_single_flight_builds_once():
    redis = FakeRedis()
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {"value": 1}

    values = await asyncio.gather(
        *(student_views.get_or_build("student:test", ttl=60, builder=build, redis=redis) for _ in range(5))
    )
    assert values == [{"value": 1}] * 5
    assert calls == 1


def test_dashboard_split_and_merge_is_field_equal():
    original = {
        "profile": {"student_no": "S001"},
        "term": {"semester_no": 1},
        "courses": [
            {
                "course_name": "Physics Lab",
                "completion_status": "NOT_TAKEN",
                "prerequisites_passed": ["Math"],
                "prerequisites_failed": [],
                "projects": [{"project_id": "p1", "available_sessions": []}],
            }
        ],
        "selection": {"selected_count": 1},
        "prerequisites": {"passed": ["Math"], "failed": []},
        "next_lab": {"session_id": "s1"},
        "selected_sessions": [{"session_id": "s1"}],
        "bitmap_data": "AA==",
    }
    static, dynamic = student_views.split_dashboard(original)
    assert student_views.merge_dashboard(static, dynamic) == original


def test_dashboard_summary_preserves_home_data_and_removes_unselected_cards():
    original = {
        "profile": {"student_no": "S001"},
        "term": {"semester_no": 1},
        "courses": [
            {
                "course_name": "Physics Lab",
                "projects": [
                    {
                        "project_id": "p1",
                        "available_sessions": [{"id": "s1"}, {"id": "s2"}],
                    }
                ],
            }
        ],
        "selection": {"selected_count": 1},
        "prerequisites": {"passed": [], "failed": []},
        "next_lab": {"session_id": "s1"},
        "selected_sessions": [{"session_id": "s1"}],
        "bitmap_data": "AA==",
    }

    summary = student_views.dashboard_summary(original)

    assert summary["profile"] == original["profile"]
    assert summary["selection"] == original["selection"]
    assert summary["selected_sessions"] == original["selected_sessions"]
    assert summary["courses"][0]["projects"][0]["available_sessions"] == [
        {"id": "s1"}
    ]
    assert "bitmap_data" not in summary


def test_dashboard_timetable_is_field_equal_to_existing_dashboard_fields():
    original = {
        "term": {"academic_year": "2026-2027", "semester_no": 1},
        "selected_sessions": [{"session_id": "s1", "week_no": 3}],
        "courses": [{"course_name": "not part of timetable"}],
    }

    assert student_views.dashboard_timetable(original) == {
        "term": original["term"],
        "selected_sessions": original["selected_sessions"],
    }


@pytest.mark.asyncio
async def test_experiment_invalidation_keeps_base_bitmap(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(student_views, "get_redis_client", lambda: redis)
    student_id, term_id = uuid4(), uuid4()
    bitmap = student_views.bitmap_key(student_id, term_id)
    redis.values[bitmap] = '{"data":"AA=="}'
    redis.values[student_views.dashboard_dynamic_key(student_id, term_id)] = "{}"
    redis.values[student_views.dashboard_summary_key(student_id, term_id)] = "{}"
    redis.values[student_views.timetable_key(student_id, term_id)] = "{}"
    redis.values[student_views.ai_context_key(student_id, term_id)] = "{}"

    await student_views.invalidate_student_views(student_id, term_id)

    assert bitmap in redis.values
    assert student_views.dashboard_dynamic_key(student_id, term_id) not in redis.values
    assert student_views.dashboard_summary_key(student_id, term_id) not in redis.values
    assert student_views.timetable_key(student_id, term_id) not in redis.values
    assert student_views.ai_context_key(student_id, term_id) not in redis.values
