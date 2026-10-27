"""Resource polling must stay bounded for small PostgreSQL COPY records."""

import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked

WORKER = Path(__file__).resolve().parents[1] / "app/tiered_archive/native_backup.py"


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
    sink = namespace["Sink"]([0], namespace["check"])
    for _ in range(100_000):
        sink.write(b"12345678")
    assert sink.digest() == hashlib.sha256(b"12345678" * 100_000).hexdigest()
    assert 1 < len(calls) < 100


def test_every_byte_counts_toward_budget_even_before_next_poll(sink_type):
    namespace, _, _ = sink_type
    sink = namespace["Sink"]([namespace["MAX"] - 1], namespace["check"])
    sink.write(b"a")
    with pytest.raises(ArchiveBlocked, match="budget"):
        sink.write(b"b")


def test_sparse_copy_records_still_poll_by_elapsed_time(sink_type):
    namespace, clock, calls = sink_type
    sink = namespace["Sink"]([0], namespace["check"])
    for i in range(1, 6):
        clock.now = i * 0.3
        sink.write(b"a")
    assert len(calls) == 5


def test_final_digest_checks_resources_even_for_empty_table(sink_type):
    namespace, _, calls = sink_type
    sink = namespace["Sink"]([0], namespace["check"])
    assert sink.digest() == hashlib.sha256(b"").hexdigest()
    assert len(calls) == 1


def test_large_copy_record_propagates_guard_failure_before_hashing(sink_type):
    namespace, _, _ = sink_type

    def fail():
        raise ArchiveBlocked("paused")

    namespace["check"] = fail
    sink = namespace["Sink"]([0], namespace["check"])
    with pytest.raises(ArchiveBlocked, match="paused"):
        sink.write(b"x" * 65536)
    assert sink.h.hexdigest() == hashlib.sha256(b"").hexdigest()


@pytest.fixture
def retained_scope(tmp_path):
    import json
    import re
    import shutil
    import stat

    template = (
        Path(__file__).resolve().parents[2]
        / "deploy/tiered-archive/native-restore/supervisor.template.py"
    )
    tree = ast.parse(template.read_text())
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "retain_encrypted"
    )
    root = tmp_path / "test"
    folder = root / "state/recovered"
    folder.mkdir(parents=True)
    refs = []
    for number, kind in enumerate(["parquet", "bin", "bin"]):
        data = b"synthetic-ciphertext" if kind == "bin" else b"synthetic-index"
        name = f"{number:04d}.{kind}"
        (folder / name).write_bytes(data)
        refs.append(
            {
                "format": kind,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "key": "staging/test/" + name,
            }
        )
    refs[0]["backup_format"] = "postgres-full-v1"
    manifest = {
        "identity": "a" * 64,
        "candidate": {
            "source": "gateway",
            "environment": "staging",
            "dataset": "database_backup",
            "organization": "00000000-0000-0000-0000-000000000000",
            "start": "2026-10-27T00:00:00+00:00",
        },
        "files": refs,
    }
    encoded = json.dumps(manifest).encode()
    (folder / "manifest.json").write_bytes(encoded)
    (folder / "full.dump").write_text("must never be retained")
    out = {
        "returncode": 0,
        "test": {
            "passed": True,
            "backup_format": "postgres-full-v1",
            "box_accesses": 0,
            "source_writes": 0,
            "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
            "archive_identity": "a" * 64,
            "archive_bytes": sum(p["bytes"] for p in refs) + len(encoded),
        },
    }
    namespace = dict(
        out=out,
        root=root,
        S=tmp_path,
        SOURCE="gateway",
        stopped=True,
        hashlib=hashlib,
        json=json,
        re=re,
        stat=stat,
        shutil=shutil,
        os=__import__("os"),
        PAYLOAD={"native_sha256": "b" * 64, "sha256": "c" * 64},
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(template), "exec"), namespace)  # noqa: S102 -- trusted repository function only
    return namespace, folder


def test_retention_keeps_only_exact_archive_parts_not_plain_dump(retained_scope):
    namespace, _ = retained_scope
    namespace["retain_encrypted"]()
    result = namespace["out"]["prepared_archive"]
    root = Path(result["root"])
    assert {p.name for p in root.iterdir()} == {
        "0000.parquet",
        "0001.bin",
        "0002.bin",
        "manifest.json",
        "approval-scope.json",
    }
    assert result["uploaded"] is False
    assert not root.stat().st_mode & 0o222
    assert all(not p.stat().st_mode & 0o277 for p in root.iterdir())


def test_changed_preparation_artifact_blocks_before_creating_proposal(retained_scope):
    namespace, folder = retained_scope
    (folder / "0001.bin").write_bytes(b"corrupt")
    with pytest.raises(AssertionError):
        namespace["retain_encrypted"]()
    assert "preparation_directory" not in namespace["out"]
    assert not list(namespace["S"].glob("prepared-native-*"))


def test_existing_prepared_scope_cannot_be_overwritten(retained_scope):
    namespace, _ = retained_scope
    namespace["retain_encrypted"]()
    root = Path(namespace["out"]["prepared_archive"]["root"])
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    with pytest.raises(FileExistsError):
        namespace["retain_encrypted"]()
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
