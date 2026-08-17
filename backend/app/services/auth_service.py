from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.crud import users as user_crud
from app.models.identity import UserAccount
from app.schemas.auth import TokenResponse, UserProfile
from app.services import verification_service as verify_svc

settings = get_settings()
pwd_hash = PasswordHash.recommended()


def _create_access_token(user_id: UUID, user_type: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "type": user_type,
        "iat": now,
        "exp": expire,
        "purpose": "access",
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def _create_refresh_token(user_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.jwt_refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": expire,
        "purpose": "refresh",
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_token(token: str) -> dict:
    """解码并验证 JWT。失败时抛出 jwt.PyJWTError。"""
    return jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )


async def authenticate_user(
    session: AsyncSession, login_name: str, password: str
) -> UserAccount | None:
    """验证凭据，成功返回 UserAccount，失败返回 None。"""
    user = await user_crud.get_user_by_login_name(session, login_name)
    if user is None:
        return None
    if user.status != "ACTIVE":
        return None
    is_valid, updated_hash = pwd_hash.verify_and_update(password, user.password_hash)
    if not is_valid:
        return None
    if updated_hash is not None:
        user.password_hash = updated_hash
    return user


async def build_user_profile(
    session: AsyncSession, user: UserAccount
) -> UserProfile:
    """根据 user_type 组装含角色信息的用户画像。"""
    profile = UserProfile(
        id=user.id,
        login_name=user.login_name,
        user_type=user.user_type,
    )

    if user.user_type == "STUDENT":
        student = await user_crud.get_student_by_user_id(session, user.id)
        if student:
            profile.student_id = student.id
            profile.name = student.name
            profile.student_no = student.student_no
            profile.enrollment_year = student.enrollment_year
            profile.major_name = await user_crud.get_major_name(
                session, student.major_id
            )

    elif user.user_type == "TEACHER":
        teacher = await user_crud.get_teacher_by_user_id(session, user.id)
        if teacher:
            profile.teacher_id = teacher.id
            profile.name = teacher.name
            profile.employee_no = teacher.employee_no
            profile.department = teacher.department
            profile.title = teacher.title

    elif user.user_type == "ADMIN":
        profile.name = user.login_name

    return profile


async def login(
    session: AsyncSession, login_name: str, password: str
) -> TokenResponse | None:
    """登录：验证 → 组装 profile → 签发 token → 更新登录时间。"""
    user = await authenticate_user(session, login_name, password)
    if user is None:
        return None

    profile = await build_user_profile(session, user)

    access_token = _create_access_token(user.id, user.user_type)
    refresh_token = _create_refresh_token(user.id)

    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=profile,
    )


async def login_by_phone(
    session: AsyncSession, phone: str
) -> TokenResponse | None:
    """手机验证码登录：根据手机号查用户 → 签发 token。"""
    user = await user_crud.get_user_by_phone(session, phone)
    if user is None or user.status != "ACTIVE":
        return None

    profile = await build_user_profile(session, user)

    access_token = _create_access_token(user.id, user.user_type)
    refresh_token = _create_refresh_token(user.id)

    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=profile,
    )


async def reset_password(
    session: AsyncSession, phone: str, code: str, new_password: str
) -> bool:
    """验证码验证通过后重置密码。成功返回 True。"""
    if not await verify_svc.verify_code(phone, code):
        return False
    user = await user_crud.get_user_by_phone(session, phone)
    if user is None or user.status != "ACTIVE":
        return False
    user.password_hash = pwd_hash.hash(new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    await session.commit()
    # A committed password change must evict any cached authentication profile.
    from app.cache.auth_principals import invalidate_auth_profile

    await invalidate_auth_profile(user.id)
    return True


async def refresh_access_token(
    session: AsyncSession, refresh_token_str: str
) -> TokenResponse | None:
    """用 refresh token 换新的 token 对。"""
    try:
        payload = decode_token(refresh_token_str)
        if payload.get("purpose") != "refresh":
            return None
        user_id = UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return None

    user = await user_crud.get_user_by_id(session, user_id)
    if user is None or user.status != "ACTIVE":
        return None

    profile = await build_user_profile(session, user)
    access_token = _create_access_token(user.id, user.user_type)
    new_refresh_token = _create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=profile,
    )
