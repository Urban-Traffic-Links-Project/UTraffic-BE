"""
src/modules/auth/service.py
Business logic của Auth — tách hoàn toàn khỏi HTTP layer (router).

Nguyên tắc: router chỉ lo nhận request và trả response,
            service lo xử lý logic nghiệp vụ.

Điều này giúp:
  1. Dễ test (test service không cần HTTP)
  2. Tái sử dụng logic từ nhiều nơi
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select
from sqlalchemy import func, or_

from src.core.exceptions import (
    CredentialsException,
    EmailAlreadyExistsException,
    InvalidRefreshTokenException,
)
from src.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from src.core.config import get_settings
from src.storage.models.auth import RefreshToken, User, UserRole, UserSession
import hashlib
import secrets
import smtplib
from email.message import EmailMessage
from fastapi import HTTPException, status

settings = get_settings()


# ════════════════════════════════════════════════════════════
# USER OPERATIONS
# ════════════════════════════════════════════════════════════

def get_user_by_email(session: Session, email: str) -> User | None:
    """Tìm user theo email — trả None nếu không tồn tại."""
    return session.exec(select(User).where(User.email == email)).first()


def create_user(session: Session, email: str, password: str, full_name: str | None = None) -> User:
    """
    Tạo tài khoản mới.
    Raise EmailAlreadyExistsException nếu email đã tồn tại.
    """
    # Kiểm tra email trùng
    if get_user_by_email(session, email):
        raise EmailAlreadyExistsException

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
    )
    session.add(user)
    session.commit()
    session.refresh(user)   # Đọc lại từ DB để lấy id, created_at...
    return user


# ════════════════════════════════════════════════════════════
# TOKEN OPERATIONS
# ════════════════════════════════════════════════════════════

def _create_token_pair(session: Session, user: User, ip: str | None, device: str | None) -> tuple[str, str]:
    """
    Hàm nội bộ: tạo cặp (access_token, refresh_token) và lưu refresh token vào DB.
    Trả về tuple (access_token_string, refresh_token_string).
    """
    # 1. Tạo Access Token (JWT, không lưu DB — có jti để blacklist khi logout)
    access_token = create_access_token({
        "sub": str(user.id),
        "role": user.role.value,
        "email": user.email,
    })

    # 2. Tạo Refresh Token (random, lưu hash vào DB)
    raw_refresh_token = generate_refresh_token()
    token_hash = hash_refresh_token(raw_refresh_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)

    db_refresh_token = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        ip_address=ip,
        device_info=device,
        expires_at=expires_at,
    )
    session.add(db_refresh_token)

    # 3. Tạo UserSession
    session.flush()  # Cần flush để có db_refresh_token.id trước khi tạo session
    user_session = UserSession(
        user_id=user.id,
        refresh_token_id=db_refresh_token.id,
        ip_address=ip,
        device_info=device,
    )
    session.add(user_session)
    session.commit()

    return access_token, raw_refresh_token


def login(session: Session, email: str, password: str, ip: str | None = None, device: str | None = None):
    """
    Xử lý đăng nhập — tương ứng bước 1-6 trong sequence diagram:
    1. Tìm user theo email
    2. Kiểm tra password
    3. Tạo cặp token
    4. Lưu refresh token vào DB
    5. Trả về tokens
    """
    # Bước 1+2: tìm user, xác thực password và kiểm tra trạng thái tài khoản.
    # Với tài khoản bị khóa, vẫn trả cùng CredentialsException như sai email/password
    # để không lộ trạng thái tài khoản cho người thử đăng nhập.
    user = get_user_by_email(session, email)
    if not user or not verify_password(password, user.password_hash) or not user.is_active:
        raise CredentialsException

    # Cập nhật last_login_at
    user.last_login_at = datetime.now(timezone.utc)
    session.add(user)

    # Bước 3+4: tạo token pair
    access_token, refresh_token = _create_token_pair(session, user, ip, device)

    return user, access_token, refresh_token


def refresh_tokens(session: Session, raw_refresh_token: str, ip: str | None = None, device: str | None = None):
    """
    Refresh Token Rotation — tương ứng bước 13-18 trong sequence diagram:
    1. Tìm refresh token theo hash
    2. Kiểm tra còn hạn và chưa bị thu hồi
    3. Thu hồi token CŨ (is_revoked=True)
    4. Cấp cặp token MỚI
    """
    token_hash = hash_refresh_token(raw_refresh_token)

    # Tìm token trong DB
    db_token = session.exec(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).first()

    # Kiểm tra token hợp lệ
    if not db_token:
        raise InvalidRefreshTokenException

    if db_token.is_revoked:
        # Token đã bị revoke mà vẫn dùng → nghi ngờ replay attack
        # Thu hồi toàn bộ session của user này để bảo vệ
        _revoke_all_user_tokens(session, db_token.user_id)
        raise InvalidRefreshTokenException

    if db_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise InvalidRefreshTokenException

    # Thu hồi token cũ
    db_token.is_revoked = True
    db_token.revoked_at = datetime.now(timezone.utc)
    session.add(db_token)

    # Lấy thông tin user
    user = session.get(User, db_token.user_id)
    if not user or not user.is_active:
        raise InvalidRefreshTokenException

    # Cấp token mới
    access_token, new_refresh_token = _create_token_pair(session, user, ip, device)
    return user, access_token, new_refresh_token


def logout(session: Session, raw_refresh_token: str) -> None:
    """
    Đăng xuất — thu hồi refresh token, tương ứng bước 20-23 trong diagram.
    Access token sẽ tự hết hạn sau 15 phút (không cần blacklist cho demo).

    Dùng logout_with_blacklist() nếu muốn blacklist access token ngay lập tức.
    """
    token_hash = hash_refresh_token(raw_refresh_token)
    db_token = session.exec(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).first()

    if db_token and not db_token.is_revoked:
        db_token.is_revoked = True
        db_token.revoked_at = datetime.now(timezone.utc)
        session.add(db_token)
        session.commit()


async def logout_with_blacklist(
    session: Session,
    raw_refresh_token: str,
    access_token_str: str | None = None,
) -> None:
    """
    Đăng xuất nâng cao — thu hồi refresh token VÀ blacklist JTI trên Redis.

    Flow (theo Sequence 1 trong báo cáo):
    1. Revoke refresh token trong PostgreSQL (is_revoked=True)
    2. Blacklist JTI của access token trên Redis-Auth DB 0
       - Key: blacklist:{jti}  Value: "1"  TTL: thời gian còn lại của token
    3. Cả hai bước độc lập — nếu Redis fail, bước 1 vẫn được ghi.
    """
    # Bước 1: Revoke refresh token trong DB
    logout(session, raw_refresh_token)

    # Bước 2: Blacklist access token JTI trên Redis (nếu token được gửi kèm)
    if not access_token_str:
        return

    try:
        payload = decode_access_token(access_token_str)
        jti: str | None = payload.get("jti")
        exp = payload.get("exp")  # Unix timestamp

        if not jti or not exp:
            return  # Token cũ không có jti (trước khi update) — skip

        # TTL = thời gian còn lại của token (giây), tối thiểu 1 giây
        now_ts = int(datetime.now(timezone.utc).timestamp())
        ttl = max(1, int(exp) - now_ts)

        from src.integrations.redis_client import get_redis_auth
        redis_auth = get_redis_auth()
        await redis_auth.setex(f"blacklist:{jti}", ttl, "1")

    except Exception:
        # Redis lỗi hoặc token expired → không block logout
        pass


def _revoke_all_user_tokens(session: Session, user_id: uuid.UUID) -> None:
    """Thu hồi toàn bộ refresh token của 1 user (khi phát hiện replay attack)."""
    tokens = session.exec(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.is_revoked == False,  # noqa: E712
        )
    ).all()
    now = datetime.now(timezone.utc)
    for token in tokens:
        token.is_revoked = True
        token.revoked_at = now
        session.add(token)
    session.commit()


def _generate_reset_code() -> str:
    """Tạo mã OTP 6 số."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_reset_code(code: str) -> str:
    """
    Hash mã reset password.
    Không nên lưu mã plain text trong DB.
    """
    raw = f"{settings.secret_key}:{code}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _send_reset_password_email(to_email: str, code: str) -> None:
    """
    Gửi mã reset password qua SMTP.
    Cần cấu hình SMTP trong settings/.env.
    """
    subject = "HCMTraffic - Mã xác thực đổi mật khẩu"

    body = f"""
Xin chào,

Mã xác thực đổi mật khẩu của bạn là: {code}

Mã này có hiệu lực trong 10 phút.
Nếu bạn không yêu cầu đổi mật khẩu, vui lòng bỏ qua email này.

HCMTraffic
""".strip()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = to_email
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(message)


def send_forgot_password_code(session: Session, email: str) -> None:
    """
    Gửi mã xác thực quên mật khẩu.

    Lưu ý bảo mật:
    - Không báo email có tồn tại hay không.
    - Nếu email không tồn tại vẫn trả success.
    """
    user = get_user_by_email(session, email)

    if not user or not user.is_active:
        return

    code = _generate_reset_code()

    user.reset_password_code_hash = _hash_reset_code(code)
    user.reset_password_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    user.reset_password_code_attempts = 0

    session.add(user)
    session.commit()

    _send_reset_password_email(to_email=user.email, code=code)


def reset_password_with_code(
    session: Session,
    email: str,
    code: str,
    new_password: str,
) -> None:
    """
    Xác thực mã OTP và đổi mật khẩu.
    """
    if len(new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Mật khẩu mới phải có ít nhất 8 ký tự.",
        )

    user = get_user_by_email(session, email)

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mã xác thực không hợp lệ hoặc đã hết hạn.",
        )

    if not user.reset_password_code_hash or not user.reset_password_code_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mã xác thực không hợp lệ hoặc đã hết hạn.",
        )

    expires_at = user.reset_password_code_expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at < datetime.now(timezone.utc):
        user.reset_password_code_hash = None
        user.reset_password_code_expires_at = None
        user.reset_password_code_attempts = 0
        session.add(user)
        session.commit()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mã xác thực không hợp lệ hoặc đã hết hạn.",
        )

    if user.reset_password_code_attempts >= 5:
        user.reset_password_code_hash = None
        user.reset_password_code_expires_at = None
        user.reset_password_code_attempts = 0
        session.add(user)
        session.commit()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bạn đã nhập sai quá số lần cho phép. Vui lòng gửi lại mã mới.",
        )

    if _hash_reset_code(code.strip()) != user.reset_password_code_hash:
        user.reset_password_code_attempts += 1
        session.add(user)
        session.commit()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mã xác thực không hợp lệ hoặc đã hết hạn.",
        )

    user.password_hash = hash_password(new_password)
    user.reset_password_code_hash = None
    user.reset_password_code_expires_at = None
    user.reset_password_code_attempts = 0

    session.add(user)

    # Đổi mật khẩu xong thì thu hồi các refresh token cũ để bắt đăng nhập lại.
    _revoke_all_user_tokens(session, user.id)

    session.commit()

# ════════════════════════════════════════════════════════════
# ADMIN USER MANAGEMENT
# ════════════════════════════════════════════════════════════

def _role_value(role) -> str:
    """Lấy giá trị string từ UserRole enum hoặc string."""
    return getattr(role, "value", str(role))


def require_admin(current_user: User) -> None:
    """Chỉ cho phép tài khoản role=admin truy cập API quản trị."""
    if _role_value(current_user.role) != UserRole.admin.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền quản trị tài khoản.",
        )


def _parse_user_role(role: str | UserRole) -> UserRole:
    """Validate role admin/user."""
    if isinstance(role, UserRole):
        return role

    normalized = str(role or "").strip().lower()

    try:
        return UserRole(normalized)
    except ValueError:
        allowed = ", ".join(item.value for item in UserRole)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Role không hợp lệ. Chỉ hỗ trợ: {allowed}.",
        )


def _admin_user_to_dict(user: User) -> dict:
    """Convert User model sang response dict, tránh trả password_hash."""
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": _role_value(user.role),
        "is_active": user.is_active,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


def admin_list_users(
    session: Session,
    search: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    skip: int = 0,
    limit: int = 20,
) -> dict:
    """Danh sách user cho admin, có search/filter/pagination."""
    limit = max(1, min(limit, 100))
    skip = max(0, skip)

    conditions = []

    if search:
        keyword = f"%{search.strip()}%"
        conditions.append(
            or_(
                User.email.ilike(keyword),
                User.full_name.ilike(keyword),
            )
        )

    if role:
        conditions.append(User.role == _parse_user_role(role))

    if is_active is not None:
        conditions.append(User.is_active == is_active)

    total_query = select(func.count()).select_from(User)
    list_query = select(User)

    for condition in conditions:
        total_query = total_query.where(condition)
        list_query = list_query.where(condition)

    total = session.exec(total_query).one()
    users = session.exec(
        list_query.order_by(User.created_at.desc()).offset(skip).limit(limit)
    ).all()

    return {
        "total": int(total or 0),
        "skip": skip,
        "limit": limit,
        "items": [_admin_user_to_dict(user) for user in users],
    }


def admin_get_user(session: Session, user_id: uuid.UUID) -> dict:
    """Lấy chi tiết 1 user."""
    user = session.get(User, user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy tài khoản.",
        )

    return _admin_user_to_dict(user)


def admin_create_user(
    session: Session,
    email: str,
    password: str,
    full_name: str | None = None,
    role: str = "user",
    is_active: bool = True,
) -> dict:
    """Admin tạo tài khoản mới."""
    if len(password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Mật khẩu phải có ít nhất 8 ký tự.",
        )

    if get_user_by_email(session, email):
        raise EmailAlreadyExistsException

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        role=_parse_user_role(role),
        is_active=is_active,
    )

    session.add(user)
    session.commit()
    session.refresh(user)

    return _admin_user_to_dict(user)


def admin_update_user(
    session: Session,
    user_id: uuid.UUID,
    current_admin_id: uuid.UUID,
    full_name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> dict:
    """Admin cập nhật full_name, role, trạng thái active."""
    user = session.get(User, user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy tài khoản.",
        )

    is_self = user.id == current_admin_id

    if is_self and role is not None and _parse_user_role(role) != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin không thể tự hạ quyền của chính mình.",
        )

    if is_self and is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin không thể tự khóa tài khoản của chính mình.",
        )

    if full_name is not None:
        user.full_name = full_name

    if role is not None:
        user.role = _parse_user_role(role)

    if is_active is not None:
        user.is_active = is_active

        if not is_active:
            _revoke_all_user_tokens(session, user.id)

    session.add(user)
    session.commit()
    session.refresh(user)

    return _admin_user_to_dict(user)


def admin_reset_user_password(
    session: Session,
    user_id: uuid.UUID,
    new_password: str,
) -> None:
    """Admin đặt lại mật khẩu user và revoke các refresh token cũ."""
    if len(new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Mật khẩu mới phải có ít nhất 8 ký tự.",
        )

    user = session.get(User, user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy tài khoản.",
        )

    user.password_hash = hash_password(new_password)
    user.reset_password_code_hash = None
    user.reset_password_code_expires_at = None
    user.reset_password_code_attempts = 0

    session.add(user)
    _revoke_all_user_tokens(session, user.id)
    session.commit()


def admin_revoke_user_sessions(session: Session, user_id: uuid.UUID) -> None:
    """Admin thu hồi toàn bộ phiên đăng nhập của user."""
    user = session.get(User, user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy tài khoản.",
        )

    _revoke_all_user_tokens(session, user.id)
