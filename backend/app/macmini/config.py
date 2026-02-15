"""Macmini stack configuration via environment variables."""
from functools import lru_cache
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production"] = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg2://greenmind:greenmind@localhost:5432/greenmind"

    # JWT
    jwt_secret_key: str = "change-me-jwt-secret-min-32-chars-long"
    jwt_access_expire_minutes: int = 30
    jwt_refresh_expire_days: int = 7

    # Ingestion auth (gateway bearer token)
    ingest_token: str = "change-me-ingest-token"

    # Rate limiting
    ingest_rate_limit: str = "120/minute"
    login_rate_limit: str = "10/minute"

    # S3 / MinIO
    s3_provider: Literal["minio", "aws"] = "minio"
    s3_endpoint: Optional[str] = "http://minio:9000"
    s3_region: str = "eu-central-1"
    s3_bucket: str = "greenmind-exports"
    s3_access_key_id: str = "minioadmin"
    s3_secret_access_key: str = "minioadmin"
    s3_secure: bool = False

    # Timescale features
    enable_continuous_aggregates: bool = True

    # Default admin seeding
    admin_email: str = "admin@greenmind.local"
    admin_password: str = "change-me-admin-password"

    # CORS
    cors_origins: str = "*"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
