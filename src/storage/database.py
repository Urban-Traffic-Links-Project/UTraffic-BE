"""
src/storage/database.py
Quản lý kết nối PostgreSQL với SQLModel + Psycopg 3.

Hai khái niệm quan trọng:
- Engine: "cổng kết nối" tới DB, tạo 1 lần duy nhất khi app khởi động
- Session: "phiên làm việc" với DB, tạo mới cho mỗi request rồi đóng lại
"""

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from src.core.config import get_settings

settings = get_settings()

# ── Tạo Engine ───────────────────────────────────────────────
engine = create_engine(
    settings.database_url,
    echo=settings.debug,  # In SQL query ra terminal khi debug=True
    pool_pre_ping=True,  # Tự kiểm tra kết nối trước khi dùng (tránh lỗi "connection closed")
    pool_size=10,  # Số kết nối tối đa trong pool
    max_overflow=20,  # Số kết nối tạm thêm khi pool đầy
)

def create_db_and_tables() -> None:
    """
    Tạo tất cả bảng trong DB dựa trên SQLModel models.
    Gọi hàm này khi app khởi động (dùng cho dev/test).
    Production dùng Alembic migration thay thế.
    """
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """
    Dependency function — FastAPI tự động gọi hàm này cho mỗi request.

    Dùng như sau trong router:
        @app.get("/users")
        def get_users(session: Session = Depends(get_session)):
            ...

    'with Session(engine)' đảm bảo session luôn được đóng sau khi dùng,
    dù có lỗi xảy ra hay không (giống try/finally).
    """
    with Session(engine) as session:
        yield session
