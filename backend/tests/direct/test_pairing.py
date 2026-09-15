import hashlib
import time
import uuid

import pytest

from app.direct import pairing
from app.direct.auth import issue_token
from app.direct.database import transaction
from app.direct.models import Device, Enrollment, Pairing

PREFIX = "/api/v1/direct-ingest"
ORIGIN = "https://test.green-mind.ch"


@pytest.fixture
def setup(pipeline, monkeypatch):
    p = pipeline
    p.cfg.dashboard_api_url = "http://backend:8000/api/v1"
    p.cfg.dashboard_origin = ORIGIN
    p.org, p.zone = str(uuid.uuid4()), str(uuid.uuid4())

    async def identity(request, cfg, zone_id=None):
        if request.headers.get("authorization") != "Bearer dashboard-session":
            raise pairing.DirectError(401, "dashboard_login_required")
        if zone_id is not None and str(zone_id) != p.zone:
            raise pairing.DirectError(404, "zone_not_found")
        return {"organization_id": p.org}

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
