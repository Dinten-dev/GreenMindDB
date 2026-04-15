"""Unit tests for application configuration."""

import os
from unittest.mock import patch

import pytest


class TestSettingsValidation:
    """Tests for Settings class validation."""

    def test_jwt_secret_min_length(self):
        """JWT secret must be at least 32 characters."""
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "short"}, clear=False):
            from pydantic import ValidationError

            from app.config import Settings

            with pytest.raises(ValidationError, match="32 characters"):
                Settings(jwt_secret_key="short")

    def test_jwt_secret_valid_length(self):
        """JWT secret with 32+ chars should pass validation."""
        from app.config import Settings

        s = Settings(jwt_secret_key="a-valid-secret-key-that-is-at-least-32-chars")
        assert len(s.jwt_secret_key) >= 32

    def test_cors_origins_parsing_from_string(self):
        """CORS origins should be parsed from comma-separated string."""
        from app.config import Settings

        s = Settings(
            cors_origins="http://localhost:3000,http://127.0.0.1:3000",
            jwt_secret_key="a-valid-secret-key-that-is-at-least-32-chars",
        )
        assert isinstance(s.cors_origins, list)
        assert "http://localhost:3000" in s.cors_origins
        assert "http://127.0.0.1:3000" in s.cors_origins

    def test_cors_origins_from_list(self):
        """CORS origins should accept a list directly."""
        from app.config import Settings

        origins = ["http://localhost:3000"]
        s = Settings(
            cors_origins=origins,
            jwt_secret_key="a-valid-secret-key-that-is-at-least-32-chars",
        )
        assert s.cors_origins == origins

    def test_production_rejects_default_jwt_secret(self):
        """Production environment must not use the default JWT secret."""
        from pydantic import ValidationError

        from app.config import Settings

        with pytest.raises(ValidationError, match="overridden in production"):
            Settings(
                environment="production",
                jwt_secret_key="dev-only-change-me-please-dev-only-change-me",
            )

    def test_default_environment_is_development(self):
        """Default environment should be 'development'."""
        from app.config import Settings

        s = Settings(jwt_secret_key="a-valid-secret-key-that-is-at-least-32-chars")
        assert s.environment == "development"
