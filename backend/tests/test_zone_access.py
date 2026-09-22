"""Zone restrictions must hold for lists, guessed URLs, exports and live data."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import create_access_token, get_current_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.master import Gateway, Sensor, Zone
from app.models.user import Organization, Role, User
from app.models.zone_access import ZoneAccess
from app.routers.ws import ConnectionManager
from app.visualization import api as visual
from app.zone_access import allowed_zone_ids


@pytest.fixture
def access_data(db, setup_test_data):
    org = setup_test_data["org"]
    member = User(
        email="zone-member@example.com",
        password_hash="unused",
        role=Role.MEMBER,
        organization_id=org.id,
        is_active=True,
        is_verified=True,
    )
    db.add(member)
    db.flush()
    zones = [setup_test_data["zone"]]
    sensors = [setup_test_data["sensor"]]
    for name in ("Peter Büro", "Rudi Meier"):
        zone = Zone(name=name, organization_id=org.id)
        db.add(zone)
        db.flush()
        gateway = Gateway(zone_id=zone.id, hardware_id=str(uuid4()), name=name)
        db.add(gateway)
        db.flush()
        sensor = Sensor(
            gateway_id=gateway.id,
            name=name,
            mac_address=str(uuid4())[:17],
            last_seen=datetime.now(UTC),
        )
        db.add(sensor)
        db.flush()
        zones.append(zone)
        sensors.append(sensor)
    for zone in zones[1:]:
        db.add(ZoneAccess(user_id=member.id, zone_id=zone.id))
    db.commit()
    token = create_access_token({"sub": str(member.id)})
    return member, zones, sensors, {"Authorization": "Bearer " + token}


def test_multiple_zone_grants_filter_lists_and_guessed_urls(client, db, access_data):
    member, zones, sensors, headers = access_data
    visible = client.get("/api/v1/zones", headers=headers)
    assert {row["id"] for row in visible.json()} == {str(z.id) for z in zones[1:]}
    rows = client.get("/api/v1/sensors", headers=headers).json()
    assert {row["id"] for row in rows} == {str(s.id) for s in sensors[1:]}
    assert {row["zone_id"] for row in client.get("/api/v1/gateways", headers=headers).json()} == {
        str(z.id) for z in zones[1:]
    }
    assert client.get(f"/api/v1/zones/{zones[0].id}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/zones/{zones[0].id}/overview", headers=headers).status_code == 404
    assert client.get(f"/api/v1/sensors?zone_id={zones[0].id}", headers=headers).json() == []
    db.query(ZoneAccess).filter_by(user_id=member.id).delete()
    db.commit()
    assert client.get("/api/v1/zones", headers=headers).json() == []
    assert client.get("/api/v1/sensors", headers=headers).json() == []


@pytest.mark.parametrize("suffix", ["data", "export"])
def test_legacy_sensor_urls_deny_ungranted_zone(client, access_data, suffix):
    _, _, sensors, headers = access_data
    assert (
        client.get(f"/api/v1/sensors/{sensors[0].id}/{suffix}", headers=headers).status_code == 404
    )


def test_admin_assigns_two_zones_and_cannot_cross_tenants(
    client, db, access_data, tenant_admin_token
):
    member, zones, _, headers = access_data
    route = f"/api/v1/organizations/members/{member.id}/zones"
    assert (
        client.put(route, headers=headers, json={"zone_ids": [str(zones[0].id)]}).status_code == 403
    )
    admin = {"Authorization": "Bearer " + tenant_admin_token}
    response = client.put(route, headers=admin, json={"zone_ids": [str(z.id) for z in zones[:2]]})
    assert response.status_code == 200
    assert set(allowed_zone_ids(db, member)) == {z.id for z in zones[:2]}
    foreign = Organization(name="Other")
    db.add(foreign)
    db.flush()
    zone = Zone(name="Foreign", organization_id=foreign.id)
    db.add(zone)
    db.commit()
    assert client.put(route, headers=admin, json={"zone_ids": [str(zone.id)]}).status_code == 404
    assert set(allowed_zone_ids(db, member)) == {z.id for z in zones[:2]}
    assert db.query(AuditLog).filter_by(action="zone_access.update").count() == 1
    assert client.get("/api/v1/organizations/members", headers=headers).status_code == 403
    assert client.put(route, headers=admin, json={"zone_ids": []}).status_code == 200
    assert allowed_zone_ids(db, member) == []


def test_read_sidecar_authorizes_same_zone_grants(db, access_data):
    member, _, sensors, _ = access_data
    visual.authorize(db, member, sensors[1].id)
    with pytest.raises(HTTPException) as error:
        visual.authorize(db, member, sensors[0].id)
    assert error.value.status_code == 404
    previous = dict(visual.app.dependency_overrides)
    visual.app.dependency_overrides[get_db] = lambda: db
    visual.app.dependency_overrides[get_current_user] = lambda: member
    try:
        with TestClient(visual.app) as client:
            for path in (
                f"/api/v1/sensors/{sensors[0].id}/data",
                f"/api/v1/sensors/{sensors[0].id}/export",
                f"/api/v1/visualization/sensors/{sensors[0].id}/waveform?at=2026-09-21T12:00:00Z",
            ):
                assert client.get(path).status_code == 404
    finally:
        visual.app.dependency_overrides.clear()
        visual.app.dependency_overrides.update(previous)


def test_existing_live_subscription_rechecks_permission_before_send():
    class Socket:
        def __init__(self):
            self.messages = []
            self.closed = False

        async def accept(self):
            pass

        async def send_json(self, message):
            self.messages.append(message)

        async def close(self, **kwargs):
            self.closed = True

    async def scenario():
        manager = ConnectionManager()
        socket = Socket()
        await manager.connect_sensor(socket, "sensor", "user", "ip")
        permitted = True
        manager.authorization_checks[socket] = lambda: permitted
        await manager.broadcast_to_sensor({"value": 1}, "sensor")
        permitted = False
        await manager.broadcast_to_sensor({"value": 2}, "sensor")
        assert socket.messages == [{"value": 1}] and socket.closed
        assert manager.total_connections == 0

    asyncio.run(scenario())


def test_wav_lists_counts_and_download_do_not_leak_other_zones(client, db, access_data):
    from datetime import timedelta

    from app.models.wav_file import WavFile

    _, _, sensors, headers = access_data
    start = datetime.now(UTC)
    recording = WavFile(
        id=uuid4(),
        sensor_id=sensors[0].id,
        gateway_id=sensors[0].gateway_id,
        sensor_mac=sensors[0].mac_address,
        s3_key="test/private-zone.wav",
        sample_rate=380,
        duration_seconds=60,
        file_size_bytes=45644,
        started_at=start,
        ended_at=start + timedelta(seconds=60),
    )
    db.add(recording)
    db.commit()
    assert client.get("/api/v1/wav/files", headers=headers).json() == []
    assert (
        client.get(f"/api/v1/wav/count?sensor_id={sensors[0].id}", headers=headers).json()["count"]
        == 0
    )
    assert client.get("/api/v1/wav/features", headers=headers).json() == []
    assert client.get(f"/api/v1/wav/download/{recording.id}", headers=headers).status_code == 404


def test_direct_read_authorization_uses_legacy_zone_grants(db, access_data):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.visualization.direct_api import authorize

    member, zones, _, _ = access_data
    engine = create_engine("sqlite://")
    device = str(uuid4())
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE direct_device(id text,organization_id text,zone_id text,mode text)")
        )
        conn.execute(
            text("INSERT INTO direct_device VALUES (:id,:org,:zone,'DIRECT')"),
            {"id": device, "org": str(member.organization_id), "zone": str(zones[1].id)},
        )
    try:
        with Session(engine) as direct_db:
            assert authorize(direct_db, db, member, device)["id"] == device
            db.query(ZoneAccess).filter_by(user_id=member.id, zone_id=zones[1].id).delete()
            db.commit()
            with pytest.raises(HTTPException) as exc:
                authorize(direct_db, db, member, device)
            assert exc.value.status_code == 404
    finally:
        engine.dispose()


def test_plant_does_not_reveal_sensor_from_hidden_zone(db, access_data):
    from app.models.plant import Plant, PlantSensorAssignment
    from app.services.plant_service import get_plant, list_plants

    member, zones, sensors, _ = access_data
    plant = Plant(name="Office plant", organization_id=member.organization_id, zone_id=zones[1].id)
    db.add(plant)
    db.flush()
    db.add(PlantSensorAssignment(plant_id=plant.id, sensor_id=sensors[0].id))
    db.commit()
    assert get_plant(db, member, plant.id).current_sensor_id is None
    assert list_plants(db, member)[0].current_sensor_id is None


def test_live_guard_checks_current_grants_and_sensor_moves(db, access_data):
    from types import SimpleNamespace

    from app.auth import COOKIE_NAME
    from app.routers.ws import live_zone_guard, settings

    member, zones, sensors, _ = access_data
    origins = settings.cors_origins
    origin = origins if isinstance(origins, str) else origins[0]
    socket = SimpleNamespace(
        headers={"origin": origin},
        cookies={COOKIE_NAME: create_access_token({"sub": str(member.id)})},
    )
    guard = live_zone_guard(socket, db.get_bind(), zones[1].id, sensors[1].id)
    assert guard()
    sensors[1].gateway_id = sensors[0].gateway_id
    db.commit()
    assert not guard()
    zone_guard = live_zone_guard(socket, db.get_bind(), zones[1].id)
    assert zone_guard()
    db.query(ZoneAccess).filter_by(user_id=member.id, zone_id=zones[1].id).delete()
    db.commit()
    assert not zone_guard()


def test_gateway_upload_survives_revocation_and_alerts_respect_grants(db, access_data, monkeypatch):
    from app.schemas.ingest import IngestRequest
    from app.services import ingest_service
    from app.services.ingest_service import process_ingestion

    monkeypatch.setattr(ingest_service, "_last_alert_times", {})

    member, zones, sensors, _ = access_data
    member.phone_number = "+41760000001"
    db.commit()
    sensor = sensors[0]

    def upload():
        ingest_service._last_alert_times.pop(sensor.mac_address, None)
        payload = IngestRequest(
            measurement_id=uuid4(),
            gateway_serial=sensor.gateway.hardware_id,
            readings=[
                {
                    "sensor_mac": sensor.mac_address,
                    "sensor_kind": "bio_signal",
                    "value": 0,
                    "unit": "mV",
                }
            ],
        )
        return process_ingestion(payload, sensor.gateway, db)

    count, alerts = upload()
    assert count == 1 and alerts == []
    db.add(ZoneAccess(user_id=member.id, zone_id=zones[0].id))
    db.commit()
    count, alerts = upload()
    assert count == 1 and [a["phone_number"] for a in alerts] == [member.phone_number]
    db.query(ZoneAccess).filter_by(user_id=member.id).delete()
    db.commit()
    count, alerts = upload()
    assert count == 1 and alerts == []


def test_provisioning_cannot_target_ungranted_zone(db, access_data):
    from datetime import timedelta

    from app.models.pairing import PairingCode
    from app.routers.provisioning import create_provisioning_job
    from app.schemas.provisioning import ProvisioningJobCreate

    member, zones, _, _ = access_data
    db.add(
        PairingCode(
            code="ABC123",
            zone_id=zones[0].id,
            created_by_user_id=member.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    )
    db.commit()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            create_provisioning_job(
                ProvisioningJobCreate(ssid="Test", password="", pairing_code="ABC123"), db, member
            )
        )
    assert exc.value.status_code == 400
