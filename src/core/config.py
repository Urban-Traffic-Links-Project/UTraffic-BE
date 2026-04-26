"""
core/config.py
Quản lý toàn bộ biến môi trường của ứng dụng.
Pydantic-settings tự động đọc từ file .env và có type-check.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_URL = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_URL / ".env"


class Settings(BaseSettings):
    # ── App ─────────────────────────────────────────────────
    app_name: str = "Utraffic API"
    app_env: str = "development"
    debug: bool = True

    # ── PostgreSQL ───────────────────────────────────────────
    database_url: str
    postgres_password: str
    postgres_db: str = "utraffic_db"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # ── Redis ───────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db_auth: int = 0
    redis_db_corr: int = 1
    redis_db_pred: int = 2
    redis_db_api: int = 3

    # ── JWT ──────────────────────────────────────────────────
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    # ── ML Engine ───────────────────────────────────────────
    ml_workspace_path: str = "./ml_workspace"
    model_checkpoint_path: str = "./ml_workspace/checkpoints/best_model.pth"
    npz_features_path: str = "./ml_workspace/data/processed_features.npz"

    # ── PostgreSQL ───────────────────────────────────────────
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


print(f"[CONFIG] chạy thành công {BASE_URL}")
@lru_cache
def get_settings() -> Settings:
    return Settings()
