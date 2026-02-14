from typing import List
from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    environment: str = "development"
    database_url: str = "postgresql://plantuser:plantpass@localhost:5432/plantdb"
    cors_origins: List[str] = ["http://localhost:3000"]
    jwt_secret_key: str = "dev-only-change-me-please-dev-only-change-me"
    jwt_access_token_expire_minutes: int = 60 * 24 * 7

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            parts = [item.strip() for item in value.split(",") if item.strip()]
            return parts or ["http://localhost:3000"]
        return value

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret(cls, value: str, info: ValidationInfo) -> str:
        env = str(info.data.get("environment", "development")).lower()
        if len(value) < 32:
            raise ValueError("JWT secret must be at least 32 characters long")
        if env in {"prod", "production"} and "dev-only-change-me" in value:
            raise ValueError("JWT secret must be overridden in production")
        return value


settings = Settings()
