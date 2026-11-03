"""Gateway removal revokes device access while retaining scientific history."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.auth import get_password_hash
from app.config import settings
from app.gateway_auth import generate_gateway_api_key, set_gateway_api_key
from app.models.ingest_log import IngestLog
from app.models.master import Gateway, Sensor
from app.models.pairing import PairingCode
from app.models.plant import Plant, PlantSensorAssignment
from app.models.timeseries import SensorReading
from app.models.user import Role, User
from app.models.wav_file import WavFile
from app.services.gateway_service import delete_gateway, gateway_commands_cache


def browser(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("method", ["delete", "post"])
def test_removal_preserves_gateway_sensor_recordings_and_assignment_history(
    client, db, setup_test_data, tenant_admin_token, method
):
    gw, sensor = setup_test_data["gateway"], setup_test_data["sensor"]
    now = datetime.now(UTC)
    plant = Plant(
        organization_id=setup_test_data["org"].id,
        zone_id=gw.zone_id,
        name="Scientific canary",
    )
    db.add(plant)
    db.flush()
    assignment = PlantSensorAssignment(plant_id=plant.id, sensor_id=sensor.id, notes="Keep history")
    reading = SensorReading(
        timestamp=now, sensor_id=sensor.id, kind="bioelectric", value=123, unit="mV"
    )
    wav = WavFile(
        gateway_id=gw.id,
        sensor_id=sensor.id,
        sensor_mac=sensor.mac_address,
        s3_key="synthetic/original.wav",
        content_sha256="a" * 64,
        duration_seconds=10,
        file_size_bytes=7644,
        started_at=now - timedelta(seconds=10),
        ended_at=now,
    )
    log = IngestLog(gateway_id=gw.id, raw_file_reference="synthetic/original.wav")
    code = PairingCode(
        code="KEPT77",
        purpose="gateway",
        zone_id=gw.zone_id,
        used_at=now,
        created_by_user_id=db.query(User)
        .filter_by(organization_id=setup_test_data["org"].id, role=Role.ADMIN)
        .one()
        .id,
        gateway_id=gw.id,
        expires_at=now + timedelta(minutes=10),
    )
    db.add_all([assignment, reading, wav, log, code])
    db.commit()
    gateway_id, sensor_id, assignment_id = gw.id, sensor.id, assignment.id
    last_seen = sensor.last_seen
    path = f"/api/v1/gateways/{gateway_id}" + ("/remove" if method == "post" else "")
    response = getattr(client, method)(path, headers=browser(tenant_admin_token))
    assert response.status_code == 204
    db.expire_all()
    retained = db.get(Gateway, gateway_id)
    assert retained is not None, "Removal must not physically delete a gateway"
    assert retained.is_active is False and retained.api_key_hash is None
    assert db.get(Sensor, sensor_id).gateway_id == gateway_id
    assert db.get(Sensor, sensor_id).last_seen.replace(tzinfo=UTC) == last_seen.replace(tzinfo=UTC)
    assert db.get(PlantSensorAssignment, assignment_id).notes == "Keep history"
    assert db.get(PlantSensorAssignment, assignment_id).is_active is True
    assert db.query(SensorReading).filter_by(sensor_id=sensor_id).one().value == 123
    assert db.get(WavFile, wav.id).content_sha256 == "a" * 64
    assert db.get(WavFile, wav.id).raw_deleted_at is None
    assert db.get(IngestLog, log.measurement_id).raw_file_reference == "synthetic/original.wav"
    assert db.get(PairingCode, code.id).gateway_id == gateway_id
    headers = browser(tenant_admin_token)
    assert (
        client.get(f"/api/v1/sensors/{sensor_id}/data?range=1h", headers=headers).status_code == 200
    )
    recordings = client.get(f"/api/v1/wav/files?sensor_id={sensor_id}", headers=headers)
    assert recordings.status_code == 200
    assert any(row["id"] == str(wav.id) for row in recordings.json())
    assert getattr(client, method)(path, headers=headers).status_code == 204


def test_inactive_gateways_are_absent_from_active_lists_and_zone_counts(
    client, db, setup_test_data, tenant_admin_token
):
    gw = setup_test_data["gateway"]
    gw.is_active = False
    db.commit()
    headers = browser(tenant_admin_token)
    assert client.get("/api/v1/gateways", headers=headers).json() == []
    zones = client.get("/api/v1/zones", headers=headers).json()
    assert zones[0]["gateway_count"] == 0
    assert zones[0]["sensor_count"] == 1
    detail = client.get(f"/api/v1/zones/{gw.zone_id}", headers=headers).json()
    assert detail["gateway_count"] == 0


@pytest.mark.parametrize("age", [timedelta(seconds=1), timedelta(days=10)])
def test_removed_gateway_sensors_are_disconnected_even_with_recent_or_old_heartbeats(
    client, db, setup_test_data, tenant_admin_token, age
):
    gw, sensor = setup_test_data["gateway"], setup_test_data["sensor"]
    gw.is_active = False
    sensor.status = "online"
    sensor.last_seen = datetime.now(UTC) - age
    db.commit()
    headers = browser(tenant_admin_token)
    response = client.get("/api/v1/sensors", headers=headers)
    assert response.status_code == 200
    assert response.json()[0]["status"] == "disconnected"
    update = client.patch(
        f"/api/v1/sensors/{sensor.id}", json={"name": "Kept sensor"}, headers=headers
    )
    assert update.status_code == 200 and update.json()["status"] == "disconnected"


@pytest.mark.parametrize("key_kind", ["modern", "digest", "legacy"])
def test_revocation_rejects_device_routes_and_cached_credentials(
    client, db, setup_test_data, tenant_admin_token, key_kind
):
    gw = setup_test_data["gateway"]
    key = generate_gateway_api_key(gw.id) if key_kind == "modern" else "synthetic-gateway-only"
    if key_kind == "legacy":
        gw.api_key_hash = get_password_hash(key)
    else:
        set_gateway_api_key(gw, key) if key_kind == "modern" else None
        if key_kind == "digest":
            from app.gateway_auth import gateway_key_digest

            gw.api_key_hash = gateway_key_digest(key)
    db.commit()
    device_headers = {"X-Api-Key": key, "X-Gateway-Id": str(gw.id)}
    payload = {"hardware_id": gw.hardware_id}
    assert (
        client.post("/api/v1/gateways/heartbeat", headers=device_headers, json=payload).status_code
        == 200
    )
    gateway_id, hardware_id = gw.id, gw.hardware_id
    assert (
        client.delete(
            f"/api/v1/gateways/{gateway_id}", headers=browser(tenant_admin_token)
        ).status_code
        == 204
    )
    probes = [
        client.post("/api/v1/gateways/heartbeat", headers=device_headers, json=payload),
        client.get(f"/api/v1/gateways/{gateway_id}/commands", headers=device_headers),
        client.get("/api/v1/gateway/desired-state", headers=device_headers),
        client.post(
            "/api/v1/gateway/state-report",
            headers=device_headers,
            json={"gateway_id": str(gateway_id)},
        ),
        client.post(
            "/api/v1/sensors/claim",
            headers=device_headers,
            json={"mac_address": "AA:BB:CC:00:00:44"},
        ),
        client.post(
            "/api/v1/ingest",
            headers=device_headers,
            json={"measurement_id": str(uuid4()), "gateway_serial": hardware_id, "readings": []},
        ),
        client.get("/api/v1/firmware/sync", headers=device_headers),
        client.get("/api/v1/gateway/app-release/1.0.0/download", headers=device_headers),
        client.post(
            "/api/v1/wav/upload",
            headers=device_headers,
            data={
                "sensor_mac": "AA:BB:CC:DD:EE:FF",
                "gateway_serial": hardware_id,
                "started_at": "2026-11-01T00:00:00Z",
                "ended_at": "2026-11-01T00:00:10Z",
            },
            files={"file": ("synthetic.wav", b"not-a-real-recording", "audio/wav")},
        ),
    ]
    assert all(response.status_code in {401, 403} for response in probes), [
        r.status_code for r in probes
    ]
    assert db.get(Gateway, gateway_id).is_active is False


def test_removal_enforces_tenant_role_and_authentication(
    client, db, setup_test_data, tenant_admin_token, admin_token
):
    gw = setup_test_data["gateway"]
    path = f"/api/v1/gateways/{gw.id}"
    client.cookies.clear()
    assert client.delete(path).status_code == 401
    assert client.delete(path, headers=browser(admin_token)).status_code == 404
    user = (
        db.query(User).filter_by(organization_id=setup_test_data["org"].id, role=Role.ADMIN).one()
    )
    user.role = Role.MEMBER
    db.commit()
    assert client.delete(path, headers=browser(tenant_admin_token)).status_code == 403
    assert db.get(Gateway, gw.id).is_active is True
    assert (
        client.delete(f"/api/v1/gateways/{uuid4()}", headers=browser(admin_token)).status_code
        == 404
    )


def test_failed_transaction_does_not_revoke_or_drop_pending_commands(
    db, setup_test_data, tenant_admin_token
):
    gw = setup_test_data["gateway"]
    user = (
        db.query(User).filter_by(organization_id=setup_test_data["org"].id, role=Role.ADMIN).one()
    )
    gateway_id, credential = gw.id, gw.api_key_hash
    gateway_commands_cache[str(gateway_id)] = [{"action": "synthetic"}]
    try:
        with patch.object(db, "commit", side_effect=RuntimeError("synthetic commit failed")):
            with pytest.raises(RuntimeError):
                delete_gateway(db, user, gateway_id)
        db.rollback()
        assert db.get(Gateway, gateway_id).is_active is True
        assert db.get(Gateway, gateway_id).api_key_hash == credential
        assert gateway_commands_cache[str(gateway_id)]
    finally:
        gateway_commands_cache.pop(str(gateway_id), None)


def test_safe_removal_checks_cookie_origin(client, db, setup_test_data, tenant_admin_token):
    gateway_id = setup_test_data["gateway"].id
    client.cookies.set("access_token", tenant_admin_token)
    response = client.post(
        f"/api/v1/gateways/{gateway_id}/remove", headers={"Origin": "https://attacker.invalid"}
    )
    assert response.status_code == 403
    assert db.get(Gateway, gateway_id).is_active is True
    accepted = client.post(
        f"/api/v1/gateways/{gateway_id}/remove",
        headers={"Origin": settings.frontend_url.rstrip("/")},
    )
    assert accepted.status_code == 204


def test_cannot_move_a_sensor_to_a_removed_gateway(client, db, setup_test_data, tenant_admin_token):
    original = setup_test_data["gateway"]
    target = Gateway(zone_id=original.zone_id, hardware_id="removed-target", is_active=False)
    db.add(target)
    db.commit()
    response = client.patch(
        f"/api/v1/sensors/{setup_test_data['sensor'].id}/move",
        json={"target_gateway_id": str(target.id)},
        headers=browser(tenant_admin_token),
    )
    assert response.status_code == 400
    assert db.get(Sensor, setup_test_data["sensor"].id).gateway_id == original.id


def test_revoked_hardware_cannot_silently_register_again(
    client, db, setup_test_data, tenant_admin_token
):
    gateway = setup_test_data["gateway"]
    identity = gateway.id
    hardware = gateway.hardware_id
    headers = browser(tenant_admin_token)
    assert client.post(f"/api/v1/gateways/{identity}/remove", headers=headers).status_code == 204
    code = client.post(
        "/api/v1/gateways/pairing-code", headers=headers, json={"zone_id": str(gateway.zone_id)}
    )
    assert code.status_code == 201
    result = client.post(
        "/api/v1/gateways/register",
        headers={"X-Api-Key": "ci-api-key"},
        json={"code": code.json()["code"], "hardware_id": hardware},
    )
    assert result.status_code == 403
    assert db.query(Gateway).filter_by(hardware_id=hardware).one().id == identity
    assert db.get(Gateway, identity).is_active is False
