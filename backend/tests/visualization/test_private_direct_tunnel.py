"""HTTP is accepted only on the fixed encrypted SSH reader transport."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.direct import config


def settings(**extra):
    return config.DirectSettings(
        environment="production",
        storage="s3",
        s3_bucket="greenmind-direct-production-hotspot",
        s3_access_key="test-access",
        s3_secret_key="test-secret",
        database_url="postgresql://test/test",
        s3_endpoint_url="http://127.0.0.1:9000",
        **extra,
    )


def allowed(monkeypatch):
    monkeypatch.setattr(config.os, "uname", lambda: SimpleNamespace(nodename="ubuntu-4gb-fsn1-1"))
    monkeypatch.setattr(config.os, "getuid", lambda: 995)
    monkeypatch.setattr(config.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=995))
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "archive")
    monkeypatch.setenv("ARCHIVE_REMOTE_AUTH_ENABLED", "true")
    monkeypatch.setenv("RAW_ARCHIVE_DELETE_ENABLED", "false")


def test_explicit_isolated_reader_transport(monkeypatch):
    allowed(monkeypatch)
    assert settings(private_s3_tunnel=True).require_tls is True
    with pytest.raises(ValidationError):
        settings()


@pytest.mark.parametrize("field,value", [("ingest_enabled", True), ("retention_enabled", True)])
def test_transport_never_applies_to_ingestion_or_retention(monkeypatch, field, value):
    allowed(monkeypatch)
    with pytest.raises(ValidationError):
        settings(private_s3_tunnel=True, **{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("RAW_ARCHIVE_WORKER_ROLE", "source"),
        ("ARCHIVE_REMOTE_AUTH_ENABLED", "false"),
        ("RAW_ARCHIVE_DELETE_ENABLED", "true"),
    ],
)
def test_source_and_destructive_configuration_rejected(monkeypatch, field, value):
    allowed(monkeypatch)
    monkeypatch.setenv(field, value)
    with pytest.raises(ValidationError):
        settings(private_s3_tunnel=True)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://2.31.22.33:9000",
        "http://127.0.0.1:9001",
        "http://other@127.0.0.1:9000",
        "http://127.0.0.1:9000/path",
        "http://127.0.0.1:9000?x=1",
        "http://127.0.0.1:9000#x",
    ],
)
def test_only_exact_loopback_forward_accepted(monkeypatch, endpoint):
    allowed(monkeypatch)
    with pytest.raises(ValidationError):
        config.DirectSettings(
            environment="production",
            storage="s3",
            s3_bucket="greenmind-direct-production-hotspot",
            s3_access_key="test-access",
            s3_secret_key="test-secret",
            database_url="postgresql://test/test",
            s3_endpoint_url=endpoint,
            private_s3_tunnel=True,
        )


@pytest.mark.parametrize("uid,host", [(0, "ubuntu-4gb-fsn1-1"), (995, "Biolingo")])
def test_wrong_host_or_account_cannot_use_exception(monkeypatch, uid, host):
    allowed(monkeypatch)
    monkeypatch.setattr(config.os, "getuid", lambda: uid)
    monkeypatch.setattr(config.os, "uname", lambda: SimpleNamespace(nodename=host))
    with pytest.raises(ValidationError):
        settings(private_s3_tunnel=True)
