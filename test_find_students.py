"""Find 20 students eligible for 张伟's 光的干涉与衍射 at Week 16 Mon 1-4."""
import asyncio
import sys
sys.path.insert(0, "backend")

from sqlalchemy import select
from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models.curriculum import ExperimentCourse, ExperimentProject
from app.models.identity import Student, Teacher, StudentBusyBitmap
from app.models.scheduling import ExperimentSession
from app.models.enrollment import StudentProjectRecord


async def main():
    async with AsyncSessionFactory() as session:
        teacher = await session.scalar(select(Teacher).where(Teacher.name == "张伟"))
        if not teacher:
            print("张伟 not found"); return
        print(f"Teacher: {teacher.name} ({teacher.employee_no})")

        project = await session.scalar(
            select(ExperimentProject).where(ExperimentProject.project_name == "光的干涉与衍射")
        )
        if not project:
            print("光的干涉与衍射 not found"); return

        s = await session.scalar(
            select(ExperimentSession).where(
                ExperimentSession.teacher_id == teacher.id,
                ExperimentSession.project_id == project.id,
                ExperimentSession.week_no == 16,
                ExperimentSession.day_of_week == 2,
                ExperimentSession.start_slot == 1,
                ExperimentSession.end_slot == 4,
            )
        )
        if not s:
            print("Session not found"); return

        print(f"Session: W{s.week_no} D{s.day_of_week} S{s.start_slot}-{s.end_slot} cap={s.capacity} sel={s.selected_count}")

        course = await session.get(ExperimentCourse, project.course_id)
        students = (await session.execute(select(Student).order_by(Student.student_no))).scalars().all()
        print(f"Checking {len(students)} students...")

        eligible = []
        for stu in students:
            # Time conflict check
            bmp = await session.scalar(
                select(StudentBusyBitmap).where(StudentBusyBitmap.student_id == stu.id)
            )
            if bmp:
                byte_idx = ((s.week_no - bmp.start_week) * bmp.days_per_week * bmp.slots_per_day
                            + (s.day_of_week - 1) * bmp.slots_per_day
                            + (s.start_slot - 1))
                has_conflict = False
                for slot in range(4):
                    idx = byte_idx + slot
                    if idx // 8 < len(bmp.bitmap):
                        if bmp.bitmap[idx // 8] & (1 << (idx % 8)):
                            has_conflict = True
                            break
                if has_conflict:
                    continue

            # Already selected this project?
            ex = await session.scalar(
                select(StudentProjectRecord).where(
                    StudentProjectRecord.student_id == stu.id,
                    StudentProjectRecord.project_id == project.id,
                    StudentProjectRecord.status.in_(["SELECTED", "COMPLETED", "MAKEUP_PENDING"]),
                )
            )
            if ex:
                continue

            eligible.append(stu)
            if len(eligible) >= 20:
                break

        print(f"\nEligible: {len(eligible)}")
        for i, stu in enumerate(eligible):
            print(f"  {i+1}. {stu.name} {stu.student_no} (major={stu.major_id})")

    await dispose_database_engine()

asyncio.run(main())
