from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import students as student_crud
from app.crud import teaching_tasks as tt_crud
from app.crud import training_plans as tp_crud
from app.models.scheduling import ProjectDemand, TeachingTask
from app.schemas.teaching_task import (
    CreateTeachingTaskRequest,
    MajorInfo,
    ProjectDemandOut,
    ProjectInfo,
    TeachingTaskCohortOut,
    TeachingTaskListResponse,
    TeachingTaskOut,
    TermInfo,
    UpdateTeachingTaskRequest,
)
from app.schemas.training_plan import CourseInfo

BUFFER_RATIO = Decimal("1.20")


def _to_term_info(term) -> TermInfo:
    return TermInfo(
        id=term.id,
        code=term.code,
        academic_year=term.academic_year,
        semester_no=term.semester_no,
        start_date=term.start_date,
        end_date=term.end_date,
        total_weeks=term.total_weeks,
        status=term.status,
    )


def _to_course_info(course) -> CourseInfo:
    return CourseInfo(
        id=course.id,
        course_code=course.course_code,
        course_name=course.course_name,
    )


def _to_major_info(major) -> MajorInfo:
    return MajorInfo(id=major.id, code=major.code, name=major.name)


def _to_project_info(project) -> ProjectInfo:
    return ProjectInfo(
        id=project.id,
        project_code=project.project_code,
        project_name=project.project_name,
        category=project.category,
    )


async def _enrich_task_out(task, session: AsyncSession) -> TeachingTaskOut:
    """构建 TeachingTaskOut，含教师和设备信息。"""
    project_ids = [d.project_id for d in (task.demands or []) if d.project_id]
    teachers_map = await tt_crud.get_teachers_by_project_ids(session, project_ids)
    equipment_map = await tt_crud.get_equipment_by_project_ids(session, project_ids)

    return TeachingTaskOut(
        id=task.id,
        task_code=task.task_code,
        course=_to_course_info(task.course),
        term=_to_term_info(task.term),
        planned_student_count=task.planned_student_count,
        week_start=task.week_start,
        week_end=task.week_end,
        status=task.status,
        cohorts=[
            TeachingTaskCohortOut(
                id=c.id,
                major=_to_major_info(c.major),
                enrollment_year=c.enrollment_year,
                student_count=c.student_count,
            )
            for c in (task.cohorts or [])
        ],
        demands=[
            ProjectDemandOut(
                id=d.id,
                project=_to_project_info(d.project),
                requirement_type=d.requirement_type,
                base_demand=d.base_demand,
                prediction_ratio=d.prediction_ratio,
                buffer_ratio=d.buffer_ratio,
                required_capacity=d.required_capacity,
                required_session_count=d.required_session_count,
                teachers=teachers_map.get(d.project_id, []),
                equipment=equipment_map.get(d.project_id, []),
            )
            for d in (task.demands or [])
        ],
    )


def _to_task_out(task) -> TeachingTaskOut:
    return TeachingTaskOut(
        id=task.id,
        task_code=task.task_code,
        course=_to_course_info(task.course),
        term=_to_term_info(task.term),
        planned_student_count=task.planned_student_count,
        week_start=task.week_start,
        week_end=task.week_end,
        status=task.status,
        cohorts=[
            TeachingTaskCohortOut(
                id=c.id,
                major=_to_major_info(c.major),
                enrollment_year=c.enrollment_year,
                student_count=c.student_count,
            )
            for c in (task.cohorts or [])
        ],
        demands=[
            ProjectDemandOut(
                id=d.id,
                project=_to_project_info(d.project),
                requirement_type=d.requirement_type,
                base_demand=d.base_demand,
                prediction_ratio=d.prediction_ratio,
                buffer_ratio=d.buffer_ratio,
                required_capacity=d.required_capacity,
                required_session_count=d.required_session_count,
            )
            for d in (task.demands or [])
        ],
    )


async def get_active_term(session: AsyncSession) -> TermInfo:
    term = await tt_crud.get_or_create_active_term(session)
    return _to_term_info(term)


async def list_teaching_tasks(
    session: AsyncSession,
) -> TeachingTaskListResponse:
    term = await tt_crud.get_or_create_active_term(session)
    tasks = await tt_crud.get_teaching_tasks(session, term.id)
    return TeachingTaskListResponse(
        items=[await _enrich_task_out(t, session) for t in tasks],
        total=len(tasks),
    )


async def sync_teaching_task(
    session: AsyncSession,
    data: CreateTeachingTaskRequest,
    created_by: UUID,
) -> TeachingTaskOut:
    """创建或刷新教学任务：统计学生 → 创建队列 → 计算项目需求。"""
    term = await tt_crud.get_or_create_active_term(session)
    base_year = int(term.academic_year.split("-")[0])  # "2026-2027" → 2026

    # 1. 检查是否已存在
    existing = await tt_crud.get_task_by_course_term(
        session, data.course_id, term.id
    )
    if existing is not None:
        await tt_crud.clear_task_children(session, existing.id)
        task = existing
        task.planned_student_count = 0
        task.week_start = data.week_start
        task.week_end = data.week_end
        task.updated_by = created_by
    else:
        task = await tt_crud.create_teaching_task(
            session, data.course_id, term.id,
            data.week_start, data.week_end, 0, created_by,
        )

    # 2. 获取培养方案中的课程配置
    plan_courses = await tt_crud.get_training_plan_courses_for_course(
        session, data.course_id
    )

    # 3. 按 (major_id, study_year) 推算目标入学年份，统计学生
    #    公式：target_enrollment_year = base_year - (study_year - 1)
    #    例如 2026-2027 学年，study_year=2 → 2025 级学生
    cohort_student_map: dict[tuple[UUID, int], int] = {}  # (major_id, enrollment_year) → count
    all_cohorts: set[tuple[UUID, int]] = set()  # unique (major_id, enrollment_year) tuples

    for pc in plan_courses:
        target_year = base_year - (pc.study_year - 1)
        all_cohorts.add((pc.plan.major_id, target_year))

    # 批量统计学生
    cohort_list = list(all_cohorts)
    raw_counts = await student_crud.count_students_by_major_year_list(
        session, cohort_list
    )
    for (major_id, enroll_year), count in raw_counts.items():
        if count > 0:
            cohort_student_map[(major_id, enroll_year)] = count

    # 4. 创建队列（每个实际有学生的组合一条）
    total_students = 0
    for (major_id, enroll_year), count in cohort_student_map.items():
        await tt_crud.add_task_cohort(
            session, task.id, major_id, enroll_year, count
        )
        total_students += count

    task.planned_student_count = total_students

    # 5. 计算项目需求（按 project_id 去重，聚合多个培养方案的同一项目需求）
    buffer = BUFFER_RATIO
    seen_projects: dict[UUID, dict] = {}

    for pc in plan_courses:
        target_year = base_year - (pc.study_year - 1)
        cohort_students = cohort_student_map.get((pc.plan.major_id, target_year), 0)
        if cohort_students == 0:
            continue

        for tp in (pc.projects or []):
            project = tp.project
            if project is None:
                continue

            pid = project.id
            if pid not in seen_projects:
                seen_projects[pid] = {
                    "project": project,
                    "requirement_type": tp.requirement_type,
                    "base_demand": 0,
                    "prediction_ratio": Decimal("1.00") if tp.requirement_type == "REQUIRED" else (project.historical_selection_ratio or Decimal("0.50")),
                    "group_size": project.default_group_size or 2,
                }

            agg = seen_projects[pid]
            if tp.requirement_type == "REQUIRED":
                # 必做：每个学生都要完成这项实验 → base = 学生人数
                agg["base_demand"] += cohort_students
            else:
                # 选做：只有往届比例的学生会选 → base = 学生人数 × 往届选择比
                ratio = project.historical_selection_ratio or Decimal("0.50")
                agg["base_demand"] += int(cohort_students * ratio)

    for pid, agg in seen_projects.items():
        required_capacity = int(Decimal(str(agg["base_demand"])) * buffer)
        await tt_crud.add_project_demand(
            session, task.id, pid, agg["requirement_type"],
            base_demand=agg["base_demand"],
            prediction_ratio=agg["prediction_ratio"],
            buffer_ratio=buffer,
            required_capacity=required_capacity,
            group_size=agg["group_size"],
        )

    await session.commit()

    # 重新加载完整数据
    task = await tt_crud.get_teaching_task_by_id(session, task.id)
    return _to_task_out(task)


async def delete_project_demand(
    session: AsyncSession, demand_id: UUID
) -> bool:
    demand = await session.get(ProjectDemand, demand_id)
    if demand is None:
        return False
    await session.delete(demand)
    await session.commit()
    return True


async def add_project_to_task(
    session: AsyncSession, task_id: UUID, data: dict
) -> TeachingTaskOut | None:
    task = await tt_crud.get_teaching_task_by_id(session, task_id)
    if task is None:
        return None
    project_id = UUID(data["project_id"])
    requirement_type = data.get("requirement_type", "REQUIRED")
    base_demand = data.get("base_demand", task.planned_student_count)
    capacity = int(Decimal(str(base_demand)) * BUFFER_RATIO)
    await tt_crud.add_project_demand(
        session, task_id, project_id, requirement_type,
        base_demand=base_demand,
        prediction_ratio=Decimal("1.00") if requirement_type == "REQUIRED" else Decimal("0.50"),
        buffer_ratio=BUFFER_RATIO,
        required_capacity=capacity,
        group_size=2,
    )
    await session.commit()
    task = await tt_crud.get_teaching_task_by_id(session, task_id)
    return await _enrich_task_out(task, session)


async def update_teaching_task(
    session: AsyncSession,
    task_id: UUID,
    data: UpdateTeachingTaskRequest,
) -> TeachingTaskOut | None:
    """更新教学任务的周范围等字段。"""
    task = await tt_crud.get_teaching_task_by_id(session, task_id)
    if task is None:
        return None
    task.week_start = data.week_start
    task.week_end = data.week_end
    await session.commit()
    task = await tt_crud.get_teaching_task_by_id(session, task_id)
    return _to_task_out(task)


async def delete_teaching_task(
    session: AsyncSession, task_id: UUID
) -> bool:
    """删除教学任务（级联删除队列和需求）。"""
    task = await session.get(TeachingTask, task_id)
    if task is None:
        return False
    await session.delete(task)
    await session.commit()
    return True


async def update_project_demand(
    session: AsyncSession, demand_id: UUID, data: dict
) -> ProjectDemandOut | None:
    """更新项目需求的容量。"""
    demand = await session.get(ProjectDemand, demand_id)
    if demand is None:
        return None
    if "required_capacity" in data:
        demand.required_capacity = data["required_capacity"]
        demand.required_session_count = max(
            1,
            data["required_capacity"] // (demand.calculation_snapshot.get("group_size", 2)),
        )
    await session.commit()
    # re-fetch with relationships
    from app.crud.teaching_tasks import get_teaching_task_by_id
    task = await get_teaching_task_by_id(session, demand.task_id)
    if task:
        for d in (task.demands or []):
            if d.id == demand.id:
                demand = d
                break
    return ProjectDemandOut(
        id=demand.id,
        project=_to_project_info(demand.project),
        requirement_type=demand.requirement_type,
        base_demand=demand.base_demand,
        prediction_ratio=demand.prediction_ratio,
        buffer_ratio=demand.buffer_ratio,
        required_capacity=demand.required_capacity,
        required_session_count=demand.required_session_count,
    )


async def sync_all_teaching_tasks(
    session: AsyncSession, created_by: UUID
) -> TeachingTaskListResponse:
    """为所有实验课程（有已发布培养方案的）创建教学任务。"""
    term = await tt_crud.get_or_create_active_term(session)
    courses = await tp_crud.get_active_courses(session)
    experiment_courses = [c for c in courses if c.course_type == "EXPERIMENT"]

    for course in experiment_courses:
        # 检查这门课是否有已发布的培养方案
        cohorts = await tt_crud.get_target_cohorts(session, course.id)
        if not cohorts:
            continue
        # 创建或刷新教学任务
        existing = await tt_crud.get_task_by_course_term(session, course.id, term.id)
        week_start = existing.week_start if existing is not None else 2
        week_end = existing.week_end if existing is not None else 16
        from app.schemas.teaching_task import CreateTeachingTaskRequest
        await sync_teaching_task(
            session,
            CreateTeachingTaskRequest(
                course_id=course.id,
                week_start=week_start,
                week_end=week_end,
            ),
            created_by,
        )

    tasks = await tt_crud.get_teaching_tasks(session, term.id)
    return TeachingTaskListResponse(
        items=[await _enrich_task_out(t, session) for t in tasks],
        total=len(tasks),
    )
