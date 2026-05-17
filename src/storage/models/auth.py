"""
BE/src/modules/auth/models.py
Nhóm bảng xác thực: users, refresh_tokens, user_sessions.

Cách đọc SQLModel:
- Field(...) = bắt buộc (NOT NULL)
- Field(default=...) = có giá trị mặc định
- sa_column = dùng SQLAlchemy Column thuần khi cần kiểu đặc biệt
"""

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import EmailStr
from sqlmodel import Column, Field, ForeignKey, Relationship, SQLModel, String


# ── Enum ─────────────────────────────────────────────────────────────────────
class UserRole(str, Enum):
    admin = "admin"
    user = "user"


# ══════════════════════════════════════════════════════════════════════════════
# BẢNG 1: users
# ══════════════════════════════════════════════════════════════════════════════
class User(SQLModel, table=True):
    """Bảng người dùng - Lưu tài khoản người dùng hệ thống."""

    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: EmailStr = Field(sa_column=Column(String(255), unique=True, index=True))
    password_hash: str = Field(max_length=255)
    full_name: str | None = Field(default=None, max_length=100)
    role: UserRole = Field(default=UserRole.user)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: datetime | None = Field(default=None)

    # Forgot password
    reset_password_code_hash: str | None = Field(default=None, max_length=255)
    reset_password_code_expires_at: datetime | None = Field(default=None)
    reset_password_code_attempts: int = Field(default=0)

    # Relationship - SQLModel tự JOIN khi cần
    refresh_tokens: list["RefreshToken"] = Relationship(
        back_populates="user", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    sessions: list["UserSession"] = Relationship(
        back_populates="user", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


# ══════════════════════════════════════════════════════════════════════════════
# BẢNG 2: refresh_tokens
# ══════════════════════════════════════════════════════════════════════════════
class RefreshToken(SQLModel, table=True):
    """
    refresh_tokens
    Quản lý refresh token theo cơ chế Rotation.
    Mỗi lần refresh → token cũ bị revoke → cấp token mới.
    """

    __tablename__ = "refresh_tokens"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # FK -> users
    user_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    )

    token_hash: str = Field(max_length=255, unique=True, index=True)
    device_info: str | None = Field(default=None, max_length=255)
    ip_address: str | None = Field(default=None, max_length=45)
    is_revoked: bool = Field(default=False)
    expires_at: datetime = Field()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: datetime | None = Field(default=None)

    # Relationship ngược lại
    user: User = Relationship(back_populates="refresh_tokens")
    sessions: list["UserSession"] = Relationship(
        back_populates="refresh_token",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# BẢNG 3: user_sessions
# ══════════════════════════════════════════════════════════════════════════════
class UserSession(SQLModel, table=True):
    """
    user_sessions
    Theo dõi phiên đăng nhập theo thiết bị.
    Admin có thể xem và revoke từ xa.
    """

    __tablename__ = "user_sessions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    refresh_token_id: uuid.UUID = Field(
        sa_column=Column(
            ForeignKey("refresh_tokens.id", ondelete="CASCADE"), index=True
        )
    )
    device_info: str | None = Field(default=None, max_length=255)
    ip_address: str | None = Field(default=None, max_length=45)
    is_active: bool = Field(default=True)
    last_used_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationship
    user: User = Relationship(back_populates="sessions")
    refresh_token: RefreshToken = Relationship(back_populates="sessions")
