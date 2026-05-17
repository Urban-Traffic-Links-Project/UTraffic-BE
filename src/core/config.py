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
    app_name: str = "UTraffic API"
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
    redis_db_auth: int = 0       # JWT blacklist, OTP store
    redis_db_corr: int = 1       # Correlation cache
    redis_db_pred: int = 2       # DMFM prediction cache
    redis_db_api: int = 3        # nodes/edges static cache
    redis_db_inference: int = 4  # TVP-VAR spread/cause inference cache

    # ── AWS S3 ───────────────────────────────────────────
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str = "ap-southeast-1"
    s3_bucket_name: str = "utraffic-data-bk-team"

    # ── JWT ──────────────────────────────────────────────────
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    # ── ML Engine ───────────────────────────────────────────
    ml_workspace_path: str = "./ml_workspace"

    # ── TomTom ──────────────────────────────────────────────
    tomtom_api_key: str | None = None
    tomtom_base_url: str = "https://api.tomtom.com"
    # Fixed bbox for IncidentDetails v5: minLon,minLat,maxLon,maxLat (EPSG:4326)
    tomtom_incident_bbox: str = "106.67422,10.75863,106.71737,10.80598"
    tomtom_incident_language: str = "en-GB"
    tomtom_incident_time_validity_filter: str = "present"

    # ── PostgreSQL ───────────────────────────────────────────
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    # ── Reset Password ───────────────────────────────────────────
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = "admin@utraffic.com"
    smtp_password: str = "utraffic_password"
    smtp_from_email: str = "noreply@utraffic.com"
    secret_key: str = "super_secret_key_for_development_purposes"

@lru_cache
def get_settings() -> Settings:
    return Settings()
