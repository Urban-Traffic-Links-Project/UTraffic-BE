"""
src/modules/auth/router.py
HTTP layer của Auth — chỉ lo nhận request, gọi service, trả response.

Endpoints:
  POST /auth/register      → đăng ký tài khoản mới
  POST /auth/login         → đăng nhập, nhận token
  POST /auth/refresh-token → đổi refresh token lấy token mới
  POST /auth/logout        → đăng xuất
  GET  /auth/me            → xem thông tin bản thân (cần login)
"""
import uuid

from fastapi import APIRouter, Query, Request

from src.api.dependencies import CurrentUser, DbSession
from src.modules.auth import service
from src.modules.auth.schemas import (
    AdminUserCreateRequest,
    AdminUserListResponse,
    AdminUserPasswordResetRequest,
    AdminUserResponse,
    AdminUserUpdateRequest,
    ForgotPasswordResetRequest,
    ForgotPasswordSendCodeRequest,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse, status_code=201)
def register(body: RegisterRequest, session: DbSession):
    """
    Đăng ký tài khoản mới.
    - 201 Created: đăng ký thành công, trả về thông tin user
    - 409 Conflict: email đã tồn tại
    """
    user = service.create_user(
        session=session,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
    )
    return user


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, session: DbSession):
    """
    Đăng nhập.
    - 200 OK: trả về user info + cặp token
    - 401 Unauthorized: sai email hoặc password

    request.client.host: lấy IP của client để lưu vào refresh_token
    """
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    user, access_token, refresh_token = service.login(
        session=session,
        email=body.email,
        password=body.password,
        ip=ip,
        device=user_agent,
    )

    return LoginResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role.value,
            is_active=user.is_active,
            created_at=user.created_at,
        ),
        tokens=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=15 * 60,  # 900 giây
        ),
    )


@router.post("/refresh-token", response_model=TokenResponse)
def refresh_token(body: RefreshTokenRequest, request: Request, session: DbSession):
    """
    Đổi Refresh Token lấy cặp token mới (Rotation).
    - 200 OK: trả về access_token + refresh_token MỚI
    - 403 Forbidden: refresh token không hợp lệ hoặc đã bị thu hồi
    """
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    _, access_token, new_refresh_token = service.refresh_tokens(
        session=session,
        raw_refresh_token=body.refresh_token,
        ip=ip,
        device=user_agent,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_at=15 * 60,
    )


@router.post("/logout", response_model=MessageResponse)
def logout(body: RefreshTokenRequest, session: DbSession):
    """
    Đăng xuất — thu hồi refresh token.
    - 200 OK: đăng xuất thành công
    Client cần tự xóa token khỏi memory/cookie sau khi nhận response.
    """
    service.logout(session=session, raw_refresh_token=body.refresh_token)
    return MessageResponse(message="Đăng xuất thành công")


@router.post("/forgot-password/send-code", response_model=MessageResponse)
def forgot_password_send_code(
    body: ForgotPasswordSendCodeRequest,
    session: DbSession,
):
    """
    Gửi mã xác thực đổi mật khẩu qua email.
    Luôn trả success để tránh lộ email có tồn tại hay không.
    """
    service.send_forgot_password_code(
        session=session,
        email=body.email,
    )

    return MessageResponse(
        message="Nếu email tồn tại trong hệ thống, mã xác thực đã được gửi."
    )


@router.post("/forgot-password/reset", response_model=MessageResponse)
def forgot_password_reset(
    body: ForgotPasswordResetRequest,
    session: DbSession,
):
    """
    Xác thực mã OTP và đổi mật khẩu mới.
    """
    service.reset_password_with_code(
        session=session,
        email=body.email,
        code=body.code,
        new_password=body.new_password,
    )

    return MessageResponse(message="Đổi mật khẩu thành công.")


# ════════════════════════════════════════════════════════════
# ADMIN USER MANAGEMENT
# ════════════════════════════════════════════════════════════

@router.get("/admin/users", response_model=AdminUserListResponse)
def admin_list_users(
    session: DbSession,
    current_user: CurrentUser,
    search: str | None = Query(default=None, description="Tìm theo email hoặc họ tên"),
    role: str | None = Query(default=None, description="admin hoặc user"),
    is_active: bool | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Admin xem danh sách tài khoản."""
    service.require_admin(current_user)
    return service.admin_list_users(
        session=session,
        search=search,
        role=role,
        is_active=is_active,
        skip=skip,
        limit=limit,
    )


@router.post("/admin/users", response_model=AdminUserResponse, status_code=201)
def admin_create_user(
    body: AdminUserCreateRequest,
    session: DbSession,
    current_user: CurrentUser,
):
    """Admin tạo tài khoản mới."""
    service.require_admin(current_user)
    return service.admin_create_user(
        session=session,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=body.role,
        is_active=body.is_active,
    )


@router.get("/admin/users/{user_id}", response_model=AdminUserResponse)
def admin_get_user(
    user_id: uuid.UUID,
    session: DbSession,
    current_user: CurrentUser,
):
    """Admin xem chi tiết tài khoản."""
    service.require_admin(current_user)
    return service.admin_get_user(session=session, user_id=user_id)


@router.patch("/admin/users/{user_id}", response_model=AdminUserResponse)
def admin_update_user(
    user_id: uuid.UUID,
    body: AdminUserUpdateRequest,
    session: DbSession,
    current_user: CurrentUser,
):
    """Admin cập nhật role/trạng thái/họ tên tài khoản."""
    service.require_admin(current_user)
    return service.admin_update_user(
        session=session,
        user_id=user_id,
        current_admin_id=current_user.id,
        full_name=body.full_name,
        role=body.role,
        is_active=body.is_active,
    )


@router.post("/admin/users/{user_id}/reset-password", response_model=MessageResponse)
def admin_reset_user_password(
    user_id: uuid.UUID,
    body: AdminUserPasswordResetRequest,
    session: DbSession,
    current_user: CurrentUser,
):
    """Admin đặt lại mật khẩu cho user."""
    service.require_admin(current_user)
    service.admin_reset_user_password(
        session=session,
        user_id=user_id,
        new_password=body.new_password,
    )
    return MessageResponse(message="Đặt lại mật khẩu thành công.")


@router.post("/admin/users/{user_id}/revoke-sessions", response_model=MessageResponse)
def admin_revoke_user_sessions(
    user_id: uuid.UUID,
    session: DbSession,
    current_user: CurrentUser,
):
    """Admin đăng xuất user khỏi tất cả thiết bị."""
    service.require_admin(current_user)
    service.admin_revoke_user_sessions(session=session, user_id=user_id)
    return MessageResponse(message="Đã thu hồi toàn bộ phiên đăng nhập của tài khoản.")


@router.get("/me", response_model=UserResponse)
def get_me(current_user: CurrentUser):
    """
    Xem thông tin tài khoản hiện tại.
    Cần gửi kèm: Header Authorization: Bearer <access_token>
    - 200 OK: trả về thông tin user
    - 401 Unauthorized: token không hợp lệ hoặc hết hạn
    """
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role.value,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
    )