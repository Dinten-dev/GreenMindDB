from urllib.parse import urlsplit

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip().lower().replace("_", "-")
    return "change-me" in normalized or "changeme" in normalized


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
    jwt_access_token_expire_minutes: int = Field(480, ge=5, le=10_080)
    cookie_domain: str = ""
    cookie_secure: bool = False

    # Experimental data paths are disabled unless an operator explicitly opts in.
    enable_experimental_provisioning: bool = False
    enable_experimental_biosignal: bool = False

    # Request and connection safeguards.
    max_wav_upload_bytes: int = Field(32 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    max_wav_bundle_files: int = Field(2_000, ge=1, le=100_000)
    max_wav_bundle_bytes: int = Field(
        2 * 1024 * 1024 * 1024,
        ge=1024 * 1024,
        le=100 * 1024 * 1024 * 1024,
    )
    websocket_max_connections: int = Field(500, ge=1, le=100_000)
    websocket_max_connections_per_user: int = Field(5, ge=1, le=1_000)
    websocket_max_connections_per_ip: int = Field(20, ge=1, le=10_000)
    websocket_idle_timeout_seconds: int = Field(300, ge=5, le=86_400)
    websocket_send_timeout_seconds: float = Field(2.0, gt=0, le=60)
    sensor_export_max_rows: int = Field(250_000, ge=1, le=5_000_000)
    sensor_export_max_bytes: int = Field(
        64 * 1024 * 1024,
        ge=1024 * 1024,
        le=1024 * 1024 * 1024,
    )
    sensor_export_max_kinds: int = Field(32, ge=1, le=100)

    # PEM Ed25519 public key shared with the gateway agent's release verifier.
    gateway_release_signing_public_key_path: str = Field("", max_length=1_024)

    # Resend / Email
    resend_api_key: str = ""
    email_from: str = "onboarding@biolingo.org"
    frontend_url: str = "https://biolingo.org"
    contact_form_to: str = ""

    # S3 / MinIO
    s3_endpoint: str = "http://minio:9000"
    s3_region: str = "eu-central-1"
    s3_access_key_id: str = "minioadmin"
    s3_secret_access_key: str = "change-me-minio-root-password"

    # ASPSMS (Electrode alerts)
    aspsms_userkey: str = ""
    aspsms_password: str = ""
    aspsms_sender_id: str = "GreenMind"

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
        if env in {"prod", "production", "stage", "staging"} and "dev-only-change-me" in value:
            raise ValueError("JWT secret must be overridden in staging and production")
        return value

    @model_validator(mode="after")
    def validate_deployed_security_settings(self) -> "Settings":
        env = self.environment.strip().lower()
        if env not in {"prod", "production", "stage", "staging"}:
            return self

        if not self.gateway_release_signing_public_key_path.strip():
            raise ValueError(
                "GATEWAY_RELEASE_SIGNING_PUBLIC_KEY_PATH is required in staging and production"
            )

        if self.enable_experimental_provisioning:
            raise ValueError(
                "ENABLE_EXPERIMENTAL_PROVISIONING must remain false in staging and production"
            )
        if self.enable_experimental_biosignal:
            raise ValueError(
                "ENABLE_EXPERIMENTAL_BIOSIGNAL must remain false in staging and production"
            )
        if not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in staging and production")

        deployed_secrets = {
            "JWT_SECRET_KEY": self.jwt_secret_key,
            "RESEND_API_KEY": self.resend_api_key,
            "S3_ACCESS_KEY_ID": self.s3_access_key_id,
            "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
        }
        for setting_name, value in deployed_secrets.items():
            if not value.strip() or _looks_like_placeholder(value):
                raise ValueError(
                    f"{setting_name} must be set to a non-placeholder value in staging and production"
                )
        if self.s3_access_key_id.strip().lower() == "minioadmin":
            raise ValueError(
                "S3_ACCESS_KEY_ID must not use the MinIO default in staging and production"
            )

        frontend_url = urlsplit(self.frontend_url)
        if frontend_url.scheme != "https" or not frontend_url.hostname:
            raise ValueError("FRONTEND_URL must be an absolute HTTPS URL in staging and production")

        if any("*" in origin for origin in self.cors_origins):
            raise ValueError("wildcard CORS origins are forbidden in staging and production")
        return self


settings = Settings()
