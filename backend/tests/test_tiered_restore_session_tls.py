"""Typed TLS session dispatch; explicit offline Root/PG metadata doubles only."""

import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import recovery, restore_inputs
from app.tiered_archive import restore_session as rs
from tests.test_tiered_restore_session import log, plan
from tests.test_tiered_restore_session import target as restore_target
from tests.test_tiered_restore_tls import (
    URL,
)
from tests.test_tiered_restore_tls import (
    binding as root_binding_fixture,
)

target = restore_target
binding = root_binding_fixture


def test_connection_none_preserves_original_driver_options(monkeypatch):
    import sqlalchemy

    calls = []
    result = object()
    monkeypatch.setattr(
        sqlalchemy, "create_engine", lambda *args, **kwargs: calls.append((args, kwargs)) or result
    )
    assert rs.connection(URL) is result
    assert calls == [
        (
            (make_url(URL),),
            {
                "pool_size": 1,
                "max_overflow": 0,
                "connect_args": {
                    "connect_timeout": 3,
                    "options": "-c default_transaction_read_only=off",
                },
            },
        )
    ]


def test_connection_reuses_exact_tls_options_and_reconnect_guard(binding, monkeypatch):
    import sqlalchemy

    current, _ = binding
    calls, guards = [], []
    result = SimpleNamespace(url=make_url(URL))
    monkeypatch.setattr(
        sqlalchemy, "create_engine", lambda *args, **kwargs: calls.append((args, kwargs)) or result
    )
    monkeypatch.setattr(
        rs,
        "restore_tls_engine_guard",
        lambda engine, value, url, **kwargs: guards.append((engine, value, url)),
    )

    def checkpoint():
        return None

    assert rs.connection(URL, tls_binding=current, checkpoint=checkpoint) is result
    assert calls[0][1]["connect_args"] == {
        "connect_timeout": 3,
        "options": "-c default_transaction_read_only=off",
        **recovery.restore_tls_connect_args(current, make_url(URL)),
    }
    assert guards == [(result, current, make_url(URL))]
    with pytest.raises(ArchiveBlocked, match="checkpoint"):
        rs.connection(URL, tls_binding=current)


def invoke(name, value, tmp_path):
    args = (URL, {"source": "gateway"}, "a" * 64)
    options = {"checkpoint": lambda: None, "tls_binding": value}
    if name == "begin_restore_session":
        return rs.begin_restore_session(*args, **options)
    if name in {"advance_restore_baseline", "begin_restore_audit"}:
        return getattr(rs, name)(*args, "b" * 64, **options)
    return getattr(rs, name)(*args, "b" * 64, tmp_path / "unit-batch", "c" * 64, **options)


@pytest.mark.parametrize(
    "name",
    [
        "begin_restore_session",
        "advance_restore_baseline",
        "apply_restore_batch",
        "begin_restore_audit",
        "audit_restore_batch",
    ],
)
def test_every_session_api_pins_tls_before_admission_or_control_writes(
    binding, tmp_path, monkeypatch, name
):
    current, calls = binding
    events = []

    class AdmissionReached(Exception):
        pass

    class Engine:
        url = make_url(URL)

        def begin(self):
            class Scope:
                def __enter__(self):
                    return object()

                def __exit__(self, *args):
                    return False

            return Scope()

        def dispose(self):
            events.append("dispose")

    def connection(url, **options):
        assert url == URL and options["tls_binding"] is current
        options["checkpoint"]()
        events.append("connect")
        return Engine()

    def identity(db, engine, value, *, checkpoint):
        assert value is current
        checkpoint()
        events.append("identity")

    def admission(db):
        events.append("admission")
        raise AdmissionReached

    monkeypatch.setattr(rs, "validate_plan", lambda *args: None)
    monkeypatch.setattr(rs, "load_batch", lambda *args: ({}, []))
    monkeypatch.setattr(rs, "frozen_batch_inputs", lambda *args: {})
    monkeypatch.setattr(restore_inputs, "frozen", lambda *args: None)
    monkeypatch.setattr(rs, "connection", connection)
    monkeypatch.setattr(rs, "restore_tls_identity", identity)
    monkeypatch.setattr(rs, "admission", admission)
    with pytest.raises(AdmissionReached):
        invoke(name, current, tmp_path)
    assert events == ["connect", "identity", "admission", "dispose"] and calls

    def expired(value, raw):
        raise ArchiveBlocked("Unit expired actual Source authority")

    events.clear()
    with pytest.raises(ArchiveBlocked, match="Source authority"):
        invoke(name, replace(current, guard=expired), tmp_path)
    assert not events


def test_all_tls_session_phases_run_real_transactions_fk_replay_and_frozen_audit(
    target, binding, tmp_path, monkeypatch
):
    current, authority = binding
    # Keep the Root-file fixture local to TLS files; real frozen-input tests use
    # the desktop operator and actual SQLite transactions, never PG authority.
    simulated = Path.lstat
    tls_private = current.client_key.path.parent
    tls_root = recovery._TLS_ROOT

    def selected(path):
        if path == tls_root or path.is_relative_to(tls_private):
            return simulated(path)
        return os.stat(path, follow_symlinks=False)

    monkeypatch.setattr(Path, "lstat", selected)
    monkeypatch.setattr(restore_inputs, "ROOT_UID", os.geteuid())
    identities = []

    def connection(url, **options):
        assert url == URL and options["tls_binding"] is current
        options["checkpoint"]()
        return target

    def identity(db, engine, value, *, checkpoint):
        assert value is current
        checkpoint()
        identities.append(True)

    monkeypatch.setattr(rs, "connection", connection)
    monkeypatch.setattr(rs, "restore_tls_identity", identity)
    head, sha, descriptors = plan(tmp_path, [[[log("first")]], [[log("second")]]], freeze=True)
    options = {"checkpoint": lambda: None, "tls_binding": current}
    started = rs.begin_restore_session(URL, head, sha, **options)
    session = started["session_id"]
    for _ in range(10):
        if rs.advance_restore_baseline(URL, head, sha, session, **options)["ready"]:
            break
    else:
        pytest.fail("Bounded synthetic master baseline did not complete")
    for descriptor in descriptors:
        loaded = rs.apply_restore_batch(URL, head, sha, session, *descriptor, **options)
        assert rs.apply_restore_batch(URL, head, sha, session, *descriptor, **options)["replayed"]
    assert loaded["phase"] == "loaded"
    epoch = rs.begin_restore_audit(URL, head, sha, session, **options)["audit_epoch"]
    for descriptor in descriptors:
        audited = rs.audit_restore_batch(URL, head, sha, session, *descriptor, **options)
    assert audited["phase"] == "verified" and audited["audit_epoch"] == epoch
    assert audited["audited_rows"] == 2 and audited["all_registered_sessions_verified"]
    assert audited["production_ready"] is False and len(identities) >= 8 and authority
