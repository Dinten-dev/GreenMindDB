"""Offline synthetic V3 contract tests; never connect to PostgreSQL."""

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine

from app.raw_archive import scientific_capture as capture
from app.raw_archive import scientific_parts as parts
from app.raw_archive import wav_catalog
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.scientific_scope_v3 import SOURCE_SCOPE, SOURCE_SCOPE_SHA256


def digest(data):
    return hashlib.sha256(data).hexdigest()


def row(fields, first=None, special=None):
    data = bytearray(struct.pack("!h", fields))
    for i in range(fields):
        v = first if i == 0 else special if i == 12 else None
        data.extend(struct.pack("!i", -1 if v is None else len(v)))
        if v is not None:
            data.extend(v)
    return bytes(data)


def manifest(tmp_path, file_rows):
    source = tmp_path / "capture"
    source.mkdir(mode=0o700)
    columns = capture.roster()
    entries = []
    for table in capture.TABLES:
        data = capture.HEADER + b"".join(file_rows.get(table, [])) + capture.TRAILER
        path = source / (table + ".pgcopy")
        path.write_bytes(data)
        path.chmod(0o400)
        count = len(file_rows.get(table, []))
        entries.append(
            {
                "table": table,
                "presence": "present_nonempty" if count else "present_empty",
                "file": path.name,
                "rows": count,
                "bytes": len(data),
                "sha256": digest(data),
                "columns": columns[table],
                "capture_order": "physical_heap_ranges",
                "identity_key": ["id"],
            }
        )
    now = datetime.now(UTC)
    identity = {
        "database": "plantdb",
        "database_oid": 16384,
        "role": "greenmind_archive_reader_33fe395",
        "session_role": "greenmind_archive_reader_33fe395",
        "role_oid": 499352,
        "session_oid": 499352,
        "role_unprivileged": True,
        "server_version_num": 150010,
        "transaction_read_only": "on",
        "in_recovery": False,
        "server_address": "172.28.20.5/32",
        "server_port": 5432,
        "postmaster_start": SOURCE_SCOPE["postmaster_start"],
    }
    proof = {
        "format": capture.FORMAT,
        "capture_id": "a" * 32,
        "source": "gateway",
        "complete": True,
        "source_read_only": True,
        "started_at": now.isoformat(),
        "finished_at": (now + timedelta(seconds=1)).isoformat(),
        "source_identity": identity,
        "snapshot_sha256": "b" * 64,
        "scope_sha256": SOURCE_SCOPE_SHA256,
        "scope": SOURCE_SCOPE,
        "files": entries,
        "total_bytes": sum(x["bytes"] for x in entries),
        "cross_database_atomic": False,
        "global_parent_fk_closure_proven": False,
    }
    data = (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path = source / "capture.json"
    path.write_bytes(data)
    path.chmod(0o400)
    return source, proof, digest(data)


def test_roster_is_all_native_columns_and_recorded_types():
    columns = capture.roster()
    assert [len(columns[t]) for t in capture.TABLES] == [62, 10]
    names = {c["name"] for c in columns["wav_feature"]}
    assert {"anomaly_s3_key", "flac_s3_key"} <= names
    assert len(SOURCE_SCOPE["constraints"]) == 8 and len(SOURCE_SCOPE["indexes"]) == 16
    assert all(c["archive_reader_select"] for c in SOURCE_SCOPE["columns"])


def test_ten_previously_excluded_anomaly_and_flac_attributes_are_present():
    all_names = {item["name"] for item in capture.roster()["wav_feature"]}
    old_names = set(wav_catalog.COLUMNS["gateway"]["wav_feature"])
    assert all_names - old_names == {
        "anomaly_expires_at",
        "anomaly_file_size_bytes",
        "anomaly_s3_key",
        "anomaly_sha256",
        "anomaly_verified_at",
        "flac_expires_at",
        "flac_file_size_bytes",
        "flac_s3_key",
        "flac_sha256",
        "flac_verified_at",
    }


def test_engine_endpoint_and_query_options_fail_closed_without_connection():
    base = "postgresql+psycopg2://greenmind_archive_reader_33fe395:dummy@127.0.0.1:5432/plantdb"
    engine = create_engine(base)
    capture._engine_binding(engine, "plantdb")
    for unsafe in (
        base + "?host=elsewhere",
        base.replace("5432", "6432"),
        base.replace("plantdb", "greenmind_direct_production"),
        base.replace("127.0.0.1", "localhost"),
    ):
        with pytest.raises(ArchiveBlocked):
            capture._engine_binding(create_engine(unsafe), "plantdb")


def test_native_counter_preserves_nan_bits_and_all_fragment_boundaries():
    nan = bytes.fromhex("7ff8000000001234")
    data = capture.HEADER + row(62, b"\0" * 16, nan) + row(62, None, b"") + capture.TRAILER
    c = capture.FrameCounter(62)
    for b in data:
        c.feed(bytes([b]))
    assert c.finish() == 2
    for broken in (data[:-1], data + b"x", data[:19] + struct.pack("!h", 61) + data[21:]):
        with pytest.raises(ArchiveBlocked):
            c = capture.FrameCounter(62)
            c.feed(broken)
            c.finish()


def test_index_and_part_preserve_original_binary_and_presence(tmp_path):
    data = row(62, b"\x01" * 16, bytes.fromhex("7ff8000000001234"))
    source, proof, capsha = manifest(tmp_path, {"wav_feature": [data]})
    indexfile = tmp_path / "index.json"
    result = parts.prepare_index(source, capsha, indexfile, checkpoint=lambda: None)
    checked, index = parts.load_index(
        source, capsha, indexfile, result["index_sha256"], checkpoint=lambda: None
    )
    assert checked["files"][1]["presence"] == "present_empty"
    assert len(index["parts"]) == 2 and index["parts"][1]["rows"] == 0
    output = tmp_path / "part.pgcopy"
    with patch.object(
        parts.shutil,
        "disk_usage",
        return_value=type("Disk", (), {"free": 20 * 1024**3, "total": 100 * 1024**3})(),
    ):
        descriptor = parts.materialize_part(
            source, capsha, indexfile, result["index_sha256"], 0, output, checkpoint=lambda: None
        )
    assert output.read_bytes() == capture.HEADER + data + capture.TRAILER
    assert descriptor["authenticated"] is descriptor["published"] is descriptor["restored"] is False
    assert index["authenticated"] is False and index["parent_fk_closure_proven"] is False


def test_source_tamper_missing_and_mixed_index_blocked(tmp_path):
    source, proof, capsha = manifest(tmp_path, {"wav_feature": [row(62)]})
    idx = tmp_path / "index.json"
    parts.prepare_index(source, capsha, idx, checkpoint=lambda: None)
    data = json.loads(idx.read_bytes())
    data["capture_id"] = "f" * 32
    raw = (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()
    idx.chmod(0o600)
    idx.write_bytes(raw)
    idx.chmod(0o400)
    with pytest.raises(ArchiveBlocked):
        parts.load_index(source, capsha, idx, digest(raw), checkpoint=lambda: None)
    file = source / "wav_feature.pgcopy"
    file.chmod(0o600)
    file.write_bytes(file.read_bytes() + b"x")
    file.chmod(0o400)
    with pytest.raises(ArchiveBlocked):
        parts.load_capture(source, capsha, checkpoint=lambda: None)


def test_gap_overlap_and_reorder_are_rejected(tmp_path):
    source, proof, capsha = manifest(tmp_path, {"wav_feature": [row(62)]})
    idx = tmp_path / "index.json"
    parts.prepare_index(source, capsha, idx, checkpoint=lambda: None)
    original = json.loads(idx.read_bytes())
    for change in ("gap", "duplicate", "reorder"):
        data = json.loads(json.dumps(original))
        if change == "gap":
            data["parts"][0]["start"] += 1
        elif change == "duplicate":
            data["parts"].append(data["parts"][0])
        else:
            data["parts"].reverse()
        raw = (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()
        idx.chmod(0o600)
        idx.write_bytes(raw)
        idx.chmod(0o400)
        with pytest.raises(ArchiveBlocked):
            parts.load_index(source, capsha, idx, digest(raw), checkpoint=lambda: None)


def test_incomplete_capture_and_legacy_projection_rejected(tmp_path):
    source, proof, capsha = manifest(tmp_path, {})
    proof["complete"] = False
    raw = json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
    p = source / "capture.json"
    p.chmod(0o600)
    p.write_bytes(raw)
    p.chmod(0o400)
    with pytest.raises(ArchiveBlocked):
        parts.load_capture(source, digest(raw), checkpoint=lambda: None)
    proof["complete"] = True
    proof["files"][0]["columns"] = proof["files"][0]["columns"][:52]
    raw = json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
    p.chmod(0o600)
    p.write_bytes(raw)
    p.chmod(0o400)
    with pytest.raises(ArchiveBlocked):
        parts.load_capture(source, digest(raw), checkpoint=lambda: None)


def test_large_native_inventory_exceeds_legacy_cap_with_bounded_memory(tmp_path):
    source, proof, capsha = manifest(tmp_path, {})
    path = source / "wav_feature.pgcopy"
    path.chmod(0o600)
    # Sparse zero payloads keep the fixture's physical disk footprint bounded.
    length = capture.ROW_BYTES - 2 - 62 * 4
    count = 520
    with path.open("wb") as f:
        f.write(capture.HEADER)
        for _ in range(count):
            f.write(struct.pack("!h", 62))
            f.write(struct.pack("!i", length))
            f.seek(length, 1)
            f.write(struct.pack("!i", -1) * 61)
        f.write(capture.TRAILER)
    path.chmod(0o400)
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(65536):
            h.update(block)
    entry = proof["files"][0]
    entry.update(
        presence="present_nonempty", rows=count, bytes=path.stat().st_size, sha256=h.hexdigest()
    )
    proof["total_bytes"] = sum(x["bytes"] for x in proof["files"])
    raw = (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()
    p = source / "capture.json"
    p.chmod(0o600)
    p.write_bytes(raw)
    p.chmod(0o400)
    capsha = digest(raw)
    assert proof["total_bytes"] > 512 * 1024**2
    tracemalloc.start()
    try:
        result = parts.prepare_index(
            source, capsha, tmp_path / "index.json", checkpoint=lambda: None
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64 * 1024**2
    assert (
        sum(item["rows"] for item in result["index"]["parts"] if item["table"] == "wav_feature")
        == count
    )
    assert all(item["bytes"] <= 32 * 1024**2 for item in result["index"]["parts"])


def test_source_identity_and_completed_snapshot_window_are_required(tmp_path):
    source, proof, _ = manifest(tmp_path, {})
    capture_file = source / "capture.json"
    for mutation in ("wrong_actor", "wrong_address", "expired_window"):
        altered = json.loads(json.dumps(proof))
        if mutation == "wrong_actor":
            altered["source_identity"]["session_oid"] = 10
        elif mutation == "wrong_address":
            altered["source_identity"]["server_address"] = "192.0.2.1/32"
        else:
            altered["finished_at"] = (
                datetime.fromisoformat(altered["started_at"]) + timedelta(seconds=111)
            ).isoformat()
        raw = json.dumps(altered, sort_keys=True, separators=(",", ":")).encode()
        capture_file.chmod(0o600)
        capture_file.write_bytes(raw)
        capture_file.chmod(0o400)
        with pytest.raises(ArchiveBlocked):
            parts.load_capture(source, digest(raw), checkpoint=lambda: None)


def test_unfinished_capture_cannot_be_resumed(tmp_path):
    source = tmp_path / "unfinished"
    source.mkdir(mode=0o700)
    with pytest.raises(FileNotFoundError):
        parts.load_capture(source, "a" * 64, checkpoint=lambda: None)


def test_science_archive_integration_is_explicitly_unimplemented():
    assert parts.archive_integration_contract()["implemented"] is False


def test_isolated_import_uses_only_explicit_minimal_app_files(tmp_path):
    backend = Path(__file__).resolve().parents[2]
    names = (
        "app/__init__.py",
        "app/direct/__init__.py",
        "app/direct/capture_assignment.py",
        "app/raw_archive/__init__.py",
        "app/raw_archive/catalog_capture.py",
        "app/raw_archive/catalog_stages.py",
        "app/raw_archive/catalog_chunks.py",
        "app/raw_archive/catalog_offline.py",
        "app/raw_archive/wav_catalog.py",
        "app/raw_archive/policy.py",
        "app/raw_archive/recovery.py",
        "app/raw_archive/telemetry.py",
        "app/raw_archive/health.py",
        "app/raw_archive/scientific_capture.py",
        "app/raw_archive/scientific_scope_v3.py",
        "app/raw_archive/scientific_parts.py",
    )
    isolated = tmp_path / "backend"
    for name in names:
        destination = isolated / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(backend / name, destination)
    program = """
import json, pathlib, sys
root=pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0,str(root))
import app.raw_archive.scientific_capture
import app.raw_archive.scientific_parts
items={name:str(pathlib.Path(module.__file__).resolve()) for name,module in sys.modules.copy().items() if name=='app' or name.startswith('app.')}
assert all(pathlib.Path(value).is_relative_to(root) for value in items.values())
print(json.dumps(sorted(items)))
"""
    child = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(isolated)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    assert "app.raw_archive.scientific_capture" in json.loads(child.stdout)


def test_capture_framed_sink_supports_explicit_counter_factory(tmp_path):
    constructed = []

    class Counter(capture.FrameCounter):
        def __init__(self, columns):
            constructed.append(columns)
            super().__init__(columns)

    with (tmp_path / "native").open("wb") as output:
        sink = capture.FramedSink(output, lambda: None, 4096, 62, Counter)
        data = capture.HEADER + row(62, b"\x01" * 16) + capture.TRAILER
        for offset in range(0, len(data), 3):
            sink.write(data[offset : offset + 3])
        assert sink.counter.finish() == 1
    assert constructed == [62]


def test_linux_native_hash_advises_each_bounded_eight_mib(tmp_path, monkeypatch):
    path = tmp_path / "native-file"
    path.write_bytes(b"x" * (9 * 1024**2))
    path.chmod(0o400)
    calls = []
    monkeypatch.setattr(parts.sys, "platform", "linux")
    monkeypatch.setattr(parts.os, "POSIX_FADV_DONTNEED", 4, raising=False)
    monkeypatch.setattr(parts.os, "posix_fadvise", lambda *args: calls.append(args), raising=False)
    assert parts._hash(path, lambda: None) == digest(b"x" * (9 * 1024**2))
    assert len(calls) == 2 and calls[0][2] == 8 * 1024**2 and calls[1][2] == 1024**2


def test_source_hash_yields_between_eight_mib_without_changing_digest(tmp_path, monkeypatch):
    import time

    path = tmp_path / "native-file"
    body = b"x" * (17 * 1024**2)
    path.write_bytes(body)
    path.chmod(0o400)
    sleeps = []
    checks = []
    monkeypatch.setattr(parts.sys, "platform", "linux")
    monkeypatch.setattr(parts.os, "POSIX_FADV_DONTNEED", 4, raising=False)
    monkeypatch.setattr(parts.os, "posix_fadvise", lambda *args: None, raising=False)
    monkeypatch.setattr(time, "sleep", lambda value: sleeps.append(value))
    assert parts._hash(path, lambda: checks.append(True), cooperative_pause=0.25) == digest(body)
    assert sleeps == [0.25, 0.25]
    assert len(checks) >= 17 * 16
    for invalid in (-1, True, float("nan"), 0.251):
        with pytest.raises(ArchiveBlocked, match="cooperative hash pause"):
            parts._hash(path, lambda: None, cooperative_pause=invalid)
