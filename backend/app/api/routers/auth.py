from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db_session
from app.schemas.auth import (
    LoginRequest,
    PhoneLoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    SendCodeRequest,
    TokenResponse,
    UserProfile,
)
from app.services import auth_service as auth_svc
from app.services import verification_service as verify_svc

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
):
    result = await auth_svc.login(session, body.login_name, body.password)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号或密码错误",
        )
    return result


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_db_session),
):
    result = await auth_svc.refresh_access_token(session, body.refresh_token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的刷新令牌",
        )
    return result


@router.post("/send-code")
async def send_code(body: SendCodeRequest):
    success = await verify_svc.send_verification_code(body.phone)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="发送过于频繁，请稍后再试",
        )
    return {"message": "验证码已发送"}


@router.post("/login/phone", response_model=TokenResponse)
async def login_by_phone(
    body: PhoneLoginRequest,
    session: AsyncSession = Depends(get_db_session),
):
    if not await verify_svc.verify_code(body.phone, body.code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="验证码错误或已过期",
        )
    result = await auth_svc.login_by_phone(session, body.phone)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="该手机号未注册",
        )
    return result


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    session: AsyncSession = Depends(get_db_session),
):
    ok = await auth_svc.reset_password(
        session, body.phone, body.code, body.new_password
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码错误或手机号未注册",
        )
    return {"message": "密码已重置，请使用新密码登录"}


@router.get("/me", response_model=UserProfile)
async def get_me(current_user: UserProfile = Depends(get_current_user)):
    return current_user
