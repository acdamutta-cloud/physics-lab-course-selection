from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import training_plans as tp_crud
from app.schemas.training_plan import (
    CourseInfo,
    CreateProjectRequest,
    CreateTrainingPlanRequest,
    MajorInfo,
    ProjectInfo,
    ProjectOrderConstraintOut,
    TrainingPlanCourseOut,
    TrainingPlanDetailOut,
    TrainingPlanListOut,
    TrainingPlanListResponse,
    TrainingPlanProjectOut,
    UpdateTrainingPlanRequest,
)


class TrainingPlanError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _to_major_info(major) -> MajorInfo:
    return MajorInfo(id=major.id, code=major.code, name=major.name)


def _to_course_info(course) -> CourseInfo:
    return CourseInfo(
        id=course.id,
        course_code=course.course_code,
        course_name=course.course_name,
        course_type=course.course_type,
    )


def _to_project_info(project) -> ProjectInfo:
    return ProjectInfo(
        id=project.id,
        project_code=project.project_code,
        project_name=project.project_name,
        category=project.category,
    )


def _to_course_out(course) -> TrainingPlanCourseOut:
    prerequisites = [
        item.prerequisite_course
        for item in course.prerequisites
        if item.prerequisite_course is not None
    ]
    return TrainingPlanCourseOut(
        id=course.id,
        course=_to_course_info(course.course),
        course_nature=course.course_nature,
        study_year=course.study_year,
        semester_no=course.semester_no,
        prerequisite_course=(
            _to_course_info(prerequisites[0]) if prerequisites else None
        ),
        prerequisite_courses=[_to_course_info(item) for item in prerequisites],
        required_project_count=course.required_project_count,
        optional_project_min_count=course.optional_project_min_count,
        order_rule_text=course.order_rule_text,
        allow_order_override=course.allow_order_override,
        projects=[
            TrainingPlanProjectOut(
                id=item.id,
                project=_to_project_info(item.project),
                requirement_type=item.requirement_type,
                display_order=item.display_order,
            )
            for item in sorted(
                course.projects, key=lambda project: project.display_order
            )
        ],
        order_constraints=[
            ProjectOrderConstraintOut(
                id=item.id,
                before_project=_to_project_info(item.before_project),
                after_project=_to_project_info(item.after_project),
                allow_override=item.allow_override,
                description=item.description,
            )
            for item in sorted(
                course.order_constraints,
                key=lambda constraint: (
                    constraint.before_project.project_code
                    if constraint.before_project is not None
                    else "",
                    constraint.after_project.project_code
                    if constraint.after_project is not None
                    else "",
                ),
            )
            if item.before_project is not None and item.after_project is not None
        ],
    )


def _is_complete(plan) -> bool:
    if not plan.courses:
        return False
    for course in plan.courses:
        required = sum(
            item.requirement_type == "REQUIRED" for item in course.projects
        )
        optional = sum(
            item.requirement_type == "OPTIONAL" for item in course.projects
        )
        if (
            not course.projects
            or course.required_project_count > required
            or course.optional_project_min_count > optional
            or not (
                getattr(course, "order_constraints", None)
                or (course.order_rule_text or "").strip()
            )
        ):
            return False
    return True


def _to_list_item(plan) -> TrainingPlanListOut:
    projects = [item for course in plan.courses for item in course.projects]
    prerequisite_names = sorted(
        {
            prerequisite.prerequisite_course.course_name
            for course in plan.courses
            for prerequisite in course.prerequisites
            if prerequisite.prerequisite_course is not None
        }
    )
    return TrainingPlanListOut(
        id=plan.id,
        plan_code=plan.plan_code,
        major=_to_major_info(plan.major),
        enrollment_year=plan.enrollment_year,
        version_no=plan.version_no,
        status=plan.status,
        effective_from=plan.effective_from,
        published_at=plan.published_at,
        updated_at=plan.updated_at,
        courses_count=len(plan.courses),
        required_projects_count=sum(
            item.requirement_type == "REQUIRED" for item in projects
        ),
        optional_projects_count=sum(
            item.requirement_type == "OPTIONAL" for item in projects
        ),
        prerequisite_names=prerequisite_names,
        completeness="COMPLETE" if _is_complete(plan) else "INCOMPLETE",
        courses=[
            _to_course_out(course)
            for course in sorted(
                plan.courses,
                key=lambda item: (item.study_year, item.semester_no, item.created_at),
            )
        ],
    )


def _to_detail(plan) -> TrainingPlanDetailOut:
    return TrainingPlanDetailOut(
        id=plan.id,
        plan_code=plan.plan_code,
        major=_to_major_info(plan.major),
        enrollment_year=plan.enrollment_year,
        version_no=plan.version_no,
        status=plan.status,
        effective_from=plan.effective_from,
        published_at=plan.published_at,
        updated_at=plan.updated_at,
        courses=[
            _to_course_out(course)
            for course in sorted(
                plan.courses,
                key=lambda item: (item.study_year, item.semester_no, item.created_at),
            )
        ],
    )


def _plan_snapshot(plan) -> dict:
    return {
        "id": str(plan.id),
        "plan_code": plan.plan_code,
        "major_id": str(plan.major_id),
        "enrollment_year": plan.enrollment_year,
        "version_no": plan.version_no,
        "status": plan.status,
        "courses_count": len(plan.courses),
    }


async def _validate_payload(
    session: AsyncSession, data: CreateTrainingPlanRequest
) -> None:
    if await tp_crud.get_active_major(session, data.major_id) is None:
        raise TrainingPlanError("专业不存在或已停用", 422)

    course_ids = {item.course_id for item in data.courses}
    prerequisite_ids = {
        prerequisite_id
        for item in data.courses
        for prerequisite_id in item.prerequisite_course_ids
    }
    courses = await tp_crud.get_active_courses_by_ids(
        session, course_ids | prerequisite_ids
    )
    missing_courses = (course_ids | prerequisite_ids) - courses.keys()
    if missing_courses:
        raise TrainingPlanError("课程或先修课程不存在、已停用", 422)
    non_experiment_courses = [
        courses[course_id].course_name
        for course_id in course_ids
        if courses[course_id].course_type != "EXPERIMENT"
    ]
    if non_experiment_courses:
        raise TrainingPlanError(
            f"培养方案修读课程必须是实验课程：{'、'.join(non_experiment_courses)}", 422
        )

    project_ids = {
        item.project_id for course in data.courses for item in course.projects
    }
    projects = await tp_crud.get_active_projects_by_ids(session, project_ids)
    if project_ids - projects.keys():
        raise TrainingPlanError("实验项目不存在或已停用", 422)

    for course in data.courses:
        if course.course_id in course.prerequisite_course_ids:
            raise TrainingPlanError("课程不能将自身设置为先修课程", 422)
        for item in course.projects:
            if projects[item.project_id].course_id != course.course_id:
                raise TrainingPlanError(
                    f"实验项目“{projects[item.project_id].project_name}”不属于所选课程",
                    422,
                )


async def list_plans(
    session: AsyncSession,
    major_id: UUID | None = None,
    enrollment_year: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> TrainingPlanListResponse:
    items, total = await tp_crud.get_plans_list(
        session,
        major_id,
        enrollment_year,
        status,
        keyword,
        offset,
        limit,
    )
    return TrainingPlanListResponse(
        items=[_to_list_item(item) for item in items], total=total
    )


async def get_plan_detail(
    session: AsyncSession, plan_id: UUID
) -> TrainingPlanDetailOut | None:
    plan = await tp_crud.get_plan_detail(session, plan_id)
    return _to_detail(plan) if plan is not None else None


async def create_plan(
    session: AsyncSession,
    data: CreateTrainingPlanRequest,
    actor_id: UUID,
) -> TrainingPlanDetailOut:
    await _validate_payload(session, data)
    try:
        current = await tp_crud.get_current_plan_for_update(
            session, data.major_id, data.enrollment_year
        )
        if current is not None:
            raise TrainingPlanError("同一专业同一入学年份已有培养方案，请从现有方案进入编辑")
        latest = await tp_crud.get_latest_plan_for_update(
            session, data.major_id, data.enrollment_year
        )
        version_no = latest.version_no + 1 if latest else 1
        plan_code = (
            f"PLAN-{data.major_id.hex[:8]}-{data.enrollment_year}-V{version_no}"
        )
        plan = await tp_crud.create_plan(
            session,
            plan_code=plan_code,
            major_id=data.major_id,
            enrollment_year=data.enrollment_year,
            version_no=version_no,
            effective_from=data.effective_from,
            actor_id=actor_id,
            courses_data=[item.model_dump() for item in data.courses],
        )
        await session.flush()
        loaded = await tp_crud.get_plan_detail(session, plan.id)
        await tp_crud.add_operation_log(
            session,
            actor_id=actor_id,
            operation_type="TRAINING_PLAN_CREATED",
            object_id=plan.id,
            before_snapshot={},
            after_snapshot=_plan_snapshot(loaded),
        )
        await session.commit()
        return _to_detail(loaded)
    except IntegrityError as exc:
        await session.rollback()
        raise TrainingPlanError("培养方案版本或课程配置发生冲突") from exc


async def update_plan(
    session: AsyncSession,
    plan_id: UUID,
    data: UpdateTrainingPlanRequest,
    actor_id: UUID,
) -> TrainingPlanDetailOut:
    await _validate_payload(session, data)
    plan = await tp_crud.get_plan_detail(session, plan_id, for_update=True)
    if plan is None:
        raise TrainingPlanError("培养方案不存在", 404)
    if plan.status != "DRAFT":
        raise TrainingPlanError("已发布或已归档方案不能原地修改，请复制为新草稿")
    if (
        plan.major_id != data.major_id
        or plan.enrollment_year != data.enrollment_year
    ):
        raise TrainingPlanError("草稿创建后不能变更所属专业或培养年份")
    before = _plan_snapshot(plan)
    try:
        await tp_crud.replace_plan_courses(
            session,
            plan,
            major_id=data.major_id,
            enrollment_year=data.enrollment_year,
            effective_from=data.effective_from,
            actor_id=actor_id,
            courses_data=[item.model_dump() for item in data.courses],
        )
        loaded = await tp_crud.get_plan_detail(session, plan.id)
        await tp_crud.add_operation_log(
            session,
            actor_id=actor_id,
            operation_type="TRAINING_PLAN_UPDATED",
            object_id=plan.id,
            before_snapshot=before,
            after_snapshot=_plan_snapshot(loaded),
        )
        await session.commit()
        return _to_detail(loaded)
    except IntegrityError as exc:
        await session.rollback()
        raise TrainingPlanError("培养方案课程配置发生冲突") from exc


async def copy_plan_to_draft(
    session: AsyncSession, plan_id: UUID, actor_id: UUID
) -> TrainingPlanDetailOut:
    source = await tp_crud.get_plan_detail(session, plan_id, for_update=True)
    if source is None:
        raise TrainingPlanError("培养方案不存在", 404)
    if source.status == "DRAFT":
        raise TrainingPlanError("当前方案已经是草稿，无需复制")

    latest = await tp_crud.get_latest_plan_for_update(
        session, source.major_id, source.enrollment_year
    )
    version_no = latest.version_no + 1 if latest else source.version_no + 1
    courses_data = []
    for course in source.courses:
        courses_data.append(
            {
                "course_id": course.course_id,
                "course_nature": course.course_nature,
                "study_year": course.study_year,
                "semester_no": course.semester_no,
                "prerequisite_course_ids": [
                    item.prerequisite_course_id for item in course.prerequisites
                ],
                "required_project_count": course.required_project_count,
                "optional_project_min_count": course.optional_project_min_count,
                "order_rule_text": course.order_rule_text,
                "allow_order_override": course.allow_order_override,
                "projects": [
                    {
                        "project_id": item.project_id,
                        "requirement_type": item.requirement_type,
                        "display_order": item.display_order,
                    }
                    for item in course.projects
                ],
            }
        )
    try:
        copied = await tp_crud.create_plan(
            session,
            plan_code=(
                f"PLAN-{source.major_id.hex[:8]}-"
                f"{source.enrollment_year}-V{version_no}"
            ),
            major_id=source.major_id,
            enrollment_year=source.enrollment_year,
            version_no=version_no,
            effective_from=source.effective_from,
            actor_id=actor_id,
            courses_data=courses_data,
        )
        await tp_crud.archive_plan(session, source, actor_id)
        loaded = await tp_crud.get_plan_detail(session, copied.id)
        await tp_crud.add_operation_log(
            session,
            actor_id=actor_id,
            operation_type="TRAINING_PLAN_DRAFT_COPIED",
            object_id=copied.id,
            before_snapshot={
                "source_plan_id": str(source.id),
                "source_status": source.status,
            },
            after_snapshot=_plan_snapshot(loaded),
        )
        await session.commit()
        return _to_detail(loaded)
    except IntegrityError as exc:
        await session.rollback()
        raise TrainingPlanError("新草稿版本号冲突，请刷新后重试") from exc


async def publish_plan(
    session: AsyncSession, plan_id: UUID, actor_id: UUID
) -> TrainingPlanListOut:
    plan = await tp_crud.get_plan_detail(session, plan_id, for_update=True)
    if plan is None:
        raise TrainingPlanError("培养方案不存在", 404)
    if plan.status != "DRAFT":
        raise TrainingPlanError("只有草稿状态的培养方案可以发布")
    if not _is_complete(plan):
        raise TrainingPlanError("培养方案内容不完整，不能发布")
    before = _plan_snapshot(plan)
    try:
        archived_ids = await tp_crud.archive_other_published(
            session, plan, actor_id
        )
        await tp_crud.publish_plan(session, plan, actor_id)
        loaded = await tp_crud.get_plan_detail(session, plan.id)
        await tp_crud.add_operation_log(
            session,
            actor_id=actor_id,
            operation_type="TRAINING_PLAN_PUBLISHED",
            object_id=plan.id,
            before_snapshot=before,
            after_snapshot={
                **_plan_snapshot(loaded),
                "archived_plan_ids": [str(item) for item in archived_ids],
            },
        )
        await session.commit()
        return _to_list_item(loaded)
    except IntegrityError as exc:
        await session.rollback()
        raise TrainingPlanError("发布状态发生冲突，请刷新后重试") from exc


async def archive_plan(
    session: AsyncSession, plan_id: UUID, actor_id: UUID
) -> TrainingPlanListOut:
    plan = await tp_crud.get_plan_detail(session, plan_id, for_update=True)
    if plan is None:
        raise TrainingPlanError("培养方案不存在", 404)
    if plan.status == "ARCHIVED":
        raise TrainingPlanError("培养方案已经归档")
    before = _plan_snapshot(plan)
    await tp_crud.archive_plan(session, plan, actor_id)
    loaded = await tp_crud.get_plan_detail(session, plan.id)
    await tp_crud.add_operation_log(
        session,
        actor_id=actor_id,
        operation_type="TRAINING_PLAN_ARCHIVED",
        object_id=plan.id,
        before_snapshot=before,
        after_snapshot=_plan_snapshot(loaded),
    )
    await session.commit()
    return _to_list_item(loaded)


async def get_majors(session: AsyncSession) -> list[MajorInfo]:
    return [
        MajorInfo(id=item.id, code=item.code, name=item.name)
        for item in await tp_crud.get_active_majors(session)
    ]


async def get_courses(session: AsyncSession) -> list[CourseInfo]:
    return [
        _to_course_info(item) for item in await tp_crud.get_active_courses(session)
    ]


async def get_course_projects(
    session: AsyncSession, course_id: UUID
) -> list[ProjectInfo]:
    return [
        _to_project_info(item)
        for item in await tp_crud.get_course_projects(session, course_id)
    ]


async def create_course_project(
    session: AsyncSession,
    course_id: UUID,
    data: CreateProjectRequest,
    actor_id: UUID,
) -> ProjectInfo:
    courses = await tp_crud.get_active_courses_by_ids(session, {course_id})
    if course_id not in courses:
        raise TrainingPlanError("课程不存在或已停用", 404)
    try:
        project = await tp_crud.create_course_project(
            session,
            course_id=course_id,
            actor_id=actor_id,
            **data.model_dump(),
        )
        await tp_crud.add_operation_log(
            session,
            actor_id=actor_id,
            operation_type="EXPERIMENT_PROJECT_CREATED",
            object_type="EXPERIMENT_PROJECT",
            object_id=project.id,
            before_snapshot={},
            after_snapshot={
                "id": str(project.id),
                "course_id": str(project.course_id),
                "project_code": project.project_code,
                "project_name": project.project_name,
            },
        )
        await session.commit()
        return _to_project_info(project)
    except IntegrityError as exc:
        await session.rollback()
        raise TrainingPlanError("项目编码已存在，或该课程已有同名项目") from exc
