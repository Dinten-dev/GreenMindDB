"""Unit tests for the health check endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.main import _content_security_policy, _redact_sensitive_path, app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_status_healthy(self, client):
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"


class TestRootEndpoint:
    """Tests for GET /."""

    def test_root_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_root_contains_app_name(self, client):
        response = client.get("/")
        data = response.json()
        assert data["name"] == "GreenMind API"
        assert "version" in data
        assert "docs" in data


class TestSecurityHeaders:
    """Tests for security headers middleware."""

    def test_x_content_type_options(self, client):
        response = client.get("/health")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options(self, client):
        response = client.get("/health")
        assert response.headers.get("X-Frame-Options") == "DENY"

    def test_referrer_policy(self, client):
        response = client.get("/health")
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_production_csp_disallows_inline_and_evaluated_scripts(self):
        policy = _content_security_policy(production=True)

        assert "unsafe-inline" not in policy
        assert "unsafe-eval" not in policy
        assert "script-src 'self'" in policy

    def test_development_csp_remains_compatible_with_api_docs(self):
        policy = _content_security_policy(production=False)

        assert "script-src 'self' 'unsafe-inline' 'unsafe-eval'" in policy


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/public/observe/session/sensitive-observe-token/context",
        "/api/v1/public/evaluate/session/sensitive-evaluate-token/evaluations",
    ],
)
def test_public_session_tokens_are_redacted_from_logged_paths(path):
    redacted = _redact_sensitive_path(path)

    assert "sensitive-" not in redacted
    assert "/session/[REDACTED]/" in redacted
