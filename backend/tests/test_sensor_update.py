"""Tests for updating sensor settings (e.g. sms_alerts_enabled)."""

from fastapi.testclient import TestClient


def test_update_sensor_sms_alerts_toggle(client: TestClient, setup_test_data, db):
    org = setup_test_data["org"]
    sensor = setup_test_data["sensor"]
    assert sensor.sms_alerts_enabled is True

    # Create admin user in the same organization
    from app.auth import get_password_hash
    from app.models.user import Role, User

    user = User(
        email="sensor-admin@test.com",
        password_hash=get_password_hash("TestPass123"),
        role=Role.ADMIN,
        name="Sensor Admin",
        is_active=True,
        is_verified=True,
        organization_id=org.id,
    )
    db.add(user)
    db.commit()

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "sensor-admin@test.com", "password": "TestPass123"},
    )
    assert resp.status_code == 200
    token = resp.cookies.get("access_token")

    # Disable SMS alerts
    res = client.patch(
        f"/api/v1/sensors/{sensor.id}",
        json={"sms_alerts_enabled": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["sms_alerts_enabled"] is False

    # Enable SMS alerts back
    res2 = client.patch(
        f"/api/v1/sensors/{sensor.id}",
        json={"sms_alerts_enabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 200, res2.text
    data2 = res2.json()
    assert data2["sms_alerts_enabled"] is True
