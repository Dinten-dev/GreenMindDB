"""Invitation lifecycle, session revocation, and bounded gateway compatibility."""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import gateway_auth
from app.auth import ALGORITHM, SECRET_KEY, create_access_token, create_session_token
from app.config import settings
from app.models.access import AccessInvitation, AuthSession
from app.models.form_submission import FormSubmission
from app.models.master import Gateway
from app.models.user import Role, User
from app.schemas.auth import SignupRequest
from app.services.email_service import EmailService, dispatch_email
from app.services.invitations import redeem_invitation


@pytest.fixture
def invitation(client, db, admin_token, monkeypatch):
    monkeypatch.setattr(settings, "management_admin_emails", "ci-admin@test.com")
    submission = FormSubmission(
        form_type="early_access", name="Invitee", email="Invited@Example.com"
    )
    db.add(submission)
    db.commit()
    sent = []
    monkeypatch.setattr(
        EmailService,
        "send_invitation_email",
        lambda email, token: sent.append((email, token)) or True,
    )
    response = client.post("/api/v1/admin/invitations", json={"submission_id": str(submission.id)})
    assert response.status_code == 201, response.text
    row = db.get(AccessInvitation, uuid.UUID(response.json()["id"]))
    assert sent[0][0] == "invited@example.com"
    assert len(bytes.fromhex(sent[0][1])) == 32
    assert row.token_hash == hashlib.sha256(sent[0][1].encode()).hexdigest()
    assert sent[0][1] not in response.text and row.token_hash not in response.text
    return SimpleNamespace(row=row, token=sent[0][1], sent=sent, submission=submission)


def signup(client, token, **extra):
    return client.post(
        "/api/v1/auth/signup",
        json={"invitation_token": token, "name": "Invitee", "password": "SecurePass123", **extra},
    )


def test_invited_owner_verified_once_and_email_bound(client, db, invitation):
    rejected = signup(client, invitation.token, email="attacker@example.com")
    assert rejected.status_code == 422
    response = signup(client, invitation.token)
    assert response.status_code == 201
    assert response.json()["user"]["email"] == "invited@example.com"
    assert response.json()["user"]["role"] == "owner"
    assert "access_token" not in response.cookies
    assert signup(client, invitation.token).status_code == 400
    user = db.query(User).filter_by(email="invited@example.com").one()
    assert user.is_verified and user.role == Role.OWNER
    assert db.query(AuthSession).filter_by(user_id=user.id).count() == 0


@pytest.mark.parametrize("state", ["expired", "revoked", "pending", "failed"])
def test_invalid_invitation_states_rejected(client, db, invitation, state):
    if state == "expired":
        invitation.row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif state == "revoked":
        invitation.row.revoked_at = datetime.now(UTC)
    else:
        invitation.row.delivery_status = state
    db.commit()
    assert signup(client, invitation.token).status_code == 400
    assert db.query(User).filter_by(email="invited@example.com").count() == 0


@pytest.mark.parametrize(
    "verified,active,role,expected",
    [
        (False, True, Role.MEMBER, 201),
        (True, True, Role.OWNER, 409),
        (False, False, Role.OWNER, 409),
        (False, True, Role.ADMIN, 201),
    ],
)
def test_existing_accounts_preserved(client, db, invitation, verified, active, role, expected):
    existing = User(
        email="invited@example.com",
        name="Existing",
        password_hash="old",
        role=role,
        is_verified=verified,
        is_active=active,
    )
    db.add(existing)
    db.commit()
    identity = existing.id
    assert signup(client, invitation.token).status_code == expected
    db.expire_all()
    assert db.query(User).filter_by(email="invited@example.com").one().id == identity
    assert db.get(User, identity).role == role
    if expected == 409:
        assert db.get(User, identity).password_hash == "old"
        assert db.get(AccessInvitation, invitation.row.id).redeemed_at is None


def test_parallel_acceptance_is_exactly_once(db, invitation):
    bind = db.get_bind()
    token = invitation.token
    db.rollback()

    def accept():
        with Session(bind) as other:
            try:
                redeem_invitation(
                    other,
                    SignupRequest(
                        invitation_token=token, name="Parallel", password="SecurePass123"
                    ),
                )
                return 201
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: accept(), range(2)))
    assert sorted(results) == [201, 400]
    db.expire_all()
    assert db.query(User).filter_by(email="invited@example.com").count() == 1


def test_unverified_mixed_case_account_can_sign_in_after_redemption(client, db, invitation):
    existing = User(
        email="Invited@Example.com", password_hash="old", role=Role.MEMBER, is_verified=False
    )
    db.add(existing)
    db.commit()
    identity = existing.id
    assert signup(client, invitation.token).status_code == 201
    response = client.post(
        "/api/v1/auth/login", json={"email": "Invited@Example.com", "password": "SecurePass123"}
    )
    assert response.status_code == 200 and response.json()["user"]["id"] == str(identity)


def test_resend_revokes_before_dispatch_and_failure_is_visible(client, db, invitation, monkeypatch):
    old_id = invitation.row.id

    def failed(email, token):
        with Session(db.get_bind()) as check:
            assert check.get(AccessInvitation, old_id).revoked_at is not None
        return False

    monkeypatch.setattr(EmailService, "send_invitation_email", failed)
    response = client.post(f"/api/v1/admin/invitations/{old_id}/resend")
    assert response.status_code == 502
    assert response.headers["cache-control"] == "private, no-store"
    assert signup(client, invitation.token).status_code == 400
    items = client.get("/api/v1/admin/access-requests?page_size=1").json()
    assert items["total"] == 1
    latest = items["items"][0]["invitation"]
    assert latest["delivery_status"] == "failed" and latest["delivery_error"]
    captured = []
    monkeypatch.setattr(
        EmailService, "send_invitation_email", lambda email, token: captured.append(token) or True
    )
    resent = client.post(f"/api/v1/admin/invitations/{latest['id']}/resend")
    assert resent.status_code == 201
    assert signup(client, captured[0]).status_code == 201


def test_invitation_admin_and_exact_origin_required(client, db, invitation, monkeypatch):
    path = "/api/v1/admin/invitations"
    payload = {"submission_id": str(invitation.submission.id)}
    assert (
        client.post(path, json=payload, headers={"Origin": "https://evil.invalid"}).status_code
        == 403
    )
    client.headers.pop("Origin")
    assert client.post(path, json=payload).status_code == 403
    client.headers["Origin"] = settings.frontend_url
    monkeypatch.setattr(settings, "management_admin_emails", "")
    assert client.post(path, json=payload).status_code == 403
    assert client.get("/api/v1/admin/access-requests").status_code == 403
    client.cookies.clear()
    assert client.get("/api/v1/admin/access-requests").status_code == 401


def test_invitation_lifetime_pagination_and_create_conflict(client, db, invitation):
    created = invitation.row.created_at.replace(tzinfo=UTC)
    expires = invitation.row.expires_at.replace(tzinfo=UTC)
    assert 47.99 < (expires - created).total_seconds() / 3600 <= 48.01
    assert (
        client.post(
            "/api/v1/admin/invitations", json={"submission_id": str(invitation.submission.id)}
        ).status_code
        == 409
    )
    assert client.get("/api/v1/admin/access-requests?page=0").status_code == 422
    assert client.get("/api/v1/admin/access-requests?page_size=101").status_code == 422
    assert client.get("/api/v1/admin/access-requests?page=2&page_size=1").json()["items"] == []


def test_logout_replay_cookie_and_bearer_and_cross_subject(client, db, admin_token):
    assert client.get("/api/v1/auth/me").status_code == 200
    session = db.query(AuthSession).one()
    other = User(email="other@example.com", password_hash="none", role=Role.OWNER, is_verified=True)
    db.add(other)
    db.commit()
    swapped = create_access_token({"sub": str(other.id), "sid": str(session.id)})
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + swapped}).status_code
        == 401
    )
    result = client.post("/api/v1/auth/logout")
    assert result.status_code == 200
    assert "Max-Age=0" in result.headers["set-cookie"]
    assert (
        client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer " + admin_token}
        ).status_code
        == 401
    )
    client.cookies.set("access_token", admin_token)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_revokes_distinct_cookie_and_bearer_sessions(client, db, admin_token):
    user = db.query(User).one()
    second = create_session_token(db, user.id)
    db.commit()
    assert (
        client.post(
            "/api/v1/auth/logout", headers={"Authorization": "Bearer " + second}
        ).status_code
        == 200
    )
    assert all(row.revoked_at is not None for row in db.query(AuthSession).all())


def test_sidless_token_and_database_errors_fail_closed(client, db, admin_token, monkeypatch):
    user = db.query(User).one()
    old = jwt.encode(
        {"sub": str(user.id), "exp": datetime.now(UTC) + timedelta(hours=1)},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + old}).status_code == 401
    )

    def unavailable(*args, **kwargs):
        raise OperationalError("unavailable", {}, None)

    monkeypatch.setattr(db, "query", unavailable)
    assert (
        client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer " + admin_token}
        ).status_code
        == 503
    )


@pytest.mark.parametrize("route", ["zone", "sensor"])
def test_idle_websocket_closes_on_session_revocation(
    client, db, setup_test_data, monkeypatch, route
):
    from app.routers import ws

    monkeypatch.setattr(ws, "SESSION_CHECK_INTERVAL", 0.05)
    user = User(
        email="socket@example.com",
        password_hash="none",
        role=Role.OWNER,
        is_verified=True,
        organization_id=setup_test_data["org"].id,
    )
    db.add(user)
    db.flush()
    token = create_session_token(db, user.id)
    db.commit()
    client.cookies.set("access_token", token)
    with client.websocket_connect(
        f"/api/v1/ws/{route}/{setup_test_data[route].id}",
        headers={"Origin": settings.cors_origins[0]},
    ) as socket:
        assert db.get_bind().pool.checkedout() == 0
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert socket.receive()["code"] == 1008


def test_failed_legacy_checks_ignore_spoofed_proxy_headers(
    client, db, setup_test_data, monkeypatch
):
    setup_test_data["gateway"].api_key_hash = gateway_auth.get_password_hash("legacy-test-secret")
    db.commit()
    calls = []
    monkeypatch.setattr(gateway_auth, "verify_password", lambda *args: calls.append(1) or False)
    modern = Gateway(
        hardware_id="modern",
        zone_id=setup_test_data["zone"].id,
        api_key_hash="sha256$" + "0" * 64,
        is_active=True,
    )
    db.add(modern)
    db.commit()
    for i in range(7):
        response = client.post(
            "/api/v1/ingest",
            json={
                "gateway_serial": setup_test_data["gateway"].hardware_id,
                "measurement_id": str(uuid.uuid4()),
                "readings": [],
            },
            headers={
                "X-Api-Key": f"bad-{i}",
                "X-Forwarded-For": f"192.0.2.{i}",
                "X-Real-IP": f"192.0.2.{i}",
                "Forwarded": f"for=192.0.2.{i}",
            },
        )
        assert response.status_code == (401 if i < 5 else 429)
    assert len(calls) == 5  # Modern hashes were excluded before bcrypt.


def test_prefixed_old_hash_checks_are_throttled(db, setup_test_data, monkeypatch):
    setup_test_data["gateway"].api_key_hash = gateway_auth.get_password_hash("legacy-test-secret")
    db.commit()
    calls = []
    monkeypatch.setattr(gateway_auth, "verify_password", lambda *args: calls.append(1) or False)
    for i in range(6):
        key = f"gmk_{setup_test_data['gateway'].id.hex}_{str(i).zfill(32)}"
        with pytest.raises(HTTPException) as error:
            gateway_auth.authenticate_gateway_api_key(db, key, "peer")
        assert error.value.status_code == (401 if i < 5 else 429)
    assert len(calls) == 5


def test_direct_legacy_verifier_cannot_bypass_throttle(setup_test_data, monkeypatch):
    calls = []
    monkeypatch.setattr(gateway_auth, "verify_password", lambda *args: calls.append(1) or False)
    gateway = setup_test_data["gateway"]
    gateway.api_key_hash = gateway_auth.get_password_hash("legacy-test-secret")
    for _ in range(5):
        assert not gateway_auth.verify_gateway_api_key(gateway, "wrong")
    with pytest.raises(HTTPException) as error:
        gateway_auth.verify_gateway_api_key(gateway, "wrong-again")
    assert error.value.status_code == 429 and len(calls) == 5


def test_smtp_acceptance_failure_and_staging_recipient_boundary(monkeypatch, tmp_path):
    secret = tmp_path / "smtp-password"
    secret.write_text("test-secret")
    monkeypatch.setattr(settings, "email_transport", "smtp")
    monkeypatch.setattr(settings, "smtp_security", "ssl")
    monkeypatch.setattr(settings, "smtp_port", 465)
    monkeypatch.setattr(settings, "smtp_username", "info@green-mind.ch")
    monkeypatch.setattr(settings, "smtp_password_file", str(secret))
    calls = []

    class SMTP:
        def __init__(self, host, port, **kwargs):
            assert host == "asmtp.mail.hostpoint.ch" and port == 465
            assert kwargs["context"].check_hostname

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, user, password):
            calls.append((user, bool(password)))

        def send_message(self, message):
            return {}

    monkeypatch.setattr("app.services.email_service.smtplib.SMTP_SSL", SMTP)
    assert dispatch_email("traver.dinten@outlook.com", "Test", text="Test")
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "staging_email_to", "traver.dinten@outlook.com")
    assert not dispatch_email("other@example.com", "Test", text="Test")
    assert len(calls) == 1
    monkeypatch.setattr(SMTP, "send_message", lambda *args: {"rejected": (550, b"rejected")})
    assert not dispatch_email("traver.dinten@outlook.com", "Test", text="Test")
