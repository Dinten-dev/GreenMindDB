"""Exercise the real dashboard authorization bridge and Direct ownership writes."""

import uuid

import httpx
import pytest

from app.direct import pairing
from app.direct.auth import issue_token
from app.direct.database import transaction
from app.direct.models import Device, Pairing

PREFIX = "/api/v1/direct-ingest"
ORIGIN = "https://test.green-mind.ch"


@pytest.fixture
def dashboard(pipeline, monkeypatch):
    p = pipeline
    p.cfg.dashboard_api_url = "http://backend:8000/api/v1"
    p.cfg.dashboard_origin = ORIGIN
    p.org, p.foreign_org = str(uuid.uuid4()), str(uuid.uuid4())
    p.zone, p.hidden_zone = str(uuid.uuid4()), str(uuid.uuid4())
    p.identity = {"organization_id": p.org, "role": "member", "is_active": True}
    p.capabilities = {"can_manage": True}
    p.zone_status = 200
    p.auth_status = 200
    p.capabilities_status = 200
    p.catalog_status = 200
    p.catalog = [{"id": p.zone, "organization_id": p.foreign_org}]
    p.allowed_zones = [p.zone]
    p.requests = []
    p.credential = {"authorization": "Bearer dashboard-session"}
    p.backend_timeout = False
    p.headers = {"Origin": ORIGIN, "Authorization": "Bearer dashboard-session"}

    def respond(request):
        assert request.url.host == "backend"
        for name in ("authorization", "cookie"):
            assert request.headers.get(name) == p.credential.get(name)
        if p.backend_timeout:
            raise httpx.ReadTimeout("dashboard timeout", request=request)
        p.requests.append(request.url.path)
        route = request.url.path.removeprefix("/api/v1")
        if route == "/auth/me":
            return httpx.Response(p.auth_status, json=p.identity)
        if route == "/administration/capabilities":
            return httpx.Response(p.capabilities_status, json=p.capabilities)
        if route == "/administration/catalog":
            return httpx.Response(p.catalog_status, json={"zones": p.catalog})
        if route == "/zones/" + p.zone:
            return httpx.Response(p.zone_status, json={"id": p.zone})
        if route == "/zones":
            return httpx.Response(200, json=[{"id": z} for z in p.allowed_zones])
        raise AssertionError(route)

    client = httpx.AsyncClient

    def backend_client(**kwargs):
        assert kwargs == {"timeout": 5, "follow_redirects": False, "trust_env": False}
        return client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(pairing.httpx, "AsyncClient", backend_client)
    return p


def pairing_code(p):
    return p.client.post(PREFIX + "/pairing-code", headers=p.headers, json={"zone_id": p.zone})


def test_platform_admin_pairing_uses_authorized_zone_company(dashboard):
    p = dashboard
    response = pairing_code(p)
    assert response.status_code == 201, response.text
    with transaction(p.engine) as db:
        saved = db.query(Pairing).one()
        assert saved.organization_id == p.foreign_org
        assert saved.zone_id == p.zone
    identity = str(uuid.uuid4())
    token, _ = issue_token(identity)
    result = p.client.post(
        PREFIX + "/register",
        json={
            "code": response.json()["code"],
            "device_id": identity,
            "token": token,
            "hardware_id": "11:22:33:44:55:66",
        },
    )
    assert result.status_code == 201, result.text
    with transaction(p.engine) as db:
        assert db.get(Device, identity).organization_id == p.foreign_org
    payload = bytes(12)
    assert (
        p.upload(payload, p.metadata(payload, device_id=identity), token_override=token).status_code
        == 201
    )


@pytest.mark.parametrize("capability", [False, "true", 1, None])
def test_member_cannot_claim_platform_admin(dashboard, capability):
    p = dashboard
    p.identity["platform_admin"] = True
    p.capabilities["can_manage"] = capability
    assert pairing_code(p).status_code == 403
    assert "/api/v1/administration/catalog" not in p.requests
    with transaction(p.engine) as db:
        assert db.query(Pairing).count() == 0


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_tenant_manager_preserves_existing_pairing(dashboard, role):
    p = dashboard
    p.identity["role"] = role
    p.capabilities_status = 404
    assert pairing_code(p).status_code == 201
    assert "/api/v1/administration/catalog" not in p.requests
    with transaction(p.engine) as db:
        assert db.query(Pairing).one().organization_id == p.org


@pytest.mark.parametrize("catalog_case", ["absent", "duplicate", "unavailable"])
def test_admin_catalog_must_identify_one_authorized_zone(dashboard, catalog_case):
    p = dashboard
    if catalog_case == "absent":
        p.catalog = []
    elif catalog_case == "duplicate":
        p.catalog *= 2
    else:
        p.catalog_status = 503
    assert pairing_code(p).status_code == (503 if catalog_case == "unavailable" else 404)
    with transaction(p.engine) as db:
        assert db.query(Pairing).count() == 0


def test_admin_cannot_bypass_zone_endpoint_denial(dashboard):
    p = dashboard
    p.zone_status = 404
    assert pairing_code(p).status_code == 404
    assert "/api/v1/administration/catalog" not in p.requests
    with transaction(p.engine) as db:
        assert db.query(Pairing).count() == 0


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_tenant_manager_cannot_pair_in_denied_foreign_zone(dashboard, role):
    p = dashboard
    p.identity["role"] = role
    p.capabilities["can_manage"] = False
    p.zone_status = 404
    assert pairing_code(p).status_code == 404
    with transaction(p.engine) as db:
        assert db.query(Pairing).count() == 0


@pytest.mark.parametrize("active,auth_status", [(False, 200), (True, 401)])
def test_inactive_or_invalid_session_cannot_pair(dashboard, active, auth_status):
    p = dashboard
    p.identity["is_active"] = active
    p.auth_status = auth_status
    assert pairing_code(p).status_code == (403 if auth_status == 200 else 401)
    assert "/api/v1/administration/capabilities" not in p.requests


def test_browser_cookie_forwards_only_existing_session(dashboard):
    p = dashboard
    p.client.cookies.set("access_token", "browser-session")
    p.client.cookies.set("unrelated", "must-not-forward")
    p.credential = {"cookie": "access_token=browser-session"}
    response = p.client.post(
        PREFIX + "/pairing-code", headers={"Origin": ORIGIN}, json={"zone_id": p.zone}
    )
    assert response.status_code == 201


def test_dashboard_timeout_does_not_create_code_or_block_sensor(dashboard):
    p = dashboard
    p.backend_timeout = True
    assert pairing_code(p).status_code == 503
    with transaction(p.engine) as db:
        assert db.query(Pairing).count() == 0
    assert p.upload(bytes(12)).status_code == 201


def test_platform_admin_device_scope_revocation_preserves_upload(dashboard):
    p = dashboard
    with transaction(p.engine) as db:
        device = db.get(Device, p.device_id)
        device.organization_id, device.zone_id = p.foreign_org, p.zone
        db.add(
            Device(
                id=str(uuid.uuid4()),
                organization_id=p.foreign_org,
                zone_id=p.hidden_zone,
                mode="DIRECT",
                key_hash="hidden",
                active=True,
                spool_bytes=0,
            )
        )
    response = p.client.get(PREFIX + "/devices", headers=p.headers)
    assert response.status_code == 200
    assert [d["id"] for d in response.json()] == [p.device_id]
    p.capabilities["can_manage"] = False
    assert p.client.get(PREFIX + "/devices", headers=p.headers).json() == []
    p.capabilities["can_manage"] = True
    p.allowed_zones = []
    assert p.client.get(PREFIX + "/devices", headers=p.headers).json() == []
    assert p.upload(bytes(12)).status_code == 201
