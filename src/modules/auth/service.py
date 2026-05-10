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

from src.core.exceptions import (
    CredentialsException,
    EmailAlreadyExistsException,
    InvalidRefreshTokenException,
)
from src.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from src.core.config import get_settings
from src.storage.models.auth import RefreshToken, User, UserSession
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
    # 1. Tạo Access Token (JWT, không lưu DB)
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
    # Bước 1+2: tìm user và xác thực password
    user = get_user_by_email(session, email)
    if not user or not verify_password(password, user.password_hash):
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