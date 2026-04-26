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
from fastapi import APIRouter, Request

from src.api.dependencies import CurrentUser, DbSession
from src.modules.auth import service
from src.modules.auth.schemas import (
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