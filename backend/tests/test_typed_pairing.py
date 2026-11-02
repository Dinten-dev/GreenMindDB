"""Purpose separation and fail-closed legacy pairing regressions."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.master import Gateway, Sensor
from app.models.pairing import PairingCode
from app.schemas.gateway import RegisterGatewayRequest
from app.services import gateway_service


def _issue(client, data, token, purpose="gateway", *, alias=False):
    path = (
        f"/api/v1/zones/{data['zone'].id}/pairing-codes"
        if alias
        else f"/api/v1/{'gateways' if purpose == 'gateway' else 'sensors'}/pairing-code"
    )
    response = client.post(
        path,
        json={"zone_id": str(data["zone"].id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    assert response.json()["purpose"] == purpose
    return response.json()["code"]


@pytest.mark.parametrize("issuer,redeemer", [("sensors", "gateway"), ("gateways", "sensor")])
def test_cross_purpose_code_cannot_register(
    client, db, setup_test_data, tenant_admin_token, issuer, redeemer
):
    zone = setup_test_data["zone"]
    response = client.post(
        f"/api/v1/{issuer}/pairing-code",
        json={"zone_id": str(zone.id)},
        headers={"Authorization": f"Bearer {tenant_admin_token}"},
    )
    assert response.status_code == 201
    code = response.json()["code"]
    if redeemer == "gateway":
        response = client.post(
            "/api/v1/gateways/register", json={"code": code, "hardware_id": "cross-purpose"}
        )
        assert db.query(Gateway).filter_by(hardware_id="cross-purpose").first() is None
    else:
        gateway = setup_test_data["gateway"]
        response = client.post(
            f"/api/v1/gateways/{gateway.id}/sensors/register",
            json={"code": code, "mac_address": "AA:BB:CC:00:00:01"},
            headers={"X-Api-Key": "ci-api-key", "X-Gateway-Id": str(gateway.id)},
        )
        assert db.query(Sensor).filter_by(mac_address="AA:BB:CC:00:00:01").first() is None
    assert response.status_code == 400
    assert db.query(PairingCode).filter_by(code=code).one().used_at is None


@pytest.mark.parametrize("purpose", ["gateway", "sensor"])
@pytest.mark.parametrize("state", ["valid", "used", "expired", "legacy", "unknown"])
def test_redemption_lifecycle(client, db, setup_test_data, tenant_admin_token, purpose, state):
    code = _issue(client, setup_test_data, tenant_admin_token, purpose)
    pc = db.query(PairingCode).filter_by(code=code).one()
    if state in {"used", "legacy"}:
        pc.used_at = datetime.now(UTC)
    if state == "legacy":
        pc.purpose = "legacy"
    if state == "expired":
        pc.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    sent_code = "ZZ0000" if state == "unknown" else code.lower()
    if purpose == "gateway":
        response = client.post(
            "/api/v1/gateways/register",
            json={"code": sent_code, "hardware_id": "lifecycle-new"},
        )
        count = db.query(Gateway).filter_by(hardware_id="lifecycle-new").count()
    else:
        gateway = setup_test_data["gateway"]
        response = client.post(
            f"/api/v1/gateways/{gateway.id}/sensors/register",
            json={"code": sent_code, "mac_address": "AA:BB:CC:00:00:02"},
            headers={"X-Api-Key": "ci-api-key", "X-Gateway-Id": str(gateway.id)},
        )
        count = db.query(Sensor).filter_by(mac_address="AA:BB:CC:00:00:02").count()
    assert response.status_code == (201 if state == "valid" else 400)
    assert count == (1 if state == "valid" else 0)
    db.refresh(pc)
    assert (pc.used_at is not None) == (state in {"valid", "used", "legacy"})


def test_zone_alias_issues_gateway_only(client, db, setup_test_data, tenant_admin_token):
    code = _issue(client, setup_test_data, tenant_admin_token, alias=True)
    assert db.query(PairingCode).filter_by(code=code).one().purpose == "gateway"


@pytest.mark.parametrize(
    "key,active,status",
    [
        (None, True, 409),
        ("wrong", True, 409),
        ("ci-api-key", False, 403),
        ("ci-api-key", True, 201),
    ],
)
def test_existing_gateway_requires_current_key(
    client, db, setup_test_data, tenant_admin_token, key, active, status
):
    code = _issue(client, setup_test_data, tenant_admin_token)
    gateway = setup_test_data["gateway"]
    gateway.is_active = active
    original = gateway.api_key_hash
    db.commit()
    response = client.post(
        "/api/v1/gateways/register",
        json={"code": code, "hardware_id": gateway.hardware_id},
        headers={"X-Api-Key": key} if key else {},
    )
    assert response.status_code == status
    db.refresh(gateway)
    assert (gateway.api_key_hash != original) == (status == 201)
    assert (db.query(PairingCode).filter_by(code=code).one().used_at is not None) == (status == 201)


@pytest.mark.parametrize("failure", ["integrity", "commit"])
def test_failure_rolls_back_device_and_code(
    client, db, setup_test_data, tenant_admin_token, monkeypatch, failure
):
    code = _issue(client, setup_test_data, tenant_admin_token)
    if failure == "integrity":

        def fail(*args):
            raise IntegrityError("synthetic", {}, Exception("conflict"))

        monkeypatch.setattr(gateway_service, "set_gateway_api_key", fail)
        error = HTTPException
    else:

        def fail():
            raise RuntimeError("synthetic commit failure")

        monkeypatch.setattr(db, "commit", fail)
        error = RuntimeError
    with pytest.raises(error):
        gateway_service.register_gateway(
            db, RegisterGatewayRequest(code=code, hardware_id="failed-new")
        )
    assert db.query(Gateway).filter_by(hardware_id="failed-new").count() == 0
    assert db.query(PairingCode).filter_by(code=code).one().used_at is None


def test_generator_never_recycles_used_code(
    client, db, setup_test_data, tenant_admin_token, monkeypatch
):
    code = _issue(client, setup_test_data, tenant_admin_token)
    pc = db.query(PairingCode).filter_by(code=code).one()
    pc.used_at = datetime.now(UTC)
    db.commit()
    sequence = iter(code + "ZX1234")
    monkeypatch.setattr(gateway_service.secrets, "choice", lambda chars: next(sequence))
    next_code = _issue(client, setup_test_data, tenant_admin_token)
    assert next_code == "ZX1234" and next_code != code


def test_purpose_database_constraints(db, setup_test_data, tenant_admin_token):
    from app.models.user import User

    user = db.query(User).filter_by(email="tenant-admin@test.com").one()
    for purpose in (None, "other", "legacy"):
        db.add(
            PairingCode(
                code="NO1234",
                purpose=purpose,
                zone_id=setup_test_data["zone"].id,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                created_by_user_id=user.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_sensor_wrong_zone_does_not_consume_code(client, db, setup_test_data, tenant_admin_token):
    from app.models.master import Zone

    zone = Zone(name="Other", organization_id=setup_test_data["org"].id)
    db.add(zone)
    db.commit()
    code = _issue(client, setup_test_data, tenant_admin_token, "sensor")
    gateway = setup_test_data["gateway"]
    gateway.zone_id = zone.id
    db.commit()
    with pytest.raises(HTTPException) as error:
        gateway_service.register_sensor(db, gateway, "AA:BB:CC:00:00:03", code)
    assert error.value.status_code == 400
    assert db.query(PairingCode).filter_by(code=code).one().used_at is None


def test_experimental_jobs_require_sensor_purpose(client, db, setup_test_data, tenant_admin_token):
    import asyncio

    from app.models.user import User
    from app.routers.provisioning import create_provisioning_job
    from app.schemas.provisioning import ProvisioningJobCreate

    code = _issue(client, setup_test_data, tenant_admin_token)
    user = db.query(User).filter_by(email="tenant-admin@test.com").one()
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            create_provisioning_job(
                ProvisioningJobCreate(ssid="Synthetic", password="", pairing_code=code), db, user
            )
        )
    assert error.value.status_code == 400


def test_issuer_avoids_orphaned_provisioning_job_codes(
    client, db, setup_test_data, tenant_admin_token, monkeypatch
):
    from app.models.provisioning import ProvisioningJob

    db.add(ProvisioningJob(ssid="Synthetic", password="", pairing_code="OLD123"))
    db.commit()
    sequence = iter("OLD123NEW123")
    monkeypatch.setattr(gateway_service.secrets, "choice", lambda chars: next(sequence))
    code = _issue(client, setup_test_data, tenant_admin_token, "sensor")
    assert code == "NEW123"


def test_sensor_registration_rechecks_concurrent_key_rotation(
    client, db, setup_test_data, tenant_admin_token, monkeypatch
):
    from app.gateway_auth import gateway_key_digest

    code = _issue(client, setup_test_data, tenant_admin_token, "sensor")
    gateway = setup_test_data["gateway"]
    locked = gateway_service._locked_pairing_code

    def rotate(*args):
        pc, now = locked(*args)
        gateway.api_key_hash = gateway_key_digest("rotated-key")
        db.flush()
        return pc, now

    monkeypatch.setattr(gateway_service, "_locked_pairing_code", rotate)
    with pytest.raises(HTTPException) as error:
        gateway_service.register_sensor(db, gateway, "AA:BB:CC:00:00:04", code)
    assert error.value.status_code == 401
    assert db.query(PairingCode).filter_by(code=code).one().used_at is None
