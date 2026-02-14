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

    database_url: str = "postgresql+psycopg2://plantuser:plantpass@localhost:5432/plantdb"

    ingest_token: str = "change-me-ingest-token"
    read_token_required: bool = False
    read_token: Optional[str] = None
    ingest_rate_limit: str = "120/minute"

    s3_provider: Literal["minio", "aws"] = "minio"
    s3_endpoint: Optional[str] = "http://minio:9000"
    s3_region: str = "eu-central-1"
    s3_bucket: str = "greenmind-exports"
    s3_access_key_id: str = "minioadmin"
    s3_secret_access_key: str = "minioadmin"
    s3_secure: bool = False

    enable_continuous_aggregates: bool = True

    @property
    def effective_read_token(self) -> str:
        return self.read_token or self.ingest_token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
