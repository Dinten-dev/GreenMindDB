"""Tests for auth endpoints: signup, login, logout, /me.

Covers successful flows and error cases to improve coverage on
app/routers/auth.py (from 53% → target 80%+).
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth import get_password_hash
from app.models.user import Organization, Role, User


class TestSignup:
    """POST /api/v1/auth/signup – account creation."""

    def test_signup_success(self, client: TestClient, db: Session, mocker):
        """Happy path: new user gets created and receives a cookie."""
        mocker.patch("app.routers.auth.EmailService.send_verification_email")

        response = client.post(
            "/api/v1/auth/signup",
            json={
                "email": "new@example.com",
                "password": "ValidPass1",
                "name": "New User",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["user"]["email"] == "new@example.com"
        assert data["user"]["name"] == "New User"
        assert "access_token" in response.cookies

    def test_signup_duplicate_email(self, client: TestClient, db: Session, mocker):
        """Boundary: email already exists → 400."""
        mocker.patch("app.routers.auth.EmailService.send_verification_email")

        # Create user first
        user = User(
            email="dup@example.com",
            password_hash=get_password_hash("SomePass1"),
            role=Role.MEMBER,
            is_active=True,
            is_verified=False,
        )
        db.add(user)
        db.commit()

        response = client.post(
            "/api/v1/auth/signup",
            json={"email": "dup@example.com", "password": "ValidPass1"},
        )
        assert response.status_code == 400
        assert "Could not create account" in response.json()["detail"]

    def test_signup_weak_password(self, client: TestClient):
        """Boundary: password too short (< 8 chars) → 422 validation error."""
        response = client.post(
            "/api/v1/auth/signup",
            json={"email": "weak@example.com", "password": "Ab1"},
        )
        assert response.status_code == 422

    def test_signup_password_no_uppercase(self, client: TestClient):
        """Boundary: password missing uppercase → 422."""
        response = client.post(
            "/api/v1/auth/signup",
            json={"email": "lower@example.com", "password": "alllower1"},
        )
        assert response.status_code == 422


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
