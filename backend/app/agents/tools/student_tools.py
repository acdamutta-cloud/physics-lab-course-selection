from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.student_selection_rules import rules_for_topics
from app.models.curriculum import AcademicTerm, ExperimentCourse
from app.models.enrollment import StudentProjectRecord
from app.models.identity import Teacher
from app.models.rules import RuleConfig, RuleSet
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.schemas.student_consultation import (
    EntityReference,
    RecommendationScope,
    SelectionPreferences,
    StudentAgentPlan,
    StudentRuleTopic,
    weekday_name,
    weekday_number,
)
from app.services.effective_session_service import effective_session_values
from app.services.operation_guide_service import search_operation_guides
from app.services.student_adjustment_service import get_adjustment_context
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


async def prepare_adjustment_entry_tool(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    plan: StudentAgentPlan,
    question: str,
) -> dict[str, object]:
    request_type = plan.requested_application_type
    if request_type not in {"RESCHEDULE", "PROJECT_CHANGE", "MAKEUP"}:
        return {
            "status": "NOT_FOUND",
            "title": "无法确定申请类型",
            "message": "请说明你想调课、换组还是申请补做。",
            "sources": [],
        }
    context = await get_adjustment_context(
        session,
        student_id=student_id,
        term=term,
        request_type=None,
    )
    reference = plan.entity_reference or EntityReference()
    has_source_locator = any(
        (
            reference.project_name,
            reference.course_name,
            reference.week_no is not None,
            reference.day_name,
            reference.start_slot is not None,
            reference.end_slot is not None,
            reference.teacher_name,
        )
    )
    matched_all: list[dict[str, object]] = []
    for source in context.get("sources", []):
        if not isinstance(source, dict) or not isinstance(source.get("session"), dict):
            continue
        item = source["session"]
        if reference.project_name and not _canonical_name_matches(
            str(item.get("project_name", "")), reference.project_name
        ):
            continue
        if reference.course_name and not _canonical_name_matches(
            str(item.get("course_name", "")), reference.course_name
        ):
            continue
        if reference.week_no is not None and item.get("week_no") != reference.week_no:
            continue
        if reference.day_name is not None and item.get("day_name") != reference.day_name:
            continue
        if reference.start_slot is not None and item.get("start_slot") != reference.start_slot:
            continue
        if reference.end_slot is not None and item.get("end_slot") != reference.end_slot:
            continue
        if reference.teacher_name and not _canonical_name_matches(
            str(item.get("teacher_name", "")), reference.teacher_name
        ):
            continue
        matched_all.append(source)
    matched = [
        source
        for source in matched_all
        if request_type in source.get("available_for", [])
    ]
    status = "UNIQUE" if len(matched) == 1 else "MULTIPLE" if matched else "NOT_FOUND"
    labels = {
        "RESCHEDULE": "调课",
        "PROJECT_CHANGE": "换组",
        "MAKEUP": "补做",
    }
    if status == "UNIQUE":
        message = "已根据你的描述匹配到以下原实验，请核对后开始操作。"
    elif status == "MULTIPLE":
        message = "匹配到多个可申请的原实验，请选择你要调整的一个。"
    elif matched_all and has_source_locator:
        status = "INELIGIBLE"
        source = matched_all[0]
        source_session = source.get("session", {})
        project_name = str(source_session.get("project_name") or "该实验")
        week_no = source_session.get("week_no")
        day_name = str(source_session.get("day_name") or "")
        start_slot = source_session.get("start_slot")
        end_slot = source_session.get("end_slot")
        time_text = (
            f"第{week_no}周{day_name} 第{start_slot}—{end_slot}节"
            if week_no is not None and start_slot is not None and end_slot is not None
            else "当前场次"
        )
        started = bool(source_session.get("started"))
        record_status = str(source.get("status") or "")
        requirement_type = str(source_session.get("requirement_type") or "")
        if request_type == "RESCHEDULE" and started:
            reason = "该场次已经开始，当前不能申请调课"
        elif request_type == "RESCHEDULE":
            reason = "该记录当前不是可调课的已选状态"
        elif request_type == "PROJECT_CHANGE" and requirement_type != "OPTIONAL":
            reason = "该实验是必做项目，不能申请换组；换组只适用于选做项目"
        elif request_type == "PROJECT_CHANGE" and started:
            reason = "该选做项目的场次已经开始，当前不能申请换组"
        elif request_type == "PROJECT_CHANGE":
            reason = "该记录当前不是可换组的已选状态"
        elif request_type == "MAKEUP" and not started:
            reason = "原场次尚未开始，当前不能申请补做"
        elif request_type == "MAKEUP" and record_status == "COMPLETED":
            reason = "该实验已经完成，当前不能申请补做"
        else:
            reason = f"该记录当前不符合{labels[request_type]}申请条件"
        message = f"已找到“{project_name}”（{time_text}），但{reason}。"
    elif matched_all and request_type == "PROJECT_CHANGE":
        status = "INELIGIBLE"
        required_count = sum(
            1
            for source in matched_all
            if str(source.get("session", {}).get("requirement_type") or "")
            != "OPTIONAL"
        )
        started_optional_count = sum(
            1
            for source in matched_all
            if str(source.get("session", {}).get("requirement_type") or "")
            == "OPTIONAL"
            and bool(source.get("session", {}).get("started"))
        )
        details: list[str] = []
        if required_count:
            details.append(f"{required_count}个是必做项目")
        if started_optional_count:
            details.append(f"{started_optional_count}个选做项目的场次已经开始")
        detail_text = "，".join(details)
        message = "当前已选记录中没有可以申请换组的项目。换组只适用于尚未开始的选做项目。"
        if detail_text:
            message += f"当前记录中{detail_text}。"
    else:
        message = "没有在当前可申请的已选实验中找到同时符合这些条件的记录，请核对项目、周次、时间或教师。"
    return {
        "status": status,
        "title": (
            f"当前不能{labels[request_type]}"
            if status == "INELIGIBLE"
            else f"确认{labels[request_type]}原实验"
        ),
        "message": message,
        "request_type": request_type,
        "sources": matched,
        "matched_sources": matched_all,
        "preferences": plan.preferences.model_dump(mode="json"),
        "original_question": question,
        "requires_confirmation": bool(matched),
    }


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


async def preview_deselection_tool(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    plan: StudentAgentPlan,
    resolved: dict[str, object],
) -> dict[str, object]:
    records = list(
        (
            await session.execute(
                select(StudentProjectRecord)
                .options(
                    selectinload(StudentProjectRecord.session).selectinload(
                        ExperimentSession.project
                    )
                )
                .where(
                    StudentProjectRecord.student_id == student_id,
                    StudentProjectRecord.term_id == term.id,
                    StudentProjectRecord.status == "SELECTED",
                )
            )
        )
        .scalars()
        .all()
    )
    if not records:
        return {"sessions": [], "message": "你本学期当前没有可以取消的已选实验场次。"}

    course_ids = {str(item) for item in resolved.get("course_ids", [])}
    project_ids = {str(item) for item in resolved.get("project_ids", [])}
    reference = plan.entity_reference or EntityReference()
    has_target_locator = bool(
        course_ids
        or project_ids
        or reference.week_no is not None
        or reference.day_name is not None
        or reference.start_slot is not None
        or reference.end_slot is not None
        or reference.teacher_name
    )
    if plan.deselection_scope != "ALL" and not has_target_locator:
        return {
            "sessions": [],
            "status": "NEED_TARGET",
            "message": "请说明要取消的课程、实验项目或场次；只有明确说“取消全部选课”时，系统才会展示全部退选清单。",
        }
    session_models = [record.session for record in records if record.session is not None]
    effective = await effective_session_values(session, session_models)
    course_map = {
        str(course.id): course
        for course in (
            await session.execute(
                select(ExperimentCourse).where(
                    ExperimentCourse.id.in_({record.course_id for record in records})
                )
            )
        )
        .scalars()
        .all()
    }
    matched: list[dict[str, object]] = []
    for record in records:
        item = record.session
        if item is None or item.project is None:
            continue
        actual = effective[item.id]
        if plan.deselection_scope != "ALL":
            if course_ids and str(record.course_id) not in course_ids:
                continue
            if project_ids and str(record.project_id) not in project_ids:
                continue
            if reference.week_no is not None and actual["week_no"] != reference.week_no:
                continue
            if (
                reference.day_name is not None
                and actual["day_of_week"] != weekday_number(reference.day_name)
            ):
                continue
            if (
                reference.start_slot is not None
                and actual["start_slot"] != reference.start_slot
            ):
                continue
            if reference.end_slot is not None and actual["end_slot"] != reference.end_slot:
                continue
            if reference.teacher_name:
                teacher = await session.get(Teacher, actual["teacher_id"])
                if teacher is None or not _canonical_name_matches(
                    teacher.name, reference.teacher_name
                ):
                    continue
        course = course_map.get(str(record.course_id))
        teacher = await session.get(Teacher, actual["teacher_id"])
        matched.append(
            {
                "session_id": str(item.id),
                "course_id": str(record.course_id),
                "course_name": course.course_name if course else "",
                "course_code": course.course_code if course else "",
                "project_id": str(record.project_id),
                "project_name": item.project.project_name,
                "week_no": actual["week_no"],
                "day_of_week": actual["day_of_week"],
                "day_name": weekday_name(actual["day_of_week"]),
                "start_slot": actual["start_slot"],
                "end_slot": actual["end_slot"],
                "teacher_name": teacher.name if teacher else "",
            }
        )
    matched.sort(
        key=lambda item: (
            str(item["course_name"]),
            int(item["week_no"]),
            int(item["day_of_week"]),
            int(item["start_slot"]),
        )
    )
    if not matched:
        return {
            "sessions": [],
            "message": "没有在你本学期当前已选场次中找到同时符合这些条件的记录，请调整课程、项目、周次、时间或教师描述。",
        }
    return {
        "sessions": matched,
        "scope": plan.deselection_scope,
        "requires_confirmation": True,
    }


async def lookup_student_rules_tool(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    topics: list[StudentRuleTopic],
) -> dict[str, object]:
    """Return only published public rules for controlled topics."""

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
    status = "FOUND" if matched_rules else "NOT_FOUND"
    return {
        "status": status,
        "matched_rules": matched_rules,
        "source_references": [
            {"source_type": "SELECTION_RULE", "rule_code": item["rule_code"]}
            for item in matched_rules
        ],
        "unknowns": [],
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

    if plan.intent == "DESELECT_SELECTION" and plan.deselection_scope == "TARGETED":
        course_names = [*reference.course_names]
        if reference.course_name:
            course_names.append(reference.course_name)
        resolved_course_ids: list[str] = []
        for name in dict.fromkeys(course_names):
            matches = [
                item
                for item in courses
                if isinstance(item, dict)
                and _canonical_name_matches(str(item.get("course_name", "")), name)
            ]
            if len(matches) != 1:
                return resolved, f"无法唯一确定课程“{name}”，请换一种说法描述课程名称。"
            resolved_course_ids.append(str(matches[0]["course_id"]))
        resolved["course_ids"] = resolved_course_ids

        project_names = [*reference.project_names]
        if reference.project_name:
            project_names.append(reference.project_name)
        resolved_project_ids: list[str] = []
        for name in dict.fromkeys(project_names):
            matches: list[dict[str, object]] = []
            for course in courses:
                if not isinstance(course, dict):
                    continue
                if resolved_course_ids and str(course.get("course_id")) not in set(
                    resolved_course_ids
                ):
                    continue
                for project in course.get("projects", []):
                    if isinstance(project, dict) and _canonical_name_matches(
                        str(project.get("project_name", "")), name
                    ):
                        matches.append(project)
            if len(matches) != 1:
                return resolved, f"无法唯一确定实验项目“{name}”，请换一种说法描述项目名称。"
            resolved_project_ids.append(str(matches[0]["project_id"]))
        resolved["project_ids"] = resolved_project_ids

        has_locator = bool(
            resolved_course_ids
            or resolved_project_ids
            or reference.week_no is not None
            or reference.day_name is not None
            or reference.start_slot is not None
            or reference.end_slot is not None
            or reference.teacher_name
        )
        if not has_locator:
            return resolved, "请说明要取消的课程、实验项目或场次条件，也可以直接说“取消全部选课”。"

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
                    "student_status": project_matches[0].get(
                        "student_status", "NOT_SELECTED"
                    ),
                    "requirement_type": project_matches[0].get("requirement_type"),
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


async def _deselect_single(
    session: AsyncSession, *, student_id: UUID, term_id: UUID, session_id: UUID
) -> dict[str, object]:
    from app.db.redis_client import get_redis_client
    from app.services.selection_service import deselect_session

    redis = get_redis_client()
    result = await deselect_session(
        redis, session, student_id=student_id, term_id=term_id, session_id=session_id
    )
    return {"session_id": str(session_id), "result": result.result, "message": result.message}


async def _deselect_all(
    session: AsyncSession, *, student_id: UUID, term_id: UUID
) -> dict[str, object]:
    from sqlalchemy import select as _select

    from app.db.redis_client import get_redis_client
    from app.models.enrollment import StudentProjectRecord
    from app.services.selection_service import deselect_session

    redis = get_redis_client()
    records = (
        await session.execute(
            _select(StudentProjectRecord).where(
                StudentProjectRecord.student_id == student_id,
                StudentProjectRecord.term_id == term_id,
                StudentProjectRecord.status == "SELECTED",
            )
        )
    ).scalars().all()
    results = []
    for record in records:
        result = await deselect_session(
            redis, session, student_id=student_id, term_id=term_id,
            session_id=record.session_id,
        )
        results.append(
            {
                "session_id": str(record.session_id),
                "result": result.result,
                "message": result.message,
            }
        )
    return {"total": len(records), "deselected": sum(1 for r in results if r["result"] == "ok"), "details": results}


async def execute_planned_tools(
    session: AsyncSession,
    *,
    student_id: UUID,
    term: AcademicTerm,
    plan: StudentAgentPlan,
    resolved: dict[str, object],
    question: str = "",
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
        elif request.name == "lookup_operation_guide":
            data = await search_operation_guides(session, query=question)
        elif request.name == "prepare_adjustment_entry":
            data = await prepare_adjustment_entry_tool(
                session,
                student_id=student_id,
                term=term,
                plan=plan,
                question=question,
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
        elif request.name == "preview_deselection":
            data = await preview_deselection_tool(
                session,
                student_id=student_id,
                term=term,
                plan=plan,
                resolved=resolved,
            )
        elif request.name == "deselect_course":
            session_ids = [UUID(str(sid)) for sid in resolved.get("session_ids", [])]
            details = []
            for sid in session_ids:
                details.append(
                    await _deselect_single(
                        session, student_id=student_id, term_id=term.id,
                        session_id=sid,
                    )
                )
            data = {"details": details}
        elif request.name == "deselect_all_courses":
            data = await _deselect_all(
                session, student_id=student_id, term_id=term.id
            )
        else:  # pragma: no cover - validate_plan rejects this first
            continue
        results.append({"name": request.name, "data": data})
    return results
