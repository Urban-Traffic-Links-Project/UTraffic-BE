"""
src/main.py
Điểm khởi động của ứng dụng FastAPI.
Chạy: uv run uvicorn src.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.config import get_settings

settings = get_settings()


# ── Lifespan: chạy khi app khởi động và tắt ─────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Code TRƯỚC yield: chạy khi server khởi động
    Code SAU yield: chạy khi server tắt
 
    Đây là pattern hiện đại của FastAPI thay cho @app.on_event("startup")
    """
    # Khởi động: import models rồi tạo bảng
    print("🚀 Đang khởi động UTraffic API...")

    # Import models để SQLModel.metadata nhận biết tất cả bảng
    import src.storage.models  # noqa: F401
    
    from src.storage.database import create_db_and_tables
    create_db_and_tables()
    print("✅ Database đã sẵn sàng")
    
    yield
 
    # Tắt server
    print("👋 UTraffic API đang tắt...")
    
# ── Khởi tạo ứng dụng FastAPI ───────────────────────────────
app = FastAPI(
    title=settings.app_name,
    description="Hệ thống phân tích tình trạng giao thông TP.HCM",
    debug=settings.debug,
    version="0.1.0",
    docs_url="/docs",  # Swagger UI — mở http://localhost:8000/docs
    redoc_url="/redoc",  # ReDoc UI  — mở http://localhost:8000/redoc
    lifespan=lifespan
)


# ── CORS — cho phép ReactJS frontend gọi API ────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Mount API router ─────────────────────────────────────────
from src.api.router import api_router # noqa: E402
app.include_router(api_router)

# ── Routes cơ bản ────────────────────────────────────────────
@app.get("/", tags=["Health"])
def root():
    """Kiểm tra server có chạy không."""
    return {
        "message": "UTraffic API đang chạy 🚦",
        "env": settings.app_env,
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
def health_check():
    """Kiểm tra trạng thái hệ thống."""
    return {
        "status": "ok",
        "app": settings.app_name,
    }
