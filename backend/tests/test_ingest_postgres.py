"""Real parallel receipt/cache checks on a disposable local PostgreSQL DB."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import gateway_auth
from app.database import get_db
from app.main import app
from app.models.ingest_log import IngestLog
from app.models.master import Gateway, Sensor, Zone
from app.models.timeseries import SensorReading
from app.models.user import Organization


@pytest.fixture
def pg_receipts():
    url = os.environ.get("DIRECT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Dedicated local PostgreSQL not configured")
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"} and parsed.database == "direct_test"
    engine = create_engine(url, pool_size=5, max_overflow=10)
    tables = [
        model.__table__ for model in (Organization, Zone, Gateway, Sensor, IngestLog, SensorReading)
    ]
    for table in tables:
        table.create(engine, checkfirst=True)
    org_id, zone_id, gateway_id, sensor_id = [uuid4() for _ in range(4)]
    with Session(engine) as db:
        db.add(Organization(id=org_id, name="Isolated test"))
        db.flush()
        db.add(Zone(id=zone_id, organization_id=org_id, name="Isolated test"))
        db.flush()
        db.add(
            Gateway(
                id=gateway_id,
                zone_id=zone_id,
                hardware_id="peter-test",
                api_key_hash=gateway_auth.get_password_hash("isolated-test-key"),
            )
        )
        db.flush()
        db.add(
            Sensor(
                id=sensor_id,
                gateway_id=gateway_id,
                mac_address="AA:BB:CC:DD:EE:FE",
                sms_alerts_enabled=False,
            )
        )
        db.commit()
    gateway_auth.clear_gateway_auth_cache()

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    yield engine
    app.dependency_overrides.pop(get_db, None)
    gateway_auth.clear_gateway_auth_cache()
    for table in reversed(tables):
        table.drop(engine)
    engine.dispose()


def test_parallel_cold_legacy_keys_share_one_verification(pg_receipts, monkeypatch):
    checked = []
    original = gateway_auth.verify_password

    def verify(key, hashed):
        checked.append(1)
        return original(key, hashed)

    monkeypatch.setattr(gateway_auth, "verify_password", verify)
    barrier = threading.Barrier(14)

    def authenticate(_):
        barrier.wait()
        with Session(pg_receipts) as db:
            return gateway_auth.authenticate_gateway_api_key(db, "isolated-test-key").hardware_id

    with ThreadPoolExecutor(max_workers=14) as pool:
        assert list(pool.map(authenticate, range(14))) == ["peter-test"] * 14
    assert len(checked) == 1


def test_concurrent_retries_ack_only_one_committed_receipt(pg_receipts):
    payload = {
        "gateway_serial": "peter-test",
        "measurement_id": str(uuid4()),
        "readings": [
            {
                "sensor_mac": "AA:BB:CC:DD:EE:FE",
                "sensor_kind": "bio_signal",
                "value": 1650,
                "unit": "mV",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ],
    }
    barrier = threading.Barrier(14)
    with TestClient(app) as client:

        def upload(_):
            barrier.wait()
            return client.post(
                "/api/v1/ingest", json=payload, headers={"X-Api-Key": "isolated-test-key"}
            )

        with ThreadPoolExecutor(max_workers=14) as pool:
            responses = list(pool.map(upload, range(14)))
    assert all(response.status_code == 201 for response in responses)
    assert sum(response.json()["status"] == "success" for response in responses) == 1
    with Session(pg_receipts) as db:
        assert db.query(SensorReading).count() == db.query(IngestLog).count() == 1
