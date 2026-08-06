"""生成学生课程完成数据。
- 按学生各自培养方案，只记录方案中的课程+先修课
- study_year < 当前年级 → 已修(PASSED/FAILED)
- study_year >= 当前年级 → 正在修(IN_PROGRESS)
- 先修课随对应实验课的study_year提前判断(实验课study_year=N，先修课按N-1处理)
- 不在培养方案的不生成记录
- 马佳宁(D2024010001)：DEMO-PHY201的某门先修课FAILED
"""
import asyncio
import random
from collections import defaultdict

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models.curriculum import (
    CoursePrerequisite,
    ExperimentCourse,
    TrainingPlan,
    TrainingPlanCourse,
)
from app.models.enrollment import StudentCourseCompletion
from app.models.identity import Student

MAJIANGNING_STUDENT_NO = "D2024010001"
MAJI_FAIL_CODE = "DEMO-TH-MATH101"


async def main():
    async with AsyncSessionFactory() as session:
        await session.execute(sql_delete(StudentCourseCompletion))
        await session.flush()

        students = (await session.execute(
            select(Student).where(Student.academic_status == "ACTIVE")
        )).scalars().all()

        all_courses = (await session.execute(select(ExperimentCourse))).scalars().all()
        course_by_id = {c.id: c for c in all_courses}
        course_by_code = {c.course_code: c for c in all_courses}

        # 缓存培养方案
        plan_cache: dict[tuple, TrainingPlan] = {}
        rng = random.Random(20260802)
        created = 0
        mjn_failed = False

        for student in students:
            year = student.enrollment_year
            # 2026-2027学年，入学年份→年级
            grade = 2026 - year + 1
            is_maji = student.student_no == MAJIANGNING_STUDENT_NO
            if grade < 1 or grade > 4:
                continue

            # 查培养方案
            cache_key = (student.major_id, year)
            if cache_key not in plan_cache:
                plan_cache[cache_key] = (await session.execute(
                    select(TrainingPlan)
                    .options(selectinload(TrainingPlan.courses))
                    .where(
                        TrainingPlan.major_id == student.major_id,
                        TrainingPlan.enrollment_year == year,
                        TrainingPlan.status == "PUBLISHED",
                    )
                    .order_by(TrainingPlan.version_no.desc())
                    .limit(1)
                )).scalar_one_or_none()
            plan = plan_cache[cache_key]
            if plan is None:
                continue

            # 课程→study_year映射
            course_study_year = {}
            for pc in plan.courses:
                sy = pc.study_year
                if pc.course_id not in course_study_year or sy < course_study_year[pc.course_id]:
                    course_study_year[pc.course_id] = sy

            # 先修课→对应实验课的study_year
            prereqs = (await session.execute(
                select(CoursePrerequisite).where(
                    CoursePrerequisite.plan_course_id.in_([pc.id for pc in plan.courses])
                )
            )).scalars().all()
            prereq_study_year = {}  # prerequisite_course_id → min_effective_study_year
            for p in prereqs:
                # 找到这个plan_course的study_year
                plan_course_sy = course_study_year.get(
                    next((pc.course_id for pc in plan.courses if pc.id == p.plan_course_id), None), 99
                )
                # 先修课应提前一年掌握
                effective_sy = max(1, plan_course_sy - 1)
                if p.prerequisite_course_id not in prereq_study_year or effective_sy < prereq_study_year[p.prerequisite_course_id]:
                    prereq_study_year[p.prerequisite_course_id] = effective_sy

            # 合并所有需要记录课程
            all_targets = set(course_study_year.keys()) | set(prereq_study_year.keys())

            for course_id in all_targets:
                course = course_by_id.get(course_id)
                if course is None:
                    continue

                # 判断应修学年
                if course_id in course_study_year:
                    required_sy = course_study_year[course_id]
                elif course_id in prereq_study_year:
                    required_sy = prereq_study_year[course_id]
                else:
                    continue

                # 马佳宁特殊处理
                if is_maji and course.course_code == MAJI_FAIL_CODE:
                    session.add(StudentCourseCompletion(
                        student_id=student.id, course_id=course_id, status="FAILED",
                    ))
                    mjn_failed = True
                    created += 1
                    continue

                # 判断状态
                if required_sy >= grade:
                    status = "IN_PROGRESS"
                else:
                    rate = 0.98 if grade == 4 else 0.95
                    status = "PASSED" if rng.random() < rate else "FAILED"

                session.add(StudentCourseCompletion(
                    student_id=student.id, course_id=course_id, status=status,
                ))
                created += 1

        await session.commit()
        print(f"Created {created} completion records for {len(students)} students")
        print(f"马佳宁挂科: {mjn_failed} ({MAJI_FAIL_CODE})")

    await dispose_database_engine()


if __name__ == "__main__":
    asyncio.run(main())
