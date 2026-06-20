"""Tests for gateway-admin endpoints: fleet overview and audit logs.

Covers the admin-only endpoints to improve coverage on
app/routers/gateway_admin.py (from 46% → target 60%+).
Gateway admin endpoints require ADMIN or OWNER role.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


class TestFleetOverview:
    """GET /api/v1/admin/gateway-fleet – fleet listing."""

    def test_fleet_overview_empty(self, client: TestClient, admin_token: str):
        """Boundary: no gateways registered → empty list, total=0."""
        response = client.get(
            "/api/v1/admin/gateway-fleet",
            cookies={"access_token": admin_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_fleet_overview_requires_auth(self, client: TestClient):
        """No token → 401."""
        response = client.get("/api/v1/admin/gateway-fleet")
        assert response.status_code == 401

    def test_fleet_overview_with_pagination(self, client: TestClient, admin_token: str):
        """Boundary: pagination params offset=0, limit=1."""
        response = client.get(
            "/api/v1/admin/gateway-fleet",
            params={"offset": 0, "limit": 1},
            cookies={"access_token": admin_token},
        )
        assert response.status_code == 200
        assert "items" in response.json()
        assert "total" in response.json()


class TestAuditLogs:
    """GET /api/v1/admin/gateway-audit-logs – audit trail."""

    def test_audit_logs_empty(self, client: TestClient, admin_token: str):
        """Boundary: no audit entries → empty list."""
        response = client.get(
            "/api/v1/admin/gateway-audit-logs",
            cookies={"access_token": admin_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_audit_logs_with_data(self, client: TestClient, admin_token: str, db: Session):
        """Audit log entry appears in the list after creation."""
        log = AuditLog(
            action="gateway_paired",
            entity_type="gateway",
            entity_id="test-gw-123",
            details="Test gateway paired",
        )
        db.add(log)
        db.commit()

        response = client.get(
            "/api/v1/admin/gateway-audit-logs",
            cookies={"access_token": admin_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["action"] == "gateway_paired"
        assert data["items"][0]["entity_type"] == "gateway"

    def test_audit_logs_filter_by_action(self, client: TestClient, admin_token: str, db: Session):
        """Filtering by action shows only matching entries."""
        db.add(AuditLog(action="gateway_paired", entity_type="gateway", entity_id="gw-1"))
        db.add(AuditLog(action="gateway_updated", entity_type="gateway", entity_id="gw-2"))
        db.commit()

        response = client.get(
            "/api/v1/admin/gateway-audit-logs",
            params={"action": "gateway_paired"},
            cookies={"access_token": admin_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["action"] == "gateway_paired"

    def test_audit_logs_requires_admin(self, client: TestClient):
        """No token → 401."""
        response = client.get("/api/v1/admin/gateway-audit-logs")
        assert response.status_code == 401
