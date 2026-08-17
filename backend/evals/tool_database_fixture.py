from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enrollment import StudentProjectRecord
from app.models.scheduling import ExperimentSession, ScheduleVersion
from app.schemas.student_consultation import weekday_name, weekday_number
from evals.config import FIXTURE_DIR

ACTIVE_RECORD_STATUSES = {"SELECTED", "COMPLETED", "ABSENT", "MAKEUP_PENDING"}
SHANGHAI = ZoneInfo("Asia/Shanghai")


def load_tool_database_fixture(fixture_id: str) -> dict[str, Any]:
    path = Path(FIXTURE_DIR) / "tool_database_states.json"
    fixtures = json.loads(path.read_text(encoding="utf-8"))
    if fixture_id not in fixtures:
        raise KeyError(f"未知工具数据库fixture：{fixture_id}")
    return deepcopy(fixtures[fixture_id])


async def _published_session(
    session: AsyncSession,
    *,
    term_id: UUID,
    selector: dict[str, Any],
) -> ExperimentSession:
    version_id = await session.scalar(
        select(ScheduleVersion.id).where(
            ScheduleVersion.term_id == term_id,
            ScheduleVersion.status == "PUBLISHED",
        )
    )
    if version_id is None:
        raise RuntimeError("工具评测fixture需要当前学期存在已发布课表。")
    candidates = list(
        (
            await session.execute(
                select(ExperimentSession)
                .options(
                    selectinload(ExperimentSession.project),
                    selectinload(ExperimentSession.teacher),
                    selectinload(ExperimentSession.laboratory),
                )
                .where(
                    ExperimentSession.schedule_version_id == version_id,
                    ExperimentSession.week_no == selector["week_no"],
                    ExperimentSession.day_of_week
                    == weekday_number(selector["day_name"]),
                    ExperimentSession.start_slot == selector["start_slot"],
                    ExperimentSession.end_slot == selector["end_slot"],
                )
            )
        )
        .scalars()
        .all()
    )
    matches = [
        item
        for item in candidates
        if item.project is not None
        and item.project.project_name == selector["project_name"]
        and item.teacher is not None
        and item.teacher.name == selector["teacher_name"]
    ]
    if len(matches) != 1:
        description = (
            f'{selector["project_name"]} 第{selector["week_no"]}周'
            f'{selector["day_name"]} 第{selector["start_slot"]}—'
            f'{selector["end_slot"]}节 {selector["teacher_name"]}老师'
        )
        raise RuntimeError(
            f"工具评测fixture无法唯一定位已发布场次：{description}；匹配数={len(matches)}。"
        )
    return matches[0]


def _shift_term_clock(term: Any, fixture: dict[str, Any]) -> None:
    """让“已开始/未开始”在不同运行日期仍保持一致，事务结束后回滚。"""

    current_week = int(
        fixture.get("relative_term_clock", {}).get("current_teaching_week", 5)
    )
    today = datetime.now(SHANGHAI).date()
    # 令今天落在指定教学周内。session_calendar_date 会自行回溯到首个周日。
    term.start_date = today - timedelta(weeks=current_week - 1)
    term.end_date = term.start_date + timedelta(days=term.total_weeks * 7 - 1)


async def apply_tool_database_fixture(
    session: AsyncSession,
    *,
    fixture_id: str,
    student_id: UUID,
    term: Any,
) -> list[dict[str, Any]]:
    """在当前未提交事务中准备确定的学生记录；调用方必须最终 rollback。"""

    fixture = load_tool_database_fixture(fixture_id)
    _shift_term_clock(term, fixture)
    await session.execute(
        update(StudentProjectRecord)
        .where(
            StudentProjectRecord.student_id == student_id,
            StudentProjectRecord.term_id == term.id,
            StudentProjectRecord.status.in_(ACTIVE_RECORD_STATUSES),
        )
        .values(
            status="WITHDRAWN",
            withdrawn_at=datetime.now(UTC),
            version_no=StudentProjectRecord.version_no + 1,
        )
    )
    await session.flush()

    summaries: list[dict[str, Any]] = []
    for record_fixture in fixture.get("records", []):
        target = await _published_session(
            session,
            term_id=term.id,
            selector=record_fixture,
        )
        assert target.project is not None
        session.add(
            StudentProjectRecord(
                student_id=student_id,
                term_id=term.id,
                course_id=target.project.course_id,
                project_id=target.project_id,
                session_id=target.id,
                requirement_type=record_fixture["requirement_type"],
                status=record_fixture["status"],
                selected_at=datetime.now(UTC),
                report_status="NOT_REQUIRED",
                version_no=1,
            )
        )
        summaries.append(
            {
                "status": record_fixture["status"],
                "course_name": "工程物理实验",
                "project_name": target.project.project_name,
                "requirement_type": record_fixture["requirement_type"],
                "week_no": target.week_no,
                "day_name": weekday_name(target.day_of_week),
                "start_slot": target.start_slot,
                "end_slot": target.end_slot,
                "teacher_name": target.teacher.name if target.teacher else "",
                "laboratory_name": (
                    target.laboratory.name if target.laboratory else ""
                ),
            }
        )
    await session.flush()
    return summaries


def context_with_database_fixture(
    base_context: dict[str, Any],
    selections: list[dict[str, Any]],
) -> dict[str, Any]:
    context = deepcopy(base_context)
    context["current_selections"] = selections
    status_by_project = {
        item["project_name"]: item["status"]
        for item in selections
        if item["status"] in ACTIVE_RECORD_STATUSES
    }
    for course in context.get("training_plan_summary", {}).get("courses", []):
        for project in course.get("projects", []):
            project["student_status"] = (
                status_by_project.get(project.get("project_name"), "AVAILABLE")
            )
    return context
