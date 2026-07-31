"""排课前置校验。"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.resource_feasibility_service import (
    ProjectLabIssue,
    ProjectLabWarning,
    evaluate_project_lab_resources,
)


@dataclass(frozen=True)
class ResourcePreflightResult:
    valid: bool
    issues: tuple[ProjectLabIssue, ...]
    warnings: tuple[ProjectLabWarning, ...]


async def validate_project_resources(
    session: AsyncSession,
    project_ids: list[UUID] | tuple[UUID, ...] | set[UUID],
) -> ResourcePreflightResult:
    """校验所有待排项目至少存在一间同室齐套实验室。"""

    ids = list(dict.fromkeys(project_ids))
    result = await evaluate_project_lab_resources(session, ids)
    invalid_ids = {
        project_id
        for project_id in ids
        if not result.options.get(project_id)
    }
    blocking_issues = tuple(
        issue for issue in result.issues if issue.project_id in invalid_ids
    )
    return ResourcePreflightResult(
        valid=not invalid_ids,
        issues=blocking_issues,
        warnings=tuple(result.warnings),
    )
