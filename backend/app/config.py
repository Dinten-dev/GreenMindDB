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
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg2://plantuser:plantpass@localhost:5432/plantdb"
    cors_origins: str | list[str] = ["http://localhost:3000"]
    jwt_secret_key: str = "dev-only-change-me-please-dev-only-change-me"
    jwt_access_token_expire_minutes: int = 60 * 24 * 7
    cookie_domain: str = ""
    cookie_secure: bool = False

    # SMTP / Email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    contact_form_to: str = ""

    # Resend Application Variables
    resend_api_key: str = ""
    email_from: str = "onboarding@biolingo.org"
    frontend_url: str = "https://biolingo.org"

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
