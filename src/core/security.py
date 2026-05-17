"""
src/core/security.py
Xử lý bảo mật: băm mật khẩu (Argon2) và JWT token (PyJWT).
 
Tại sao Argon2 thay vì bcrypt?
- Argon2 thắng Password Hashing Competition 2015
- Chống GPU brute-force tốt hơn bcrypt
- argon2-cffi là binding C → rất nhanh
"""
import hashlib
from pydoc import plain
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Lấy cấu hình từ file config của dự án
from src.core.config import get_settings

settings = get_settings()

# ── Argon2 hasher — tạo 1 lần, dùng lại ────────────────────
# time_cost=2, memory_cost=65536: cân bằng giữa bảo mật và tốc độ
ph = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)

# ════════════════════════════════════════════════════════════
# PASSWORD HASHING
# ════════════════════════════════════════════════════════════
def hash_password(plain_password: str) -> str:
    """
    Băm mật khẩu bằng Argon2.
    Mỗi lần gọi cho ra hash KHÁC NHAU (do salt ngẫu nhiên được tích hợp sẵn).
 
    plain_password = "MySecret123"
    → "$argon2id$v=19$m=65536,t=2,p=2$..."  (lưu cái này vào DB)
    """
    return ph.hash(plain_password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Kiểm tra mật khẩu nhập vào có khớp với hash trong DB không.
    Trả về True nếu đúng, False nếu sai.
    """
    try:
        return ph.verify(hashed_password, plain_password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    
# ════════════════════════════════════════════════════════════
# JWT ACCESS TOKEN
# ════════════════════════════════════════════════════════════
def create_access_token(data: dict[str, Any]) -> str:
    """
    Tạo JWT Access Token sống 15 phút.
 
    data thường chứa: {"sub": "user_uuid", "role": "user"}
 
    Cấu trúc JWT gồm 3 phần ngăn cách bởi dấu chấm:
      Header.Payload.Signature
    Payload chứa data + exp (expiry time) + jti (unique token ID)
    Signature được ký bằng JWT_SECRET_KEY → không thể giả mạo

    jti (JWT ID): UUID duy nhất cho mỗi token, dùng để blacklist
    khi logout mà token chưa hết hạn.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        **data,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),  # Unique ID để blacklist khi logout
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Giải mã và xác thực JWT Access Token.
    Raise jwt.ExpiredSignatureError nếu hết hạn.
    Raise jwt.InvalidTokenError nếu token bị giả mạo.
    """
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


# ════════════════════════════════════════════════════════════
# REFRESH TOKEN
# ════════════════════════════════════════════════════════════
def generate_refresh_token() -> str:
    """
    Tạo Refresh Token ngẫu nhiên dạng hex string (64 ký tự).
    KHÔNG phải JWT — chỉ là chuỗi random, được lưu hash vào DB.
 
    Tại sao không dùng JWT cho refresh token?
    → JWT có thể tự xác thực mà không cần DB → không thể thu hồi
    → Random token buộc phải kiểm tra DB → có thể revoke bất cứ lúc nào
    """
    return secrets.token_hex(32)  # 32 bytes → 64 hex chars

def hash_refresh_token(token: str) -> str:
    """
    Băm refresh token bằng SHA-256 trước khi lưu vào DB.
    Nếu DB bị lộ, attacker không biết token thật.
 
    SHA-256 đủ tốt ở đây vì token đã random 256-bit,
    không cần Argon2 (Argon2 dùng cho mật khẩu do người tạo ra).
    """
    return hashlib.sha256(token.encode()).hexdigest()
