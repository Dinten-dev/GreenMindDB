"""Remote workers cannot substitute an independent host lock for source ownership."""

import fcntl
import io
import json
import os
import threading
from types import SimpleNamespace

import pytest

from app.raw_archive import remote_lease
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked


def source(tmp_path, monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(tmp_path))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "catalog")
    monkeypatch.setattr(remote_lease.os, "uname", lambda: SimpleNamespace(nodename="Biolingo"))


def test_busy_actual_source_lock_requests_copier_yield(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    with (tmp_path / "jobs.lock").open("w+") as body:
        os.chmod(body.name, 0o600)
        fcntl.flock(body, fcntl.LOCK_EX)
        with pytest.raises(SafetyPause, match="archive_job_busy"):
            remote_lease.serve(io.StringIO(), io.StringIO(), healthy=lambda: None)
    assert json.loads((tmp_path / "catalog.request").read_text())["expires"] > 0


def test_disconnect_releases_real_source_lock(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    read_input, write_input = os.pipe()
    read_output, write_output = os.pipe()
    errors = []

    def server():
        try:
            with os.fdopen(read_input) as incoming, os.fdopen(write_output, "w") as outgoing:
                remote_lease.serve(incoming, outgoing, healthy=lambda: None)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=server)
    thread.start()
    with os.fdopen(write_input, "w") as incoming, os.fdopen(read_output) as outgoing:
        initial = json.loads(outgoing.readline())
        with (tmp_path / "jobs.lock").open("r+") as body:
            with pytest.raises(BlockingIOError):
                fcntl.flock(body, fcntl.LOCK_EX | fcntl.LOCK_NB)
        incoming.write(json.dumps({"session": initial["session"], "action": "renew"}) + "\n")
        incoming.flush()
        assert json.loads(outgoing.readline())["session"] == initial["session"]
        incoming.close()
        thread.join(timeout=8)
    assert not errors and not thread.is_alive()
    with (tmp_path / "jobs.lock").open("r+") as body:
        fcntl.flock(body, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_unknown_command_never_takes_source_lock(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "delete everything")
    with pytest.raises(ArchiveBlocked):
        remote_lease.serve(io.StringIO(), io.StringIO(), healthy=lambda: None)
    assert not (tmp_path / "jobs.lock").exists()


def test_source_resource_pause_never_signals_ready(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    output = io.StringIO()

    def pause():
        raise SafetyPause("source_memory")

    with pytest.raises(SafetyPause):
        remote_lease.serve(io.StringIO(), output, healthy=pause)
    assert not output.getvalue()


def test_archive_export_without_source_authority_rejected(monkeypatch):
    monkeypatch.delenv("RAW_ARCHIVE_LEASE_HOST", raising=False)
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "archive")
    with pytest.raises(ArchiveBlocked, match="actual source lease"):
        with remote_lease.catalog_lease():
            pytest.fail("No isolated-host lock may substitute the actual source lock")


def test_lease_has_absolute_unextendable_deadline(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    ticks = iter([0, 121])
    output = io.StringIO()
    remote_lease.serve(io.StringIO(), output, healthy=lambda: None, clock=lambda: next(ticks))
    assert json.loads(output.getvalue())["maximum_seconds"] == 120


def test_worker_rejects_expiry_even_before_ssh_process_exits(monkeypatch):
    worker = remote_lease.RemoteLease([])
    worker.process = SimpleNamespace(poll=lambda: None)
    worker.deadline = 100
    monkeypatch.setattr(remote_lease.time, "monotonic", lambda: 100)
    with pytest.raises(SafetyPause, match="source_lease_deadline"):
        worker.check()


def test_partial_input_cannot_hold_the_lock_past_deadline(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    incoming = SimpleNamespace(fileno=lambda: 100)
    ticks = iter([0, 1, 2, 121])
    monkeypatch.setattr(remote_lease.select, "select", lambda *args: ([incoming], [], []))
    monkeypatch.setattr(remote_lease.os, "read", lambda *args: b"{")
    output = io.StringIO()
    remote_lease.serve(incoming, output, healthy=lambda: None, clock=lambda: next(ticks))
    assert json.loads(output.getvalue())["ready"] is True
    with (tmp_path / "jobs.lock").open("r+") as body:
        fcntl.flock(body, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_renewal_preserves_source_pause_reason(monkeypatch):
    worker = remote_lease.RemoteLease([])
    worker.session = "a" * 32
    worker.process = SimpleNamespace(stdin=io.StringIO(), poll=lambda: None)
    worker.deadline = float("inf")
    worker.stop = SimpleNamespace(wait=lambda _: False)

    def paused():
        raise SafetyPause("source_memory")

    monkeypatch.setattr(worker, "response", paused)
    worker.renew()
    with pytest.raises(SafetyPause, match="source_memory"):
        worker.check()


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("source_load", "source_load"),
        ("untrusted private value", "source_lease_unavailable"),
        ({"private": "untrusted"}, "source_lease_unavailable"),
    ],
)
def test_source_pause_response_is_allowlisted(monkeypatch, reason, expected):
    worker = remote_lease.RemoteLease([])
    stream = io.StringIO(json.dumps({"ready": False, "reason": reason}) + "\n")
    worker.process = SimpleNamespace(stdout=stream)
    monkeypatch.setattr(remote_lease.select, "select", lambda *args: ([stream], [], []))
    with pytest.raises(SafetyPause, match=expected):
        worker.response()


def test_receiver_transport_failure_is_resumable_health_pause(monkeypatch, tmp_path):
    import urllib.request

    from app.raw_archive import source_probe

    memory = tmp_path / "meminfo"
    memory.write_text("MemAvailable: 900000 kB\n")
    monkeypatch.setattr(remote_lease, "Path", lambda _: memory)
    monkeypatch.setattr(remote_lease.os, "getloadavg", lambda: (1, 1, 1))
    monkeypatch.setattr(source_probe, "gateway_ports", lambda: (8120, 8152))

    def timeout(*args, **kwargs):
        raise TimeoutError("private transport context must not be printed")

    monkeypatch.setattr(urllib.request, "urlopen", timeout)
    with pytest.raises(SafetyPause, match="source_receiver_health"):
        remote_lease.source_health()
