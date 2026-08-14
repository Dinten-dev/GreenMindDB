"""Unit tests for authentication utilities."""

from datetime import timedelta

from app.auth import (
    create_access_token,
    decode_token,
    get_password_hash,
    verify_password,
)


class TestPasswordHashing:
    """Tests for bcrypt password hashing."""

    def test_hash_and_verify_password(self):
        """A hashed password should verify correctly."""
        plain = "TestPassword123!"
        hashed = get_password_hash(plain)
        assert verify_password(plain, hashed) is True

    def test_wrong_password_fails(self):
        """Wrong password should not verify."""
        hashed = get_password_hash("CorrectPassword")
        assert verify_password("WrongPassword", hashed) is False

    def test_hash_is_not_plaintext(self):
        """Hash output should not be the same as input."""
        plain = "MyPassword"
        hashed = get_password_hash(plain)
        assert hashed != plain

    def test_different_hashes_for_same_password(self):
        """bcrypt should produce different hashes for the same password (due to salt)."""
        plain = "SamePassword"
        hash1 = get_password_hash(plain)
        hash2 = get_password_hash(plain)
        assert hash1 != hash2
        assert verify_password(plain, hash1) is True
        assert verify_password(plain, hash2) is True


class TestJWTTokens:
    """Tests for JWT creation and decoding."""

    def test_create_and_decode_token(self):
        """A created token should decode back to the original payload."""
        data = {"sub": "user-123", "role": "admin"}
        token = create_access_token(data)
        payload = decode_token(token)

        assert payload is not None
        assert payload["sub"] == "user-123"
        assert payload["role"] == "admin"
        assert "exp" in payload

    def test_expired_token_returns_none(self):
        """An expired token should return None on decode."""
        data = {"sub": "user-123"}
        token = create_access_token(data, expires_delta=timedelta(seconds=-1))
        payload = decode_token(token)
        assert payload is None

    def test_invalid_token_returns_none(self):
        """An invalid token string should return None."""
        payload = decode_token("not-a-valid-jwt-token")
        assert payload is None

    def test_tampered_token_returns_none(self):
        """A tampered token should return None."""
        data = {"sub": "user-123"}
        token = create_access_token(data)
        tampered = token[:-5] + "XXXXX"
        payload = decode_token(tampered)
        assert payload is None

    def test_custom_expiry(self):
        """Token with custom expiry should still decode within validity."""
        data = {"sub": "user-456"}
        token = create_access_token(data, expires_delta=timedelta(hours=1))
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "user-456"
