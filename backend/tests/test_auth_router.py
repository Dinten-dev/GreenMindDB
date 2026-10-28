"""Tests for auth endpoints: signup, login, logout, /me.

Covers successful flows and error cases to improve coverage on
app/routers/auth.py (from 53% → target 80%+).
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth import get_password_hash, pwd_context
from app.models.user import EmailVerification, Organization, Role, User


class TestSignup:
    def test_free_signup_rejected(self, client):
        response = client.post(
            "/api/v1/auth/signup",
            json={"email": "new@example.com", "password": "ValidPass1", "name": "New User"},
        )
        assert response.status_code == 422
        assert response.headers["cache-control"] == "private, no-store"

    def test_weak_password_rejected(self, client):
        response = client.post(
            "/api/v1/auth/signup",
            json={"invitation_token": "a" * 64, "password": "Ab1", "name": "New User"},
        )
        assert response.status_code == 422

    def test_existing_verification_link_remains_functional(self, client, db):
        import hashlib
        from datetime import timedelta

        user = User(
            email="verify@example.com",
            password_hash=get_password_hash("ValidPass1"),
            role=Role.OWNER,
            is_verified=False,
        )
        db.add(user)
        db.flush()
        raw = "a" * 32
        db.add(
            EmailVerification(
                user_id=user.id,
                token=hashlib.sha256(raw.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        db.commit()
        response = client.post("/api/v1/auth/verify-email", json={"token": raw})
        assert response.status_code == 200
        db.refresh(user)
        assert user.is_verified


class TestLogin:
    """POST /api/v1/auth/login – authentication."""

    def test_login_success(self, client: TestClient, db: Session):
        """Happy path: valid credentials → 200 + cookie."""
        org = Organization(name="Login Org")
        db.add(org)
        db.flush()

        user = User(
            email="login@example.com",
            password_hash=get_password_hash("ValidPass1"),
            role=Role.MEMBER,
            is_active=True,
            is_verified=True,
            organization_id=org.id,
        )
        db.add(user)
        db.commit()

        response = client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": "ValidPass1"},
        )
        assert response.status_code == 200
        assert response.json()["user"]["email"] == "login@example.com"
        assert "access_token" in response.cookies

    def test_login_migrates_legacy_bcrypt_hash(self, client: TestClient, db: Session):
        user = User(
            email="legacy-login@example.com",
            password_hash=pwd_context.handler("bcrypt").hash("LegacyPass1"),
            role=Role.MEMBER,
            is_active=True,
            is_verified=True,
        )
        db.add(user)
        db.commit()

        response = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": "LegacyPass1"},
        )

        assert response.status_code == 200
        db.refresh(user)
        assert user.password_hash.startswith("$bcrypt-sha256$")

    def test_login_wrong_password(self, client: TestClient, db: Session):
        """Boundary: correct email, wrong password → 401."""
        user = User(
            email="wrongpw@example.com",
            password_hash=get_password_hash("CorrectPass1"),
            role=Role.MEMBER,
            is_active=True,
            is_verified=True,
        )
        db.add(user)
        db.commit()

        response = client.post(
            "/api/v1/auth/login",
            json={"email": "wrongpw@example.com", "password": "WrongPass1"},
        )
        assert response.status_code == 401
        assert "Invalid email or password" in response.json()["detail"]

    def test_login_nonexistent_user(self, client: TestClient):
        """Boundary: email does not exist → 401 (not 404, to prevent enumeration)."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": "AnyPass1"},
        )
        assert response.status_code == 401

    def test_login_inactive_user(self, client: TestClient, db: Session):
        """Boundary: account disabled → 403."""
        user = User(
            email="inactive@example.com",
            password_hash=get_password_hash("ValidPass1"),
            role=Role.MEMBER,
            is_active=False,
            is_verified=True,
        )
        db.add(user)
        db.commit()

        response = client.post(
            "/api/v1/auth/login",
            json={"email": "inactive@example.com", "password": "ValidPass1"},
        )
        assert response.status_code == 403
        assert "Account disabled" in response.json()["detail"]

    def test_login_unverified_user(self, client: TestClient, db: Session):
        """Boundary: email not verified → 403."""
        user = User(
            email="unverified@example.com",
            password_hash=get_password_hash("ValidPass1"),
            role=Role.MEMBER,
            is_active=True,
            is_verified=False,
        )
        db.add(user)
        db.commit()

        response = client.post(
            "/api/v1/auth/login",
            json={"email": "unverified@example.com", "password": "ValidPass1"},
        )
        assert response.status_code == 403
        assert "Email not verified" in response.json()["detail"]


class TestResendVerification:
    """POST /api/v1/auth/resend-verification – generic recovery flow."""

    def test_resend_rotates_token_without_enumerating_users(
        self, client: TestClient, db: Session, mocker
    ):
        user = User(
            email="resend@example.com",
            password_hash=get_password_hash("ValidPass1"),
            role=Role.MEMBER,
            is_active=True,
            is_verified=False,
        )
        db.add(user)
        db.flush()
        existing = EmailVerification(
            user_id=user.id,
            token="a" * 32,
            expires_at=datetime.now(UTC),
        )
        db.add(existing)
        db.commit()
        send = mocker.patch("app.routers.auth.EmailService.send_verification_email")

        known = client.post(
            "/api/v1/auth/resend-verification",
            json={"email": user.email},
        )
        unknown = client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "missing@example.com"},
        )

        assert known.status_code == unknown.status_code == 200
        assert known.json() == unknown.json()
        db.refresh(existing)
        assert existing.used_at is not None
        assert db.query(EmailVerification).filter_by(user_id=user.id).count() == 2
        send.assert_called_once()


class TestLogout:
    """POST /api/v1/auth/logout – cookie clearing."""

    def test_logout_clears_cookie(self, client: TestClient):
        """Logout returns 200 and clears the access_token cookie."""
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 200
        assert response.json()["detail"] == "Logged out"


class TestMe:
    """GET /api/v1/auth/me – current user info."""

    def test_me_authenticated(self, client: TestClient, admin_token: str):
        """Authenticated user gets their profile."""
        response = client.get(
            "/api/v1/auth/me",
            cookies={"access_token": admin_token},
        )
        assert response.status_code == 200
        assert response.json()["email"] == "ci-admin@test.com"

    def test_me_unauthenticated(self, client: TestClient):
        """No token → 401."""
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
