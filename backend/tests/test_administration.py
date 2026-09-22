"""Admin allowlist, storage boundary and atomic user/tenant access tests."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.auth import create_access_token, verify_password
from app.config import settings
from app.models.audit_log import AuditLog
from app.models.master import Zone
from app.models.user import Organization, Role, User
from app.models.zone_access import ZoneAccess


@pytest.fixture
def management(client, db, admin_token, monkeypatch):
    monkeypatch.setattr(settings, "management_admin_emails", " CI-ADMIN@test.com ")
    monkeypatch.setattr(settings, "storage_monitor_path", "/host-storage")
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.mark.parametrize("free,warning", [(4.999, True), (5, False), (5.001, False), (0, True)])
def test_storage_boundary(client, management, monkeypatch, free, warning):
    monkeypatch.setattr(
        "app.routers.administration.os.statvfs",
        lambda _: SimpleNamespace(f_blocks=100000, f_frsize=4096, f_bavail=int(free * 1000)),
    )
    response = client.get("/api/v1/administration/storage", headers=management)
    assert response.status_code == 200
    assert response.json()["warning"] is warning
    assert response.json()["free_percent"] == pytest.approx(free)
    assert response.headers["Cache-Control"] == "private, no-store"


def test_storage_errors_do_not_report_zero_space(client, management, monkeypatch):
    def unavailable(_):
        raise OSError("private path details")

    monkeypatch.setattr("app.routers.administration.os.statvfs", unavailable)
    response = client.get("/api/v1/administration/storage", headers=management)
    assert response.status_code == 503
    assert "private path" not in response.text
    monkeypatch.setattr(settings, "storage_monitor_path", "")
    assert client.get("/api/v1/administration/storage", headers=management).status_code == 503


def test_allowlist_required_even_for_role_admin(client, admin_token, monkeypatch):
    monkeypatch.setattr(settings, "management_admin_emails", "")
    headers = {"Authorization": f"Bearer {admin_token}"}
    assert client.get("/api/v1/administration/capabilities", headers=headers).json() == {
        "can_manage": False
    }
    for path in ["storage", "catalog", "users"]:
        assert client.get("/api/v1/administration/" + path, headers=headers).status_code == 403
    assert client.post("/api/v1/administration/users", headers=headers, json={}).status_code == 403
    assert (
        client.put(
            "/api/v1/administration/users/" + str(uuid4()), headers=headers, json={}
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert client.get("/api/v1/administration/users").status_code == 401


def test_profile_name_cannot_grant_admin_access(client, db, management):
    user = User(
        email="ordinary@example.com",
        name="ci-admin@test.com",
        password_hash="disabled",
        role=Role.MEMBER,
        is_verified=True,
        is_active=True,
    )
    db.add(user)
    db.commit()
    token = create_access_token({"sub": str(user.id)})
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/v1/administration/capabilities", headers=headers).json() == {
        "can_manage": False
    }
    assert client.get("/api/v1/administration/storage", headers=headers).status_code == 403


def test_create_move_revoke_deactivate_and_audit(client, db, management):
    first, second = Organization(name="First"), Organization(name="Second")
    db.add_all([first, second])
    db.flush()
    a, b = (
        Zone(name="First zone", organization_id=first.id),
        Zone(name="Second zone", organization_id=second.id),
    )
    db.add_all([a, b])
    db.commit()
    payload = {
        "name": "New member",
        "email": "New.Member@example.com",
        "password": "LongSecurePass12",
        "organization_id": str(first.id),
        "zone_ids": [str(a.id)],
    }
    response = client.post("/api/v1/administration/users", headers=management, json=payload)
    assert response.status_code == 201, response.text
    created = response.json()
    uid = created["id"]
    saved = db.query(User).filter_by(email="new.member@example.com").one()
    assert verify_password(payload["password"], saved.password_hash)
    assert payload["password"] not in str([x.details for x in db.query(AuditLog).all()])
    member_headers = {"Authorization": "Bearer " + create_access_token({"sub": uid})}
    assert [z["id"] for z in client.get("/api/v1/zones", headers=member_headers).json()] == [
        str(a.id)
    ]
    edit = {
        "name": "Renamed",
        "phone_number": None,
        "organization_id": str(second.id),
        "role": "member",
        "is_active": True,
        "zone_ids": [str(a.id)],
    }
    assert (
        client.put("/api/v1/administration/users/" + uid, headers=management, json=edit).status_code
        == 422
    )
    assert db.query(ZoneAccess).filter_by(user_id=saved.id).one().zone_id == a.id
    edit["zone_ids"] = [str(b.id)]
    moved = client.put("/api/v1/administration/users/" + uid, headers=management, json=edit)
    assert moved.status_code == 200, moved.text
    assert moved.json()["organization_name"] == "Second"
    assert [z["id"] for z in client.get("/api/v1/zones", headers=member_headers).json()] == [
        str(b.id)
    ]
    edit["zone_ids"] = []
    assert (
        client.put("/api/v1/administration/users/" + uid, headers=management, json=edit).status_code
        == 200
    )
    assert client.get("/api/v1/zones", headers=member_headers).json() == []
    edit["is_active"] = False
    assert (
        client.put("/api/v1/administration/users/" + uid, headers=management, json=edit).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me", headers=member_headers).status_code == 403
    assert db.query(AuditLog).filter(AuditLog.action.like("administration.%")).count() == 4
    assert (
        client.post("/api/v1/administration/users", headers=management, json=payload).status_code
        == 409
    )
    listing = client.get(
        "/api/v1/administration/users?search=new.member&limit=1", headers=management
    ).json()
    assert listing["total"] == 1 and listing["users"][0]["id"] == uid
    assert "password_hash" not in str(listing)


def test_protected_admin_and_last_company_manager(client, db, management):
    admin = db.query(User).filter_by(email="ci-admin@test.com").one()
    data = {
        "name": "Blocked",
        "phone_number": None,
        "organization_id": str(admin.organization_id),
        "role": "member",
        "is_active": False,
        "zone_ids": [],
    }
    assert (
        client.put(
            "/api/v1/administration/users/" + str(admin.id), headers=management, json=data
        ).status_code
        == 409
    )
    company = Organization(name="Single owner")
    db.add(company)
    db.flush()
    owner = User(
        email="owner@example.com",
        password_hash="disabled",
        role=Role.OWNER,
        organization_id=company.id,
        is_active=True,
        is_verified=True,
    )
    db.add(owner)
    db.commit()
    data["organization_id"] = str(company.id)
    assert (
        client.put(
            "/api/v1/administration/users/" + str(owner.id), headers=management, json=data
        ).status_code
        == 409
    )


def test_create_validation_and_catalog(client, db, management):
    org = db.query(Organization).first()
    data = {
        "name": "Test",
        "email": "valid@example.com",
        "password": "short",
        "organization_id": str(org.id),
        "zone_ids": [],
    }
    assert (
        client.post("/api/v1/administration/users", headers=management, json=data).status_code
        == 422
    )
    data["password"] = "LongSecurePass12"
    data["zone_ids"] = [str(uuid4())]
    assert (
        client.post("/api/v1/administration/users", headers=management, json=data).status_code
        == 422
    )
    assert client.get("/api/v1/administration/catalog", headers=management).json()["companies"]
