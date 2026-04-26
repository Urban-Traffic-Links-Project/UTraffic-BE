"""
src/api/dependencies.py
Dependency Injection cho FastAPI.

Dependency là hàm FastAPI tự động gọi và inject kết quả vào route handler.
Ví dụ: get_current_user sẽ tự chạy trước khi route handler chạy,
        extract user từ JWT, và truyền vào làm tham số.

Cách dùng trong router:
    @router.get("/me")
    def get_me(current_user: User = Depends(get_current_user)):
        return current_user
"""
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from src.core.exceptions import InvalidTokenException, TokenExpiredException, InactiveUserException
from src.core.security import decode_access_token
from src.storage.database import get_session
from src.storage.models.auth import User

# ── HTTP Bearer scheme — FastAPI tự thêm ô "Authorize" vào Swagger UI ───────
bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    """
    Dependency: extract và xác thực user từ JWT Access Token.

    Flow:
      1. FastAPI lấy token từ Header: Authorization: Bearer <token>
      2. Giải mã JWT
      3. Tìm user trong DB theo user_id trong payload
      4. Trả về User object — route handler nhận được user đã xác thực
    """
    token = credentials.credentials

    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredException from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenException from exc

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise InvalidTokenException

    user = session.get(User, user_id)
    if not user:
        raise InvalidTokenException

    if not user.is_active:
        raise InactiveUserException

    return user


def get_current_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """
    Dependency: chỉ cho phép admin.
    Dùng chồng lên get_current_user — trước tiên phải login hợp lệ.
    """
    from src.core.exceptions import AdminRequiredException
    from src.storage.models.auth import UserRole
    if current_user.role != UserRole.admin:
        raise AdminRequiredException
    return current_user


# ── Type aliases tiện dụng ───────────────────────────────────
# Thay vì viết dài: current_user: User = Depends(get_current_user)
# Viết ngắn:        current_user: CurrentUser
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentAdmin = Annotated[User, Depends(get_current_admin)]
DbSession = Annotated[Session, Depends(get_session)]
