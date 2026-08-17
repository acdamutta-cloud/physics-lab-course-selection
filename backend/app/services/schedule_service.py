from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graphs.scheduling_graph import resolve_runtime_strategy
from app.agents.nodes.validation_agent import (
    evaluate_candidate_preference_effects,
    review_candidate_soft_constraints,
)
from app.models.curriculum import AcademicTerm, ExperimentCourse, ExperimentProject
from app.models.identity import Teacher
from app.models.resources import Laboratory, TeacherProjectQualification
from app.models.rules import RuleConfig, RuleSet
from app.models.scheduling import (
    CourseTimeAvailability,
    ExperimentSession,
    ProjectDemand,
    ScheduleJob,
    ScheduleVersion,
    TeachingTask,
)
from app.scheduler.cp_sat_solver import (
    SolverDemand,
    SolverLabOption,
    SolverTeacherOption,
    solve_candidate,
)
from app.schemas.schedule import (
    GenerateScheduleRequest,
    ScheduleCandidateOut,
    ScheduleJobOut,
    ScheduleSessionOut,
)
from app.services.course_availability_service import (
    refresh_course_time_availability,
)
from app.services.resource_feasibility_service import get_project_lab_options
from app.services.teacher_timetable_service import rebuild_teacher_timetable


class ScheduleServiceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "SCHEDULE_GENERATION_FAILED",
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


async def _resolve_term(
    session: AsyncSession,
    term_id: UUID | None,
) -> AcademicTerm:
    if term_id is not None:
        term = await session.get(AcademicTerm, term_id)
    else:
        term = (
            await session.execute(
                select(AcademicTerm)
                .where(AcademicTerm.status == "ACTIVE")
                .order_by(AcademicTerm.start_date.desc())
            )
        ).scalars().first()
    if term is None:
        raise ScheduleServiceError(
            "没有可用于排课的学期。",
            code="TERM_NOT_FOUND",
            status_code=404,
        )
    return term


async def _resolve_rule_set(
    session: AsyncSession,
) -> tuple[RuleSet, list[RuleConfig]]:
    rule_set = (
        await session.execute(
            select(RuleSet)
            .where(
                RuleSet.rule_domain == "SCHEDULING",
                RuleSet.version_no == 2,
                RuleSet.status == "DRAFT",
            )
            .order_by(RuleSet.updated_at.desc())
        )
    ).scalars().first()
    if rule_set is None:
        rule_set = (
            await session.execute(
                select(RuleSet)
                .where(
                    RuleSet.rule_domain == "SCHEDULING",
                    RuleSet.status == "PUBLISHED",
                )
                .order_by(RuleSet.version_no.desc())
            )
        ).scalars().first()
    if rule_set is None:
        raise ScheduleServiceError(
            "未找到排课规则集。",
            code="SCHEDULING_RULE_SET_NOT_FOUND",
        )
    rules = list(
        (
            await session.execute(
                select(RuleConfig)
                .where(
                    RuleConfig.rule_set_id == rule_set.id,
                    RuleConfig.enabled.is_(True),
                )
                .order_by(RuleConfig.priority.desc())
            )
        ).scalars()
    )
    return rule_set, rules


async def _load_demands(
    session: AsyncSession,
    *,
    term: AcademicTerm,
) -> tuple[list[SolverDemand], list[TeachingTask], list[str]]:
    rows = (
        await session.execute(
            select(TeachingTask, ExperimentCourse, ProjectDemand, ExperimentProject)
            .join(ExperimentCourse, ExperimentCourse.id == TeachingTask.course_id)
            .join(ProjectDemand, ProjectDemand.task_id == TeachingTask.id)
            .join(
                ExperimentProject,
                ExperimentProject.id == ProjectDemand.project_id,
            )
            .where(
                TeachingTask.term_id == term.id,
                TeachingTask.status.in_(("DRAFT", "READY", "SCHEDULING")),
                ExperimentProject.status == "ACTIVE",
            )
            .order_by(TeachingTask.task_code, ExperimentProject.project_code)
        )
    ).all()
    if not rows:
        raise ScheduleServiceError(
            "当前学期没有可排的教学任务或实验项目。",
            code="NO_SCHEDULABLE_TASKS",
        )

    project_ids = list(dict.fromkeys(row[2].project_id for row in rows))
    lab_options = await get_project_lab_options(session, project_ids)
    laboratory_ids = {
        option.laboratory_id
        for options in lab_options.values()
        for option in options
    }
    laboratories = {
        item.id: item
        for item in (
            (
                await session.execute(
                    select(Laboratory).where(Laboratory.id.in_(laboratory_ids))
                )
            )
            .scalars()
            .all()
        )
    }
    qualification_rows = (
        await session.execute(
            select(TeacherProjectQualification, Teacher)
            .join(Teacher, Teacher.id == TeacherProjectQualification.teacher_id)
            .where(
                TeacherProjectQualification.project_id.in_(project_ids),
                TeacherProjectQualification.status == "ACTIVE",
                Teacher.status == "ACTIVE",
            )
        )
    ).all()
    teachers_by_project: dict[UUID, list[SolverTeacherOption]] = {}
    for qualification, teacher in qualification_rows:
        teachers_by_project.setdefault(qualification.project_id, []).append(
            SolverTeacherOption(
                teacher_id=teacher.id,
                teacher_name=teacher.name,
            )
        )

    project_names = {
        project.id: project.project_name
        for _, _, _, project in rows
    }
    missing_lab_ids = [
        project_id
        for project_id in project_ids
        if not lab_options.get(project_id)
    ]
    missing_teacher_ids = [
        project_id
        for project_id in project_ids
        if not teachers_by_project.get(project_id)
    ]
    if missing_lab_ids or missing_teacher_ids:
        messages: list[str] = []
        if missing_lab_ids:
            names = [
                project_names[project_id]
                for project_id in missing_lab_ids[:5]
            ]
            suffix = (
                f"等 {len(missing_lab_ids)} 个项目"
                if len(missing_lab_ids) > 5
                else ""
            )
            messages.append(
                "器材或实验室能力未齐套："
                + "、".join(names)
                + suffix
            )
        if missing_teacher_ids:
            names = [
                project_names[project_id]
                for project_id in missing_teacher_ids[:5]
            ]
            suffix = (
                f"等 {len(missing_teacher_ids)} 个项目"
                if len(missing_teacher_ids) > 5
                else ""
            )
            messages.append("缺少有效教师资质：" + "、".join(names) + suffix)
        raise ScheduleServiceError(
            "排课前置检查未通过。" + "；".join(messages),
            code="SCHEDULING_PREFLIGHT_FAILED",
        )

    warnings: list[str] = []
    tasks_by_id: dict[UUID, TeachingTask] = {}
    demands: list[SolverDemand] = []
    for task, course, demand, project in rows:
        tasks_by_id[task.id] = task
        teachers = tuple(
            sorted(
                teachers_by_project.get(project.id, []),
                key=lambda item: (item.teacher_name, str(item.teacher_id)),
            )
        )
        project_labs = lab_options.get(project.id, [])
        # 开课场次按总需求容量与单场有效容量计算。原字段仍保留在快照中，
        # 不在此处改写，避免把实验小组数误当成课表场次数。
        max_capacity = max(option.effective_capacity for option in project_labs)
        best_capacity_labs = [
            option
            for option in project_labs
            if option.effective_capacity == max_capacity
        ]
        occurrence_count = max(
            1,
            math.ceil(demand.required_capacity / max_capacity),
        )
        if occurrence_count != demand.required_session_count:
            warnings.append(
                f"{task.task_code}/{project.project_code}: "
                f"数据库 required_session_count={demand.required_session_count}，"
                f"本次按容量换算为 {occurrence_count} 场。"
            )
        demands.append(
            SolverDemand(
                task_id=task.id,
                course_id=course.id,
                course_name=course.course_name,
                project_id=project.id,
                project_name=project.project_name,
                week_start=max(1, task.week_start),
                week_end=min(term.total_weeks, task.week_end),
                required_slots=project.required_slots,
                required_capacity=demand.required_capacity,
                occurrence_count=occurrence_count,
                teachers=teachers,
                laboratories=tuple(
                    SolverLabOption(
                        laboratory_id=option.laboratory_id,
                        laboratory_code=option.laboratory_code,
                        laboratory_name=laboratories[
                            option.laboratory_id
                        ].name,
                        effective_capacity=option.effective_capacity,
                    )
                    for option in best_capacity_labs
                ),
            )
        )
    if any(task.status == "DRAFT" for task in tasks_by_id.values()):
        warnings.append("本次包含 DRAFT 教学任务，候选课表不会自动发布。")
    return demands, list(tasks_by_id.values()), warnings


async def _refresh_availability(
    session: AsyncSession,
    *,
    demands: list[SolverDemand],
    term_id: UUID,
) -> tuple[
    dict[tuple[UUID, int, int, int], tuple[float, float]],
    list[dict[str, Any]],
    bool,
]:
    course_ids = list(dict.fromkeys(item.course_id for item in demands))
    snapshots: list[dict[str, Any]] = []
    for course_id in course_ids:
        result = await refresh_course_time_availability(
            session,
            course_id,
            term_id,
        )
        snapshots.append(
            {
                "course_id": str(course_id),
                "calculation_batch_id": str(result.calculation_batch_id),
                "source_hash": result.source_hash,
                "target_student_count": result.target_student_count,
                "known_student_count": result.known_student_count,
                "unknown_student_count": result.unknown_student_count,
            }
        )
    rows = list(
        (
            await session.execute(
                select(CourseTimeAvailability).where(
                    CourseTimeAvailability.term_id == term_id,
                    CourseTimeAvailability.course_id.in_(course_ids),
                )
            )
        ).scalars()
    )
    lookup = {
        (
            row.course_id,
            row.week_no,
            row.day_of_week,
            row.slot_no,
        ): (float(row.free_ratio), float(row.data_coverage_ratio))
        for row in rows
    }
    has_known_data = any(
        snapshot["known_student_count"] > 0 for snapshot in snapshots
    )
    return lookup, snapshots, has_known_data


async def _teacher_directory(session: AsyncSession) -> dict[str, str]:
    teachers = list(
        (
            await session.execute(
                select(Teacher).where(Teacher.status == "ACTIVE")
            )
        ).scalars()
    )
    return {str(item.id): item.name for item in teachers}


def _common_score(
    metrics: dict[str, float],
    comparison_weights: dict[str, float],
) -> float:
    penalty = sum(
        comparison_weights.get(code, 0.0) * value
        for code, value in metrics.items()
    ) / 100
    return max(0.0, 100 * (1 - penalty))


async def generate_initial_schedule(
    session: AsyncSession,
    body: GenerateScheduleRequest,
    actor_id: UUID,
) -> ScheduleJobOut:
    term = await _resolve_term(session, body.term_id)
    rule_set, rules = await _resolve_rule_set(session)
    now = datetime.now(UTC)
    job = ScheduleJob(
        term_id=term.id,
        job_type="INITIAL",
        status="RUNNING",
        scheduling_rule_set_id=rule_set.id,
        input_snapshot={
            "preference_text": body.preference_text.strip(),
            "runtime_weight_policy": (
                "DB_BASE_PLUS_PREFERENCE_DELTA_NORMALIZED_NO_DB_WRITE"
            ),
        },
        progress=5,
        started_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(job)
    await session.flush()
    # 先持久化任务壳，后续即使求解失败也保留可追踪的失败记录。
    await session.commit()

    try:
        demands, tasks, warnings = await _load_demands(session, term=term)
        job.progress = 25
        availability, availability_snapshots, has_availability = (
            await _refresh_availability(
                session,
                demands=demands,
                term_id=term.id,
            )
        )
        job.progress = 45

        soft_rules = [
            rule for rule in rules if rule.enforcement_type == "SCORE"
        ]
        base_weights = {
            rule.rule_code: float(rule.weight) for rule in soft_rules
        }
        priorities = {
            rule.rule_code: rule.priority for rule in soft_rules
        }
        applicability = {rule.rule_code: True for rule in soft_rules}
        applicability["STUDENT_AVAILABILITY_COVERAGE"] = has_availability
        # 当前数据库尚未落教师偏好时段表，不能把缺失数据当作偏好满足。
        applicability["TEACHER_PREFERRED_TIME"] = False
        strategy = await resolve_runtime_strategy(
            {
                "preference_text": body.preference_text.strip(),
                "base_weights": base_weights,
                "applicability": applicability,
                "teacher_directory": await _teacher_directory(session),
                "course_directory": {
                    str(item.course_id): item.course_name
                    for item in demands
                },
                "project_directory": {
                    str(item.project_id): item.project_name
                    for item in demands
                },
                "total_weeks": term.total_weeks,
                "rule_priorities": priorities,
                "max_candidate_count": 5,
            }
        )
        if strategy.get("validation_errors"):
            raise ScheduleServiceError(
                "运行时排课策略校验失败："
                + "；".join(strategy["validation_errors"]),
                code="RUNTIME_STRATEGY_INVALID",
            )
        warnings.extend(strategy.get("warnings", []))
        target_teacher_ids = {
            UUID(teacher_id)
            for item in strategy.get("parsed_preferences", [])
            if item.get("rule_code") == "TEACHER_TARGET_LOAD_SCORE"
            for teacher_id in item.get("target_teacher_ids", [])
        }
        # 每位教师每学期最少上课场次下限（默认 20），防止减负偏好把教师减到没课。
        active_teachers = list(
            (
                await session.execute(
                    select(Teacher).where(Teacher.status == "ACTIVE")
                )
            ).scalars()
        )
        min_session_counts = {
            teacher.id: teacher.min_session_count
            for teacher in active_teachers
            if teacher.min_session_count > 0
        }
        course_early_week_preferences = {
            UUID(preference["course_id"]): int(
                preference["preferred_end_week"]
            )
            for item in strategy.get("parsed_preferences", [])
            if item.get("rule_code")
            == "COURSE_EARLY_WEEK_PREFERENCE"
            for preference in item.get("course_week_preferences", [])
        }
        project_early_week_preferences = {
            UUID(preference["project_id"]): int(
                preference["preferred_end_week"]
            )
            for item in strategy.get("parsed_preferences", [])
            if item.get("rule_code")
            == "PROJECT_EARLY_WEEK_PREFERENCE"
            for preference in item.get("project_week_preferences", [])
        }

        # 锁定学期行，避免并发任务分配重复 version_no。
        await session.execute(
            select(AcademicTerm)
            .where(AcademicTerm.id == term.id)
            .with_for_update()
        )
        current_max = await session.scalar(
            select(func.max(ScheduleVersion.version_no)).where(
                ScheduleVersion.term_id == term.id
            )
        )
        next_version = int(current_max or 0) + 1
        comparison_weights = strategy.get("comparison_weights", {})
        versions: list[ScheduleVersion] = []
        solved_profiles: list[tuple[dict[str, Any], Any, float]] = []

        shortfall_warnings: set[str] = set()
        for profile_index, profile in enumerate(strategy["profiles"]):
            result = solve_candidate(
                demands=demands,
                days_per_week=term.days_per_week,
                slots_per_day=term.slots_per_day,
                solver_weights=profile["solver_weights"],
                availability=availability,
                target_teacher_ids=target_teacher_ids,
                variation_seed=profile_index,
                course_early_week_preferences=(
                    course_early_week_preferences
                ),
                project_early_week_preferences=(
                    project_early_week_preferences
                ),
                min_session_counts=min_session_counts,
            )
            if not result.hard_constraint_passed:
                raise ScheduleServiceError(
                    "候选课表无法满足硬约束："
                    + "；".join(result.errors[:3]),
                    code="HARD_CONSTRAINT_INFEASIBLE",
                )
            teacher_session_counts: dict[UUID, int] = {}
            for item in result.sessions:
                teacher_session_counts[item.teacher_id] = (
                    teacher_session_counts.get(item.teacher_id, 0) + 1
                )
            for teacher in active_teachers:
                floor = teacher.min_session_count
                if floor > 0 and teacher_session_counts.get(teacher.id, 0) < floor:
                    shortfall_warnings.add(
                        f"教师{teacher.name}本学期已排 "
                        f"{teacher_session_counts.get(teacher.id, 0)} 场，"
                        f"低于最少场次下限 {floor} 场"
                    )
            common_score = _common_score(
                result.metrics,
                comparison_weights,
            )
            solved_profiles.append((profile, result, common_score))
        warnings.extend(sorted(shortfall_warnings))

        peer_metrics = [
            result.metrics for _, result, _ in solved_profiles
        ]
        for profile_index, (profile, result, common_score) in enumerate(
            solved_profiles
        ):
            soft_review = review_candidate_soft_constraints(
                result.metrics,
                comparison_weights,
                peer_metrics,
            )
            preference_effects = evaluate_candidate_preference_effects(
                strategy.get("parsed_preferences", []),
                result.metrics,
                comparison_weights,
            )
            version = ScheduleVersion(
                term_id=term.id,
                version_no=next_version + profile_index,
                source_job_id=job.id,
                status="CANDIDATE",
                hard_constraint_passed=True,
                soft_score=Decimal(str(round(common_score, 4))),
                score_details={
                    "comparison_score": round(common_score, 4),
                    "profile_solver_score": round(result.soft_score, 4),
                    "normalized_penalties": result.metrics,
                    "validation": {
                        "agent": "validation_agent",
                        "hard_constraint_passed": True,
                        "soft_constraint_review": soft_review,
                        "preference_effects": preference_effects,
                    },
                },
                optimization_params={
                    "profile_code": profile["profile_code"],
                    "focus_rule_code": profile["focus_rule_code"],
                    "base_weights": base_weights,
                    "comparison_weights": comparison_weights,
                    "solver_weights": profile["solver_weights"],
                    "preference_adjustments": strategy.get(
                        "parsed_preferences",
                        [],
                    ),
                    "runtime_only": True,
                },
                scheduling_rule_set_id=rule_set.id,
                created_by=actor_id,
                updated_by=actor_id,
            )
            session.add(version)
            await session.flush()
            session.add_all(
                [
                    ExperimentSession(
                        schedule_version_id=version.id,
                        session_code=(
                            f"AI-V{version.version_no}-{index:05d}"
                        ),
                        task_id=item.task_id,
                        project_id=item.project_id,
                        week_no=item.week_no,
                        day_of_week=item.day_of_week,
                        start_slot=item.start_slot,
                        end_slot=item.end_slot,
                        teacher_id=item.teacher_id,
                        laboratory_id=item.laboratory_id,
                        capacity=item.capacity,
                        selected_count=0,
                        status="DRAFT",
                        locked=False,
                        created_by=actor_id,
                        updated_by=actor_id,
                    )
                    for index, item in enumerate(result.sessions, start=1)
                ]
            )
            versions.append(version)
            await session.flush()
            job.progress = 45 + int(
                50 * (profile_index + 1) / len(solved_profiles)
            )

        job.status = "SUCCEEDED"
        job.progress = 100
        job.finished_at = datetime.now(UTC)
        job.input_snapshot = {
            "preference_text": body.preference_text.strip(),
            "runtime_weight_policy": (
                "DB_BASE_PLUS_PREFERENCE_DELTA_NORMALIZED_NO_DB_WRITE"
            ),
            "parsed_preferences": strategy.get("parsed_preferences", []),
            "comparison_weights": comparison_weights,
            "profiles": strategy["profiles"],
            "availability_snapshots": availability_snapshots,
            "task_ids": [str(item.id) for item in tasks],
            "session_count_basis": (
                "CEIL_REQUIRED_CAPACITY_DIV_MAX_FEASIBLE_LAB_CAPACITY"
            ),
            "warnings": warnings,
            "selected_candidate_version_id": None,
        }
        await session.commit()
        return await get_schedule_job(session, job.id)
    except ScheduleServiceError as error:
        job.status = "FAILED"
        job.progress = min(job.progress, 99)
        job.finished_at = datetime.now(UTC)
        job.error_code = error.code
        job.error_message = error.message
        await session.commit()
        raise
    except Exception:
        await session.rollback()
        persisted_job = await session.get(ScheduleJob, job.id)
        if persisted_job is not None:
            persisted_job.status = "FAILED"
            persisted_job.progress = min(persisted_job.progress, 99)
            persisted_job.finished_at = datetime.now(UTC)
            persisted_job.error_code = "INTERNAL_SCHEDULE_ERROR"
            persisted_job.error_message = "排课执行发生内部错误，请查看服务日志。"
            await session.commit()
        raise


async def select_schedule_candidate(
    session: AsyncSession,
    *,
    job_id: UUID,
    version_id: UUID,
    actor_id: UUID,
) -> ScheduleJobOut:
    job = await session.get(ScheduleJob, job_id)
    version = await session.get(ScheduleVersion, version_id)
    if job is None:
        raise ScheduleServiceError(
            "排课任务不存在。",
            code="SCHEDULE_JOB_NOT_FOUND",
            status_code=404,
        )
    if (
        version is None
        or version.source_job_id != job.id
        or version.status != "CANDIDATE"
    ):
        raise ScheduleServiceError(
            "所选版本不是该任务的有效候选方案。",
            code="INVALID_SCHEDULE_CANDIDATE",
        )
    snapshot = dict(job.input_snapshot)
    snapshot["selected_candidate_version_id"] = str(version.id)
    snapshot["selected_at"] = datetime.now(UTC).isoformat()
    snapshot["selected_by"] = str(actor_id)
    job.input_snapshot = snapshot
    job.updated_by = actor_id
    await session.commit()
    return await get_schedule_job(session, job.id)


async def publish_selected_schedule(
    session: AsyncSession,
    *,
    job_id: UUID,
    version_id: UUID,
    actor_id: UUID,
) -> ScheduleJobOut:
    job = await session.get(ScheduleJob, job_id)
    version = await session.get(ScheduleVersion, version_id)
    if job is None:
        raise ScheduleServiceError(
            "排课任务不存在。",
            code="SCHEDULE_JOB_NOT_FOUND",
            status_code=404,
        )
    selected_id = (job.input_snapshot or {}).get(
        "selected_candidate_version_id"
    )
    if selected_id != str(version_id):
        raise ScheduleServiceError(
            "请先选择候选方案，再确认发布。",
            code="SCHEDULE_CANDIDATE_NOT_SELECTED",
        )
    if (
        version is None
        or version.source_job_id != job.id
        or version.term_id != job.term_id
    ):
        raise ScheduleServiceError(
            "所选版本不是该任务的有效候选方案。",
            code="INVALID_SCHEDULE_CANDIDATE",
        )
    if version.status == "PUBLISHED":
        return await get_schedule_job(session, job.id)
    if version.status != "CANDIDATE":
        raise ScheduleServiceError(
            "只有候选课表可以发布。",
            code="SCHEDULE_VERSION_NOT_CANDIDATE",
        )

    now = datetime.now(UTC)
    try:
        await session.execute(
            select(AcademicTerm)
            .where(AcademicTerm.id == job.term_id)
            .with_for_update()
        )
        published_versions = list(
            (
                await session.execute(
                    select(ScheduleVersion)
                    .where(
                        ScheduleVersion.term_id == job.term_id,
                        ScheduleVersion.status == "PUBLISHED",
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        for published in published_versions:
            if published.id == version.id:
                continue
            published.status = "ARCHIVED"
            published.updated_by = actor_id
        # 先释放同学期唯一 PUBLISHED 约束，再发布新版本。
        await session.flush()

        version.status = "PUBLISHED"
        version.published_by = actor_id
        version.published_at = now
        version.updated_by = actor_id
        await session.flush()
        timetable_entry_count = await rebuild_teacher_timetable(
            session,
            version.id,
        )

        # 新课表发布后，将该学期所有学生的选课记录标记为 WITHDRAWN
        from app.models.enrollment import StudentProjectRecord

        withdrawn_result = await session.execute(
            update(StudentProjectRecord)
            .where(
                StudentProjectRecord.term_id == job.term_id,
                StudentProjectRecord.status.in_(["SELECTED", "MAKEUP_PENDING"]),
            )
            .values(status="WITHDRAWN", updated_by=actor_id)
        )
        withdrawn_count = withdrawn_result.rowcount

        snapshot = dict(job.input_snapshot)
        snapshot["withdrawn_student_records"] = withdrawn_count
        snapshot["published_candidate_version_id"] = str(version.id)
        snapshot["published_at"] = now.isoformat()
        snapshot["published_by"] = str(actor_id)
        snapshot["teacher_timetable_entry_count"] = timetable_entry_count
        job.input_snapshot = snapshot
        job.updated_by = actor_id
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return await get_schedule_job(session, job.id)


async def get_schedule_job(
    session: AsyncSession,
    job_id: UUID,
) -> ScheduleJobOut:
    job = await session.get(ScheduleJob, job_id)
    if job is None:
        raise ScheduleServiceError(
            "排课任务不存在。",
            code="SCHEDULE_JOB_NOT_FOUND",
            status_code=404,
        )
    term = await session.get(AcademicTerm, job.term_id)
    version_rows = list(
        (
            await session.execute(
                select(ScheduleVersion)
                .where(ScheduleVersion.source_job_id == job.id)
                .order_by(
                    ScheduleVersion.soft_score.desc(),
                    ScheduleVersion.version_no,
                )
            )
        ).scalars()
    )
    version_ids = [item.id for item in version_rows]
    session_rows = (
        await session.execute(
            select(
                ExperimentSession,
                ExperimentProject,
                ExperimentCourse,
                Teacher,
                Laboratory,
            )
            .join(
                ExperimentProject,
                ExperimentProject.id == ExperimentSession.project_id,
            )
            .join(
                TeachingTask,
                TeachingTask.id == ExperimentSession.task_id,
            )
            .join(
                ExperimentCourse,
                ExperimentCourse.id == TeachingTask.course_id,
            )
            .join(Teacher, Teacher.id == ExperimentSession.teacher_id)
            .join(
                Laboratory,
                Laboratory.id == ExperimentSession.laboratory_id,
            )
            .where(ExperimentSession.schedule_version_id.in_(version_ids))
            .order_by(
                ExperimentSession.schedule_version_id,
                ExperimentSession.week_no,
                ExperimentSession.day_of_week,
                ExperimentSession.start_slot,
            )
        )
    ).all() if version_ids else []
    sessions_by_version: dict[UUID, list[ScheduleSessionOut]] = {}
    for item, project, course, teacher, laboratory in session_rows:
        sessions_by_version.setdefault(item.schedule_version_id, []).append(
            ScheduleSessionOut(
                id=item.id,
                session_code=item.session_code,
                task_id=item.task_id,
                project_id=item.project_id,
                course_name=course.course_name,
                project_name=project.project_name,
                week_no=item.week_no,
                day_of_week=item.day_of_week,
                start_slot=item.start_slot,
                end_slot=item.end_slot,
                teacher_id=item.teacher_id,
                teacher_name=teacher.name,
                laboratory_id=item.laboratory_id,
                laboratory_code=laboratory.lab_code,
                laboratory_name=laboratory.name,
                capacity=item.capacity,
                selected_count=item.selected_count,
            )
        )
    snapshot = job.input_snapshot or {}
    selected_value = snapshot.get("selected_candidate_version_id")
    peer_metrics = [
        (version.score_details or {}).get("normalized_penalties", {})
        for version in version_rows
    ]

    def score_details_with_review(
        version: ScheduleVersion,
    ) -> dict[str, Any]:
        details = dict(version.score_details or {})
        validation = dict(details.get("validation") or {})
        if "soft_constraint_review" not in validation:
            metrics = details.get("normalized_penalties") or {}
            comparison_weights = (
                version.optimization_params.get("comparison_weights")
                or snapshot.get("comparison_weights", {})
            )
            validation["soft_constraint_review"] = (
                review_candidate_soft_constraints(
                    metrics,
                    comparison_weights,
                    peer_metrics,
                )
            )
        if "preference_effects" not in validation:
            metrics = details.get("normalized_penalties") or {}
            comparison_weights = (
                version.optimization_params.get("comparison_weights")
                or snapshot.get("comparison_weights", {})
            )
            parsed_preferences = (
                version.optimization_params.get("preference_adjustments")
                or snapshot.get("parsed_preferences", [])
            )
            validation["preference_effects"] = (
                evaluate_candidate_preference_effects(
                    parsed_preferences,
                    metrics,
                    comparison_weights,
                )
            )
        details["validation"] = validation
        return details

    return ScheduleJobOut(
        id=job.id,
        term_id=job.term_id,
        term_code=term.code if term else "",
        status=job.status,
        progress=job.progress,
        preference_text=snapshot.get("preference_text", ""),
        parsed_preferences=snapshot.get("parsed_preferences", []),
        comparison_weights=snapshot.get("comparison_weights", {}),
        warnings=snapshot.get("warnings", []),
        selected_candidate_version_id=(
            UUID(selected_value) if selected_value else None
        ),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_code=job.error_code,
        error_message=job.error_message,
        candidates=[
            ScheduleCandidateOut(
                id=version.id,
                version_no=version.version_no,
                status=version.status,
                profile_code=version.optimization_params.get(
                    "profile_code",
                    "BALANCED",
                ),
                hard_constraint_passed=version.hard_constraint_passed,
                soft_score=float(version.soft_score or 0),
                score_details=score_details_with_review(version),
                runtime_weights=version.optimization_params.get(
                    "solver_weights",
                    {},
                ),
                session_count=len(sessions_by_version.get(version.id, [])),
                sessions=sessions_by_version.get(version.id, []),
            )
            for version in version_rows
        ],
    )


async def get_published_schedule(
    session: AsyncSession,
    *,
    term_id: UUID | None,
) -> ScheduleJobOut:
    term = await _resolve_term(session, term_id)
    published = (
        await session.execute(
            select(ScheduleVersion)
            .where(
                ScheduleVersion.term_id == term.id,
                ScheduleVersion.status == "PUBLISHED",
            )
            .order_by(ScheduleVersion.published_at.desc())
        )
    ).scalars().first()
    if published is None:
        raise ScheduleServiceError(
            "当前学期尚未发布课表。",
            code="PUBLISHED_SCHEDULE_NOT_FOUND",
            status_code=404,
        )
    if published.source_job_id is None:
        raise ScheduleServiceError(
            "当前正式课表不是由 AI 排课任务生成，暂不支持候选详情查询。",
            code="PUBLISHED_SCHEDULE_SOURCE_JOB_NOT_FOUND",
            status_code=404,
        )
    return await get_schedule_job(session, published.source_job_id)
