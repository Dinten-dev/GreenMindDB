"""Boundary-value unit tests for GreenMindDB (SKO LE4).

Tests cover four areas with explicit boundary analysis:
1. Auth logic – edge-case token payloads
2. Ingest endpoint – empty/min/max payloads, auth failures
3. Zone CRUD – create, list, delete-nonexistent
4. Sensor data aggregation – value extremes

All tests use the SQLite-based fixtures from conftest.py (no Docker required).
"""

import sys

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth import create_access_token, decode_token

# ── Auth Logic ──────────────────────────────────────────────────────────


class TestAuthBoundary:
    """Boundary tests for JWT token handling."""

    def test_decode_empty_string_returns_none(self):
        """Boundary: empty string is the shortest possible invalid token.

        An empty string must not crash the decoder – it should return None.
        """
        result = decode_token("")
        assert result is None

    def test_token_without_sub_claim_rejected(self, client: TestClient, db: Session):
        """Boundary: token with valid signature but missing 'sub' claim.

        The auth middleware requires 'sub' to identify the user.
        A token without 'sub' must return 401, not 500.
        """
        # Create a structurally valid JWT without the required 'sub' claim
        token = create_access_token({"role": "admin"})  # no 'sub' key

        response = client.get(
            "/api/v1/zones",
            cookies={"access_token": token},
        )
        assert response.status_code == 401
        assert "Invalid token payload" in response.json()["detail"]


# ── Ingest Endpoint ─────────────────────────────────────────────────────


class TestIngestBoundary:
    """Boundary tests for POST /api/v1/ingest validation."""

    def test_ingest_missing_api_key_returns_401(self, client: TestClient):
        """Boundary: request without X-Api-Key header.

        The ingest endpoint requires gateway authentication via X-Api-Key.
        Missing header must return 401 before any payload processing.
        """
        payload = {
            "measurement_id": "00000000-0000-0000-0000-000000000001",
            "gateway_serial": "test-gw-ci",
            "readings": [],
        }
        response = client.post("/api/v1/ingest", json=payload)
        assert response.status_code == 401
        assert "Missing X-Api-Key" in response.json()["detail"]

    def test_ingest_unknown_gateway_serial_returns_404(self, client: TestClient):
        """Boundary: valid API key format but gateway_serial does not exist in DB.

        The endpoint returns 404 (Not Found) with an error detail
        so the gateway knows it is unauthorized/unknown but doesn't hard-reset.
        """
        payload = {
            "measurement_id": "00000000-0000-0000-0000-000000000002",
            "gateway_serial": "nonexistent-serial-xyz",
            "readings": [],
        }
        response = client.post(
            "/api/v1/ingest",
            json=payload,
            headers={"X-Api-Key": "any-key"},
        )
        assert response.status_code == 404

    def test_ingest_empty_readings_list(self, client: TestClient, setup_test_data: dict):
        """Boundary: readings list is empty (length = 0).

        Zero readings is a valid edge case (gateway sends heartbeat-like
        ingest with no sensor data). Should succeed with ingested=0.
        """
        payload = {
            "measurement_id": "00000000-0000-0000-0000-000000000010",
            "gateway_serial": "test-gw-ci",
            "readings": [],  # Grenzwert: leere Liste
        }
        response = client.post(
            "/api/v1/ingest",
            json=payload,
            headers={"X-Api-Key": "ci-api-key"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"
        assert data["ingested"] == 0  # Grenzwert: 0 Readings → 0 ingested

    def test_ingest_single_reading_minimum(self, client: TestClient, setup_test_data: dict):
        """Boundary: exactly 1 reading (minimum non-empty payload).

        The smallest valid payload with data. Must persist exactly 1 reading.
        """
        payload = {
            "measurement_id": "00000000-0000-0000-0000-000000000011",
            "gateway_serial": "test-gw-ci",
            "readings": [
                {
                    "sensor_mac": "AA:BB:CC:DD:EE:FF",
                    "sensor_kind": "leaf_voltage",
                    "value": 0.0,  # Grenzwert: kleinstmöglicher positiver Wert = 0
                    "unit": "mV",
                }
            ],
        }
        response = client.post(
            "/api/v1/ingest",
            json=payload,
            headers={"X-Api-Key": "ci-api-key"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"
        assert data["ingested"] == 1  # Grenzwert: genau 1

    def test_ingest_extreme_sensor_value(self, client: TestClient, setup_test_data: dict):
        """Boundary: sensor value at sys.float_info.max (largest finite float).

        Ensures the system does not overflow or reject extreme values.
        Real sensors can produce unexpectedly large readings on hardware fault.
        """
        payload = {
            "measurement_id": "00000000-0000-0000-0000-000000000012",
            "gateway_serial": "test-gw-ci",
            "readings": [
                {
                    "sensor_mac": "AA:BB:CC:DD:EE:FF",
                    "sensor_kind": "leaf_voltage",
                    "value": sys.float_info.max,  # Grenzwert: max float ~1.8e308
                    "unit": "mV",
                }
            ],
        }
        response = client.post(
            "/api/v1/ingest",
            json=payload,
            headers={"X-Api-Key": "ci-api-key"},
        )
        assert response.status_code == 201
        assert response.json()["ingested"] == 1

    def test_ingest_negative_sensor_value(self, client: TestClient, setup_test_data: dict):
        """Boundary: negative sensor signal value (-999.99).

        Negative values are valid in bioelectrical measurements (e.g.,
        inverted polarity). The system must not reject them.
        """
        payload = {
            "measurement_id": "00000000-0000-0000-0000-000000000013",
            "gateway_serial": "test-gw-ci",
            "readings": [
                {
                    "sensor_mac": "AA:BB:CC:DD:EE:FF",
                    "sensor_kind": "leaf_voltage",
                    "value": -999.99,  # Grenzwert: negativer Messwert
                    "unit": "mV",
                }
            ],
        }
        response = client.post(
            "/api/v1/ingest",
            json=payload,
            headers={"X-Api-Key": "ci-api-key"},
        )
        assert response.status_code == 201
        assert response.json()["ingested"] == 1


# ── Zone CRUD ───────────────────────────────────────────────────────────


class TestZoneBoundary:
    """Boundary tests for zone create/list/delete."""

    def test_zone_create_and_list(self, client: TestClient, admin_token: str):
        """Boundary: create a single zone and verify it appears in the list.

        Validates the full create → list round-trip with the minimum
        required fields (name only, location and GPS optional).
        """
        # Create zone with minimum required fields
        create_resp = client.post(
            "/api/v1/zones",
            json={"name": "Boundary Test Zone"},
            cookies={"access_token": admin_token},
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["name"] == "Boundary Test Zone"
        assert created["zone_type"] == "GREENHOUSE"  # default

        # Verify it appears in list
        list_resp = client.get(
            "/api/v1/zones",
            cookies={"access_token": admin_token},
        )
        assert list_resp.status_code == 200
        zones = list_resp.json()
        zone_names = [z["name"] for z in zones]
        assert "Boundary Test Zone" in zone_names

    def test_zone_delete_nonexistent_returns_404(self, client: TestClient, admin_token: str):
        """Boundary: DELETE a zone ID that does not exist.

        Must return 404, not 500 or silent success.
        """
        fake_zone_id = "99999999-9999-9999-9999-999999999999"
        response = client.delete(
            f"/api/v1/zones/{fake_zone_id}",
            cookies={"access_token": admin_token},
        )
        assert response.status_code == 404


# ── Ingest Idempotency ──────────────────────────────────────────────────


class TestIngestIdempotency:
    """Idempotency tests for duplicate measurement_id detection."""

    def test_ingest_duplicate_measurement_id_returns_duplicate(
        self, client: TestClient, setup_test_data: dict
    ):
        """Boundary: same measurement_id sent twice → idempotent response.

        The ingest service stores a log entry on first success.
        Repeating the same measurement_id returns status='duplicate'
        with ingested=0 (idempotent design, not an error).
        """
        measurement_id = "00000000-0000-0000-0000-000000000099"
        payload = {
            "measurement_id": measurement_id,
            "gateway_serial": "test-gw-ci",
            "readings": [
                {
                    "sensor_mac": "AA:BB:CC:DD:EE:FF",
                    "sensor_kind": "leaf_voltage",
                    "value": 42.0,
                    "unit": "mV",
                }
            ],
        }

        # First request → success
        resp1 = client.post(
            "/api/v1/ingest",
            json=payload,
            headers={"X-Api-Key": "ci-api-key"},
        )
        assert resp1.status_code == 201
        assert resp1.json()["status"] == "success"
        assert resp1.json()["ingested"] == 1

        # Second request with same measurement_id → duplicate (idempotent)
        resp2 = client.post(
            "/api/v1/ingest",
            json=payload,
            headers={"X-Api-Key": "ci-api-key"},
        )
        assert resp2.status_code == 201
        assert resp2.json()["status"] == "duplicate"
        assert resp2.json()["ingested"] == 0  # Grenzwert: keine Daten doppelt gespeichert

    def test_ingest_electrode_disconnect_triggers_sms(
        self, client: TestClient, db: Session, setup_test_data: dict, mocker
    ):
        """Boundary: bio_signal value <= 10.0 mV triggers SMS alert."""
        # 1. Create a user in the same organization with a phone number
        from app.models.user import User
        org = setup_test_data["org"]
        user = User(
            email="test-alert-user@example.com",
            password_hash="foo",
            organization_id=org.id,
            phone_number="+41760000000",
            is_active=True,
        )
        db.add(user)
        db.commit()

        # Mock notification service
        mock_send = mocker.patch("app.routers.ingest.notification_service.send_electrode_disconnect_alert", return_value=True)

        # 2. Ingest low value (flatline) for bio_signal
        sensor = setup_test_data["sensor"]
        measurement_id = "00000000-0000-0000-0000-000000009999"
        payload = {
            "measurement_id": measurement_id,
            "gateway_serial": "test-gw-ci",
            "readings": [
                {
                    "sensor_mac": sensor.mac_address,
                    "sensor_kind": "bio_signal",
                    "value": 0.0,
                    "unit": "mV",
                }
            ],
        }

        resp = client.post(
            "/api/v1/ingest",
            json=payload,
            headers={"X-Api-Key": "ci-api-key"},
        )
        assert resp.status_code == 201
        
        # Verify alert was sent
        mock_send.assert_called_once_with(
            phone_number="+41760000000",
            sensor_mac=sensor.mac_address,
            zone_name="Test Zone",
        )
