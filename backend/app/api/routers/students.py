from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.crud import students as student_crud
from app.db.session import get_db_session
from app.schemas.auth import UserProfile
from app.services.semester_course_service import get_active_term
from app.crud.teaching_tasks import get_or_create_active_term

router = APIRouter(prefix="/students", tags=["学生"])


@router.get("/me/busy-bitmap")
async def get_my_bitmap(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
):
    """获取当前学生的忙闲位图。"""
    if current_user.user_type != "STUDENT":
        raise HTTPException(status_code=403, detail="仅学生可查询")

    # 获取当前活跃学期
    term = await get_or_create_active_term(session)

    # 通过 student_no 找到 student_id
    from sqlalchemy import select
    from app.models.identity import Student
    student = (await session.execute(
        select(Student).where(Student.student_no == current_user.login_name.upper())
    )).scalar_one_or_none()
    if student is None:
        raise HTTPException(status_code=404, detail="学生信息不存在")

    bitmap = await student_crud.get_student_bitmap(session, student.id, term.id)
    if bitmap is None:
        return {"weeks": 18, "days": 7, "slots": 12, "data": None}

    import base64
    return {
        "weeks": bitmap.end_week - bitmap.start_week + 1,
        "days": bitmap.days_per_week,
        "slots": bitmap.slots_per_day,
        "data": base64.b64encode(bitmap.bitmap).decode(),
        "start_week": bitmap.start_week,
        "end_week": bitmap.end_week,
    }
