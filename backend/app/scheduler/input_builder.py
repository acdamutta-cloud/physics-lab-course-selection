"""排课求解器输入构建。"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.resource_feasibility_service import (
    ProjectLabOption,
    get_project_lab_options,
)


async def build_project_lab_options(
    session: AsyncSession,
    project_ids: list[UUID] | tuple[UUID, ...] | set[UUID],
) -> dict[UUID, list[ProjectLabOption]]:
    """构建只包含同室器材齐套实验室的项目候选域。"""

    return await get_project_lab_options(session, project_ids)
