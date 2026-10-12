"""Private authentication forwards no signing key and fails closed."""

import json
from contextlib import contextmanager
from uuid import uuid4

import pytest

from app.raw_archive import remote_auth
from app.raw_archive.policy import ArchiveBlocked


@pytest.fixture
def origin(monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_AUTH_SOURCE_URL", remote_auth.ORIGIN)


def client(status, body, calls):
    class Response:
        status_code = status

        def iter_bytes(self):
            yield body

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            yield Response()

    return Client


def test_original_auth_identity_only(origin):
    actor = str(uuid4())
    calls = []
    result = remote_auth.authenticate(
        {"authorization": "Bearer fixture-token", "access_token": None},
        client_factory=client(
            200, json.dumps({"id": actor, "email": "ignored@example.invalid"}).encode(), calls
        ),
    )
    assert result == (200, {"id": actor})
    assert calls[0]["trust_env"] is False and calls[0]["follow_redirects"] is False
    assert calls[1] == (
        "GET",
        remote_auth.ORIGIN,
        {"headers": {"X-Forwarded-Proto": "https", "Authorization": "Bearer fixture-token"}},
    )


@pytest.mark.parametrize("status", [401, 403])
def test_source_rejection_preserved(origin, status):
    assert remote_auth.authenticate(
        {"authorization": None, "access_token": "fixture"}, client_factory=client(status, b"{}", [])
    ) == (status, None)


@pytest.mark.parametrize(
    "status,body",
    [
        (302, b"{}"),
        (503, b"{}"),
        (200, b"x" * 8193),
        (200, b"{}"),
        (200, b'{"id":null}'),
        (200, b'{"id":"not-uuid"}'),
    ],
)
def test_redirect_outage_and_invalid_identity_rejected(origin, status, body):
    with pytest.raises((ArchiveBlocked, ValueError)):
        remote_auth.authenticate(
            {"authorization": "Bearer fixture", "access_token": None},
            client_factory=client(status, body, []),
        )


@pytest.mark.parametrize(
    "data",
    [
        {"authorization": "x\r\nHost: hostile", "access_token": None},
        {"authorization": None, "access_token": "x; extra=y"},
        {"authorization": 123, "access_token": None},
        {"authorization": "x" * 4097, "access_token": None},
        {"authorization": "x", "access_token": None, "url": "http://foreign"},
    ],
)
def test_invalid_input_never_connects(origin, data):
    calls = []
    with pytest.raises(ArchiveBlocked):
        remote_auth.authenticate(data, client_factory=client(200, b"{}", calls))
    assert calls == []


def test_unconfigured_auth_never_connects(monkeypatch):
    monkeypatch.delenv("RAW_ARCHIVE_AUTH_SOURCE_URL", raising=False)
    with pytest.raises(ArchiveBlocked):
        remote_auth.authenticate({"authorization": "Bearer fixture", "access_token": None})


def test_missing_token_never_connects(origin):
    calls = []
    assert remote_auth.authenticate(
        {"authorization": None, "access_token": None}, client_factory=client(200, b"{}", calls)
    ) == (401, None)
    assert calls == []
