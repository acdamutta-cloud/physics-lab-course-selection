from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.selection_plan import (
    OptionalProjectAlternative,
    SelectionPlanDraft,
    SelectionPlanItem,
)
from app.schemas.student_consultation import (
    RecommendationPlan,
    RecommendationSession,
    SelectionPreferences,
)
from app.services import selection_plan_service as service


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, key, value, **_kwargs):
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)


def recommendation(project_id=None, session_id=None, *, name="单摆实验", week=4):
    return RecommendationSession(
        session_id=session_id or uuid4(),
        project_id=project_id or uuid4(),
        project_name=name,
        course_name="大学物理实验",
        requirement_type="REQUIRED",
        category="MECHANICS",
        week_no=week,
        day_of_week=4,
        start_slot=5,
        end_slot=8,
        laboratory_name="基础实验室A101",
        campus_name="主校区",
        remaining=5,
        reasons=["符合偏好星期"],
    )


@pytest.mark.asyncio
async def test_create_plan_keeps_preferences_and_three_alternatives(monkeypatch):
    redis = FakeRedis()
    student_id, term_id = uuid4(), uuid4()
    selected = recommendation()
    alternatives = [
        recommendation(selected.project_id, week=week) for week in (5, 6, 7)
    ]

    async def eligible(*_args, **kwargs):
        return SimpleNamespace(eligible=True, violations=[])

    async def recommend(*_args, **kwargs):
        assert kwargs["limit"] == 3
        return alternatives

    monkeypatch.setattr(service, "check_selection_eligibility", eligible)
    monkeypatch.setattr(service, "recommend_project_session_alternatives", recommend)
    preferences = SelectionPreferences(
        avoid_evening=True, preferred_days=["周一", "周三"]
    )
    draft = await service.create_plan(
        redis,
        SimpleNamespace(),
        student_id=student_id,
        term=SimpleNamespace(id=term_id),
        plan=RecommendationPlan(name="推荐方案1", sessions=[selected]),
        preferences=preferences,
    )

    assert draft.preferences == preferences
    assert draft.items[0].alternatives == alternatives
    persisted = await service.get_plan(
        redis, student_id=student_id, plan_id=draft.plan_id
    )
    assert persisted.items[0].selected.session_id == selected.session_id


@pytest.mark.asyncio
async def test_update_item_only_accepts_displayed_candidate_and_keeps_old_choice(
    monkeypatch,
):
    redis = FakeRedis()
    student_id, term_id = uuid4(), uuid4()
    selected = recommendation()
    alternative = recommendation(selected.project_id, week=5)
    draft = SelectionPlanDraft(
        plan_id=uuid4(),
        student_id=student_id,
        term_id=term_id,
        name="推荐方案1",
        coverage_status="COMPLETE",
        preferences=SelectionPreferences(),
        items=[
            SelectionPlanItem(
                project_id=selected.project_id,
                selected=selected,
                alternatives=[alternative],
            )
        ],
    )
    await service._save(redis, draft)

    async def eligible(*_args, **_kwargs):
        return SimpleNamespace(eligible=True, violations=[])

    monkeypatch.setattr(service, "check_selection_eligibility", eligible)
    changed = await service.update_item(
        redis,
        SimpleNamespace(),
        student_id=student_id,
        plan_id=draft.plan_id,
        project_id=selected.project_id,
        session_id=alternative.session_id,
    )

    assert changed.items[0].selected.session_id == alternative.session_id
    assert changed.items[0].alternatives[0].session_id == selected.session_id
    assert changed.items[0].adjusted is True


@pytest.mark.asyncio
async def test_execute_keeps_success_and_recommends_after_failure(monkeypatch):
    redis = FakeRedis()
    student_id, term_id = uuid4(), uuid4()
    first, second = recommendation(name="单摆实验"), recommendation(name="光电效应")
    draft = SelectionPlanDraft(
        plan_id=uuid4(),
        student_id=student_id,
        term_id=term_id,
        name="推荐方案1",
        coverage_status="COMPLETE",
        preferences=SelectionPreferences(avoid_evening=True),
        items=[
            SelectionPlanItem(project_id=first.project_id, selected=first),
            SelectionPlanItem(project_id=second.project_id, selected=second),
        ],
        status="READY",
        confirmation_token="a" * 32,
    )
    await service._save(redis, draft)

    calls = 0

    async def select_session(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            result="ok" if calls == 1 else "full",
            message="选课成功。" if calls == 1 else "该场次名额已满。",
        )

    replacement = recommendation(second.project_id, week=8)

    async def recommend(*_args, **kwargs):
        assert kwargs["preferences"].avoid_evening is True
        return [replacement]

    monkeypatch.setattr(service.selection_service, "select_session", select_session)
    monkeypatch.setattr(service, "recommend_project_session_alternatives", recommend)
    result = await service.execute_plan(
        redis,
        SimpleNamespace(),
        student_id=student_id,
        term=SimpleNamespace(id=term_id),
        plan_id=draft.plan_id,
        confirmation_token="a" * 32,
    )

    assert result.succeeded == 1
    assert result.failed == 1
    assert result.plan.status == "PARTIAL"
    assert result.plan.items[0].status == "SUCCEEDED"
    assert result.plan.items[1].status == "FAILED"
    assert result.plan.items[1].selected.session_id == second.session_id
    assert result.plan.items[1].alternatives == [replacement]


@pytest.mark.asyncio
async def test_replace_optional_project_invalidates_confirmation(monkeypatch):
    redis = FakeRedis()
    student_id, term_id = uuid4(), uuid4()
    source = recommendation()
    source.requirement_type = "OPTIONAL"
    target = recommendation(name="光纤传输特性")
    target.requirement_type = "OPTIONAL"
    draft = SelectionPlanDraft(
        plan_id=uuid4(),
        student_id=student_id,
        term_id=term_id,
        name="推荐方案1",
        coverage_status="COMPLETE",
        preferences=SelectionPreferences(),
        items=[
            SelectionPlanItem(
                project_id=source.project_id,
                selected=source,
                original_project_id=source.project_id,
                original_project_name=source.project_name,
                project_alternatives=[
                    OptionalProjectAlternative(
                        project_id=target.project_id,
                        project_name=target.project_name,
                        category=target.category,
                        selected=target,
                    )
                ],
            )
        ],
        status="PARTIAL",
        confirmation_token="b" * 32,
    )
    await service._save(redis, draft)

    async def eligible(*_args, **_kwargs):
        return SimpleNamespace(eligible=True, violations=[])

    monkeypatch.setattr(service, "check_selection_eligibility", eligible)
    changed = await service.replace_optional_project(
        redis,
        SimpleNamespace(),
        student_id=student_id,
        plan_id=draft.plan_id,
        project_id=source.project_id,
        target_project_id=target.project_id,
        session_id=target.session_id,
    )

    assert changed.items[0].project_id == target.project_id
    assert changed.items[0].original_project_name == source.project_name
    assert changed.items[0].project_adjusted is True
    assert changed.confirmation_token is None
    assert changed.status == "EDITING"
    assert changed.version == 2
