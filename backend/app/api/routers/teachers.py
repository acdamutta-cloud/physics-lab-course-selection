from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import get_current_user
from app.db.session import get_db_session
from app.models.curriculum import ExperimentCourse, ExperimentProject
from app.models.scheduling import ExperimentSession, ScheduleVersion

router = APIRouter(prefix="/teachers", tags=["教师"])


@router.get("/me/schedule")
async def get_my_schedule(
    week: int = Query(1, ge=1, le=20),
    session: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_user),
):
    """获取当前教师指定周次的课表（含项目、课程、实验室信息）。"""
    if current_user.user_type != "TEACHER":
        raise HTTPException(status_code=403, detail="仅教师可查询")

    # 找到教师记录
    from app.models.identity import Teacher
    teacher = (await session.execute(
        select(Teacher).where(Teacher.employee_no == current_user.login_name.upper())
    )).scalar_one_or_none()
    if teacher is None:
        raise HTTPException(status_code=404, detail="教师信息不存在")

    # 查已发布排课
    sessions = (await session.execute(
        select(ExperimentSession)
        .options(
            selectinload(ExperimentSession.project),
            selectinload(ExperimentSession.laboratory),
        )
        .join(ScheduleVersion, ScheduleVersion.id == ExperimentSession.schedule_version_id)
        .where(
            ExperimentSession.teacher_id == teacher.id,
            ExperimentSession.week_no == week,
            ScheduleVersion.status == "PUBLISHED",
        )
        .order_by(ExperimentSession.day_of_week, ExperimentSession.start_slot)
    )).scalars().all()

    result = []
    for s in sessions:
        course = (await session.execute(
            select(ExperimentCourse).where(ExperimentCourse.id == s.project.course_id)
        )).scalar_one_or_none() if s.project else None

        result.append({
            "id": str(s.id),
            "session_code": s.session_code,
            "day_of_week": s.day_of_week,
            "start_slot": s.start_slot,
            "end_slot": s.end_slot,
            "week_no": s.week_no,
            "capacity": s.capacity,
            "selected_count": s.selected_count,
            "project": {
                "id": str(s.project_id),
                "project_name": s.project.project_name if s.project else "",
                "project_code": s.project.project_code if s.project else "",
            },
            "course": {
                "course_name": course.course_name if course else "",
                "course_code": course.course_code if course else "",
            } if course else None,
            "laboratory": {
                "name": s.laboratory.name if s.laboratory else "",
                "lab_code": s.laboratory.lab_code if s.laboratory else "",
            } if s.laboratory else None,
        })
    return result
