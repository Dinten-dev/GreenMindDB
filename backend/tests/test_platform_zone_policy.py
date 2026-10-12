"""The reader preserves the live configurable platform policy."""

from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.config import settings
from app.models.user import Role
from app.zone_access import is_platform_admin, zone_access_filter


@pytest.mark.parametrize("active,verified", [(False, True), (True, False), (False, False)])
def test_unverified_or_inactive_configured_user_is_not_platform_admin(
    monkeypatch, active, verified
):
    monkeypatch.setattr(settings, "management_admin_emails", "operator@example.test")
    user = SimpleNamespace(email="operator@example.test", is_active=active, is_verified=verified)
    assert not is_platform_admin(user)


def test_configured_platform_user_and_tenant_owner_remain_distinct(monkeypatch):
    monkeypatch.setattr(settings, "management_admin_emails", " Operator@Example.test ,")
    user = SimpleNamespace(
        email="operator@example.test",
        is_active=True,
        is_verified=True,
        id="user",
        organization_id="tenant",
        role=Role.OWNER,
    )
    assert is_platform_admin(user)
    assert str(zone_access_filter(user).compile(dialect=postgresql.dialect())) == "true"
    user.email = "tenant-owner@example.test"
    assert not is_platform_admin(user)
    assert "organization_id" in str(zone_access_filter(user))
    user.role = Role.MEMBER
    assert "zone_access" in str(zone_access_filter(user))


@pytest.mark.parametrize("configured", [True, False])
def test_direct_lookup_and_zone_policy_use_same_platform_authority(monkeypatch, configured):
    from app.visualization import direct_api

    monkeypatch.setattr(
        settings, "management_admin_emails", "operator@example.test" if configured else ""
    )
    user = SimpleNamespace(
        email="operator@example.test",
        is_active=True,
        is_verified=True,
        id="user",
        organization_id="tenant",
        role=Role.OWNER,
    )
    calls = []

    class Result:
        def mappings(self):
            return self

        def first(self):
            return {"id": "device", "zone_id": "zone", "mode": "native"}

    class DB:
        def execute(self, query, params):
            calls.append(params)
            return Result()

    monkeypatch.setattr(
        direct_api, "require_zone_access", lambda legacy, actor, zone: calls.append((actor, zone))
    )
    assert direct_api.authorize(DB(), object(), user, "device")["id"] == "device"
    assert calls[0]["platform"] is configured
    assert calls[1] == (user, "zone")
