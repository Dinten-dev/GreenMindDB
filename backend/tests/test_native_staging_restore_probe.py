"""Resource polling must stay bounded for small PostgreSQL COPY records."""

import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked

WORKER = Path(__file__).resolve().parents[2] / "deploy/tiered-archive/native-restore/worker.py"


@pytest.fixture
def sink_type():
    source = ast.parse(WORKER.read_text())
    node = next(
        item for item in source.body if isinstance(item, ast.ClassDef) and item.name == "Sink"
    )
    clock = SimpleNamespace(now=0.0)
    calls = []
    namespace = {
        "hashlib": hashlib,
        "time": SimpleNamespace(monotonic=lambda: clock.now),
        "check": lambda: calls.append(clock.now),
        "MAX": 1024 * 1024,
        "ArchiveBlocked": ArchiveBlocked,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(WORKER), "exec"), namespace)  # noqa: S102 -- trusted repository class only
    return namespace, clock, calls


def test_small_copy_records_do_not_poll_resources_per_row(sink_type):
    namespace, _, calls = sink_type
    sink = namespace["Sink"]([0])
    for _ in range(100_000):
        sink.write(b"12345678")
    assert sink.digest() == hashlib.sha256(b"12345678" * 100_000).hexdigest()
    assert 1 < len(calls) < 100


def test_every_byte_counts_toward_budget_even_before_next_poll(sink_type):
    namespace, _, _ = sink_type
    sink = namespace["Sink"]([namespace["MAX"] - 1])
    sink.write(b"a")
    with pytest.raises(ArchiveBlocked, match="budget"):
        sink.write(b"b")


def test_sparse_copy_records_still_poll_by_elapsed_time(sink_type):
    namespace, clock, calls = sink_type
    sink = namespace["Sink"]([0])
    for i in range(1, 6):
        clock.now = i * 0.3
        sink.write(b"a")
    assert len(calls) == 5


def test_final_digest_checks_resources_even_for_empty_table(sink_type):
    namespace, _, calls = sink_type
    sink = namespace["Sink"]([0])
    assert sink.digest() == hashlib.sha256(b"").hexdigest()
    assert len(calls) == 1


def test_large_copy_record_propagates_guard_failure_before_hashing(sink_type):
    namespace, _, _ = sink_type

    def fail():
        raise ArchiveBlocked("paused")

    namespace["check"] = fail
    sink = namespace["Sink"]([0])
    with pytest.raises(ArchiveBlocked, match="paused"):
        sink.write(b"x" * 65536)
    assert sink.h.hexdigest() == hashlib.sha256(b"").hexdigest()
