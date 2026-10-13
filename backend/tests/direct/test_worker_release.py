"""Archive-required release must never silently use single-copy cleanup."""

from types import SimpleNamespace

import pytest

from app.direct import worker
from app.direct.config import DirectSettings


def test_archive_offload_does_not_run_legacy_release(monkeypatch):
    calls = []
    cfg = DirectSettings(payload_offload_enabled=True, archive_broker_socket="/private/broker.sock")
    store = SimpleNamespace(get_archive=object(), archive_available=object())
    monkeypatch.setattr(worker, "release_verified_chunks", lambda *a: calls.append("legacy"))
    monkeypatch.setattr(
        worker, "offload_verified_chunks", lambda *a, **kw: calls.append("archive") or 760
    )
    assert worker.release_payloads(object(), cfg, store) == 760
    assert calls == ["archive"]


def test_paused_archive_release_preserves_all_sql_payloads(monkeypatch):
    cfg = DirectSettings(payload_release_requires_archive=True)

    def forbidden(*args, **kwargs):
        pytest.fail("Paused archive release must retain original payloads")

    monkeypatch.setattr(worker, "release_verified_chunks", forbidden)
    monkeypatch.setattr(worker, "offload_verified_chunks", forbidden)
    assert worker.release_payloads(object(), cfg, object()) == 0


def test_legacy_release_is_explicitly_compatible_by_default(monkeypatch):
    monkeypatch.setattr(worker, "release_verified_chunks", lambda *a: 760)
    assert worker.release_payloads(object(), DirectSettings(), object()) == 760


def test_archive_required_release_rejects_destructive_retention():
    with pytest.raises(ValueError, match="retention"):
        DirectSettings(payload_release_requires_archive=True, retention_enabled=True)
