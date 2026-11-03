"""Leases, memory bounds, authenticated access, replay and raw independence."""

import copy
import time
import uuid

import pytest
from sqlalchemy import event

from app.direct import pairing
from app.direct.database import transaction
from app.direct.live_state import LiveStore
from app.direct.models import Device
from app.direct.protocol import DirectError
from tests.direct.test_raw400_profile import raw_meta

PREFIX = "/api/v1/direct-ingest/devices/"
ORIGIN = "https://test.green-mind.ch"
PRINCIPAL = "a" * 64


def prepare_live(p, monkeypatch):
    p.cfg.live_socket = "/private/tmp/synthetic-live.sock"
    p.cfg.live_enabled = True
    p.cfg.dashboard_api_url = "http://backend:8000/api/v1"
    p.cfg.dashboard_origin = ORIGIN
    p.viewer_active = True
    p.user_id = str(uuid.uuid4())
    with transaction(p.engine) as db:
        row = db.get(Device, p.device_id)
        p.org, p.zone = row.organization_id, row.zone_id
    p.allowed_zones = [p.zone]
    p.headers = {"Origin": ORIGIN, "Authorization": "Bearer synthetic-viewer"}
    p.device_headers = {"Authorization": "Bearer " + p.token}
    p.base = PREFIX + p.device_id
    p.lease = str(uuid.uuid4())
    p.ram = LiveStore()
    payload = bytes(800)
    # Preview binds to an already accepted raw session, not an arbitrary upload clock.
    p.start_us = int((time.time() - 1) * 1e6)
    meta = raw_meta(p, payload)
    assert p.upload(payload, meta).status_code == 201
    p.spec = dict(
        session_id=p.session_id,
        session_start_us=p.start_us,
        source_rate=400,
        raw_rate=400,
        preview_profile="raw400-box32-v1",
        firmware_version="direct-test-v1",
    )

    async def identity(request, cfg, zone_id=None):
        if request.headers.get("authorization") != "Bearer synthetic-viewer" or not p.viewer_active:
            raise DirectError(401, "dashboard_login_required")
        return {"id": p.user_id, "organization_id": p.org, "allowed_zone_ids": p.allowed_zones}

    async def dispatch(body):
        return p.ram.dispatch(body)

    monkeypatch.setattr(pairing, "dashboard_identity", identity)
    p.app.state.live_dispatch = dispatch
    return p


@pytest.fixture
def live(pipeline, monkeypatch):
    return prepare_live(pipeline, monkeypatch)


def lease(p, value=None):
    return p.client.put(p.base + "/live-leases/" + (value or p.lease), headers=p.headers, json={})


def control(p):
    return p.client.get(p.base + "/control", headers=p.device_headers, params=p.spec)


def read(p, **params):
    return p.client.get(
        p.base + "/live-preview", headers=p.headers, params={"lease_id": p.lease, **params}
    )


def packet(p, generation, sequence=0, frame=383):
    end = p.start_us + (frame + 1) * 2500
    return {
        "generation": generation,
        "session_id": p.session_id,
        "sequence": sequence,
        "points": [{"frame": frame, "samples": 32, "mv": 1650.0}],
        "rail": {"start_us": end - 800000, "end_us": end, "raw_samples": 320, "rail_samples": 256},
    }


def test_live_is_disabled_by_default_and_cannot_affect_raw_ingest(pipeline):
    p = pipeline
    assert p.client.get(PREFIX + p.device_id + "/control").status_code == 503
    assert p.upload(bytes(1200)).status_code == 201


def test_preview_does_not_write_sql_and_preserves_raw_samples(live):
    p = live
    statements = []

    def record(_, __, statement, *args):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(p.engine, "before_cursor_execute", record)
    try:
        assert lease(p).status_code == 200
        c = control(p)
        assert c.status_code == 200 and c.json()["enabled"]
        body = packet(p, c.json()["generation"])
        assert (
            p.client.post(p.base + "/live-preview", headers=p.device_headers, json=body).status_code
            == 200
        )
        assert (
            p.client.post(p.base + "/live-preview", headers=p.device_headers, json=body).json()[
                "status"
            ]
            == "duplicate"
        )
        result = read(p)
        assert result.status_code == 200
        assert result.headers["Cache-Control"] == "private, no-store"
        view = result.json()
        assert view["rail"]["percent"] == 80
        assert view["rail"]["complete"] is False
        assert view["window_complete"] is False
        assert view["points"][0]["mv"] == 1650
        increment = read(p, after=view["cursor"], generation=view["generation"]).json()
        assert increment["points"] == [] and increment["rail"]["percent"] == 80
        mark = {"generation": view["generation"], "session_id": view["session_id"], "frame": 383}
        response = p.client.post(
            p.base + "/live-markers/" + str(uuid.uuid4()),
            params={"lease_id": p.lease},
            headers=p.headers,
            json=mark,
        )
        assert response.status_code == 200
        assert response.json()["at_us"] == p.start_us + 383 * 2500
        assert len(read(p).json()["markers"]) == 1
        assert not {"INSERT", "UPDATE", "DELETE", "ALTER"}.intersection(statements)
    finally:
        event.remove(p.engine, "before_cursor_execute", record)
    assert p.upload(bytes(800), raw_meta(p, bytes(800))).json()["status"] == "duplicate"


def test_origin_session_zone_device_and_lease_ownership_are_rechecked(live):
    p = live
    path = p.base + "/live-leases/" + p.lease
    assert (
        p.client.put(
            path, headers={"Authorization": "Bearer synthetic-viewer"}, json={}
        ).status_code
        == 403
    )
    assert p.client.put(path, headers={"Origin": ORIGIN}, json={}).status_code == 401
    assert lease(p).status_code == 200
    p.viewer_active = False
    assert read(p).status_code == 401
    p.viewer_active = True
    p.allowed_zones = []
    assert read(p).status_code == 404
    p.allowed_zones = [p.zone]
    p.user_id = str(uuid.uuid4())
    assert read(p).status_code == 404
    with transaction(p.engine) as db:
        db.get(Device, p.device_id).active = False
    assert control(p).status_code == 403
    assert read(p).status_code == 403


def test_wrong_device_profile_session_and_bounded_bodies_fail(live):
    p = live
    assert lease(p).status_code == 200
    original = copy.deepcopy(p.spec)
    p.spec["session_id"] = str(uuid.uuid4())
    assert control(p).status_code == 409
    p.spec = original | {"firmware_version": "wrong"}
    assert control(p).status_code == 409
    p.spec = original
    assert control(p).status_code == 200
    assert (
        p.client.post(
            p.base + "/live-preview", headers=p.device_headers, content=b"x" * 4097
        ).status_code
        == 413
    )
    assert (
        p.client.put(
            p.base + "/live-leases/" + p.lease, headers=p.headers, content=b"x" * 257
        ).status_code
        == 413
    )
    assert read(p, seconds=120).status_code == 422
    wrong = PREFIX + str(uuid.uuid4()) + "/control"
    assert p.client.get(wrong, headers=p.device_headers, params=p.spec).status_code == 403


def test_broker_failure_cannot_block_raw_ack_or_enable_retention(live):
    p = live

    async def broken(_):
        raise DirectError(503, "live_unavailable")

    p.app.state.live_dispatch = broken
    assert lease(p).status_code == 503
    assert p.upload(bytes(800), raw_meta(p, bytes(800))).json()["status"] == "duplicate"
    assert p.cfg.retention_enabled is False


def test_independent_viewers_last_release_expiry_and_no_hidden_collecting():
    now = [100.0]
    store = LiveStore(clock=lambda: now[0])
    d, a, b = [str(uuid.uuid4()) for _ in range(3)]
    spec = dict(
        session_id=str(uuid.uuid4()),
        session_start_us=1800000000000000,
        source_rate=400,
        raw_rate=400,
        preview_profile="raw400-box32-v1",
        firmware_version="test",
    )
    store.lease(d, a, PRINCIPAL)
    store.lease(d, b, "b" * 64)
    assert store.control(d, spec)["enabled"]
    store.release(d, a, PRINCIPAL)
    assert store.control(d, spec)["enabled"]
    now[0] += 31
    assert store.control(d, spec) == {"enabled": False, "expires_in_ms": 0}
    assert len(store.devices) == 0


def test_shared_store_between_two_http_workers_and_restart(live):
    from fastapi.testclient import TestClient

    from app.direct.main import create_app

    p = live
    other = create_app(p.cfg, p.engine, p.store)
    other.state.live_dispatch = p.app.state.live_dispatch
    with TestClient(other) as client:
        assert lease(p).status_code == 200
        result = client.get(p.base + "/control", params=p.spec, headers=p.device_headers)
        assert result.json()["enabled"] is True
        assert read(p).json()["capability"] == "ready"
    # Owner restart clears only volatile state; raw duplicate identity survives.
    p.ram.devices.clear()
    assert read(p).status_code == 404
    assert p.upload(bytes(800), raw_meta(p, bytes(800))).json()["status"] == "duplicate"


def test_bounds_and_cross_principal_cannot_displace_a_valid_lease():
    store = LiveStore(max_devices=1, max_leases=1)
    d, lease_id = str(uuid.uuid4()), str(uuid.uuid4())
    store.lease(d, lease_id, PRINCIPAL)
    for device, ident, principal in [
        (d, lease_id, "b" * 64),
        (d, str(uuid.uuid4()), PRINCIPAL),
        (str(uuid.uuid4()), str(uuid.uuid4()), PRINCIPAL),
    ]:
        with pytest.raises(DirectError):
            store.lease(device, ident, principal)
    assert store.devices[d].leases[lease_id][0] == PRINCIPAL


def test_sixteen_point_catchup_is_bounded_and_ack_carries_demand(live):
    p = live
    p.ram.wall = lambda: p.start_us / 1e6 + 1.4
    assert lease(p).status_code == 200
    generation = control(p).json()["generation"]
    body = packet(p, generation, frame=511)
    body["points"] = [dict(frame=(i + 1) * 32 - 1, samples=32, mv=1650.0) for i in range(16)]
    body["rail"] = dict(
        start_us=p.start_us, end_us=p.start_us + 1280000, raw_samples=512, rail_samples=0
    )
    response = p.client.post(p.base + "/live-preview", headers=p.device_headers, json=body)
    assert response.status_code == 200
    assert response.json()["generation"] == generation
    assert 0 < response.json()["expires_in_ms"] <= 30000
    assert len(read(p).json()["points"]) == 16
    body["sequence"] = 1
    body["points"].append(dict(frame=543, samples=32, mv=1650.0))
    assert (
        p.client.post(p.base + "/live-preview", headers=p.device_headers, json=body).status_code
        == 422
    )
