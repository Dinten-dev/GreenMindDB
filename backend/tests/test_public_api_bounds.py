"""Cost, parser ordering and compatibility regressions for S1/S2."""

import asyncio
from uuid import uuid4

import pytest
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import gateway_auth as auth
from app.gateway_key_migration import enroll_key, read_secret
from app.models.master import Gateway
from app.request_limits import JSON_BYTES, RequestBodyLimitMiddleware
from app.schemas.evaluation import PlantEvaluationCreate
from app.schemas.observation import ObservationSessionCreate, PlantObservationCreate


@pytest.mark.parametrize("fleet", [1, 100])
def test_unhinted_invalid_keys_never_verify_fleet_hashes(db, setup_test_data, monkeypatch, fleet):
    seed = setup_test_data["gateway"]
    seed.api_key_hash = auth.get_password_hash("legacy-secret")
    db.add_all(
        [
            Gateway(zone_id=seed.zone_id, hardware_id=f"legacy-{i}", api_key_hash=seed.api_key_hash)
            for i in range(fleet)
        ]
    )
    db.commit()
    calls = []
    monkeypatch.setattr(auth, "verify_password", lambda *args: calls.append(1) or False)
    for key in ["unknown", "gmk_broken", f"gmk_{uuid4().hex}_{'x' * 32}"]:
        with pytest.raises(HTTPException) as error:
            auth.authenticate_gateway_api_key(db, key)
        assert error.value.status_code == 401
    assert calls == []
    with pytest.raises(HTTPException):
        auth.authenticate_gateway_api_key(db, "unknown", gateway_id=seed.id)
    assert calls == [1]


def test_legacy_identity_cache_and_controlled_enrollment(db, setup_test_data, monkeypatch):
    gateway = setup_test_data["gateway"]
    gateway.api_key_hash = auth.get_password_hash("legacy-secret")
    db.commit()
    original = auth.verify_password
    calls = []
    monkeypatch.setattr(auth, "verify_password", lambda *args: calls.append(1) or original(*args))
    assert (
        auth.authenticate_gateway_api_key(db, "legacy-secret", gateway_id=gateway.id).id
        == gateway.id
    )
    assert (
        auth.authenticate_gateway_api_key(db, "legacy-secret", gateway_id=gateway.id).id
        == gateway.id
    )
    assert calls == [1]
    with pytest.raises(HTTPException):
        auth.authenticate_gateway_api_key(db, "legacy-secret", gateway_id=uuid4())
    auth.clear_gateway_auth_cache()
    assert enroll_key(db, gateway.id, "legacy-secret")
    db.commit()
    assert not enroll_key(db, gateway.id, "legacy-secret")
    calls.clear()
    auth.clear_gateway_auth_cache()
    assert auth.authenticate_gateway_api_key(db, "legacy-secret").id == gateway.id
    assert calls == []
    gateway.is_active = False
    db.commit()
    with pytest.raises(HTTPException) as rejected:
        auth.authenticate_gateway_api_key(db, "legacy-secret")
    assert rejected.value.status_code == 403


def test_successes_count_against_global_bcrypt_budget(db, setup_test_data, monkeypatch):
    gateway = setup_test_data["gateway"]
    gateway.api_key_hash = "$2b$synthetic-test-hash"
    monkeypatch.setattr(auth, "verify_password", lambda *args: True)
    for i in range(30):
        assert auth.verify_gateway_api_key(gateway, "secret", f"peer-{i}")
    with pytest.raises(HTTPException) as error:
        auth.verify_gateway_api_key(gateway, "secret", "another-peer")
    assert error.value.status_code == 429


def test_busy_verifier_does_not_queue_request(db, setup_test_data):
    gateway = setup_test_data["gateway"]
    gateway.api_key_hash = "$2b$synthetic-test-hash"
    with auth._legacy_lock:
        with pytest.raises(HTTPException) as error:
            auth.verify_gateway_api_key(gateway, "secret")
    assert error.value.status_code == 429


def test_enrollment_preserves_wrong_key_disabled_and_truncation_guards(db, setup_test_data):
    gateway = setup_test_data["gateway"]
    old = gateway.api_key_hash = auth.get_password_hash("legacy-secret")
    db.commit()
    with pytest.raises(ValueError):
        enroll_key(db, gateway.id, "wrong-key")
    assert gateway.api_key_hash == old
    gateway.is_active = False
    with pytest.raises(ValueError):
        enroll_key(db, gateway.id, "legacy-secret")
    gateway.is_active = True
    gateway.api_key_hash = "$2b$synthetic-test-hash"
    with pytest.raises(ValueError, match="Truncated"):
        enroll_key(db, gateway.id, "a" * 73)


def test_secret_file_is_private_and_not_a_symlink(tmp_path):
    secret = tmp_path / "credential"
    secret.write_text("synthetic-secret\n")
    secret.chmod(0o600)
    assert read_secret(secret) == "synthetic-secret"
    secret.chmod(0o644)
    with pytest.raises(ValueError):
        read_secret(secret)
    secret.chmod(0o600)
    secret.write_bytes(b"a\n" * 200)
    with pytest.raises(ValueError):
        read_secret(secret)
    link = tmp_path / "link"
    link.symlink_to(secret)
    with pytest.raises(OSError):
        read_secret(link)


def test_public_firmware_route_uses_identity_bound_legacy_auth(client, db, setup_test_data):
    gateway = setup_test_data["gateway"]
    gateway.api_key_hash = auth.get_password_hash("legacy-secret")
    db.commit()
    assert (
        client.get("/api/v1/firmware/sync", headers={"X-Api-Key": "legacy-secret"}).status_code
        == 401
    )
    response = client.get(
        "/api/v1/firmware/sync",
        headers={"X-Api-Key": "legacy-secret", "X-Gateway-Id": str(gateway.id)},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/public/observe/session",
        "/api/v1/public/observe/session/invalid/observations",
        "/api/v1/public/evaluate/session/invalid/evaluations",
    ],
)
def test_oversized_json_rejected_before_decoder_or_session(client, monkeypatch, path):
    from starlette.requests import Request

    async def forbidden(*args):
        raise AssertionError("JSON parser must not run")

    monkeypatch.setattr(Request, "json", forbidden)
    assert (
        client.post(
            path, content=b"[" + b" " * JSON_BYTES, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )


def run_asgi(app, chunks, *, path="/api/v1/public/observe/session", headers=()):
    async def run():
        events = iter(
            [
                {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
                for i, c in enumerate(chunks)
            ]
        )
        sent = []

        async def receive():
            return next(events, {"type": "http.disconnect"})

        async def send(event):
            sent.append(event)

        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": list(headers),
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
        }
        await app(scope, receive, send)
        return next(e["status"] for e in sent if e["type"] == "http.response.start")

    return asyncio.run(run())


@pytest.mark.parametrize(
    "headers", [[], [(b"content-length", b"1")], [(b"transfer-encoding", b"chunked")]]
)
def test_streamed_and_misleading_lengths_cannot_reach_parser(headers):
    async def forbidden(*args):
        raise AssertionError("Body must be rejected before application")

    assert (
        run_asgi(RequestBodyLimitMiddleware(forbidden), [b"x" * JSON_BYTES, b"x"], headers=headers)
        == 413
    )


def test_exact_byte_limit_is_accepted_and_bad_length_denied():
    from starlette.responses import Response

    async def app(scope, receive, send):
        body = await receive()
        assert len(body["body"]) == JSON_BYTES
        await Response(status_code=204)(scope, receive, send)

    limiter = RequestBodyLimitMiddleware(app)
    assert run_asgi(limiter, [b"x" * JSON_BYTES]) == 204
    assert run_asgi(limiter, [], headers=[(b"content-length", b"bad")]) == 400
    assert (
        run_asgi(limiter, [], headers=[(b"content-length", b"1"), (b"content-length", b"1")]) == 400
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/wav/upload",
        "/api/v1/admin/gateway-app-releases",
        "/api/v1/firmware/upload",
        "/api/v1/public/observe/session/a/observations/b/photos",
    ],
)
def test_multipart_streams_have_their_own_preparse_caps(path):
    app = FastAPI()
    called = []

    @app.post(path)
    async def upload(file: UploadFile = File(...)):
        called.append(1)
        return {"bytes": len(await file.read())}

    app.add_middleware(RequestBodyLimitMiddleware)
    with TestClient(app) as client:
        response = client.post(path, files={"file": ("test.bin", b"x" * (JSON_BYTES + 1))})
    assert response.status_code == 200
    assert response.json()["bytes"] == JSON_BYTES + 1
    called.clear()
    cap = RequestBodyLimitMiddleware(app).policy(path)[0]
    # Multipart parsing must preserve HTTPException(413) for streamed overflow.
    headers = [(b"content-type", b"multipart/form-data; boundary=boundary")]
    body = b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="test.bin"\r\n\r\n'
    assert (
        run_asgi(
            app,
            [body, b"x" * (cap // 2), b"x" * (cap // 2), b"--boundary--\r\n"],
            path=path,
            headers=headers,
        )
        == 413
    )
    assert called == []


def test_public_field_and_list_limits_preserve_normal_values():
    identity = uuid4()
    for spelling in [str(identity), identity.hex, "{" + str(identity) + "}", identity.urn]:
        assert ObservationSessionCreate(public_id=spelling).public_id == spelling
    assert ObservationSessionCreate(public_id="invalid").public_id == "invalid"
    with pytest.raises(ValidationError):
        ObservationSessionCreate(public_id="x" * 129)
    data = {"wellbeing_score": 3, "plant_condition": "healthy", "notes": "x" * 4000}
    assert PlantObservationCreate(**data).notes == data["notes"]
    with pytest.raises(ValidationError):
        PlantObservationCreate(**dict(data, notes="x" * 4001))
    evaluation = {
        "overall_score": 3,
        "color_raw": "light_green",
        "structure_raw": "firm_taut",
        "growth_raw": "normal",
        "water_raw": "optimal",
        "anomalies_raw": ["spots"],
    }
    assert PlantEvaluationCreate(**evaluation)
    with pytest.raises(ValidationError):
        PlantEvaluationCreate(**dict(evaluation, anomalies_raw=["spots"] * 6))
    with pytest.raises(ValidationError):
        PlantEvaluationCreate(**dict(evaluation, detail_notes="x" * 4001))


def test_provisioning_auth_does_not_block_event_loop(monkeypatch):
    import threading
    from types import SimpleNamespace

    from app.routers import provisioning

    owner = threading.get_ident()
    closed = []

    def authenticate(*args, **kwargs):
        assert threading.get_ident() != owner
        assert kwargs["gateway_id"] == "synthetic-id"
        assert args[2] == "192.0.2.1"
        raise HTTPException(401, "Invalid API key")

    async def close(code):
        closed.append(code)

    monkeypatch.setattr(provisioning, "authenticate_gateway_api_key", authenticate)
    socket = SimpleNamespace(client=SimpleNamespace(host="192.0.2.1"), close=close)
    asyncio.run(provisioning.provisioning_ws(socket, None, "synthetic-key", "synthetic-id"))
    assert closed == [1008]


def test_valid_uuid_spellings_and_malformed_ids_keep_service_contract(
    client, db, setup_test_data, tenant_admin_token
):
    import uuid

    headers = {"Authorization": f"Bearer {tenant_admin_token}"}
    plant = client.post(
        "/api/v1/plants",
        json={"name": "UUID control", "zone_id": str(setup_test_data["zone"].id)},
        headers=headers,
    )
    assert plant.status_code == 201
    access = client.post(f"/api/v1/plants/{plant.json()['id']}/observation-access", headers=headers)
    assert access.status_code == 200
    identity = uuid.UUID(access.json()["public_id"])
    for spelling in [str(identity), identity.hex, "{" + str(identity) + "}", identity.urn]:
        assert (
            client.post("/api/v1/public/observe/session", json={"public_id": spelling}).status_code
            == 200
        )
    assert (
        client.post("/api/v1/public/observe/session", json={"public_id": "invalid"}).status_code
        == 400
    )
