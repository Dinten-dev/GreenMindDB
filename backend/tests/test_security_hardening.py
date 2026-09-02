"""Regression tests for high-impact authentication and device trust boundaries."""

from __future__ import annotations

import base64
import hashlib
import io
import tarfile
import uuid
import wave
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.auth import create_access_token, get_password_hash
from app.config import Settings, settings
from app.gateway_auth import (
    authenticate_gateway_api_key,
    generate_gateway_api_key,
    set_gateway_api_key,
)
from app.image_security import validate_observation_image
from app.models.firmware import FirmwareRelease
from app.models.gateway_remote import GatewayCommand
from app.models.ingest_log import IngestLog
from app.models.master import Gateway, Sensor, Zone
from app.models.pairing import PairingCode
from app.models.timeseries import SensorReading
from app.models.user import Organization, Role, User
from app.models.wav_file import WavFile
from app.schemas.gateway import RegisterGatewayRequest
from app.schemas.ingest import IngestRequest
from app.services.gateway_remote_service import toggle_app_release, upload_app_release
from app.services.gateway_service import gateway_commands_cache, register_gateway


def _user(
    db: Session,
    *,
    email: str,
    role: Role,
    organization_id: uuid.UUID | None,
    verified: bool = True,
) -> User:
    user = User(
        email=email,
        password_hash=get_password_hash("ValidPass1"),
        role=role,
        is_active=True,
        is_verified=verified,
        organization_id=organization_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "ValidPass1"},
    )
    assert response.status_code == 200


def _wav(sample_rate: int = 10, frames: int = 10) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frames)
    return output.getvalue()


def _release_archive(member_name: str = "greenmind-release/payload.txt") -> bytes:
    payload = b"signed gateway release"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo(member_name)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def test_observation_image_validation_uses_decoded_type_not_declared_mime():
    valid = io.BytesIO()
    Image.new("RGB", (2, 2), color="green").save(valid, format="PNG")
    valid.seek(0)
    assert validate_observation_image(valid) == "image/png"

    with pytest.raises(ValueError, match="invalid"):
        validate_observation_image(io.BytesIO(b"\xff\xd8not-a-real-jpeg"))


def test_deployed_settings_require_release_key_and_exact_cors_origins():
    secure_secret = "s" * 32

    with pytest.raises(ValidationError, match="SIGNING_PUBLIC_KEY_PATH"):
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret_key=secure_secret,
            gateway_release_signing_public_key_path="",
            resend_api_key="re_test_placeholder",
            frontend_url="https://app.example.invalid",
            s3_access_key_id="production-storage-user",
            s3_secret_access_key="production-storage-secret",
            cookie_secure=True,
        )

    with pytest.raises(ValidationError, match="wildcard CORS"):
        Settings(
            _env_file=None,
            environment="staging",
            jwt_secret_key=secure_secret,
            gateway_release_signing_public_key_path="/run/secrets/release-signing-public.pem",
            cors_origins=["https://*.example.invalid"],
            resend_api_key="re_test_placeholder",
            frontend_url="https://app.example.invalid",
            s3_access_key_id="production-storage-user",
            s3_secret_access_key="production-storage-secret",
            cookie_secure=True,
        )

    local = Settings(
        _env_file=None,
        environment="development",
        jwt_secret_key=secure_secret,
        gateway_release_signing_public_key_path="",
    )
    assert local.gateway_release_signing_public_key_path == ""


@pytest.mark.parametrize(
    "flag_name",
    ["enable_experimental_provisioning", "enable_experimental_biosignal"],
)
def test_deployed_settings_reject_experimental_data_paths(flag_name: str):
    with pytest.raises(ValidationError, match=flag_name.upper()):
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret_key="s" * 32,
            gateway_release_signing_public_key_path="/run/secrets/release-signing-public.pem",
            resend_api_key="re_test_placeholder",
            frontend_url="https://app.example.invalid",
            s3_access_key_id="production-storage-user",
            s3_secret_access_key="production-storage-secret",
            cookie_secure=True,
            **{flag_name: True},
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"resend_api_key": ""}, "RESEND_API_KEY"),
        ({"frontend_url": "http://app.example.invalid"}, "absolute HTTPS URL"),
        ({"frontend_url": "https://"}, "absolute HTTPS URL"),
    ],
)
def test_deployed_settings_require_working_email_verification_config(overrides, message):
    values = {
        "resend_api_key": "re_test_placeholder",
        "frontend_url": "https://app.example.invalid",
        "s3_access_key_id": "production-storage-user",
        "s3_secret_access_key": "production-storage-secret",
        "cookie_secure": True,
    }
    values.update(overrides)
    with pytest.raises(ValidationError, match=message):
        Settings(
            _env_file=None,
            environment="staging",
            jwt_secret_key="s" * 32,
            gateway_release_signing_public_key_path="/run/secrets/release-signing-public.pem",
            **values,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"jwt_secret_key": "CHANGE_ME_" + "x" * 32}, "JWT_SECRET_KEY"),
        ({"resend_api_key": "change-me-resend-key"}, "RESEND_API_KEY"),
        ({"s3_access_key_id": "CHANGE_ME_MINIO_USER"}, "S3_ACCESS_KEY_ID"),
        ({"s3_secret_access_key": "change-me-minio-secret"}, "S3_SECRET_ACCESS_KEY"),
        ({"s3_access_key_id": "minioadmin"}, "MinIO default"),
    ],
)
def test_deployed_settings_reject_placeholder_secrets(overrides, message):
    values = {
        "jwt_secret_key": "s" * 32,
        "resend_api_key": "re_test_placeholder",
        "s3_access_key_id": "production-storage-user",
        "s3_secret_access_key": "production-storage-secret",
        "cookie_secure": True,
    }
    values.update(overrides)
    with pytest.raises(ValidationError, match=message):
        Settings(
            _env_file=None,
            environment="production",
            gateway_release_signing_public_key_path="/run/secrets/release-signing-public.pem",
            frontend_url="https://app.example.invalid",
            **values,
        )


def test_deployed_settings_require_secure_cookie_and_staging_jwt_override():
    common = {
        "_env_file": None,
        "environment": "staging",
        "gateway_release_signing_public_key_path": "/run/secrets/release-signing-public.pem",
        "frontend_url": "https://app.example.invalid",
        "resend_api_key": "re_test_placeholder",
        "s3_access_key_id": "production-storage-user",
        "s3_secret_access_key": "production-storage-secret",
    }
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(jwt_secret_key="s" * 32, cookie_secure=False, **common)
    with pytest.raises(ValidationError, match="overridden"):
        Settings(
            jwt_secret_key="dev-only-change-me-please-dev-only-change-me",
            cookie_secure=True,
            **common,
        )


def test_unverified_existing_token_is_rejected(client: TestClient, db: Session):
    user = _user(
        db,
        email="pending-token@example.com",
        role=Role.OWNER,
        organization_id=None,
        verified=False,
    )
    token = create_access_token({"sub": str(user.id)})

    response = client.get("/api/v1/auth/me", cookies={"access_token": token})

    assert response.status_code == 403
    assert response.json()["detail"] == "Email not verified"


def test_tenant_owner_cannot_access_platform_admin_apis(client: TestClient, db: Session):
    organization = Organization(name="Tenant Owner Org")
    db.add(organization)
    db.commit()
    owner = _user(
        db,
        email="tenant-owner@example.com",
        role=Role.OWNER,
        organization_id=organization.id,
    )
    _login(client, owner.email)

    assert client.get("/api/v1/admin/gateway-fleet").status_code == 403
    assert client.get("/api/v1/firmware/dashboard").status_code == 403


def test_member_can_read_but_cannot_mutate_tenant_infrastructure(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
):
    member = _user(
        db,
        email="read-only-member@example.com",
        role=Role.MEMBER,
        organization_id=setup_test_data["org"].id,
    )
    _login(client, member.email)

    assert client.get("/api/v1/zones").status_code == 200
    assert client.get("/api/v1/plants").status_code == 200
    assert client.get("/api/v1/gateways").status_code == 200
    assert client.get("/api/v1/sensors").status_code == 200
    assert client.post("/api/v1/zones", json={"name": "Forbidden"}).status_code == 403
    assert (
        client.post(
            "/api/v1/plants",
            json={"name": "Forbidden", "zone_id": str(setup_test_data["zone"].id)},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/gateways/pairing-code",
            json={"zone_id": str(setup_test_data["zone"].id)},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/sensors/{setup_test_data['sensor'].id}",
            json={"name": "Forbidden"},
        ).status_code
        == 403
    )

    unattached_member = _user(
        db,
        email="unattached-member@example.com",
        role=Role.MEMBER,
        organization_id=None,
    )
    _login(client, unattached_member.email)
    assert client.post("/api/v1/organizations", json={"name": "Forbidden"}).status_code == 403


def test_plant_sensor_assignment_requires_same_tenant_zone_and_unique_active_use(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
):
    owner = _user(
        db,
        email="plant-owner@example.com",
        role=Role.OWNER,
        organization_id=setup_test_data["org"].id,
    )
    _login(client, owner.email)

    first_plant = client.post(
        "/api/v1/plants",
        json={"name": "First Plant", "zone_id": str(setup_test_data["zone"].id)},
    )
    assert first_plant.status_code == 201
    first_plant_id = first_plant.json()["id"]

    other_org = Organization(name="Assignment Other Org")
    db.add(other_org)
    db.flush()
    other_zone = Zone(
        organization_id=other_org.id,
        name="Assignment Other Zone",
        zone_type="GREENHOUSE",
    )
    same_org_other_zone = Zone(
        organization_id=setup_test_data["org"].id,
        name="Assignment Same Org Other Zone",
        zone_type="GREENHOUSE",
    )
    db.add_all([other_zone, same_org_other_zone])
    db.flush()
    other_gateway = Gateway(
        zone_id=other_zone.id,
        hardware_id="assignment-other-tenant-gateway",
        is_active=True,
    )
    wrong_zone_gateway = Gateway(
        zone_id=same_org_other_zone.id,
        hardware_id="assignment-wrong-zone-gateway",
        is_active=True,
    )
    db.add_all([other_gateway, wrong_zone_gateway])
    db.flush()
    other_sensor = Sensor(
        gateway_id=other_gateway.id,
        mac_address="00:11:22:33:44:66",
        sensor_type="leaf_voltage",
    )
    wrong_zone_sensor = Sensor(
        gateway_id=wrong_zone_gateway.id,
        mac_address="00:11:22:33:44:77",
        sensor_type="leaf_voltage",
    )
    db.add_all([other_sensor, wrong_zone_sensor])
    db.commit()

    for forbidden_sensor in (other_sensor, wrong_zone_sensor):
        response = client.post(
            f"/api/v1/plants/{first_plant_id}/assign-sensor",
            json={"sensor_id": str(forbidden_sensor.id)},
        )
        assert response.status_code == 404

    assigned = client.post(
        f"/api/v1/plants/{first_plant_id}/assign-sensor",
        json={"sensor_id": str(setup_test_data["sensor"].id)},
    )
    assert assigned.status_code == 200

    second_plant = client.post(
        "/api/v1/plants",
        json={"name": "Second Plant", "zone_id": str(setup_test_data["zone"].id)},
    )
    assert second_plant.status_code == 201
    conflict = client.post(
        f"/api/v1/plants/{second_plant.json()['id']}/assign-sensor",
        json={"sensor_id": str(setup_test_data["sensor"].id)},
    )
    assert conflict.status_code == 409


def test_experimental_routers_are_disabled_by_default(client: TestClient):
    assert client.get("/api/v1/provisioning/jobs/pending").status_code == 404
    assert client.get(f"/api/v1/biosignal/sessions/{uuid.uuid4()}").status_code == 404


def test_prefixed_gateway_key_rotation_is_immediate(db: Session, setup_test_data: dict):
    gateway = setup_test_data["gateway"]
    first_key = generate_gateway_api_key(gateway.id)
    assert first_key.startswith(f"gmk_{gateway.id.hex}_")
    assert len(first_key.encode("utf-8")) <= 72
    set_gateway_api_key(gateway, first_key)
    db.commit()

    assert gateway.api_key_hash.startswith("sha256$")
    assert first_key not in gateway.api_key_hash
    assert authenticate_gateway_api_key(db, first_key).id == gateway.id

    replacement_key = generate_gateway_api_key(gateway.id)
    set_gateway_api_key(gateway, replacement_key)
    db.commit()

    with pytest.raises(HTTPException) as revoked:
        authenticate_gateway_api_key(db, first_key)
    assert revoked.value.status_code == 401
    assert authenticate_gateway_api_key(db, replacement_key).id == gateway.id


def test_legacy_gateway_key_fallback_remains_compatible(db: Session, setup_test_data: dict):
    assert authenticate_gateway_api_key(db, "ci-api-key").id == setup_test_data["gateway"].id


def test_gateway_path_identity_prevents_command_bola(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
):
    first_gateway = setup_test_data["gateway"]
    second_gateway = Gateway(
        zone_id=setup_test_data["zone"].id,
        hardware_id="second-gateway",
        api_key_hash=get_password_hash("second-api-key"),
        is_active=True,
    )
    db.add(second_gateway)
    db.commit()
    queued = [{"action": "delete_sensor", "mac_address": "00:11:22:33:44:55"}]
    gateway_commands_cache[str(second_gateway.id)] = queued.copy()

    response = client.get(
        f"/api/v1/gateways/{second_gateway.id}/commands",
        headers={"X-Api-Key": "ci-api-key"},
    )

    assert response.status_code == 403
    assert gateway_commands_cache[str(second_gateway.id)] == queued
    gateway_commands_cache.pop(str(second_gateway.id), None)
    assert first_gateway.id != second_gateway.id


def test_command_result_cannot_target_another_gateway(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
):
    first_gateway = setup_test_data["gateway"]
    second_gateway = Gateway(
        zone_id=setup_test_data["zone"].id,
        hardware_id="command-target-gateway",
        api_key_hash=get_password_hash("command-target-key"),
        is_active=True,
    )
    db.add(second_gateway)
    db.flush()
    command = GatewayCommand(
        gateway_id=second_gateway.id,
        command_type="restart_gateway_service",
        status="delivered",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db.add(command)
    db.commit()

    response = client.post(
        "/api/v1/gateway/command-result",
        headers={"X-Api-Key": "ci-api-key"},
        json={
            "gateway_id": str(first_gateway.id),
            "command_id": str(command.id),
            "result": "executed",
        },
    )

    assert response.status_code == 404
    db.refresh(command)
    assert command.status == "delivered"


def test_gateway_reprovision_cannot_cross_organizations(
    db: Session,
    setup_test_data: dict,
):
    existing_gateway = setup_test_data["gateway"]
    other_org = Organization(name="Other Tenant")
    db.add(other_org)
    db.flush()
    other_zone = Zone(
        organization_id=other_org.id,
        name="Other Zone",
        zone_type="GREENHOUSE",
    )
    db.add(other_zone)
    db.flush()
    other_user = _user(
        db,
        email="other-tenant@example.com",
        role=Role.OWNER,
        organization_id=other_org.id,
    )
    pairing = PairingCode(
        code="XZ91AB",
        zone_id=other_zone.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        created_by_user_id=other_user.id,
    )
    db.add(pairing)
    db.commit()

    request = RegisterGatewayRequest(
        code=pairing.code,
        hardware_id=existing_gateway.hardware_id,
    )
    with pytest.raises(HTTPException) as rejected:
        register_gateway(db, request, current_api_key="ci-api-key")

    assert rejected.value.status_code == 409
    db.refresh(existing_gateway)
    db.refresh(pairing)
    assert existing_gateway.zone_id == setup_test_data["zone"].id
    assert pairing.used_at is None


def test_ingest_rejects_sensor_owned_by_another_gateway(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
):
    second_gateway = Gateway(
        zone_id=setup_test_data["zone"].id,
        hardware_id="sensor-owner-gateway",
        api_key_hash=get_password_hash("sensor-owner-key"),
        is_active=True,
    )
    db.add(second_gateway)
    db.flush()
    other_sensor = Sensor(
        gateway_id=second_gateway.id,
        mac_address="00:11:22:33:44:55",
        sensor_type="leaf_voltage",
    )
    db.add(other_sensor)
    db.commit()
    measurement_id = uuid.uuid4()

    response = client.post(
        "/api/v1/ingest",
        headers={"X-Api-Key": "ci-api-key"},
        json={
            "measurement_id": str(measurement_id),
            "gateway_serial": setup_test_data["gateway"].hardware_id,
            "readings": [
                {
                    "sensor_mac": other_sensor.mac_address,
                    "sensor_kind": "leaf_voltage",
                    "value": 1.0,
                    "unit": "mV",
                }
            ],
        },
    )

    assert response.status_code == 403
    db.refresh(other_sensor)
    assert other_sensor.gateway_id == second_gateway.id
    assert db.query(IngestLog).filter(IngestLog.measurement_id == measurement_id).first() is None


def test_ingest_schema_rejects_nonfinite_and_oversized_batches():
    base = {
        "measurement_id": str(uuid.uuid4()),
        "gateway_serial": "bounded-gateway",
        "readings": [
            {
                "sensor_mac": "AA:BB:CC:DD:EE:FF",
                "sensor_kind": "leaf_voltage",
                "value": float("nan"),
                "unit": "mV",
            }
        ],
    }
    with pytest.raises(ValidationError):
        IngestRequest.model_validate(base)

    valid_reading = {
        "sensor_mac": "AA:BB:CC:DD:EE:FF",
        "sensor_kind": "leaf_voltage",
        "value": 1.0,
        "unit": "mV",
    }
    with pytest.raises(ValidationError):
        IngestRequest.model_validate({**base, "readings": [valid_reading] * 5_001})


def test_sensor_export_rejects_rows_before_building_archive(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
    tenant_admin_token: str,
    monkeypatch,
):
    sensor = setup_test_data["sensor"]
    now = datetime.now(UTC)
    db.add_all(
        [
            SensorReading(
                timestamp=now - timedelta(seconds=offset),
                sensor_id=sensor.id,
                kind="leaf_voltage",
                value=float(offset),
                unit="mV",
            )
            for offset in (1, 2)
        ]
    )
    db.commit()
    monkeypatch.setattr(settings, "sensor_export_max_rows", 1)

    response = client.get(
        f"/api/v1/sensors/{sensor.id}/export",
        cookies={"access_token": tenant_admin_token},
    )

    assert response.status_code == 413
    assert "row limit" in response.json()["detail"]


def test_sensor_export_caps_kinds_and_bytes(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
    tenant_admin_token: str,
    monkeypatch,
):
    sensor = setup_test_data["sensor"]
    now = datetime.now(UTC)
    db.add_all(
        [
            SensorReading(
                timestamp=now - timedelta(seconds=index),
                sensor_id=sensor.id,
                kind=kind,
                value=1.0,
                unit="mV",
            )
            for index, kind in enumerate(("kind_a", "kind_b"), start=1)
        ]
    )
    db.commit()
    monkeypatch.setattr(settings, "sensor_export_max_kinds", 1)

    too_many_kinds = client.get(
        f"/api/v1/sensors/{sensor.id}/export",
        cookies={"access_token": tenant_admin_token},
    )
    assert too_many_kinds.status_code == 413
    assert "kind limit" in too_many_kinds.json()["detail"]

    monkeypatch.setattr(settings, "sensor_export_max_kinds", 32)
    monkeypatch.setattr(settings, "sensor_export_max_bytes", 32)
    too_many_bytes = client.get(
        f"/api/v1/sensors/{sensor.id}/export",
        cookies={"access_token": tenant_admin_token},
    )
    assert too_many_bytes.status_code == 413
    assert "byte limit" in too_many_bytes.json()["detail"]


def test_sensor_export_sanitizes_archive_names_metadata_and_csv_cells(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
    tenant_admin_token: str,
):
    sensor = setup_test_data["sensor"]
    sensor.name = "danger\r\n../sensor"
    setup_test_data["zone"].name = "Main Zone\nInjected"
    db.add(
        SensorReading(
            timestamp=datetime.now(UTC) - timedelta(seconds=1),
            sensor_id=sensor.id,
            kind="leaf/../../voltage",
            value=1.0,
            unit="=FORMULA()",
        )
    )
    db.commit()

    response = client.get(
        f"/api/v1/sensors/{sensor.id}/export",
        cookies={"access_token": tenant_admin_token},
    )

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition and "../" not in disposition
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len(names) == 1
        assert "/" not in names[0] and ".." not in names[0]
        csv_text = archive.read(names[0]).decode("utf-8")
    assert "# Zone: Main Zone Injected" in csv_text
    assert "'=FORMULA()" in csv_text


def test_wav_upload_is_owned_validated_and_idempotent(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
    mocker,
):
    start = datetime.now(UTC) - timedelta(minutes=1)
    end = start + timedelta(seconds=1)
    wav_bytes = _wav()
    upload = mocker.patch("app.routers.wav.wav_service.upload_wav")
    form = {
        "sensor_mac": setup_test_data["sensor"].mac_address,
        "gateway_serial": setup_test_data["gateway"].hardware_id,
        "sample_rate": "10",
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "timestamp_source": "filename",
    }

    first = client.post(
        "/api/v1/wav/upload",
        headers={"X-Api-Key": "ci-api-key"},
        data=form,
        files={"file": ("signal.wav", wav_bytes, "audio/wav")},
    )
    second = client.post(
        "/api/v1/wav/upload",
        headers={"X-Api-Key": "ci-api-key"},
        data=form,
        files={"file": ("signal.wav", wav_bytes, "audio/wav")},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["status"] == "duplicate"
    assert upload.call_count == 1
    record = db.query(WavFile).one()
    assert record.sensor_id == setup_test_data["sensor"].id
    assert record.gateway_id == setup_test_data["gateway"].id


def test_wav_upload_accepts_sparse_legacy_window(
    client: TestClient,
    setup_test_data: dict,
    mocker,
):
    start = datetime.now(UTC) - timedelta(minutes=20)
    end = start + timedelta(minutes=10)
    mocker.patch("app.routers.wav.wav_service.upload_wav")

    response = client.post(
        "/api/v1/wav/upload",
        headers={"X-Api-Key": "ci-api-key"},
        data={
            "sensor_mac": setup_test_data["sensor"].mac_address,
            "gateway_serial": setup_test_data["gateway"].hardware_id,
            "sample_rate": "10",
            "started_at": start.isoformat(),
            "ended_at": end.isoformat(),
            "timestamp_source": "filename",
        },
        files={"file": ("sparse.wav", _wav(), "audio/wav")},
    )

    assert response.status_code == 201
    assert response.json()["timing_status"] == "partial"
    assert response.json()["coverage_ratio"] == pytest.approx(1 / 600)


def test_ingest_persists_source_continuity_metadata(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
):
    response = client.post(
        "/api/v1/ingest",
        headers={"X-Api-Key": "ci-api-key"},
        json={
            "measurement_id": str(uuid.uuid4()),
            "gateway_serial": setup_test_data["gateway"].hardware_id,
            "readings": [
                {
                    "sensor_mac": setup_test_data["sensor"].mac_address,
                    "sensor_kind": "bio_signal",
                    "value": 123.0,
                    "unit": "mV",
                    "source_sequence": 42,
                    "source_uptime_ms": 123456,
                    "source_dropped_samples_total": 7,
                }
            ],
        },
    )

    assert response.status_code == 201
    reading = db.query(SensorReading).one()
    assert reading.source_sequence == 42
    assert reading.source_uptime_ms == 123456
    assert reading.source_dropped_samples_total == 7


def test_websocket_rejects_wrong_origin_and_cross_tenant_zone(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
):
    viewer = _user(
        db,
        email="websocket-viewer@example.com",
        role=Role.MEMBER,
        organization_id=setup_test_data["org"].id,
    )
    _login(client, viewer.email)

    with pytest.raises(WebSocketDisconnect) as wrong_origin:
        with client.websocket_connect(
            f"/api/v1/ws/zone/{setup_test_data['zone'].id}",
            headers={"origin": "https://attacker.invalid"},
        ):
            pass
    assert wrong_origin.value.code == 1008

    other_org = Organization(name="WebSocket Other Org")
    db.add(other_org)
    db.flush()
    other_zone = Zone(
        organization_id=other_org.id,
        name="Other WebSocket Zone",
        zone_type="GREENHOUSE",
    )
    db.add(other_zone)
    db.commit()
    with pytest.raises(WebSocketDisconnect) as cross_tenant:
        with client.websocket_connect(
            f"/api/v1/ws/zone/{other_zone.id}",
            headers={"origin": "http://localhost:3000"},
        ):
            pass
    assert cross_tenant.value.code == 1008


def test_firmware_download_requires_gateway_auth(
    client: TestClient,
    db: Session,
    setup_test_data: dict,
    tmp_path,
    monkeypatch,
):
    from app.services import firmware_service

    content = b"firmware-binary"
    artifact = tmp_path / "sensor.bin"
    artifact.write_bytes(content)
    monkeypatch.setattr(firmware_service, "FIRMWARE_STORAGE_DIR", str(tmp_path))
    release = FirmwareRelease(
        version="1.2.3",
        board_type="ESP32_WROOM",
        hardware_revision="v1",
        file_path=artifact.name,
        sha256=hashlib.sha256(content).hexdigest(),
        is_active=True,
    )
    db.add(release)
    db.commit()

    assert client.get(f"/api/v1/firmware/download/{release.id}").status_code == 401
    assert client.get(f"/firmware/{artifact.name}").status_code == 404
    response = client.get(
        f"/api/v1/firmware/download/{release.id}",
        headers={"X-Api-Key": "ci-api-key"},
    )
    assert response.status_code == 200
    assert response.content == content

    artifact.write_bytes(b"tampered")
    tampered = client.get(
        f"/api/v1/firmware/download/{release.id}",
        headers={"X-Api-Key": "ci-api-key"},
    )
    assert tampered.status_code == 409


def test_gateway_release_activation_requires_valid_ed25519_signature(
    db: Session,
    tmp_path,
    monkeypatch,
):
    from app.services import gateway_remote_service

    organization = Organization(name="Release Admin Org")
    db.add(organization)
    db.commit()
    admin = _user(
        db,
        email="release-admin@example.com",
        role=Role.ADMIN,
        organization_id=organization.id,
    )
    private_key = Ed25519PrivateKey.generate()
    public_key_path = tmp_path / "release-signing-public.pem"
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    content = _release_archive()
    digest = hashlib.sha256(content).hexdigest()
    signature = base64.b64encode(private_key.sign(digest.encode("utf-8"))).decode("ascii")
    monkeypatch.setattr(gateway_remote_service, "GATEWAY_RELEASE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "gateway_release_signing_public_key_path", str(public_key_path))

    release = upload_app_release(
        db,
        admin,
        UploadFile(filename="release.tar.gz", file=io.BytesIO(content)),
        version="3.0.0",
        mandatory=False,
        channel="stable",
        min_version=None,
        changelog=None,
        signature=signature,
    )
    assert toggle_app_release(db, admin, release.id, True).is_active is True

    (tmp_path / release.artifact_path).write_bytes(content + b"tampered")
    release.is_active = False
    db.commit()
    with pytest.raises(HTTPException) as tampered:
        toggle_app_release(db, admin, release.id, True)
    assert tampered.value.status_code == 409


def test_gateway_release_archive_rejects_traversal(
    db: Session,
    tmp_path,
    monkeypatch,
):
    from app.services import gateway_remote_service

    organization = Organization(name="Traversal Admin Org")
    db.add(organization)
    db.commit()
    admin = _user(
        db,
        email="traversal-admin@example.com",
        role=Role.ADMIN,
        organization_id=organization.id,
    )
    monkeypatch.setattr(gateway_remote_service, "GATEWAY_RELEASE_DIR", str(tmp_path))
    signature = base64.b64encode(b"x" * 64).decode("ascii")

    with pytest.raises(HTTPException) as unsafe:
        upload_app_release(
            db,
            admin,
            UploadFile(filename="release.tar.gz", file=io.BytesIO(_release_archive("../escape"))),
            version="4.0.0",
            mandatory=False,
            channel="stable",
            min_version=None,
            changelog=None,
            signature=signature,
        )
    assert unsafe.value.status_code == 422
    assert not (tmp_path.parent / "escape").exists()
