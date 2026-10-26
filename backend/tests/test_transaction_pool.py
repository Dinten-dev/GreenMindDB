from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from app.transaction_pool import attach_read_guards


def engine(host="127.0.0.1", port=6432, role="greenmind_archive_reader_33fe395"):
    return SimpleNamespace(url=make_url(f"postgresql://{role}@{host}:{port}/plantdb"))


def test_legacy_pool_behavior_unchanged(monkeypatch):
    monkeypatch.delenv("GREENMIND_TRANSACTION_POOL_READS", raising=False)
    original = engine(port=5432, role="admin")
    assert attach_read_guards(original, statement_ms=8000, lock_ms=150) is original
    with pytest.raises(ValueError, match="explicit"):
        attach_read_guards(engine(), statement_ms=8000, lock_ms=150)


@pytest.mark.parametrize(
    "host,port,role",
    [
        ("localhost", 6432, "greenmind_archive_reader_33fe395"),
        ("127.0.0.1", 5432, "greenmind_archive_reader_33fe395"),
        ("127.0.0.1", 6432, "admin"),
    ],
)
def test_wrong_pool_rejected(monkeypatch, host, port, role):
    monkeypatch.setenv("GREENMIND_TRANSACTION_POOL_READS", "true")
    with pytest.raises(ValueError, match="named"):
        attach_read_guards(engine(host, port, role), statement_ms=8000, lock_ms=150)


@pytest.mark.parametrize(
    "limits",
    [
        {"statement_ms": 8001, "lock_ms": 150},
        {"statement_ms": 3000, "lock_ms": 501},
        {"statement_ms": 3000, "lock_ms": 150, "idle_ms": 5001},
        {"statement_ms": 3000, "lock_ms": 150, "work_mem_kib": 8193},
    ],
)
def test_reserve_bounds_cannot_expand(monkeypatch, limits):
    monkeypatch.setenv("GREENMIND_TRANSACTION_POOL_READS", "true")
    with pytest.raises(ValueError, match="bounded"):
        attach_read_guards(engine(), **limits)


def test_guards_run_before_each_transaction(monkeypatch):
    monkeypatch.setenv("GREENMIND_TRANSACTION_POOL_READS", "true")
    callbacks = []

    def decorator(target, name):
        assert name == "begin"

        def register(fn):
            callbacks.append(fn)
            return fn

        return register

    monkeypatch.setattr("app.transaction_pool.event.listens_for", decorator)
    target = engine(role="greenmind_wav_metadata_b4ae52d674aa")
    attach_read_guards(target, statement_ms=3000, lock_ms=500, idle_ms=5000)
    statements = []
    connection = SimpleNamespace(exec_driver_sql=statements.append)
    callbacks[0](connection)
    callbacks[0](connection)
    half = len(statements) // 2
    assert statements[:half] == statements[half:]
    assert statements[0] == "SET LOCAL transaction_read_only=on"
    assert "SET LOCAL statement_timeout=3000" in statements
    assert "SET LOCAL idle_in_transaction_session_timeout=5000" in statements
    assert all(s.startswith("SET LOCAL ") for s in statements)
