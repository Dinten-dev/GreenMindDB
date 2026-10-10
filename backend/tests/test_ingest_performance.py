"""Receipt correctness and credential revocation under the optimized ingest path."""

import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import gateway_auth
from app.auth import create_access_token
from app.models.timeseries import SensorReading
from app.models.user import Role, User
from app.routers import ingest


@pytest.fixture(autouse=True)
def empty_auth_cache():
    gateway_auth.clear_gateway_auth_cache()
    yield
    gateway_auth.clear_gateway_auth_cache()


def test_legacy_cache_skips_bcrypt_but_rechecks_revocation(db, setup_test_data, monkeypatch):
    gateway = setup_test_data["gateway"]
    original = gateway_auth.verify_password
    checks = []

    def verify(key, value):
        checks.append(1)
        return original(key, value)

    monkeypatch.setattr(gateway_auth, "verify_password", verify)
    assert gateway_auth.authenticate_gateway_api_key(db, "ci-api-key").id == gateway.id
    count = len(checks)
    assert gateway_auth.authenticate_gateway_api_key(db, "ci-api-key").id == gateway.id
    assert len(checks) == count
    gateway.is_active = False
    db.commit()
    with pytest.raises(HTTPException) as rejected:
        gateway_auth.authenticate_gateway_api_key(db, "ci-api-key")
    assert rejected.value.status_code == 403
    gateway.is_active = True
    gateway.api_key_hash = gateway_auth.get_password_hash("changed-key")
    db.commit()
    with pytest.raises(HTTPException) as rejected:
        gateway_auth.authenticate_gateway_api_key(db, "ci-api-key")
    assert rejected.value.status_code == 401
    assert gateway_auth.authenticate_gateway_api_key(db, "changed-key").id == gateway.id


def test_cache_expires_and_never_caches_failed_keys(db, setup_test_data, monkeypatch):
    monkeypatch.setattr(gateway_auth, "_CACHE_SECONDS", -1)
    gateway_auth.authenticate_gateway_api_key(db, "ci-api-key")
    checked = []
    original = gateway_auth.verify_password
    monkeypatch.setattr(
        gateway_auth, "verify_password", lambda k, h: checked.append(k) or original(k, h)
    )
    gateway_auth.authenticate_gateway_api_key(db, "ci-api-key")
    assert checked
    before = len(gateway_auth._verified_keys)
    with pytest.raises(HTTPException):
        gateway_auth.authenticate_gateway_api_key(db, "wrong-key")
    assert len(gateway_auth._verified_keys) <= before


def test_cache_is_bounded(db, setup_test_data, monkeypatch):
    monkeypatch.setattr(gateway_auth, "_CACHE_LIMIT", 2)
    for i in range(5):
        gateway_auth._remember_verified(db, f"random-{i}", setup_test_data["gateway"])
    assert len(gateway_auth._verified_keys) == 2
    assert all(isinstance(key[1], bytes) for key in gateway_auth._verified_keys)


def test_persistence_worker_ack_and_idempotency(client, db, setup_test_data, monkeypatch):
    owner = threading.get_ident()
    threads = []
    original = ingest.process_ingestion

    def process(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(ingest, "process_ingestion", process)
    payload = {
        "gateway_serial": setup_test_data["gateway"].hardware_id,
        "measurement_id": str(uuid4()),
        "readings": [
            {
                "sensor_mac": setup_test_data["sensor"].mac_address,
                "sensor_kind": "bio_signal",
                "value": 1650,
                "unit": "mV",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ],
    }
    first = client.post("/api/v1/ingest", json=payload, headers={"X-Api-Key": "ci-api-key"})
    second = client.post("/api/v1/ingest", json=payload, headers={"X-Api-Key": "ci-api-key"})
    assert first.status_code == second.status_code == 201
    assert first.json()["status"] == "success"
    assert second.json()["status"] == "duplicate"
    assert db.query(SensorReading).count() == 1
    assert threads and all(value != owner for value in threads)


def test_persist_failure_never_returns_success(client, setup_test_data, monkeypatch):
    def fail(*args, **kwargs):
        raise HTTPException(503, "test storage unavailable")

    monkeypatch.setattr(ingest, "process_ingestion", fail)
    response = client.post(
        "/api/v1/ingest",
        headers={"X-Api-Key": "ci-api-key"},
        json={
            "gateway_serial": setup_test_data["gateway"].hardware_id,
            "measurement_id": str(uuid4()),
            "readings": [
                {
                    "sensor_mac": setup_test_data["sensor"].mac_address,
                    "sensor_kind": "bio_signal",
                    "value": 1650,
                    "unit": "mV",
                }
            ],
        },
    )
    assert response.status_code == 503


def test_committed_upload_reaches_authorized_live_subscriber(client, db, setup_test_data):
    viewer = User(
        email="live-proof@example.invalid",
        password_hash="unused",
        organization_id=setup_test_data["org"].id,
        role=Role.ADMIN,
        is_active=True,
        is_verified=True,
    )
    db.add(viewer)
    db.commit()
    client.cookies.set("access_token", create_access_token({"sub": str(viewer.id)}))
    sensor = setup_test_data["sensor"]
    with client.websocket_connect(
        f"/api/v1/ws/sensor/{sensor.id}", headers={"origin": "http://localhost:3000"}
    ) as socket:
        response = client.post(
            "/api/v1/ingest",
            headers={"X-Api-Key": "ci-api-key"},
            json={
                "gateway_serial": setup_test_data["gateway"].hardware_id,
                "measurement_id": str(uuid4()),
                "readings": [
                    {
                        "sensor_mac": sensor.mac_address,
                        "sensor_kind": "bio_signal",
                        "value": 1650,
                        "unit": "mV",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                ],
            },
        )
        assert response.status_code == 201
        message = socket.receive_json()
        assert message["event"] == "live_reading"
        assert message["sensor_id"] == str(sensor.id)
        assert message["readings"][0]["value"] == 1650
        assert db.query(SensorReading).count() == 1


def test_maximum_batch_still_acknowledges_all_samples(client, db, setup_test_data):
    start = datetime.now(UTC) - timedelta(hours=1)
    readings = [
        {
            "sensor_mac": setup_test_data["sensor"].mac_address,
            "sensor_kind": "bio_signal",
            "value": 1650,
            "unit": "mV",
            "timestamp": (start + timedelta(microseconds=i * 2632)).isoformat(),
        }
        for i in range(5000)
    ]
    response = client.post(
        "/api/v1/ingest",
        headers={"X-Api-Key": "ci-api-key"},
        json={
            "gateway_serial": setup_test_data["gateway"].hardware_id,
            "measurement_id": str(uuid4()),
            "readings": readings,
        },
    )
    assert response.status_code == 201 and response.json()["ingested"] == 5000
    assert db.query(SensorReading).count() == 5000


def test_late_sql_chunk_failure_rolls_back_the_whole_upload(
    client, db, setup_test_data, monkeypatch
):
    from sqlalchemy.orm import Session

    from app.models.ingest_log import IngestLog

    original = Session.execute
    calls = []

    def execute(session, statement, *args, **kwargs):
        if getattr(statement, "is_insert", False) and statement.table.name == "sensor_reading":
            calls.append(1)
            if len(calls) == 2:
                raise HTTPException(503, "local rollback proof")
        return original(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", execute)
    start = datetime.now(UTC) - timedelta(hours=1)
    response = client.post(
        "/api/v1/ingest",
        headers={"X-Api-Key": "ci-api-key"},
        json={
            "gateway_serial": setup_test_data["gateway"].hardware_id,
            "measurement_id": str(uuid4()),
            "readings": [
                {
                    "sensor_mac": setup_test_data["sensor"].mac_address,
                    "sensor_kind": "bio_signal",
                    "value": 1650,
                    "unit": "mV",
                    "timestamp": (start + timedelta(microseconds=i * 2632)).isoformat(),
                }
                for i in range(600)
            ],
        },
    )
    assert response.status_code == 503 and len(calls) == 2
    assert db.query(SensorReading).count() == db.query(IngestLog).count() == 0
