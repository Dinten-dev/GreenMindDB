"""Existing allowlisted operator read scope; ordinary tenant isolation stays intact."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import zone_access
from app.auth import create_access_token
from app.config import settings
from app.direct.models import Base as DirectBase
from app.direct.models import Device
from app.models.master import Zone
from app.models.user import Organization, Role, User
from app.routers.administration import is_management_admin
from app.visualization import direct_api


@pytest.fixture
def operator_scope(db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "management_admin_emails", " OPERATOR@example.test ")
    orgs = [Organization(name="Operator company"), Organization(name="Sensor company")]
    db.add_all(orgs)
    db.flush()
    zones = [Zone(name=f"Zone{n}", organization_id=o.id) for n, o in enumerate(orgs)]
    operator = User(
        email="operator@example.test",
        name="Ordinary display name",
        password_hash="unused",
        role=Role.MEMBER,
        organization_id=orgs[0].id,
        is_active=True,
        is_verified=True,
    )
    db.add_all([*zones, operator])
    db.commit()
    engine = create_engine(f"sqlite:///{tmp_path}/direct.db")
    DirectBase.metadata.create_all(engine)
    identity = str(uuid4())
    with Session(engine) as direct, direct.begin():
        direct.add(
            Device(
                id=identity,
                organization_id=str(orgs[1].id),
                zone_id=str(zones[1].id),
                mode="DIRECT",
                key_hash="test-only",
                active=True,
                spool_bytes=0,
            )
        )
    headers = {"Authorization": "Bearer " + create_access_token({"sub": str(operator.id)})}
    yield SimpleNamespace(
        user=operator, zones=zones, engine=engine, device=identity, headers=headers
    )
    engine.dispose()


def test_operator_zone_read_and_direct_read_use_same_allowlist(client, db, operator_scope):
    p = operator_scope
    assert client.get("/api/v1/administration/capabilities", headers=p.headers).json() == {
        "can_manage": True
    }
    assert {z["id"] for z in client.get("/api/v1/zones", headers=p.headers).json()} == {
        str(z.id) for z in p.zones
    }
    assert client.get(f"/api/v1/zones/{p.zones[1].id}", headers=p.headers).status_code == 200
    with Session(p.engine) as direct:
        assert direct_api.authorize(direct, db, p.user, p.device)["zone_id"] == str(p.zones[1].id)


def test_allowlist_revocation_removes_cross_company_reads(client, db, operator_scope, monkeypatch):
    p = operator_scope
    monkeypatch.setattr(settings, "management_admin_emails", "")
    assert client.get("/api/v1/administration/capabilities", headers=p.headers).json() == {
        "can_manage": False
    }
    assert client.get(f"/api/v1/zones/{p.zones[1].id}", headers=p.headers).status_code == 404
    with Session(p.engine) as direct, pytest.raises(HTTPException) as error:
        direct_api.authorize(direct, db, p.user, p.device)
    assert error.value.status_code == 404
    with Session(p.engine) as direct:
        assert direct.get(Device, p.device).active  # Read revocation never changes a sensor.


def test_platform_read_does_not_remove_zone_write_role_requirement(client, db, operator_scope):
    p = operator_scope
    assert client.delete(f"/api/v1/zones/{p.zones[1].id}", headers=p.headers).status_code == 403
    assert db.get(Zone, p.zones[1].id) is not None


@pytest.mark.parametrize("role", [Role.ADMIN, Role.OWNER])
def test_ordinary_manager_cannot_read_foreign_company(client, db, operator_scope, role):
    p = operator_scope
    p.user.email, p.user.role, p.user.name = "ordinary@example.test", role, "operator@example.test"
    db.commit()
    assert client.get("/api/v1/administration/capabilities", headers=p.headers).json() == {
        "can_manage": False
    }
    assert client.get(f"/api/v1/zones/{p.zones[1].id}", headers=p.headers).status_code == 404
    assert client.delete(f"/api/v1/zones/{p.zones[1].id}", headers=p.headers).status_code == 404
    assert db.get(Zone, p.zones[1].id) is not None
    with Session(p.engine) as direct, pytest.raises(HTTPException) as error:
        direct_api.authorize(direct, db, p.user, p.device)
    assert error.value.status_code == 404


@pytest.mark.parametrize("field", ["is_active", "is_verified"])
def test_disabled_or_unverified_operator_cannot_span_companies(client, db, operator_scope, field):
    p = operator_scope
    setattr(p.user, field, False)
    db.commit()
    assert not zone_access.is_platform_admin(p.user) and not is_management_admin(p.user)
    assert client.get(f"/api/v1/zones/{p.zones[1].id}", headers=p.headers).status_code == 403
    with Session(p.engine) as direct, pytest.raises(HTTPException) as error:
        direct_api.authorize(direct, db, p.user, p.device)
    assert error.value.status_code == 404


def test_platform_read_still_requires_existing_zone_and_device(db, operator_scope):
    p = operator_scope
    with Session(p.engine) as direct, direct.begin():
        direct.get(Device, p.device).zone_id = str(uuid4())
    with Session(p.engine) as direct, pytest.raises(HTTPException) as error:
        direct_api.authorize(direct, db, p.user, p.device)
    assert error.value.status_code == 404
    with Session(p.engine) as direct, pytest.raises(HTTPException) as error:
        direct_api.authorize(direct, db, p.user, uuid4())
    assert error.value.status_code == 404
