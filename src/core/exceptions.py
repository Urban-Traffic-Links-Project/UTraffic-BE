"""
src/core/exceptions.py
Định nghĩa các lỗi tùy chỉnh cho toàn hệ thống.

FastAPI sẽ tự convert các HTTPException thành JSON response:
  raise CredentialsException
  → HTTP 401 {"detail": "Email hoặc mật khẩu không đúng"}
"""
from fastapi import HTTPException, status

# ── 401 Unauthorized ─────────────────────────────────────────
CredentialsException = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Email hoặc mật khẩu không đúng",
    headers={"WWW-Authenticate": "Bearer"},
)

TokenExpiredException = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Token đã hết hạn",
    headers={"WWW-Authenticate": "Bearer"},
)

InvalidTokenException = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Token không hợp lệ",
    headers={"WWW-Authenticate": "Bearer"},
)

InvalidRefreshTokenException = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Refresh token không hợp lệ hoặc đã bị thu hồi",
)

# ── 403 Forbidden ────────────────────────────────────────────
InactiveUserException = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Tài khoản đã bị vô hiệu hóa",
)

AdminRequiredException = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Chỉ admin mới có quyền thực hiện thao tác này",
)

# ── 404 Not Found ────────────────────────────────────────────
UserNotFoundException = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Không tìm thấy người dùng",
)

# ── 409 Conflict ─────────────────────────────────────────────
EmailAlreadyExistsException = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="Email này đã được đăng ký",
)