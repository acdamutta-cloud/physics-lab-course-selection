from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    login_name: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)


class UserProfile(BaseModel):
    """角色无关的用户画像，含可选的角色特定字段。"""
    id: UUID
    login_name: str
    user_type: str  # STUDENT / TEACHER / ADMIN

    name: str | None = None
    # 学生专用
    student_no: str | None = None
    enrollment_year: int | None = None
    major_name: str | None = None
    # 教师专用
    employee_no: str | None = None
    department: str | None = None
    title: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # 秒
    user: UserProfile


class RefreshRequest(BaseModel):
    refresh_token: str


class SendCodeRequest(BaseModel):
    phone: str = Field(..., pattern=r"^1\d{10}$")


class PhoneLoginRequest(BaseModel):
    phone: str = Field(..., pattern=r"^1\d{10}$")
    code: str = Field(..., min_length=6, max_length=6)


class ResetPasswordRequest(BaseModel):
    phone: str = Field(..., pattern=r"^1\d{10}$")
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=6, max_length=64)
