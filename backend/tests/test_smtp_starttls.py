import smtplib
import ssl

import pytest

from app.config import settings
from app.services.email_service import dispatch_email


@pytest.mark.parametrize("failure", [None, "unsupported_tls", "certificate", "auth", "refused"])
def test_starttls_is_required_before_credentials_and_never_falls_back(
    monkeypatch, tmp_path, caplog, failure
):
    secret = tmp_path / "smtp-password"
    secret.write_text("test-transport-secret")
    for key, value in {
        "email_transport": "smtp",
        "smtp_security": "starttls",
        "smtp_port": 587,
        "smtp_username": "info@green-mind.ch",
        "smtp_password_file": str(secret),
        "environment": "staging",
        "staging_email_to": "controlled@example.com",
    }.items():
        monkeypatch.setattr(settings, key, value)
    events = []

    class SMTP:
        def __init__(self, host, port, **kwargs):
            assert host == "asmtp.mail.hostpoint.ch" and port == 587
            assert kwargs["timeout"] == 10
            events.append("connect")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            events.append("ehlo")

        def starttls(self, *, context):
            events.append("tls")
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            if failure == "unsupported_tls":
                raise smtplib.SMTPNotSupportedError("TLS unavailable")
            if failure == "certificate":
                raise ssl.SSLCertVerificationError("test-transport-secret")

        def login(self, username, password):
            assert events == ["connect", "ehlo", "tls", "ehlo"]
            assert username == "info@green-mind.ch" and password == "test-transport-secret"
            events.append("login")
            if failure == "auth":
                raise smtplib.SMTPAuthenticationError(535, b"test-transport-secret")

        def send_message(self, message):
            assert message["To"] == "controlled@example.com"
            events.append("send")
            return {"controlled@example.com": (550, b"refused")} if failure == "refused" else {}

    def forbidden_ssl(*args, **kwargs):
        events.append("ssl_fallback")
        raise AssertionError("Automatic fallback must never occur")

    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", SMTP)
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP_SSL", forbidden_ssl)
    assert dispatch_email("controlled@example.com", "Test", text="Test") is (failure is None)
    assert events.count("connect") == 1 and "ssl_fallback" not in events
    if failure in {"unsupported_tls", "certificate"}:
        assert "login" not in events and "send" not in events
    if failure == "auth":
        assert "send" not in events
    assert "test-transport-secret" not in caplog.text
