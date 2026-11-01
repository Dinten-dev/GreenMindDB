"""Real copy failure origins and private-message redaction, without source IO."""

import importlib.util
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace, TracebackType

import pytest

from app.raw_archive import archive_copy_once as entry
from app.raw_archive import failure_diagnostics as diagnostics
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import execution, schedule, wav_source
from tests.raw_archive import test_tiered_copy_entry as entry_tests

guarded = entry_tests.guarded
SECRET = "postgresql://private-user:PRIVATE_TOKEN@private-host/private-db"


def adapter():
    return wav_source.WavSource(
        None, SimpleNamespace(resolve=lambda _: []), None, "direct", "staging", None, lambda: None
    )


def blocked():
    try:
        adapter().metadata(["synthetic", 1])
    except ArchiveBlocked as error:
        return error
    pytest.fail("actual source guard did not execute")


def test_real_archive_blocked_origin_and_deterministic_guardcode():
    first = diagnostics.failure_diagnostic(blocked())
    second = diagnostics.failure_diagnostic(blocked())
    assert first == second and first["guard_code"].startswith("copy_guard_v1_")
    assert first["exception_class"] == "ArchiveBlocked"
    assert first["frames"][0]["file"] == "tiered_archive/wav_source.py"
    assert first["frames"][0]["function"] == "_legacy_metadata" and first["frames"][0]["line"] > 0
    assert len(first["frames"][0]["source_sha256"]) == 64
    assert "No verified WAVs" not in json.dumps(first)


def test_dynamic_message_locals_and_unknown_classname_never_exported(monkeypatch, capsys):
    class PrivateError(Exception):
        def __str__(self):
            pytest.fail("free exception text accessed")

    PrivateError.__name__ = "PRIVATE_TOKEN"

    def fail():
        local_password = SECRET
        assert local_password
        raise PrivateError(SECRET, {"token": SECRET})

    monkeypatch.setattr(entry, "run", fail)
    assert entry.main() == 1
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["status"] == "blocked" and report["deleted"] == 0
    assert (
        report["reason"] == "OtherError" and report["diagnostic"]["exception_class"] == "OtherError"
    )
    assert SECRET not in output and "PRIVATE_TOKEN" not in output


def test_forged_actual_app_filename_and_globals_are_not_trusted():
    name = Path(wav_source.__file__).resolve()
    try:
        exec(  # noqa: S102 -- synthetic forged code, never production input
            compile("raise ArchiveBlocked('PRIVATE_TOKEN')", str(name), "exec"), wav_source.__dict__
        )
    except ArchiveBlocked as error:
        result = diagnostics.failure_diagnostic(error)
    assert result["frames"] == [] and "PRIVATE_TOKEN" not in json.dumps(result)


def test_external_source_and_forged_evidence_cache_are_not_exported(tmp_path):
    path = tmp_path / "PRIVATE_TOKEN.py"
    path.write_text("raise ArchiveBlocked('PRIVATE_TOKEN')")
    try:
        exec(compile(path.read_text(), str(path), "exec"), {"ArchiveBlocked": ArchiveBlocked})  # noqa: S102 -- synthetic external code
    except ArchiveBlocked as error:
        error.__dict__[diagnostics._CACHE_KEY] = (object(), b'{"guard_code":"PRIVATE_TOKEN"}')
        result = diagnostics.failure_diagnostic(error)
    assert result["frames"] == [] and "PRIVATE_TOKEN" not in json.dumps(result)


def test_more_than_four_real_app_frames_are_bounded():
    error = blocked()
    source = error.__traceback__
    while source.tb_next is not None:
        source = source.tb_next
    trace = None
    for _ in range(10):
        trace = TracebackType(trace, source.tb_frame, source.tb_lasti, source.tb_lineno)
    other = ArchiveBlocked(SECRET).with_traceback(trace)
    result = diagnostics.failure_diagnostic(other)
    assert len(result["frames"]) == 4 and not result["frames_truncated"]
    assert all(frame["file"] == "tiered_archive/wav_source.py" for frame in result["frames"])


def test_overlong_trace_does_not_claim_outer_frame_as_origin():
    def recurse(count):
        if count:
            return recurse(count - 1)
        raise ArchiveBlocked(SECRET)

    try:
        recurse(diagnostics.MAX_TRACEBACK + 2)
    except ArchiveBlocked as error:
        result = diagnostics.failure_diagnostic(error)
    assert result["frames_truncated"] and result["frames"] == []
    assert SECRET not in json.dumps(result)


def test_entry_and_durable_last_run_share_identical_static_evidence(guarded, monkeypatch, capsys):
    monkeypatch.setenv("RAW_ARCHIVE_COPY_MODE", "tiered-copy-only")
    monkeypatch.setattr(execution, "monitored", lambda *a, **k: nullcontext())

    def fail(*args, **kwargs):
        adapter().metadata(["synthetic", 1])

    monkeypatch.setattr(schedule, "tick", fail)
    assert entry.main() == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    report = json.loads(lines[0])
    root = entry.os.environ["TIERED_ARCHIVE_STATE_DIR"]
    stored = json.loads((Path(root) / "copy-last-run.json").read_text())
    assert stored["diagnostic"] == report["diagnostic"]
    assert stored["state"] == "failed" and stored["reason"] == report["reason"] == "ArchiveBlocked"
    assert stored["diagnostic"]["frames"][0]["function"] == "_legacy_metadata"
    assert {"started_at", "finished_at", "elapsed_seconds", "self_peak_rss"} <= set(stored)


@pytest.mark.parametrize("reason", ["source_lease_lost", SECRET])
def test_pause_exit75_is_preserved_but_unknown_pause_text_is_redacted(monkeypatch, capsys, reason):
    def pause():
        raise SafetyPause(reason)

    monkeypatch.setattr(entry, "run", pause)
    assert entry.main() == 75
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    report = json.loads(lines[0])
    assert report["status"] == "paused" and report["deleted"] == 0
    assert report["reason"] == (
        "source_lease_lost" if reason == "source_lease_lost" else "unclassified_pause"
    )
    assert SECRET not in lines[0]


def test_source_unavailable_diagnostic_never_replaces_original_failure(monkeypatch):
    monkeypatch.setattr(diagnostics, "_source", lambda path: None)
    error = blocked()
    result = diagnostics.failure_diagnostic(error)
    assert result["frames"] == [] and result["exception_class"] == "ArchiveBlocked"


@pytest.mark.parametrize("future", [False, True])
def test_legitimate_module_compilation_respects_own_future_flags(tmp_path, monkeypatch, future):
    file = tmp_path / "source_fixture.py"
    file.write_text(
        ("from __future__ import annotations\n" if future else "")
        + "from app.raw_archive.policy import ArchiveBlocked\n"
        "def fail(value: str):\n"
        "    raise ArchiveBlocked(value)\n"
    )
    name = "app.diagnostic_fixture"
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(diagnostics, "APP_ROOT", tmp_path)
    spec.loader.exec_module(module)
    try:
        module.fail(SECRET)
    except ArchiveBlocked as error:
        result = diagnostics.failure_diagnostic(error)
    assert result["frames"][0]["file"] == "source_fixture.py"
    assert result["frames"][0]["function"] == "fail"
    assert SECRET not in json.dumps(result)
