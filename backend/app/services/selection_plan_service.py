"""Short-lived, explicitly confirmed batch selection plans."""

from __future__ import annotations

import secrets
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import (
    AcademicTerm,
    ExperimentProject,
    ProjectOrderConstraint,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingPlanProject,
)
from app.models.enrollment import StudentProjectRecord
from app.models.identity import Student
from app.models.scheduling import ExperimentSession
from app.schemas.selection_plan import (
    OptionalProjectAlternative,
    SelectionPlanDraft,
    SelectionPlanExecutionResult,
    SelectionPlanItem,
    SelectionPlanPreview,
)
from app.schemas.student_consultation import RecommendationPlan, SelectionPreferences
from app.services import selection_service
from app.services.student_consultation_service import (
    check_selection_eligibility,
    recommend_project_session_alternatives,
    session_end_ordinal,
    session_start_ordinal,
    sessions_overlap,
)

PLAN_TTL_SECONDS = 30 * 60


def _key(student_id: UUID, plan_id: UUID) -> str:
    return f"student:selection-plan:{student_id}:{plan_id}"


async def _save(redis: Redis, draft: SelectionPlanDraft) -> None:
    await redis.set(
        _key(draft.student_id, draft.plan_id),
        draft.model_dump_json(),
        ex=PLAN_TTL_SECONDS,
    )


async def get_plan(
    redis: Redis, *, student_id: UUID, plan_id: UUID
) -> SelectionPlanDraft:
    raw = await redis.get(_key(student_id, plan_id))
    if raw is None:
        raise LookupError("选课方案不存在或已经过期，请重新生成。")
    return SelectionPlanDraft.model_validate_json(raw)


async def create_plan(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    plan: RecommendationPlan,
    preferences: SelectionPreferences,
) -> SelectionPlanDraft:
    items: list[SelectionPlanItem] = []
    selected_ids = {item.session_id for item in plan.sessions}
    for item in plan.sessions:
        eligibility = await check_selection_eligibility(
            db, student_id=student_id, session_id=item.session_id
        )
        if not eligibility.eligible:
            raise ValueError(f"“{item.project_name}”当前已不可选，请重新生成方案。")
        alternatives = await recommend_project_session_alternatives(
            db,
            student_id=student_id,
            term=term,
            project_id=item.project_id,
            preferences=preferences,
            excluded_session_ids=selected_ids,
            plan_session_ids=selected_ids - {item.session_id},
            limit=3,
        )
        items.append(
            SelectionPlanItem(
                project_id=item.project_id,
                selected=item,
                alternatives=alternatives,
                original_project_id=item.project_id,
                original_project_name=item.project_name,
            )
        )
    draft = SelectionPlanDraft(
        plan_id=uuid4(),
        student_id=student_id,
        term_id=term.id,
        name=plan.name,
        coverage_status=plan.coverage_status,
        preferences=preferences,
        items=items,
        retained_selections=plan.retained_selections,
        reasons=plan.reasons,
        warnings=plan.warnings,
    )
    await _save(redis, draft)
    return draft


async def recommend_optional_project_replacements(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    plan_id: UUID,
    project_id: UUID,
    limit: int = 3,
) -> tuple[SelectionPlanDraft, list[OptionalProjectAlternative]]:
    draft = await get_plan(redis, student_id=student_id, plan_id=plan_id)
    if draft.status not in {"EDITING", "PARTIAL"}:
        raise ValueError("当前方案状态不允许更换选做项目。")
    item = next(
        (value for value in draft.items if value.project_id == project_id), None
    )
    if item is None:
        raise LookupError("方案中不存在该实验项目。")
    if item.selected.requirement_type != "OPTIONAL":
        raise ValueError("只有选做项目可以更换为其他项目。")
    source_project = await db.get(ExperimentProject, project_id)
    student = await db.get(Student, student_id)
    if source_project is None or student is None:
        raise LookupError("未找到当前实验项目或学生信息。")

    occupied_project_ids = {value.project_id for value in draft.items}
    occupied_project_ids.update(value.project_id for value in draft.retained_selections)
    occupied_project_ids.update(
        (
            await db.execute(
                select(StudentProjectRecord.project_id).where(
                    StudentProjectRecord.student_id == student_id,
                    StudentProjectRecord.term_id == term.id,
                    StudentProjectRecord.status.in_(
                        {"SELECTED", "COMPLETED", "ABSENT", "MAKEUP_PENDING"}
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    occupied_project_ids.discard(project_id)
    target_projects = list(
        (
            await db.execute(
                select(ExperimentProject)
                .join(
                    TrainingPlanProject,
                    TrainingPlanProject.project_id == ExperimentProject.id,
                )
                .join(
                    TrainingPlanCourse,
                    TrainingPlanCourse.id == TrainingPlanProject.plan_course_id,
                )
                .join(TrainingPlan, TrainingPlan.id == TrainingPlanCourse.plan_id)
                .where(
                    TrainingPlan.major_id == student.major_id,
                    TrainingPlan.enrollment_year == student.enrollment_year,
                    TrainingPlan.status == "PUBLISHED",
                    TrainingPlanCourse.course_id == source_project.course_id,
                    TrainingPlanProject.requirement_type == "OPTIONAL",
                    ExperimentProject.id != project_id,
                    ExperimentProject.id.notin_(occupied_project_ids),
                    ExperimentProject.status == "ACTIVE",
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    plan_session_ids = {
        value.selected.session_id
        for value in draft.items
        if value.project_id != project_id
    }
    candidates: list[OptionalProjectAlternative] = []
    for target in target_projects:
        sessions = await recommend_project_session_alternatives(
            db,
            student_id=student_id,
            term=term,
            project_id=target.id,
            preferences=draft.preferences,
            plan_session_ids=plan_session_ids,
            limit=4,
        )
        if not sessions:
            continue
        candidates.append(
            OptionalProjectAlternative(
                project_id=target.id,
                project_name=target.project_name,
                category=target.category,
                selected=sessions[0],
                alternatives=sessions[1:4],
                reasons=sessions[0].reasons,
                warnings=sessions[0].warnings,
            )
        )
    candidates.sort(
        key=lambda value: (
            -value.selected.preference_score,
            -value.selected.remaining,
            value.project_name,
        )
    )
    item.project_alternatives = candidates[: max(1, limit)]
    await _save(redis, draft)
    return draft, item.project_alternatives


async def replace_optional_project(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    plan_id: UUID,
    project_id: UUID,
    target_project_id: UUID,
    session_id: UUID,
) -> SelectionPlanDraft:
    draft = await get_plan(redis, student_id=student_id, plan_id=plan_id)
    if draft.status not in {"EDITING", "PARTIAL"}:
        raise ValueError("当前方案状态不允许更换选做项目。")
    item = next(
        (value for value in draft.items if value.project_id == project_id), None
    )
    if item is None:
        raise LookupError("方案中不存在该实验项目。")
    if item.selected.requirement_type != "OPTIONAL":
        raise ValueError("只有选做项目可以更换为其他项目。")
    project_candidate = next(
        (
            value
            for value in item.project_alternatives
            if value.project_id == target_project_id
        ),
        None,
    )
    if project_candidate is None:
        raise ValueError("目标项目不属于本轮已展示的推荐范围。")
    session_candidate = next(
        (
            value
            for value in [project_candidate.selected, *project_candidate.alternatives]
            if value.session_id == session_id
        ),
        None,
    )
    if session_candidate is None:
        raise ValueError("目标场次不属于该项目已展示的候选范围。")
    eligibility = await check_selection_eligibility(
        db, student_id=student_id, session_id=session_id
    )
    if not eligibility.eligible:
        raise ValueError("目标项目场次当前已不可选，请刷新推荐。")
    if any(
        value.project_id == target_project_id and value is not item
        for value in draft.items
    ):
        raise ValueError("目标选做项目已经存在于当前方案。")

    original_project_id = item.original_project_id or item.project_id
    original_project_name = item.original_project_name or item.selected.project_name
    item.project_id = target_project_id
    item.selected = session_candidate
    item.alternatives = [
        value
        for value in [project_candidate.selected, *project_candidate.alternatives]
        if value.session_id != session_id
    ][:3]
    item.original_project_id = original_project_id
    item.original_project_name = original_project_name
    item.project_alternatives = []
    item.adjusted = True
    item.project_adjusted = target_project_id != original_project_id
    item.status = "PENDING"
    item.result_message = None
    draft.version += 1
    draft.status = "EDITING"
    draft.confirmation_token = None
    await _save(redis, draft)
    return draft


async def update_item(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    plan_id: UUID,
    project_id: UUID,
    session_id: UUID,
) -> SelectionPlanDraft:
    draft = await get_plan(redis, student_id=student_id, plan_id=plan_id)
    if draft.status not in {"EDITING", "PARTIAL"}:
        raise ValueError("当前方案状态不允许调整场次。")
    item = next(
        (value for value in draft.items if value.project_id == project_id), None
    )
    if item is None:
        raise LookupError("方案中不存在该实验项目。")
    candidate = next(
        (
            value
            for value in [item.selected, *item.alternatives]
            if value.session_id == session_id
        ),
        None,
    )
    if candidate is None:
        raise ValueError("目标场次不属于已展示并校验的候选范围。")
    eligibility = await check_selection_eligibility(
        db, student_id=student_id, session_id=session_id
    )
    if not eligibility.eligible:
        raise ValueError("目标场次当前已不可选，请刷新推荐。")
    original = item.selected
    original_id = original.session_id
    item.selected = candidate
    item.alternatives = [
        value
        for value in [original, *item.alternatives]
        if value.session_id != session_id
    ][:3]
    item.adjusted = item.adjusted or original_id != session_id
    item.status = "PENDING"
    item.result_message = None
    draft.version += 1
    draft.status = "EDITING"
    draft.confirmation_token = None
    await _save(redis, draft)
    return draft


async def preview_plan(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    plan_id: UUID,
) -> SelectionPlanPreview:
    draft = await get_plan(redis, student_id=student_id, plan_id=plan_id)
    violations: list[str] = []
    pending = [item for item in draft.items if item.status != "SUCCEEDED"]
    raw_sessions: list[ExperimentSession] = []
    raw_by_project: dict[UUID, ExperimentSession] = {}
    for item in pending:
        result = await check_selection_eligibility(
            db, student_id=student_id, session_id=item.selected.session_id
        )
        if not result.eligible:
            violations.extend(value.message for value in result.violations)
        raw = await db.get(ExperimentSession, item.selected.session_id)
        if raw is None:
            violations.append(f"“{item.selected.project_name}”场次不存在。")
        else:
            raw_sessions.append(raw)
            raw_by_project[item.project_id] = raw
    for index, left in enumerate(raw_sessions):
        for right in raw_sessions[index + 1 :]:
            if sessions_overlap(left, right):
                violations.append("方案中的实验场次彼此存在时间冲突。")
    if raw_by_project:
        student = await db.get(Student, student_id)
        if student is not None:
            constraints = list(
                (
                    await db.execute(
                        select(ProjectOrderConstraint)
                        .join(
                            TrainingPlanCourse,
                            TrainingPlanCourse.id
                            == ProjectOrderConstraint.plan_course_id,
                        )
                        .join(
                            TrainingPlan,
                            TrainingPlan.id == TrainingPlanCourse.plan_id,
                        )
                        .where(
                            TrainingPlan.major_id == student.major_id,
                            TrainingPlan.enrollment_year == student.enrollment_year,
                            TrainingPlan.status == "PUBLISHED",
                            ProjectOrderConstraint.before_project_id.in_(
                                raw_by_project
                            ),
                            ProjectOrderConstraint.after_project_id.in_(raw_by_project),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for constraint in constraints:
                before = raw_by_project[constraint.before_project_id]
                after = raw_by_project[constraint.after_project_id]
                if session_end_ordinal(before) >= session_start_ordinal(after):
                    violations.append("方案中的实验项目先后顺序不符合培养方案要求。")
    preview = SelectionPlanPreview(
        valid=not violations,
        version=draft.version,
        new_count=len(pending),
        adjusted_count=sum(item.adjusted for item in pending),
        violations=list(dict.fromkeys(violations)),
        warnings=draft.warnings,
    )
    return preview


async def prepare_plan(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    plan_id: UUID,
    version: int,
) -> tuple[SelectionPlanDraft, SelectionPlanPreview]:
    draft = await get_plan(redis, student_id=student_id, plan_id=plan_id)
    if draft.version != version:
        raise ValueError("方案已经发生变化，请重新查看并确认。")
    preview = await preview_plan(redis, db, student_id=student_id, plan_id=plan_id)
    if not preview.valid:
        raise ValueError("方案校验未通过，请先调整冲突场次。")
    draft.confirmation_token = secrets.token_urlsafe(32)
    draft.status = "READY"
    await _save(redis, draft)
    return draft, preview


async def execute_plan(
    redis: Redis,
    db: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    plan_id: UUID,
    confirmation_token: str,
) -> SelectionPlanExecutionResult:
    draft = await get_plan(redis, student_id=student_id, plan_id=plan_id)
    if draft.status not in {"READY", "PARTIAL"} or not secrets.compare_digest(
        draft.confirmation_token or "", confirmation_token
    ):
        raise ValueError("确认信息无效或已经使用，请重新确认方案。")
    draft.status = "EXECUTING"
    draft.confirmation_token = None
    await _save(redis, draft)

    succeeded = failed = 0
    plan_ids = {item.selected.session_id for item in draft.items}
    for item in draft.items:
        if item.status == "SUCCEEDED":
            continue
        result = await selection_service.select_session(
            redis,
            db,
            student_id=student_id,
            term_id=term.id,
            session_id=item.selected.session_id,
        )
        if result.result in {"ok", "already_selected"}:
            item.status = "SUCCEEDED"
            item.result_message = result.message
            succeeded += 1
            continue
        item.status = "FAILED"
        item.result_message = result.message
        failed += 1
        item.alternatives = await recommend_project_session_alternatives(
            db,
            student_id=student_id,
            term=term,
            project_id=item.project_id,
            preferences=draft.preferences,
            excluded_session_ids=plan_ids,
            plan_session_ids=plan_ids - {item.selected.session_id},
            limit=3,
        )
    draft.version += 1
    draft.status = "PARTIAL" if failed else "COMPLETED"
    await _save(redis, draft)
    return SelectionPlanExecutionResult(
        plan=draft,
        succeeded=succeeded,
        failed=failed,
    )
