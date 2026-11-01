"""CLI routing and private-profile tests; never open a server connection."""

import hashlib
import json
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import __main__ as cli
from app.tiered_archive import sql_runtime
from tests.test_sql_deletion import setup_job


def test_review_only_fence_uses_catalogue_without_source(tmp_path, monkeypatch):
    candidate, _, ledger, _, _, _ = setup_job(tmp_path)

    class ReadOnlyLedger:
        def __init__(self, *_args, **kwargs):
            assert kwargs == {"read_only": True}

        def get(self, identity):
            return {"candidate": identity, "source": {}}

        def close(self):
            pass

    monkeypatch.setattr(sql_runtime, "Ledger", ReadOnlyLedger)
    monkeypatch.setattr(sql_runtime.runtime, "candidate_from_json", lambda _: candidate)
    plan = sql_runtime.fence_plan(candidate.identity)
    assert plan["status"] == "REVIEW_ONLY" and plan["source_accesses"] == 0
    assert "CREATE TRIGGER" in plan["ddl"]
    ledger.close()


def test_cli_routes_explicit_evict_under_lease(tmp_path, monkeypatch, capsys):
    trace = []

    @contextmanager
    def bounded(*_args, **_kwargs):
        trace.append("bounded")
        yield

    def lease(callback):
        trace.append("archive-source-lock")
        return callback()

    def fake_evict(identity, **kwargs):
        trace.append("sql-evict")
        assert len(kwargs) == 8 and identity == "a" * 64
        return {"status": "TEST_ONLY", "identity": identity}

    from app.tiered_archive import execution

    monkeypatch.setattr(execution, "bounded_job", bounded)
    monkeypatch.setattr(cli, "with_source_catalogue_lease", lease)
    monkeypatch.setattr(sql_runtime, "evict", fake_evict)
    argv = ["tool", "sql-evict", "--identity", "a" * 64]
    for field in ("restore-profile", "proof", "authorization", "snapshot-token"):
        argv.extend(
            ["--" + field + "-file", str(tmp_path / field), "--" + field + "-sha256", "b" * 64]
        )
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    assert trace == ["archive-source-lock", "bounded", "sql-evict"]
    assert json.loads(capsys.readouterr().out)["status"] == "TEST_ONLY"


def test_sql_scoped_store_and_operator_client_forbid_writes():
    store = sql_runtime.ScopedReadStore(SimpleNamespace(identity="box"))
    with pytest.raises(ArchiveBlocked, match="read-only"):
        store.publish("key", None)
    sql_runtime._read_only_request(httpx.Request("GET", "https://example.invalid"))
    with pytest.raises(ArchiveBlocked, match="GET only"):
        sql_runtime._read_only_request(httpx.Request("POST", "https://example.invalid"))


def test_profile_requires_exact_frozen_digest(tmp_path, monkeypatch):
    path = tmp_path / "profile"
    path.write_bytes(b"test-value")
    path.chmod(0o400)
    monkeypatch.setattr(sql_runtime, "frozen", lambda _: (1, 2, 3))
    assert (
        sql_runtime._private_file(path, hashlib.sha256(b"test-value").hexdigest(), 8192)
        == b"test-value"
    )
    with pytest.raises(ArchiveBlocked, match="profile differs"):
        sql_runtime._private_file(path, "0" * 64, 8192)


def test_source_bridge_binds_session_and_consumes_its_challenge(tmp_path, monkeypatch):
    """Simulated bridge exercises the actual private challenge exchange."""
    import os
    import threading
    import time
    from pathlib import Path

    from app.raw_archive import scientific_scope_control
    from app.tiered_archive import restore_inputs

    session = "a" * 32
    challenge = tmp_path / "challenge"
    frame = tmp_path / "frame"
    expected = b"frozen source frame"
    original_lstat = Path.lstat

    def fake_lstat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == tmp_path:
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0)
        return info

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    monkeypatch.setattr(restore_inputs, "protected_parents", lambda path: None)
    monkeypatch.setattr(scientific_scope_control, "current_source_session", lambda: session)
    monkeypatch.setattr(sql_runtime, "frozen", lambda path: original_lstat(path).st_ino)
    observed = []

    def bridge():
        deadline = time.monotonic() + 3
        while not challenge.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        observed.append(json.loads(challenge.read_bytes()))
        temporary = tmp_path / "source-temporary"
        temporary.write_bytes(expected)
        os.chmod(temporary, 0o400)
        temporary.rename(frame)

    worker = threading.Thread(target=bridge)
    worker.start()
    digest = sql_runtime._await_source_frame(
        challenge, frame, "lease-lock", {}, lambda: None, seconds=3
    )
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert observed == [
        {
            "format": "greenmind.science-source-challenge.v1",
            "action": "lease-lock",
            "lease_session": session,
            "payload": {},
        }
    ]
    assert digest == hashlib.sha256(expected).hexdigest()
    assert not challenge.exists()


@pytest.mark.parametrize("action", ["sql-scope-bootstrap", "sql-scope-reconcile"])
def test_cli_routes_scope_with_root_registry_pins(tmp_path, monkeypatch, capsys, action):
    from app.tiered_archive import execution

    trace = []

    @contextmanager
    def bounded(*_args, **_kwargs):
        trace.append("bounded")
        yield

    monkeypatch.setattr(execution, "bounded_job", bounded)
    monkeypatch.setattr(cli, "with_source_catalogue_lease", lambda callback: callback())

    def fake_evict(identity, **kwargs):
        trace.append(kwargs["operation"])
        assert kwargs["registry_file"] == tmp_path / "registry"
        return {"status": "SIMULATED"}

    monkeypatch.setattr(sql_runtime, "evict", fake_evict)
    argv = ["tool", action, "--identity", "a" * 64]
    for field in ("restore-profile", "proof", "authorization", "snapshot-token", "registry"):
        argv.extend(
            ["--" + field + "-file", str(tmp_path / field), "--" + field + "-sha256", "b" * 64]
        )
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    assert trace == ["bounded", action.removeprefix("sql-")]
    assert json.loads(capsys.readouterr().out)["status"] == "SIMULATED"


def test_audit_ddl_cli_is_review_only(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["tool", "sql-audit-plan", "--writer-role", "greenmind_sql_writer"]
    )
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "REVIEW_ONLY" and result["executed"] is False


def test_sql_scope_registry_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(sql_runtime.os, "geteuid", lambda: 0)
    monkeypatch.delenv("TIERED_ARCHIVE_SQL_SCOPE_ENABLED", raising=False)
    with pytest.raises(ArchiveBlocked, match="enablement"):
        sql_runtime.prepare_sql_registry()
