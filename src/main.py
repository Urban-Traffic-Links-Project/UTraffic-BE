"""
src/main.py
Điểm khởi động của ứng dụng FastAPI.
Chạy: uv run uvicorn src.main:app --reload
"""
from fastapi import FastAPI

from src.core.config import get_settings

settings = get_settings()

# ── Khởi tạo ứng dụng FastAPI ───────────────────────────────
app = FastAPI(
    title=settings.app_name,
    description="Hệ thống phân tích tình trạng giao thông TP.HCM",
    debug=settings.debug,
    version="0.1.0",
    docs_url="/docs",       # Swagger UI — mở http://localhost:8000/docs
    redoc_url="/redoc",     # ReDoc UI  — mở http://localhost:8000/redoc
)

@app.get("/")
def root():
    """Kiểm tra server có chạy không"""
    return {
        "message": "Utraffic API đang chạy",
        "env": settings.app_env,
        "docs": "http://localhost:8000/docs",
        "redoc": "http://localhost:8000/redoc"
    }


@app.get("/health")
def health_check():
    """Kiểm tra trạng thái các thành phần hệ thống."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "database": "chưa kết nối",
        "redis": "chưa kết nối",
    }
 