"""Test fixtures for GreenMindDB integration tests."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.auth import get_password_hash

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
            db_url = f"postgresql+psycopg2://{stack_env['POSTGRES_USER']}:{stack_env['POSTGRES_PASSWORD']}@postgres:5432/{stack_env['POSTGRES_DB']}"

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
    """Seed master data: greenhouse, zone, plant, device, sensor."""
    admin_pwd = get_password_hash(docker_stack["ADMIN_PASSWORD"])
    admin_id = str(uuid4())
    admin_email = docker_stack["ADMIN_EMAIL"]
    org_id = str(uuid4())

    sql = f"""
    -- Clean up first
    DELETE FROM sensor_reading;
    DELETE FROM sensor;
    DELETE FROM device;
    DELETE FROM users;
    DELETE FROM greenhouse;
    DELETE FROM organization;

    -- Insert organization
    INSERT INTO organization (id, name)
    VALUES ('{org_id}', 'Test Org')
    ON CONFLICT DO NOTHING;

    INSERT INTO greenhouse (id, organization_id, name, location)
    VALUES ('11111111-1111-1111-1111-111111111111', '{org_id}', 'Test Greenhouse', 'Mac mini Lab')
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO device (id, greenhouse_id, serial, type, fw_version, last_seen, status)
    VALUES (
      '44444444-4444-4444-4444-444444444444',
      '11111111-1111-1111-1111-111111111111',
      'MACMINI-DEV-001', 'gateway', '1.0.0', now(), 'online'
    )
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO sensor (id, device_id, kind, unit, label)
    VALUES (
      '55555555-5555-5555-5555-555555555555',
      '44444444-4444-4444-4444-444444444444',
      'leaf_voltage', 'uV', 'Leaf Sensor 1'
    )
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO sensor (id, device_id, kind, unit, label)
    VALUES (
      '66666666-6666-6666-6666-666666666666',
      '44444444-4444-4444-4444-444444444444',
      'air_temp', 'C', 'Env Sensor 1'
    )
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO users (id, organization_id, email, password_hash, role, name, is_active, created_at)
    VALUES ('{admin_id}', '{org_id}', '{admin_email}', '{admin_pwd}', 'admin', 'System Admin', true, NOW())
    ON CONFLICT DO NOTHING;
    """
    _run_sql(docker_stack, sql)
    return docker_stack


@pytest.fixture(scope="session")
def base_url(seeded_stack: dict[str, str]) -> str:
    if os.getenv("IN_DOCKER_TEST") == "1":
        return "http://backend:8000"
    return f"https://localhost:{seeded_stack['PROXY_HTTPS_PORT']}"


@pytest.fixture(scope="session")
def admin_token(base_url: str, seeded_stack: dict[str, str]) -> str:
    """Login as admin and return access token."""
    resp = httpx.post(
        f"{base_url}/auth/login",
        json={"email": seeded_stack["ADMIN_EMAIL"], "password": seeded_stack["ADMIN_PASSWORD"]},
        verify=False,
        timeout=10.0,
    )
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return resp.json()["access_token"]


@pytest.fixture(scope="session")
def operator_user_and_token(
    base_url: str, admin_token: str, seeded_stack: dict[str, str]
) -> dict[str, str]:
    """Create an operator user via admin API and return {token, user_id, email}."""
    # Create operator
    create_resp = httpx.post(
        f"{base_url}/admin/users",
        json={
            "email": "operator@test.local",
            "password": "test-operator-pass",
            "role": "operator",
            "greenhouse_id": "11111111-1111-1111-1111-111111111111",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
        verify=False,
        timeout=10.0,
    )
    assert create_resp.status_code == 201, f"Operator creation failed: {create_resp.text}"
    user_id = create_resp.json()["id"]

    # Login as operator
    login_resp = httpx.post(
        f"{base_url}/auth/login",
        json={"email": "operator@test.local", "password": "test-operator-pass"},
        verify=False,
        timeout=10.0,
    )
    assert login_resp.status_code == 200, f"Operator login failed: {login_resp.text}"
    data = login_resp.json()
    return {
        "token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "user_id": user_id,
        "email": "operator@test.local",
    }
