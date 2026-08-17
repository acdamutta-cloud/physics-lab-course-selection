from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.auth_principals import get_or_build_profile
from app.crud import users as user_crud
from app.db.session import get_db_session
from app.schemas.auth import UserProfile
from app.services import auth_service as auth_svc

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> UserProfile:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = auth_svc.decode_token(credentials.credentials)
        if payload.get("purpose") != "access":
            raise HTTPException(status_code=401, detail="无效的令牌用途")
        user_id = UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async def build_profile() -> UserProfile | None:
        user = await user_crud.get_user_by_id(session, user_id)
        if user is None or user.status != "ACTIVE":
            return None
        return await auth_svc.build_user_profile(session, user)

    profile = await get_or_build_profile(user_id, build_profile)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )

    return profile
