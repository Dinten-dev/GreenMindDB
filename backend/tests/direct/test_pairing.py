import hashlib
import time
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.direct import pairing
from app.direct.assembler import assemble_one
from app.direct.auth import issue_token
from app.direct.database import transaction
from app.direct.models import Chunk, Device, Enrollment, Pairing, Piece, Segment

PREFIX = "/api/v1/direct-ingest"
ORIGIN = "https://test.green-mind.ch"


@pytest.fixture
def setup(pipeline, monkeypatch):
    p = pipeline
    p.cfg.dashboard_api_url = "http://backend:8000/api/v1"
    p.cfg.dashboard_origin = ORIGIN
    p.org, p.zone = str(uuid.uuid4()), str(uuid.uuid4())
    p.allowed_zones = [p.zone]

    async def identity(request, cfg, zone_id=None):
        if request.headers.get("authorization") != "Bearer dashboard-session":
            raise pairing.DirectError(401, "dashboard_login_required")
        if zone_id is not None and str(zone_id) != p.zone:
            raise pairing.DirectError(404, "zone_not_found")
        return {"organization_id": p.org, "allowed_zone_ids": p.allowed_zones}

    monkeypatch.setattr(pairing, "dashboard_identity", identity)
    p.headers = {"Origin": ORIGIN, "Authorization": "Bearer dashboard-session"}
    return p


def code(p):
    response = p.client.post(PREFIX + "/pairing-code", headers=p.headers, json={"zone_id": p.zone})
    assert response.status_code == 201, response.text
    return response.json()["code"]


def registration(p, value=None):
    device_id = str(uuid.uuid4())
    token, _ = issue_token(device_id)
    return {
        "code": value or code(p),
        "device_id": device_id,
        "token": token,
        "hardware_id": "14:c1:9f:d9:42:9c",
    }


def test_pairing_disabled_by_default(pipeline):
    assert pipeline.client.get(PREFIX + "/setup").status_code == 503


def test_dashboard_authorization_origin_zone(setup):
    p = setup
    assert p.client.post(PREFIX + "/pairing-code", json={"zone_id": p.zone}).status_code == 403
    assert (
        p.client.post(
            PREFIX + "/pairing-code", headers={"Origin": ORIGIN}, json={"zone_id": p.zone}
        ).status_code
        == 401
    )
    assert (
        p.client.post(
            PREFIX + "/pairing-code", headers=p.headers, json={"zone_id": str(uuid.uuid4())}
        ).status_code
        == 404
    )
    with transaction(p.engine) as db:
        assert db.query(Pairing).count() == 0


def test_retry_and_upload(setup):
    p = setup
    body = registration(p)
    for _ in range(2):
        response = p.client.post(PREFIX + "/register", json=body)
        assert response.status_code == 201, response.text
    with transaction(p.engine) as db:
        device = db.get(Device, body["device_id"])
        assert device.organization_id == p.org and device.zone_id == p.zone
        assert device.mode == "DIRECT" and device.legacy_sensor_id is None
        assert device.key_hash == hashlib.sha256(body["token"].encode()).hexdigest()
        assert db.query(Enrollment).count() == 1
        assert db.query(Pairing).one().code_hash != body["code"]
    payload = bytes(12)
    result = p.upload(
        payload, p.metadata(payload, device_id=body["device_id"]), token_override=body["token"]
    )
    assert result.status_code == 201
    visible = p.client.get(PREFIX + "/devices", headers=p.headers).json()
    assert len(visible) == 1 and visible[0]["status"] == "online"
    assert visible[0]["last_seen"] and "token" not in visible[0]


def test_used_code_cannot_be_stolen(setup):
    p = setup
    body = registration(p)
    assert p.client.post(PREFIX + "/register", json=body).status_code == 201
    attacker = registration(p, body["code"])
    attacker["hardware_id"] = "11:22:33:44:55:66"
    assert p.client.post(PREFIX + "/register", json=attacker).status_code == 409
    changed = dict(body, token=issue_token(body["device_id"])[0])
    assert p.client.post(PREFIX + "/register", json=changed).status_code == 409


def test_reconfigure_requires_same_ownership(setup):
    p = setup
    body = registration(p)
    assert p.client.post(PREFIX + "/register", json=body).status_code == 201
    body["code"] = code(p)
    assert p.client.post(PREFIX + "/register", json=body).status_code == 201
    p.org = str(uuid.uuid4())
    body["code"] = code(p)
    assert p.client.post(PREFIX + "/register", json=body).status_code == 409


def test_expiry_and_size_limit(setup):
    p = setup
    body = registration(p)
    with transaction(p.engine) as db:
        db.query(Pairing).one().expires_at = time.time() - 1
    assert p.client.post(PREFIX + "/register", json=body).status_code == 400
    assert p.client.post(PREFIX + "/register", content=b"x" * 1025).status_code == 413
    with transaction(p.engine) as db:
        assert db.query(Enrollment).count() == 0


def test_rate_limit(setup):
    p = setup
    for _ in range(10):
        assert p.client.post(PREFIX + "/register", json={}).status_code == 422
    assert p.client.post(PREFIX + "/register", json={}).status_code == 429


def test_zone_revocation_hides_device_without_interrupting_reception(setup):
    p = setup
    body = registration(p)
    assert p.client.post(PREFIX + "/register", json=body).status_code == 201
    assert len(p.client.get(PREFIX + "/devices", headers=p.headers).json()) == 1
    p.allowed_zones = []
    assert p.client.get(PREFIX + "/devices", headers=p.headers).json() == []
    payload = bytes(12)
    result = p.upload(
        payload, p.metadata(payload, device_id=body["device_id"]), token_override=body["token"]
    )
    assert result.status_code == 201


def test_device_list_preserves_received_time_without_chunk_history_scan(setup):
    p = setup
    with transaction(p.engine) as db:
        device = db.get(Device, p.device_id)
        device.organization_id, device.zone_id = p.org, p.zone
    payload = bytes(12)
    assert p.upload(payload).status_code == 201
    with transaction(p.engine) as db:
        stamp = db.query(Chunk).one().received_at
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(p.engine, "before_cursor_execute", capture)
    try:
        response = p.client.get(PREFIX + "/devices", headers=p.headers)
    finally:
        event.remove(p.engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    row = response.json()[0]
    assert row["last_seen"] == datetime.fromtimestamp(stamp, UTC).isoformat()
    assert row["status"] == "online" and row["spool_bytes"] == len(payload)
    assert all("direct_chunk" not in statement for statement in statements)


def test_duplicate_conflict_and_further_acceptance_keep_exact_status(setup, monkeypatch):
    p = setup
    with transaction(p.engine) as db:
        device = db.get(Device, p.device_id)
        device.organization_id, device.zone_id = p.org, p.zone
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    payload = bytes(12)
    assert p.upload(payload).status_code == 201
    first = p.client.get(PREFIX + "/devices", headers=p.headers).json()[0]["last_seen"]
    monkeypatch.setattr(time, "time", lambda: now + 10)
    assert p.upload(payload).json()["status"] == "duplicate"
    assert p.upload(bytes([1]) * 12).status_code == 409
    assert p.client.get(PREFIX + "/devices", headers=p.headers).json()[0]["last_seen"] == first
    assert p.upload(payload, p.metadata(payload, sequence=1, first_frame=1)).status_code == 201
    row = p.client.get(PREFIX + "/devices", headers=p.headers).json()[0]
    assert row["last_seen"] == datetime.fromtimestamp(now + 10, UTC).isoformat()
    assert row["spool_bytes"] == 24


def test_offline_device_retains_original_time_when_segment_metadata_is_absent(setup):
    p = setup
    with transaction(p.engine) as db:
        device = db.get(Device, p.device_id)
        device.organization_id, device.zone_id = p.org, p.zone
    assert p.upload(bytes(12)).status_code == 201
    with transaction(p.engine) as db:
        stamp = time.time() - 100
        db.query(Chunk).one().received_at = stamp
        db.query(Piece).delete()
        db.query(Segment).delete()
    row = p.client.get(PREFIX + "/devices", headers=p.headers).json()[0]
    assert row["status"] == "offline"
    assert row["last_seen"] == datetime.fromtimestamp(stamp, UTC).isoformat()
