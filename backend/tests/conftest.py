"""Test fixtures for GreenMindDB tests.

Provides two fixture scopes:
1. SQLite-based fixtures (db, client, admin_token, setup_test_data) for fast
   unit and integration tests that don't need Docker.
2. Docker-based fixtures (docker_stack, seeded_stack, base_url) for full-stack
   integration tests. These are skipped when SKIP_DOCKER_TESTS=1.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session, sessionmaker

from app.auth import get_password_hash
from app.database import Base, get_db
from app.main import app
from app.models.master import Gateway, Sensor, Zone
from app.models.user import Organization, Role, User

# ── SQLite-based fixtures (no Docker required) ──────────────────────

SQLITE_URL = "sqlite:///./test_ci.db"
_sqlite_engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})

# Register PostgreSQL UUID type with SQLite compiler so models using
# sqlalchemy.dialects.postgresql.UUID can be created in the test DB.


def _visit_uuid(self, type_, **kw):
    """Render PostgreSQL UUID columns as CHAR(32) in SQLite."""
    return "CHAR(32)"


SQLiteTypeCompiler.visit_UUID = _visit_uuid

# Patch the PostgreSQL UUID type's bind processor so it accepts both
# uuid.UUID objects and plain string UUIDs when running against SQLite.
# Without this, SQLAlchemy calls `value.hex` on raw strings and crashes.

_original_bind_processor = PG_UUID.bind_processor


def _patched_bind_processor(self, dialect):
    """Return a processor that converts strings to UUID objects before binding."""
    import uuid as _uuid_mod

    orig = _original_bind_processor(self, dialect)

    def process(value):
        if value is None:
            return value
        if isinstance(value, str):
            try:
                value = _uuid_mod.UUID(value)
            except ValueError:
                pass
        if orig is not None:
            return orig(value)
        return value

    return process


PG_UUID.bind_processor = _patched_bind_processor


@event.listens_for(_sqlite_engine, "connect")
def _register_sqlite_functions(dbapi_conn, connection_record):
    """Register PostgreSQL-compatible functions for SQLite."""
    import sqlite3
    import uuid as uuid_mod

    dbapi_conn.create_function("now", 0, lambda: datetime.now(UTC).isoformat())

    # Allow SQLite to handle UUID comparisons with string inputs
    sqlite3.register_adapter(uuid_mod.UUID, lambda u: str(u))


_TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=_sqlite_engine)


@pytest.fixture(autouse=True)
def _reset_tables():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=_sqlite_engine)
    yield
    Base.metadata.drop_all(bind=_sqlite_engine)


@pytest.fixture
def db():
    """Provide a clean SQLite session."""
    session = _TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db: Session):
    """TestClient with overridden DB dependency."""

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
def admin_token(client: TestClient, db: Session) -> str:
    """Create an admin user and return a valid JWT token."""
    org = Organization(name="Test Org CI")
    db.add(org)
    db.flush()

    user = User(
        email="ci-admin@test.com",
        password_hash=get_password_hash("TestPass123"),
        role=Role.ADMIN,
        name="CI Admin",
        is_active=True,
        is_verified=True,
        organization_id=org.id,
    )
    db.add(user)
    db.commit()

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "ci-admin@test.com", "password": "TestPass123"},
    )
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    token = resp.cookies.get("access_token")
    assert token, "No access_token cookie in login response"
    return token


@pytest.fixture
def setup_test_data(db: Session) -> dict:
    """Seed minimal test data: org, zone, gateway, sensor."""
    org = Organization(name="Test Org Fixture")
    db.add(org)
    db.flush()

    zone = Zone(
        organization_id=org.id,
        name="Test Zone",
        location="CI Lab",
        zone_type="GREENHOUSE",
    )
    db.add(zone)
    db.flush()

    gw = Gateway(
        zone_id=zone.id,
        hardware_id="test-gw-ci",
        name="CI Gateway",
        api_key_hash=get_password_hash("ci-api-key"),
        status="online",
        is_active=True,
        last_seen=datetime.now(UTC),
    )
    db.add(gw)
    db.flush()

    sensor = Sensor(
        gateway_id=gw.id,
        mac_address="AA:BB:CC:DD:EE:FF",
        name="CI Sensor",
        sensor_type="leaf_voltage",
        status="online",
        last_seen=datetime.now(UTC),
    )
    db.add(sensor)
    db.commit()

    return {
        "org": org,
        "zone": zone,
        "gateway": gw,
        "sensor": sensor,
    }


# ── Docker-based fixtures (full-stack, skipped in CI) ───────────────

ROOT_DIR = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT_DIR / "compose" / "docker-compose.yml"
ENV_FILE = ROOT_DIR / "compose" / ".env.test"


def _read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _compose_cmd(env_file: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "--env-file",
        str(env_file),
    ]


def _wait_for_health(base_url: str, timeout_seconds: int = 180) -> None:
    import httpx

    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=3.0, verify=False)
            if resp.status_code == 200:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Stack did not become healthy in time. Last error: {last_error}")


def _run_sql(stack_env: dict[str, str], sql: str) -> None:
    if os.getenv("IN_DOCKER_TEST") == "1":
        from sqlalchemy import create_engine, text

        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            db_url = (
                f"postgresql+psycopg2://{stack_env['POSTGRES_USER']}:"
                f"{stack_env['POSTGRES_PASSWORD']}@postgres:5432/{stack_env['POSTGRES_DB']}"
            )

        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text(sql))
        return

    cmd = _compose_cmd(ENV_FILE) + [
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        stack_env["POSTGRES_USER"],
        "-d",
        stack_env["POSTGRES_DB"],
        "-v",
        "ON_ERROR_STOP=1",
        "-f",
        "-",
    ]
    subprocess.run(cmd, input=sql.encode("utf-8"), check=True)


@pytest.fixture(scope="session")
def docker_stack() -> dict[str, str]:
    if os.getenv("SKIP_DOCKER_TESTS") == "1":
        pytest.skip("Docker-based integration tests disabled via SKIP_DOCKER_TESTS=1")

    if os.getenv("IN_DOCKER_TEST") == "1":
        _wait_for_health("http://backend:8000")
        yield dict(os.environ)
        return

    stack_env = _read_env_file(ENV_FILE)

    compose_base = _compose_cmd(ENV_FILE)

    subprocess.run(compose_base + ["down", "-v"], check=False)
    subprocess.run(compose_base + ["up", "-d", "--build"], check=True)

    base_url = f"https://localhost:{stack_env['PROXY_HTTPS_PORT']}"
    _wait_for_health(base_url)

    yield stack_env

    subprocess.run(compose_base + ["down", "-v"], check=False)


@pytest.fixture(scope="session")
def seeded_stack(docker_stack: dict[str, str]) -> dict[str, str]:
    """Seed master data: greenhouse, gateway, sensor."""
    admin_pwd = get_password_hash(docker_stack["ADMIN_PASSWORD"])
    admin_id = str(uuid4())
    admin_email = docker_stack["ADMIN_EMAIL"]
    org_id = str(uuid4())

    sql = f"""
    DELETE FROM sensor_reading;
    DELETE FROM sensor;
    DELETE FROM gateway;
    DELETE FROM users;
    DELETE FROM zone;
    DELETE FROM organization;

    INSERT INTO organization (id, name)
    VALUES ('{org_id}', 'Test Org')
    ON CONFLICT DO NOTHING;

    INSERT INTO zone (id, organization_id, name, location, zone_type)
    VALUES ('11111111-1111-1111-1111-111111111111', '{org_id}', 'Test Zone', 'Mac mini Lab', 'GREENHOUSE')
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO gateway (id, zone_id, hardware_id, name, fw_version, last_seen, status)
    VALUES (
      '44444444-4444-4444-4444-444444444444',
      '11111111-1111-1111-1111-111111111111',
      'RPi-TEST-001', 'Test Gateway', '1.0.0', now(), 'online'
    )
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO sensor (id, gateway_id, mac_address, name, sensor_type, last_seen, status)
    VALUES (
      '55555555-5555-5555-5555-555555555555',
      '44444444-4444-4444-4444-444444444444',
      'AA:BB:CC:DD:EE:01', 'Leaf Sensor 1', 'leaf_voltage', now(), 'online'
    )
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO sensor (id, gateway_id, mac_address, name, sensor_type, last_seen, status)
    VALUES (
      '66666666-6666-6666-6666-666666666666',
      '44444444-4444-4444-4444-444444444444',
      'AA:BB:CC:DD:EE:02', 'Env Sensor 1', 'air_temp', now(), 'online'
    )
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO users (id, organization_id, email, password_hash, role, name, is_active, is_verified, created_at)
    VALUES ('{admin_id}', '{org_id}', '{admin_email}', '{admin_pwd}', 'admin', 'System Admin', true, true, NOW())
    ON CONFLICT DO NOTHING;
    """
    _run_sql(docker_stack, sql)
    return docker_stack


@pytest.fixture(scope="session")
def docker_base_url(seeded_stack: dict[str, str]) -> str:
    if os.getenv("IN_DOCKER_TEST") == "1":
        return "http://backend:8000"
    return f"https://localhost:{seeded_stack['PROXY_HTTPS_PORT']}"


@pytest.fixture(scope="session")
def docker_admin_token(docker_base_url: str, seeded_stack: dict[str, str]) -> str:
    """Login as admin and return access token (Docker stack only)."""
    import httpx

    resp = httpx.post(
        f"{docker_base_url}/auth/login",
        json={"email": seeded_stack["ADMIN_EMAIL"], "password": seeded_stack["ADMIN_PASSWORD"]},
        verify=False,
        timeout=10.0,
    )
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return resp.json()["access_token"]
