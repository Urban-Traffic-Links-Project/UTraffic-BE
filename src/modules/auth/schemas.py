"""
src/modules/auth/schemas.py
Pydantic schemas cho Auth module — định nghĩa cấu trúc Request và Response.
 
Phân biệt với SQLModel table models:
  - SQLModel (table=True) → ánh xạ tới bảng DB
  - Schema (SQLModel không có table=True) → validate dữ liệu vào/ra API
"""
import uuid
from datetime import datetime

from pydantic import EmailStr
from sqlmodel import SQLModel


# ════════════════════════════════════════════════════════════
# REQUEST schemas — dữ liệu CLIENT gửi LÊN
# ════════════════════════════════════════════════════════════

class LoginRequest(SQLModel):
    """Body của POST /auth/login"""
    email: EmailStr     # Pydantic tự validate định dạng email
    password: str


class RegisterRequest(SQLModel):
    """Body của POST /auth/register"""
    email: EmailStr
    password: str
    full_name: str | None = None


class RefreshTokenRequest(SQLModel):
    """Body của POST /auth/refresh-token"""
    refresh_token: str


class ForgotPasswordSendCodeRequest(SQLModel):
    """Body của POST /auth/forgot-password/send-code"""
    email: EmailStr


class ForgotPasswordResetRequest(SQLModel):
    """Body của POST /auth/forgot-password/reset"""
    email: EmailStr
    code: str
    new_password: str


class AdminUserCreateRequest(SQLModel):
    """Admin tạo tài khoản mới."""
    email: EmailStr
    password: str
    full_name: str | None = None
    role: str = "user"
    is_active: bool = True


class AdminUserUpdateRequest(SQLModel):
    """Admin cập nhật thông tin tài khoản."""
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class AdminUserPasswordResetRequest(SQLModel):
    """Admin đặt lại mật khẩu cho user."""
    new_password: str


# ════════════════════════════════════════════════════════════
# RESPONSE schemas — dữ liệu SERVER trả VỀ
# ════════════════════════════════════════════════════════════

class TokenResponse(SQLModel):
    """
    Response của POST /auth/login và POST /auth/refresh-token.
    Trả về cả 2 token để client lưu trữ:
      - access_token  → lưu vào Memory/SessionStorage (ngắn hạn)
      - refresh_token → lưu vào HttpOnly Cookie (dài hạn, bảo mật hơn)
    """
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: int             # Số giây access_token còn sống (= 15 * 60 = 900)



class UserResponse(SQLModel):
    """Thông tin user trả về sau khi đăng nhập/đăng ký (không có password)."""
    id: uuid.UUID
    email: str
    full_name: str | None
    role: str
    is_active: bool
    created_at: datetime
 
 
class LoginResponse(SQLModel):
    """Response đầy đủ của POST /auth/login."""
    user: UserResponse
    tokens: TokenResponse
 
 
class MessageResponse(SQLModel):
    """Response đơn giản chỉ có message — dùng cho logout."""
    message: str

class AdminUserResponse(SQLModel):
    """Thông tin user cho màn admin quản lý tài khoản."""
    id: uuid.UUID
    email: str
    full_name: str | None
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

class AdminUserListResponse(SQLModel):
    """Response danh sách user có phân trang."""
    items: list[AdminUserResponse]
    total: int
    skip: int
    limit: int