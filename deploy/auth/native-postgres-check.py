"""Synthetic PostgreSQL proof; execute only against a disposable gm_auth_check_* DB."""

import importlib.util
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

assert make_url(os.environ["DATABASE_URL"]).database.startswith("gm_auth_check_")

from app.auth import create_session_token, decode_token, get_password_hash, session_is_valid
from app.database import Base, engine
from app.models.access import AccessInvitation, AuthSession
from app.models.form_submission import FormSubmission
from app.models.user import EmailVerification, Organization, Role, User
from app.schemas.auth import SignupRequest
from app.services.email_service import EmailService
from app.services.invitations import redeem_invitation, send_invitation

Base.metadata.create_all(engine, tables=[Organization.__table__, User.__table__, FormSubmission.__table__, EmailVerification.__table__])
with Session(engine) as db:
    admin = User(email="admin@example.com", name="Synthetic administrator", role=Role.ADMIN, password_hash=get_password_hash("SecurePass123"), is_verified=True, is_active=True)
    db.add(admin)
    db.commit()
    admin_id = admin.id
    original = (admin.email, admin.password_hash, admin.role, admin.is_verified)

spec = importlib.util.spec_from_file_location("migration0024", Path("alembic/versions/0024_invitations_sessions.py"))
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
with engine.begin() as connection:
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
with Session(engine) as db:
    admin = db.get(User, admin_id)
    assert original == (admin.email, admin.password_hash, admin.role, admin.is_verified)

tokens = []
EmailService.send_invitation_email = staticmethod(lambda email, token: tokens.append(token) or True)


def invite(email):
    with Session(engine) as db:
        submission = FormSubmission(form_type="early_access", name="Synthetic invitee", email=email)
        db.add(submission)
        db.commit()
        row, sent = send_invitation(db, submission.id, db.get(User, admin_id))
        assert sent
        return row.id, tokens[-1]


def accept(token):
    with Session(engine) as db:
        try:
            user = redeem_invitation(db, SignupRequest(invitation_token=token, name="Synthetic invitee", password="SecurePass123"))
            return 201, str(user.id)
        except HTTPException as error:
            return error.status_code, None


identity, token = invite("parallel@example.com")
barrier = threading.Barrier(2)
def parallel_accept(_):
    barrier.wait(timeout=5)
    return accept(token)
with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(parallel_accept, range(2)))
assert sorted(row[0] for row in results) == [201, 400]
assert accept(token)[0] == 400
with Session(engine) as db:
    assert db.scalar(select(func.count()).select_from(User).where(User.email == "parallel@example.com")) == 1
    claimed = db.get(AccessInvitation, identity)
    assert claimed.redeemed_at and claimed.redeemed_by
    new_user = db.get(User, claimed.redeemed_by)
    assert new_user.is_verified and new_user.role == Role.OWNER
    user_id = new_user.id
    session_token = create_session_token(db, user_id)
    db.commit()
payload = decode_token(session_token)
with Session(engine) as db:
    db.execute(__import__('sqlalchemy').text("SET TRANSACTION READ ONLY"))
    assert session_is_valid(db, payload)
with Session(engine) as db:
    db.execute(update(AuthSession).where(AuthSession.id == uuid.UUID(payload["sid"])).values(revoked_at=datetime.now(UTC)))
    db.commit()
with Session(engine) as db:
    assert not session_is_valid(db, payload)

expired_id, expired_token = invite("expired@example.com")
with Session(engine) as db:
    db.get(AccessInvitation, expired_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
assert accept(expired_token)[0] == 400

old_id, old_token = invite("resend@example.com")
with Session(engine) as db:
    old = db.get(AccessInvitation, old_id)
    row, accepted = send_invitation(db, old.submission_id, db.get(User, admin_id), old_id)
    assert accepted
    new_token = tokens[-1]
assert accept(old_token)[0] == 400
assert accept(new_token)[0] == 201

# Two submissions bound to the same address must still produce one account.
_, first = invite("collision@example.com")
_, second = invite("collision@example.com")
barrier = threading.Barrier(2)
def collision(candidate):
    barrier.wait(timeout=5)
    return accept(candidate)
with ThreadPoolExecutor(max_workers=2) as pool:
    outcomes = list(pool.map(collision, [first, second]))
assert sorted(row[0] for row in outcomes) == [201, 409]
with Session(engine) as db:
    assert db.scalar(select(func.count()).select_from(User).where(User.email == "collision@example.com")) == 1
    existing_admin = User(email="pending-admin@example.com", name="Existing administrator", role=Role.ADMIN, password_hash="pending", is_verified=False, is_active=True)
    db.add(existing_admin)
    db.commit()
    existing_admin_id = existing_admin.id
_, existing_admin_token = invite("pending-admin@example.com")
assert accept(existing_admin_token)[0] == 201
with Session(engine) as db:
    assert db.get(User, existing_admin_id).role == Role.ADMIN
    assert db.get(User, existing_admin_id).is_verified

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.master import Gateway, Zone

Base.metadata.create_all(engine)
settings.cookie_secure = True
settings.cookie_domain = ""
settings.frontend_url = "https://test.green-mind.ch"
settings.cors_origins = [settings.frontend_url]
with Session(engine) as db:
    organization = Organization(name="Synthetic native test")
    db.add(organization)
    db.flush()
    owner = db.get(User, user_id)
    owner.organization_id = organization.id
    zone = Zone(name="Synthetic test zone", organization_id=organization.id)
    db.add(zone)
    db.flush()
    gateway = Gateway(zone_id=zone.id, hardware_id="auth-native-synthetic-gateway", api_key_hash=get_password_hash("synthetic-old-gateway-key"), is_active=True)
    db.add(gateway)
    db.commit()
    zone_id = zone.id
with TestClient(app, base_url=settings.frontend_url) as client:
    credentials = {"email": "parallel@example.com", "password": "SecurePass123"}
    login = client.post("/api/v1/auth/login", json=credentials)
    assert login.status_code == 200
    cookie_header = login.headers["set-cookie"].lower()
    assert "httponly" in cookie_header and "secure" in cookie_header and "samesite=lax" in cookie_header
    bearer = login.cookies["access_token"]
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + bearer}).status_code == 401
    client.cookies.set("access_token", bearer)
    assert client.get("/api/v1/sensors").status_code == 401
    assert client.post("/api/v1/auth/signup", json={"email": "free@example.com", "password": "SecurePass123", "name": "Free signup"}).status_code == 422
    client.cookies.clear()
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 200
    with client.websocket_connect(f"wss://test.green-mind.ch/api/v1/ws/zone/{zone_id}", headers={"Origin": settings.frontend_url}) as socket:
        assert engine.pool.checkedout() == 0
        revoked_at = time.monotonic()
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert socket.receive()["code"] == 1008
        websocket_close_seconds = time.monotonic() - revoked_at
        assert websocket_close_seconds < 30
    payload = {"gateway_serial": "auth-native-synthetic-gateway", "measurement_id": str(uuid.uuid4()), "readings": []}
    headers = {"X-Api-Key": "synthetic-old-gateway-key"}
    ingest = client.post("/api/v1/ingest", json=payload, headers=headers)
    assert ingest.status_code == 201, ingest.status_code
    assert client.post("/api/v1/ingest", json=payload, headers=headers).json()["status"] == "duplicate"

engine.dispose()
print(json.dumps({"passed": True, "migration_preserves_users": True, "parallel_acceptance": [201, 400], "parallel_same_email": [201, 409], "expiry_and_resend": True, "read_only_session_check": True, "cross_connection_revocation": True, "http_logout_replay_rejected": True, "cookie_flags_preserved": True, "websocket_close_seconds": round(websocket_close_seconds, 3), "legacy_gateway_ingest": True, "free_signup_blocked": True, "actual_emails_sent": 0}))
