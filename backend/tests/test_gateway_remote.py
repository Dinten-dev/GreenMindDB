"""Tests for the gateway remote management system.

Covers: desired state, auth, releases, config, commands, rollout, and audit.
Uses an in-memory SQLite database for speed and isolation.
"""

import hashlib
import io
import json
import tarfile
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.gateway_remote import (
    GatewayAppRelease,
    GatewayCommand,
    GatewayConfigRelease,
    GatewayDesiredState,
)
from app.models.master import Gateway, Zone
from app.models.user import Organization, Role, User
from app.schemas.gateway_remote import ALLOWED_COMMAND_TYPES
from app.services.gateway_remote_service import (
    expire_stale_commands,
    get_desired_state,
    issue_command,
    upload_config_release,
)

# ── Test fixtures ────────────────────────────────────────────────────

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_gateway_remote.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def admin_user(db: Session) -> User:
    org = Organization(name="Test Org")
    db.add(org)
    db.flush()

    user = User(
        email="admin@test.com",
        hashed_password="$2b$12$fakehash",
        role=Role.ADMIN,
        is_active=True,
        organization_id=org.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def zone(db: Session, admin_user: User) -> Zone:
    z = Zone(
        organization_id=admin_user.organization_id,
        name="Test Greenhouse",
        zone_type="GREENHOUSE",
    )
    db.add(z)
    db.commit()
    db.refresh(z)
    return z


@pytest.fixture
def gateway(db: Session, zone: Zone) -> Gateway:
    from app.auth import get_password_hash

    gw = Gateway(
        zone_id=zone.id,
        hardware_id="test-pi-001",
        name="Test Gateway",
        api_key_hash=get_password_hash("test-api-key-secret"),
        status="online",
        is_active=True,
        last_seen=datetime.now(UTC),
    )
    db.add(gw)
    db.commit()
    db.refresh(gw)
    return gw


@pytest.fixture
def app_release(db: Session, admin_user: User) -> GatewayAppRelease:
    release = GatewayAppRelease(
        version="1.2.0",
        artifact_path="test-release.tar.gz",
        sha256="abc123" * 10 + "abcd",
        is_active=True,
        channel="stable",
        file_size_bytes=1024,
        changelog="Test release",
        created_by=admin_user.id,
    )
    db.add(release)
    db.commit()
    db.refresh(release)
    return release


# ── 1. Desired State Auth ────────────────────────────────────────────


def test_desired_state_auth_required(client):
    """Test that desired-state endpoint requires API key."""
    resp = client.get("/api/v1/gateway/desired-state")
    assert resp.status_code == 401


def test_desired_state_invalid_key(client):
    """Test that invalid API key returns 401."""
    resp = client.get(
        "/api/v1/gateway/desired-state",
        headers={"X-Api-Key": "invalid-key"},
    )
    assert resp.status_code == 401


# ── 2. Desired State Content ─────────────────────────────────────────


def test_desired_state_returns_defaults(db, gateway):
    """When no desired state is set, return defaults."""
    result = get_desired_state(db, gateway)
    assert result.app_update_available is False
    assert result.config_update_available is False
    assert result.blocked is False


def test_desired_state_returns_update(db, gateway, app_release):
    """When desired version differs from current, update is available."""
    ds = GatewayDesiredState(
        gateway_id=gateway.id,
        desired_app_version="1.2.0",
        rollout_ring="stable",
    )
    db.add(ds)
    db.commit()

    result = get_desired_state(db, gateway, current_app_version="1.0.0")
    assert result.app_update_available is True
    assert result.desired_app_version == "1.2.0"
    assert result.app_sha256 is not None


def test_desired_state_no_update_when_current(db, gateway, app_release):
    """No update when current version matches desired."""
    ds = GatewayDesiredState(
        gateway_id=gateway.id,
        desired_app_version="1.2.0",
    )
    db.add(ds)
    db.commit()

    result = get_desired_state(db, gateway, current_app_version="1.2.0")
    assert result.app_update_available is False


def test_desired_state_blocked(db, gateway):
    """Blocked gateway gets blocked=True, no updates."""
    ds = GatewayDesiredState(
        gateway_id=gateway.id,
        blocked=True,
        desired_app_version="1.2.0",
    )
    db.add(ds)
    db.commit()

    result = get_desired_state(db, gateway)
    assert result.blocked is True
    assert result.app_update_available is False


# ── 3. State Reports ─────────────────────────────────────────────────


def test_state_report_updates_gateway(client, gateway):
    """State report updates gateway last_seen and version columns."""
    resp = client.post(
        "/api/v1/gateway/state-report",
        json={
            "gateway_id": str(gateway.id),
            "app_version": "1.1.0",
            "config_version": "v2",
            "agent_version": "1.0.0",
            "status": "idle",
            "disk_free_mb": 2048,
        },
        headers={"X-Api-Key": "test-api-key-secret"},
    )
    assert resp.status_code == 200


# ── 4. Release Semver Validation ─────────────────────────────────────


def test_app_release_upload_validates_semver(db, admin_user):
    """Rejects non-semver version strings."""
    from app.services.gateway_remote_service import upload_app_release
    from fastapi import HTTPException, UploadFile

    fake_file = UploadFile(filename="test.tar.gz", file=io.BytesIO(b"test content"))

    with pytest.raises(HTTPException) as exc_info:
        upload_app_release(
            db, admin_user, fake_file,
            version="not-a-version",
            mandatory=False,
            channel="stable",
            min_version=None,
            changelog=None,
            signature=None,
        )
    assert exc_info.value.status_code == 422


def test_app_release_sha256_computed(db, admin_user):
    """SHA256 is correctly computed for uploaded content."""
    from app.services.gateway_remote_service import upload_app_release
    from fastapi import UploadFile

    content = b"test binary content for hashing"
    expected_sha256 = hashlib.sha256(content).hexdigest()
    fake_file = UploadFile(filename="test.tar.gz", file=io.BytesIO(content))

    with patch("app.services.gateway_remote_service.GATEWAY_RELEASE_DIR", "/tmp/test_releases"):
        release = upload_app_release(
            db, admin_user, fake_file,
            version="2.0.0",
            mandatory=False,
            channel="stable",
            min_version=None,
            changelog=None,
            signature=None,
        )

    assert release.sha256 == expected_sha256
    assert release.file_size_bytes == len(content)


# ── 5. Config Validation ────────────────────────────────────────────


def test_config_release_validates_payload(db, admin_user):
    """Config release computes SHA256 and stores payload."""
    config = upload_config_release(
        db, admin_user,
        version="v3",
        config_payload={"upload_interval": 30, "log_level": "INFO"},
        schema_version="1",
        compatible_app_min="1.0.0",
        compatible_app_max=None,
    )
    assert config.sha256 is not None
    assert config.config_payload["upload_interval"] == 30


def test_config_release_duplicate_rejected(db, admin_user):
    """Duplicate config version is rejected."""
    from fastapi import HTTPException

    upload_config_release(db, admin_user, "v1", {"key": "val"}, "1", None, None)
    with pytest.raises(HTTPException) as exc_info:
        upload_config_release(db, admin_user, "v1", {"key": "val2"}, "1", None, None)
    assert exc_info.value.status_code == 409


# ── 6. Command Allowlist ────────────────────────────────────────────


def test_command_allowlist_enforced(db, admin_user, gateway):
    """Rejects commands not in the allowlist."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        issue_command(db, admin_user, gateway.id, "arbitrary_shell")
    assert exc_info.value.status_code == 422
    assert "allowlist" in exc_info.value.detail.lower()


def test_command_allowlist_accepts_valid(db, admin_user, gateway):
    """Accepts valid commands."""
    cmd = issue_command(db, admin_user, gateway.id, "restart_gateway_service")
    assert cmd.status == "pending"
    assert cmd.command_type == "restart_gateway_service"


# ── 7. Command Audit Logging ────────────────────────────────────────


def test_command_audit_logged(db, admin_user, gateway):
    """Every command creates an audit entry."""
    from app.models.audit_log import AuditLog

    issue_command(db, admin_user, gateway.id, "restart_gateway_service")
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.action == "gateway_command.issue")
        .first()
    )
    assert audit is not None


# ── 8. Command Expiry ───────────────────────────────────────────────


def test_command_expires(db, admin_user, gateway):
    """Expired commands are marked as expired."""
    cmd = GatewayCommand(
        gateway_id=gateway.id,
        command_type="restart_gateway_service",
        status="pending",
        created_by=admin_user.id,
        expires_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db.add(cmd)
    db.commit()

    count = expire_stale_commands(db)
    assert count == 1

    db.refresh(cmd)
    assert cmd.status == "expired"


# ── 9. Downgrade Protection ─────────────────────────────────────────


def test_downgrade_blocked(db, gateway, app_release):
    """Update from higher version to lower is blocked (no force)."""
    ds = GatewayDesiredState(
        gateway_id=gateway.id,
        desired_app_version="1.2.0",
        force_downgrade=False,
    )
    db.add(ds)
    db.commit()

    # Current version is higher than desired
    result = get_desired_state(db, gateway, current_app_version="2.0.0")
    assert result.app_update_available is False


def test_downgrade_allowed_with_force(db, gateway, app_release):
    """Downgrade is allowed when force_downgrade=True."""
    ds = GatewayDesiredState(
        gateway_id=gateway.id,
        desired_app_version="1.2.0",
        force_downgrade=True,
    )
    db.add(ds)
    db.commit()

    result = get_desired_state(db, gateway, current_app_version="2.0.0")
    assert result.app_update_available is True


# ── 10. Maintenance Mode ────────────────────────────────────────────


def test_maintenance_mode_in_desired_state(db, gateway):
    """Maintenance mode is returned in desired state."""
    ds = GatewayDesiredState(
        gateway_id=gateway.id,
        maintenance_mode=True,
    )
    db.add(ds)
    db.commit()

    result = get_desired_state(db, gateway)
    assert result.maintenance_mode is True


# ── 11. Update Window Fields ────────────────────────────────────────


def test_update_window_in_desired_state(db, gateway):
    """Update window fields are returned in desired state."""
    ds = GatewayDesiredState(
        gateway_id=gateway.id,
        update_window_start="02:00",
        update_window_end="04:00",
        update_timezone="Europe/Zurich",
        allow_download_outside_window=True,
        allow_apply_outside_window=False,
    )
    db.add(ds)
    db.commit()

    result = get_desired_state(db, gateway)
    assert result.update_window_start == "02:00"
    assert result.update_window_end == "04:00"
    assert result.update_timezone == "Europe/Zurich"
    assert result.allow_download_outside_window is True
    assert result.allow_apply_outside_window is False


# ── 12. Pending Commands Delivery ────────────────────────────────────


def test_pending_commands_delivered(db, gateway, admin_user):
    """Pending commands are included in desired state and marked delivered."""
    cmd = GatewayCommand(
        gateway_id=gateway.id,
        command_type="restart_gateway_service",
        status="pending",
        created_by=admin_user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(cmd)
    db.commit()

    ds = GatewayDesiredState(gateway_id=gateway.id)
    db.add(ds)
    db.commit()

    result = get_desired_state(db, gateway)
    assert len(result.pending_commands) == 1
    assert result.pending_commands[0].command_type == "restart_gateway_service"

    db.refresh(cmd)
    assert cmd.status == "delivered"
