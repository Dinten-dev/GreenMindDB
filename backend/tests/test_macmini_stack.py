"""Integration tests for the GreenMindDB Mac mini stack."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration


class TestHealth:
    def test_health_returns_healthy(self, base_url):
        resp = httpx.get(f"{base_url}/health", verify=False, timeout=10.0)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"


class TestAuth:
    def test_admin_login_returns_user_and_cookie(self, base_url, seeded_stack):
        resp = httpx.post(
            f"{base_url}/api/v1/auth/login",
            json={"email": seeded_stack["ADMIN_EMAIL"], "password": seeded_stack["ADMIN_PASSWORD"]},
            verify=False,
            timeout=10.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Token must NOT be in JSON body (security: httpOnly cookie only)
        assert "access_token" not in data
        assert data["user"]["role"] == "admin"
        # Auth cookie must be set
        assert "access_token" in resp.cookies

    def test_invalid_credentials_returns_401(self, base_url):
        resp = httpx.post(
            f"{base_url}/api/v1/auth/login",
            json={"email": "bad@example.com", "password": "wrong"},
            verify=False,
            timeout=10.0,
        )
        assert resp.status_code == 401


class TestIngestion:
    def test_ingest_requires_api_key(self, base_url):
        payload = {
            "measurement_id": "123e4567-e89b-12d3-a456-426614174000",
            "device_serial": "MACMINI-DEV-001",
            "readings": [{"sensor_kind": "air_temp", "value": 22.5, "unit": "C"}],
        }
        resp = httpx.post(
            f"{base_url}/api/v1/ingest",
            json=payload,
            verify=False,
            timeout=10.0,
        )
        assert resp.status_code == 401
