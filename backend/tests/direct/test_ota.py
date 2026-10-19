import hashlib
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.direct.auth import issue_token
from app.direct.database import transaction
from app.direct.models import Device
from app.direct.ota_service import PREFIX, PROFILE, OtaSettings, create_app


@pytest.fixture
def ota(pipeline, tmp_path):
    p = pipeline
    p.root = tmp_path / "ota"
    (p.root / "images").mkdir(parents=True)
    p.image = bytearray(b"\xff" * 2048)
    p.image[0] = 0xE9
    p.image[12:14] = b"\x09\x00"
    p.digest = hashlib.sha256(p.image).hexdigest()
    p.path = p.root / "images" / (p.digest + ".bin")
    p.path.write_bytes(p.image)
    p.command = str(uuid.uuid4())
    p.policy = {
        "devices": {
            p.device_id: {
                "command_id": p.command,
                "from_version": "seed",
                "release_id": p.digest,
                "expires_at": time.time() + 3600,
            }
        },
        "releases": {
            p.digest: {
                "profile": PROFILE,
                "version": "trial",
                "file": p.path.name,
                "sha256": p.digest,
                "size": len(p.image),
            }
        },
    }
    p.save = lambda: (p.root / "policy.json").write_text(json.dumps(p.policy))
    p.save()
    p.ota = TestClient(
        create_app(OtaSettings(ota_root=p.root, require_tls=True), p.engine),
        base_url="https://sensor.example",
    )
    p.headers = {"Authorization": "Bearer " + p.token}
    p.check = lambda **kwargs: p.ota.get(
        PREFIX + "/check",
        headers=p.headers,
        params={"profile": PROFILE, "current_version": "seed", **kwargs},
    )
    return p


def test_check_download_and_no_automatic_fleet_offer(ota):
    p = ota
    response = p.check()
    assert response.status_code == 200
    assert response.json()["device_id"] == p.device_id
    assert response.json()["sha256"] == p.digest
    binary = p.ota.get(PREFIX + "/download/" + p.command, headers=p.headers)
    assert binary.status_code == 200 and binary.content == p.image
    assert binary.headers["content-length"] == "2048"
    assert p.check(profile="wrong-board").status_code == 204
    assert p.check(current_version="trial").status_code == 204
    assert p.check(current_version="old-version").status_code == 204
    other = str(uuid.uuid4())
    token, digest = issue_token(other)
    with transaction(p.engine) as db:
        db.add(
            Device(
                id=other,
                organization_id=str(uuid.uuid4()),
                zone_id=str(uuid.uuid4()),
                mode="DIRECT",
                key_hash=digest,
                active=True,
            )
        )
    p.headers = {"Authorization": "Bearer " + token}
    assert p.check().status_code == 204
    assert p.ota.get(PREFIX + "/download/" + p.command, headers=p.headers).status_code == 404


def test_tls_auth_disabled_and_wrong_mode(ota):
    p = ota
    assert p.ota.get(PREFIX + "/check").status_code == 401
    assert (
        p.ota.get("http://sensor.example" + PREFIX + "/check", headers=p.headers).status_code == 400
    )
    with transaction(p.engine) as db:
        db.get(Device, p.device_id).active = False
    assert p.check().status_code == 403
    with transaction(p.engine) as db:
        device = db.get(Device, p.device_id)
        device.active = True
        device.mode = "DUAL"
    assert p.check().status_code == 409


@pytest.mark.parametrize("fault", ["corruption", "truncated", "excess", "symlink", "wrong-chip"])
def test_bad_image_never_served(ota, fault):
    p = ota
    if fault == "symlink":
        destination = p.root / "outside.bin"
        destination.write_bytes(p.image)
        p.path.unlink()
        p.path.symlink_to(destination)
    else:
        data = bytearray(p.image)
        if fault == "corruption":
            data[-1] = 0
        elif fault == "truncated":
            data.pop()
        elif fault == "excess":
            data.append(0)
        else:
            data[12] = 0
            new_hash = hashlib.sha256(data).hexdigest()
            release = p.policy["releases"].pop(p.digest)
            release.update(sha256=new_hash, file=new_hash + ".bin")
            p.policy["releases"][new_hash] = release
            p.policy["devices"][p.device_id]["release_id"] = new_hash
            p.path = p.root / "images" / release["file"]
            p.save()
        p.path.write_bytes(data)
    result = p.ota.get(PREFIX + "/download/" + p.command, headers=p.headers)
    assert result.status_code == 503
    assert result.json()["error"] == "ota_image_integrity"


@pytest.mark.parametrize(
    "field,value", [("file", "../outside.bin"), ("size", 0x300001), ("sha256", "g" * 64)]
)
def test_invalid_catalog_fails_closed(ota, field, value):
    ota.policy["releases"][ota.digest][field] = value
    ota.save()
    assert ota.check().status_code == 503


def test_expired_and_unassigned(ota):
    p = ota
    p.policy["devices"][p.device_id]["expires_at"] = time.time() - 1
    p.save()
    assert p.check().status_code == 204
    assert p.ota.get(PREFIX + "/download/" + p.command, headers=p.headers).status_code == 404
    p.policy["devices"] = {}
    p.save()
    assert p.check().status_code == 204


def test_pool_released_before_binary_read(ota, monkeypatch):
    from app.direct import ota_service

    p = ota
    balance = [0]
    event.listen(p.engine, "checkout", lambda *_: balance.__setitem__(0, balance[0] + 1))
    event.listen(p.engine, "checkin", lambda *_: balance.__setitem__(0, balance[0] - 1))
    real = ota_service.verified_image

    def verify(*args):
        assert balance[0] == 0
        return real(*args)

    monkeypatch.setattr(ota_service, "verified_image", verify)
    assert p.ota.get(PREFIX + "/download/" + p.command, headers=p.headers).status_code == 200


def test_report_identity_size_health_and_audit(ota):
    p = ota
    report = {
        "device_id": p.device_id,
        "command_id": p.command,
        "profile": PROFILE,
        "firmware_version": "trial",
        "status": "booted",
        "error": "",
        "trial_boot": True,
        "slot": "app1",
        "spool_bytes": 0,
        "acknowledgements": 10,
    }

    def send(data):
        return p.ota.post(PREFIX + "/report", headers=p.headers, json=data)

    assert send({**report, "device_id": str(uuid.uuid4())}).status_code == 403
    assert send({**report, "acknowledgements": 9}).status_code == 409
    assert send({**report, "firmware_version": "seed"}).status_code == 409
    assert (
        send(
            {**report, "status": "staged", "firmware_version": "seed", "spool_bytes": 768}
        ).status_code
        == 409
    )
    assert send(report).status_code == 200
    row = json.loads((p.root / "reports" / (p.device_id + ".jsonl")).read_text())
    assert row["status"] == "booted" and row["slot"] == "app1"
    assert p.token not in json.dumps(row)
    assert p.ota.post(PREFIX + "/report", headers=p.headers, content=b"x" * 2049).status_code == 413


@pytest.mark.parametrize("change", ["expired", "cleared", "replaced"])
def test_late_boot_report_does_not_block_future_updates(ota, change):
    p = ota
    command = p.policy["devices"][p.device_id]
    p.policy["commands"] = {p.command: {**command, "device_id": p.device_id}}
    if change == "expired":
        command["expires_at"] = time.time() - 1
    elif change == "cleared":
        p.policy["devices"].clear()
    else:
        p.policy["devices"][p.device_id] = {**command, "command_id": str(uuid.uuid4())}
    p.save()
    report = {
        "device_id": p.device_id,
        "command_id": p.command,
        "profile": PROFILE,
        "firmware_version": "trial",
        "status": "booted",
        "trial_boot": True,
        "slot": "app1",
        "spool_bytes": 0,
        "acknowledgements": 10,
    }
    assert p.ota.post(PREFIX + "/report", headers=p.headers, json=report).status_code == 200
    assert p.ota.get(PREFIX + "/download/" + p.command, headers=p.headers).status_code == 404


def test_historical_report_keeps_device_isolation(ota):
    p = ota
    command = p.policy["devices"].pop(p.device_id)
    p.policy["commands"] = {p.command: {**command, "device_id": str(uuid.uuid4())}}
    p.save()
    report = {
        "device_id": p.device_id,
        "command_id": p.command,
        "profile": PROFILE,
        "firmware_version": "trial",
        "status": "booted",
        "trial_boot": True,
        "slot": "app1",
        "spool_bytes": 0,
        "acknowledgements": 10,
    }
    assert p.ota.post(PREFIX + "/report", headers=p.headers, json=report).status_code == 404
