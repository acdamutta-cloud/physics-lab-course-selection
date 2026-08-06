import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models import (
    ExperimentProject,
    ProjectOrderConstraint,
    TrainingPlanCourse,
)

SIMULATED_SUFFIX = "（模拟）"


def canonical_project_name(
    project: ExperimentProject, *, strip_simulated_suffix: bool
) -> str:
    name = project.project_name
    if strip_simulated_suffix and name.endswith(SIMULATED_SUFFIX):
        return name[: -len(SIMULATED_SUFFIX)]
    return name


def canonical_description(
    constraint: ProjectOrderConstraint, *, strip_simulated_suffix: bool
) -> str:
    before_name = canonical_project_name(
        constraint.before_project,
        strip_simulated_suffix=strip_simulated_suffix,
    )
    after_name = canonical_project_name(
        constraint.after_project,
        strip_simulated_suffix=strip_simulated_suffix,
    )
    return (
        f"完成“{before_name}”后再进入“{after_name}”。"
    )


async def normalize(
    *, apply_changes: bool, strip_simulated_suffix: bool = False
) -> int:
    async with AsyncSessionFactory() as session:
        changed_projects = 0
        if strip_simulated_suffix:
            projects = list(
                (
                    await session.execute(
                        select(ExperimentProject).order_by(
                            ExperimentProject.project_code
                        )
                    )
                )
                .scalars()
                .all()
            )
            for project in projects:
                canonical_name = canonical_project_name(
                    project, strip_simulated_suffix=True
                )
                if project.project_name == canonical_name:
                    continue
                changed_projects += 1
                print(
                    f"[{project.project_code}] "
                    f"{project.project_name} -> {canonical_name}"
                )
                if apply_changes:
                    project.project_name = canonical_name

        constraints = list(
            (
                await session.execute(
                    select(ProjectOrderConstraint)
                    .options(
                        selectinload(ProjectOrderConstraint.before_project),
                        selectinload(ProjectOrderConstraint.after_project),
                    )
                    .order_by(
                        ProjectOrderConstraint.plan_course_id,
                        ProjectOrderConstraint.created_at,
                    )
                )
            )
            .scalars()
            .all()
        )
        constraints.sort(
            key=lambda item: (
                str(item.plan_course_id),
                item.before_project.project_code,
                item.after_project.project_code,
            )
        )

        changed_constraints = 0
        descriptions_by_course: dict[object, list[str]] = {}
        for constraint in constraints:
            description = canonical_description(
                constraint,
                strip_simulated_suffix=strip_simulated_suffix,
            )
            descriptions_by_course.setdefault(constraint.plan_course_id, []).append(
                description
            )
            if constraint.description == description:
                continue
            changed_constraints += 1
            print(
                f"[{constraint.before_project.project_code} -> "
                f"{constraint.after_project.project_code}]"
            )
            print(f"  原值：{constraint.description or '—'}")
            print(f"  新值：{description}")
            if apply_changes:
                constraint.description = description

        plan_courses = list(
            (
                await session.execute(
                    select(TrainingPlanCourse).where(
                        TrainingPlanCourse.id.in_(descriptions_by_course)
                    )
                )
            )
            .scalars()
            .all()
        )
        changed_course_summaries = 0
        for plan_course in plan_courses:
            order_rule_text = "; ".join(
                descriptions_by_course.get(plan_course.id, [])
            )
            if plan_course.order_rule_text == order_rule_text:
                continue
            changed_course_summaries += 1
            if apply_changes:
                plan_course.order_rule_text = order_rule_text

        if apply_changes:
            await session.commit()
        else:
            await session.rollback()

        print(
            f"项目名称待更新 {changed_projects} 条，"
            f"约束说明待更新 {changed_constraints} 条，"
            f"课程汇总待更新 {changed_course_summaries} 条。"
        )
        return changed_projects + changed_constraints + changed_course_summaries


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="使用实验项目主数据中的正式名称规范项目顺序说明。"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="提交修改；未提供时仅检查差异。",
    )
    parser.add_argument(
        "--strip-simulated-suffix",
        action="store_true",
        help="同时移除项目名称末尾的“（模拟）”并同步顺序说明。",
    )
    args = parser.parse_args()
    try:
        await normalize(
            apply_changes=args.apply,
            strip_simulated_suffix=args.strip_simulated_suffix,
        )
    finally:
        await dispose_database_engine()


if __name__ == "__main__":
    asyncio.run(main())
