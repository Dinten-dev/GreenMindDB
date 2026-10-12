"""Independent settings: no new requirement is imposed on existing deployments."""

from pathlib import Path
import os
import pwd
from urllib.parse import urlsplit
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DirectSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DIRECT_", hide_input_in_errors=True)

    environment: Literal["development", "staging", "production"] = "development"
    ingest_enabled: bool = False
    dashboard_api_url: str = ""
    dashboard_origin: str = ""
    database_url: SecretStr = SecretStr("")
    require_tls: bool = True
    max_chunk_bytes: int = Field(64_000, ge=1024, le=256_000)
    max_segment_bytes: int = Field(32_000_000, ge=1024, le=64_000_000)
    max_chunks_per_segment: int = Field(1200, ge=60, le=10_000)
    max_spool_bytes: int = Field(1_073_741_824, ge=1024)
    max_device_spool_bytes: int = Field(268_435_456, ge=1024)
    late_seconds: int = Field(604_800, ge=60, le=7_776_000)
    idle_seconds: int = Field(120, ge=1, le=3600)
    poll_seconds: int = Field(5, ge=1, le=300)
    payload_offload_enabled: bool = False
    archive_broker_socket: Path | None = None
    storage: Literal["local", "s3"] = "local"
    artifact_root: Path = Path("./direct-artifacts")
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    private_s3_tunnel: bool = False
    retention_enabled: bool = False
    retention_dry_run: bool = True
    raw_days: int = Field(90, ge=90, le=3650)
    feature_days: int = Field(730, ge=730, le=7300)

    @model_validator(mode="after")
    def validate_isolation(self):
        if self.archive_broker_socket is not None and not self.archive_broker_socket.is_absolute():
            raise ValueError("Direct archive broker requires an absolute private socket path")
        if self.payload_offload_enabled and self.archive_broker_socket is None:
            raise ValueError("Payload offload requires a verified archive broker")
        if self.payload_offload_enabled and self.retention_enabled:
            raise ValueError(
                "Independent destructive retention must remain disabled during offload"
            )
        if self.environment != "development":
            if not self.require_tls:
                raise ValueError("Direct TLS is mandatory outside development")
            if self.storage != "s3" or not self.s3_bucket.startswith("greenmind-direct-"):
                raise ValueError("Use a dedicated greenmind-direct-* bucket")
            if not self.s3_bucket.startswith(f"greenmind-direct-{self.environment}"):
                raise ValueError("Direct bucket must match its staging/production environment")
            if (
                not self.s3_access_key.get_secret_value()
                or not self.s3_secret_key.get_secret_value()
            ):
                raise ValueError("Direct bucket-scoped credentials are required")
            if not self.s3_endpoint_url.startswith("https://") and not self.private_tunnel_permitted():
                raise ValueError("Direct object storage requires HTTPS")
            if not self.database_url.get_secret_value().startswith("postgresql"):
                raise ValueError("Direct requires a dedicated PostgreSQL database")
        if self.late_seconds > self.raw_days * 86400:
            raise ValueError("Late-arrival horizon cannot exceed raw retention")
        return self

    def private_tunnel_permitted(self):
        """Only the isolated reader uses the fixed authenticated SSH forward."""
        if not self.private_s3_tunnel:
            return False
        endpoint = urlsplit(self.s3_endpoint_url)
        if (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1'
                or endpoint.port != 9000 or endpoint.path not in ('', '/')
                or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password
                or self.ingest_enabled or self.retention_enabled
                or os.uname().nodename != 'ubuntu-4gb-fsn1-1'
                or os.getenv('RAW_ARCHIVE_WORKER_ROLE') != 'archive'
                or os.getenv('ARCHIVE_REMOTE_AUTH_ENABLED') != 'true'
                or os.getenv('RAW_ARCHIVE_DELETE_ENABLED') != 'false'):
            return False
        try:
            return os.getuid() == pwd.getpwnam('greenmind-archive-read').pw_uid
        except KeyError:
            return False
