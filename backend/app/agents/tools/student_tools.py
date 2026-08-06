from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.student_selection_rules import rules_for_topics
from app.models.curriculum import AcademicTerm
from app.models.identity import Teacher
from app.models.rules import RuleConfig, RuleSet
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.schemas.student_consultation import (
    EntityReference,
    RecommendationScope,
    SelectionPreferences,
    StudentAgentPlan,
    StudentRuleTopic,
    weekday_number,
)
from app.services.student_consultation_service import (
    check_selection_eligibility,
    get_remaining_projects,
    get_training_plan_context,
    recommend_selection_plans,
)


def _canonical_name_matches(canonical_name: str, reference_name: str) -> bool:
    """Match a canonical business name inside a natural-language reference."""

    canonical = "".join(canonical_name.split())
    reference = "".join(reference_name.split())
    if not canonical or not reference:
        return False
    return canonical == reference or canonical in reference or reference in canonical


def _session_resolution_error(
    reference: EntityReference,
    resolved: dict[str, object],
    *,
    candidate_count: int,
) -> str:
    """Describe zero and ambiguous session matches without conflating them."""

    project_name = str(resolved.get("project_name") or "该实验项目")
    if candidate_count == 0:
        time_parts: list[str] = []
        if reference.week_no is not None:
            time_parts.append(f"第{reference.week_no}周")
        if reference.day_name is not None:
            time_parts.append(reference.day_name)
        if reference.start_slot is not None and reference.end_slot is not None:
            time_parts.append(f"第{reference.start_slot}—{reference.end_slot}节")
        elif reference.start_slot is not None:
            time_parts.append(f"第{reference.start_slot}节开始")
        teacher_text = (
            f"{reference.teacher_name}老师的" if reference.teacher_name else ""
        )
        conditions = "".join(time_parts)
        return f"未找到{conditions}{teacher_text}{project_name}场次，请核对条件。"

    missing: list[str] = []
    if reference.week_no is None:
        missing.append("周次")
    if reference.day_name is None:
        missing.append("星期")
    if reference.start_slot is None or reference.end_slot is None:
        missing.append("节次")
    if reference.teacher_name is None:
        missing.append("教师")
    if missing:
        return (
            f"找到多个符合条件的{project_name}场次，请补充" + "、".join(missing) + "。"
        )
    return f"这些条件仍对应多个{project_name}场次，请从选课页面选择具体场次。"


async def resolve_session_from_question(
    session: AsyncSession,
    *,
    term: AcademicTerm,
    question: str,
) -> ExperimentSession | None:
    rows = list(
        (
            await session.execute(
                select(ExperimentSession)
                .options(selectinload(ExperimentSession.project))
                .join(
                    ScheduleVersion,
                    ScheduleVersion.id == ExperimentSession.schedule_version_id,
                )
                .where(
                    ScheduleVersion.term_id == term.id,
                    ScheduleVersion.status == "PUBLISHED",
                    ExperimentSession.status.in_(["DRAFT", "OPEN", "FULL"]),
                )
                .order_by(
                    ExperimentSession.week_no,
                    ExperimentSession.day_of_week,
                    ExperimentSession.start_slot,
                )
            )
        )
        .scalars()
        .all()
    )
    for item in rows:
        if str(item.id) in question:
            return item
    matching = [
        item
        for item in rows
        if item.project is not None and item.project.project_name in question
    ]
    if len(matching) == 1:
        return matching[0]
    # Never guess a session when the project has several time options. UUID is
    # the stable selection identifier; natural-language time parsing can be
    # added later without weakening the qualification boundary.
    return None


async def check_eligibility_tool(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    question: str,
) -> dict[str, object]:
    target = await resolve_session_from_question(session, term=term, question=question)
    if target is None:
        return {"unknown": "未能从问题中确定具体实验项目或场次，请提供项目名称。"}
    result = await check_selection_eligibility(
        session, student_id=student_id, session_id=target.id
    )
    return result.model_dump(mode="json")


async def training_plan_tool(
    session: AsyncSession, *, student_id: UUID, term: AcademicTerm
) -> dict[str, object]:
    return await get_training_plan_context(session, student_id=student_id, term=term)


async def remaining_projects_tool(
    session: AsyncSession, *, student_id: UUID, term: AcademicTerm
) -> dict[str, object]:
    return await get_remaining_projects(session, student_id=student_id, term=term)


async def recommendation_tool(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    preferences: SelectionPreferences,
    scope: RecommendationScope,
    resolved: dict[str, object],
) -> dict[str, object]:
    plans = await recommend_selection_plans(
        session,
        student_id=student_id,
        term=term,
        preferences=preferences,
        scope=scope,
        course_ids={UUID(str(item)) for item in resolved.get("course_ids", [])},
        project_ids={UUID(str(item)) for item in resolved.get("project_ids", [])},
    )
    return {"plans": [item.model_dump(mode="json") for item in plans]}


async def lookup_student_rules_tool(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    topics: list[StudentRuleTopic],
) -> dict[str, object]:
    """Return only published rules and current-plan facts for controlled topics."""

    definitions = rules_for_topics(topics)
    rule_codes = [item["rule_code"] for item in definitions]
    published_rows: list[RuleConfig] = []
    if rule_codes:
        published_rows = list(
            (
                await session.execute(
                    select(RuleConfig)
                    .join(RuleSet, RuleSet.id == RuleConfig.rule_set_id)
                    .where(
                        RuleSet.rule_domain == "SELECTION",
                        RuleSet.status == "PUBLISHED",
                        RuleConfig.enabled.is_(True),
                        RuleConfig.rule_code.in_(rule_codes),
                    )
                    .order_by(RuleConfig.priority.desc(), RuleConfig.rule_code)
                )
            )
            .scalars()
            .all()
        )

    matched_rules = [
        {
            "rule_code": row.rule_code,
            "rule_name": row.rule_name,
            "description": row.description,
            "enforcement_type": row.enforcement_type,
        }
        for row in published_rows
    ]
    training_plan_facts: list[dict[str, object]] = []
    unknowns: list[str] = []
    if "TRAINING_PLAN" in topics:
        context = await get_training_plan_context(
            session, student_id=student_id, term=term
        )
        if unknown := context.get("unknown"):
            unknowns.append(str(unknown))
        else:
            training_plan_facts = [
                {
                    "course_name": course.get("course_name"),
                    "study_year": course.get("study_year"),
                    "semester_no": course.get("semester_no"),
                    "required_project_count": course.get("required_project_count"),
                    "optional_project_min_count": course.get(
                        "optional_project_min_count"
                    ),
                    "prerequisites": course.get("prerequisites", []),
                    "order_rules": course.get("order_rules", []),
                }
                for course in context.get("courses", [])
                if isinstance(course, dict)
            ]

    status = "FOUND" if matched_rules or training_plan_facts else "NOT_FOUND"
    return {
        "status": status,
        "matched_rules": matched_rules,
        "training_plan_facts": training_plan_facts,
        "source_references": [
            {"source_type": "SELECTION_RULE", "rule_code": item["rule_code"]}
            for item in matched_rules
        ]
        + (
            [{"source_type": "TRAINING_PLAN", "term_id": str(term.id)}]
            if training_plan_facts
            else []
        ),
        "unknowns": unknowns,
    }


async def resolve_plan_entities(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    plan: StudentAgentPlan,
    page_session_id: UUID | None = None,
) -> tuple[dict[str, object], str | None]:
    """Resolve model-produced references to canonical IDs without guessing."""

    reference = plan.entity_reference or EntityReference()
    context = await get_training_plan_context(session, student_id=student_id, term=term)
    courses = context.get("courses", []) if isinstance(context, dict) else []
    resolved: dict[str, object] = {}

    if plan.intent == "RECOMMEND_SELECTION":
        scope = plan.recommendation_scope
        resolved_course_ids: list[str] = []
        for name in scope.course_names:
            matches = [
                item
                for item in courses
                if isinstance(item, dict)
                and _canonical_name_matches(str(item.get("course_name", "")), name)
            ]
            if len(matches) != 1:
                return resolved, f"无法唯一确定课程“{name}”，请提供完整课程名称。"
            resolved_course_ids.append(str(matches[0]["course_id"]))
        if scope.mode == "COURSES" and not resolved_course_ids:
            return resolved, "请说明需要推荐哪一门或哪几门实验课程。"
        resolved["course_ids"] = resolved_course_ids

        resolved_project_ids: list[str] = []
        for name in scope.project_names:
            matches: list[dict[str, object]] = []
            for course in courses:
                if not isinstance(course, dict):
                    continue
                if resolved_course_ids and str(course.get("course_id")) not in set(
                    resolved_course_ids
                ):
                    continue
                for project in course.get("projects", []):
                    if not isinstance(project, dict):
                        continue
                    project_name = str(project.get("project_name", ""))
                    if _canonical_name_matches(project_name, name):
                        matches.append(project)
            if len(matches) != 1:
                return (
                    resolved,
                    f"无法唯一确定项目“{name}”，请补充完整项目名称或所属课程。",
                )
            resolved_project_ids.append(str(matches[0]["project_id"]))
        if scope.mode == "PROJECTS" and not resolved_project_ids:
            return resolved, "请说明需要推荐哪一个或哪几个实验项目。"
        resolved["project_ids"] = resolved_project_ids

    if reference.course_name:
        matches = [
            item
            for item in courses
            if isinstance(item, dict)
            and _canonical_name_matches(
                str(item.get("course_name", "")), reference.course_name
            )
        ]
        if len(matches) == 1:
            resolved["course_id"] = matches[0]["course_id"]
            resolved["course_name"] = matches[0]["course_name"]
        elif len(matches) > 1:
            return resolved, "找到多个名称相近的实验课程，请说明具体课程名称。"
        else:
            return resolved, "当前培养方案中未找到这门实验课程，请确认课程名称。"

    project_matches: list[dict[str, object]] = []
    if reference.project_name:
        for course in courses:
            if not isinstance(course, dict):
                continue
            for project in course.get("projects", []):
                if not isinstance(project, dict):
                    continue
                name = str(project.get("project_name", ""))
                if _canonical_name_matches(name, reference.project_name):
                    project_matches.append(
                        {
                            **project,
                            "course_id": course.get("course_id"),
                            "course_name": course.get("course_name"),
                        }
                    )
        if len(project_matches) == 1:
            resolved.update(
                {
                    "project_id": project_matches[0]["project_id"],
                    "project_name": project_matches[0]["project_name"],
                    "course_id": project_matches[0]["course_id"],
                    "course_name": project_matches[0]["course_name"],
                }
            )
        elif len(project_matches) > 1:
            return resolved, "找到多个名称相近的实验项目，请补充所属课程。"
        else:
            return resolved, "当前培养方案中未找到这个实验项目，请确认项目名称。"

    candidate_session_id = page_session_id or reference.session_id
    session_stmt = (
        select(ExperimentSession)
        .options(selectinload(ExperimentSession.project))
        .join(
            ScheduleVersion, ScheduleVersion.id == ExperimentSession.schedule_version_id
        )
        .where(
            ScheduleVersion.term_id == term.id,
            ScheduleVersion.status == "PUBLISHED",
            ExperimentSession.status.in_(["DRAFT", "OPEN", "FULL"]),
        )
    )
    if reference.teacher_name:
        session_stmt = session_stmt.join(
            Teacher, Teacher.id == ExperimentSession.teacher_id
        ).where(Teacher.name == reference.teacher_name)
    if candidate_session_id:
        candidate = (
            await session.execute(
                session_stmt.where(ExperimentSession.id == candidate_session_id)
            )
        ).scalar_one_or_none()
        if candidate is None:
            return resolved, "当前学期中未找到该实验场次，请重新选择。"
        if resolved.get("project_id") and str(candidate.project_id) != str(
            resolved["project_id"]
        ):
            return resolved, "页面场次与问题中的实验项目不一致，请重新确认。"
        resolved.update(
            {
                "session_id": candidate.id,
                "project_id": candidate.project_id,
                "project_name": (
                    candidate.project.project_name if candidate.project else None
                ),
            }
        )

    needs_session = plan.intent in {"CHECK_ELIGIBILITY", "EXPLAIN_CONFLICT"}
    if needs_session and "session_id" not in resolved:
        if "project_id" not in resolved:
            return resolved, "请提供具体实验项目和场次时间，我才能核验资格。"
        stmt = session_stmt.where(
            ExperimentSession.project_id == UUID(str(resolved["project_id"]))
        )
        for field in ("week_no", "start_slot", "end_slot"):
            value = getattr(reference, field)
            if value is not None:
                stmt = stmt.where(getattr(ExperimentSession, field) == value)
        if reference.day_name is not None:
            stmt = stmt.where(
                ExperimentSession.day_of_week == weekday_number(reference.day_name)
            )
        candidates = list((await session.execute(stmt)).scalars().all())
        if len(candidates) == 0:
            return resolved, _session_resolution_error(
                reference, resolved, candidate_count=0
            )
        if len(candidates) > 1:
            return resolved, _session_resolution_error(
                reference, resolved, candidate_count=len(candidates)
            )
        resolved["session_id"] = candidates[0].id

    return resolved, None


def _filter_course_context(
    data: dict[str, object], resolved: dict[str, object]
) -> dict[str, object]:
    course_id = resolved.get("course_id")
    if not course_id or not isinstance(data.get("courses"), list):
        return data
    return {
        **data,
        "courses": [
            item
            for item in data["courses"]
            if isinstance(item, dict) and str(item.get("course_id")) == str(course_id)
        ],
    }


async def execute_planned_tools(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    plan: StudentAgentPlan,
    resolved: dict[str, object],
) -> list[dict[str, object]]:
    """Execute the validated student read-only tool plan."""

    results: list[dict[str, object]] = []
    for request in plan.tool_requests:
        if request.name == "lookup_student_rules":
            data = await lookup_student_rules_tool(
                session,
                student_id=student_id,
                term=term,
                topics=plan.rule_topics,
            )
        elif request.name == "get_training_plan_context":
            data = await training_plan_tool(session, student_id=student_id, term=term)
            data = _filter_course_context(data, resolved)
        elif request.name == "get_remaining_projects":
            data = await remaining_projects_tool(
                session, student_id=student_id, term=term
            )
            course_id = resolved.get("course_id")
            course_name = resolved.get("course_name")
            if course_id and course_name and isinstance(data.get("projects"), list):
                data = {
                    **data,
                    "projects": [
                        item
                        for item in data["projects"]
                        if isinstance(item, dict)
                        and str(item.get("course_name")) == str(course_name)
                    ],
                }
        elif request.name in {
            "check_selection_eligibility",
            "explain_selection_conflicts",
        }:
            session_id = UUID(str(resolved["session_id"]))
            eligibility = await check_selection_eligibility(
                session, student_id=student_id, session_id=session_id
            )
            data = eligibility.model_dump(mode="json")
            if request.name == "explain_selection_conflicts":
                data = {
                    **data,
                    "course_reasons": [
                        item.model_dump(mode="json")
                        for item in eligibility.violations
                        if item.scope == "COURSE"
                    ],
                    "project_reasons": [
                        item.model_dump(mode="json")
                        for item in eligibility.violations
                        if item.scope == "PROJECT"
                    ],
                    "time_reasons": [
                        item.model_dump(mode="json")
                        for item in eligibility.violations
                        if item.scope == "SESSION"
                    ],
                    "data_reasons": [
                        item.model_dump(mode="json")
                        for item in eligibility.violations
                        if item.scope == "DATA"
                    ],
                }
        elif request.name == "recommend_selection_plans":
            data = await recommendation_tool(
                session,
                student_id=student_id,
                term=term,
                preferences=plan.preferences,
                scope=plan.recommendation_scope,
                resolved=resolved,
            )
        else:  # pragma: no cover - validate_plan rejects this first
            continue
        results.append({"name": request.name, "data": data})
    return results
