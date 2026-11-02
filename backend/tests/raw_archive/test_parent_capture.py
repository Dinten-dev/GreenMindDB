"""Local-only faithful parent native framing/parts and readonly capture tests."""

import contextlib
import copy
import hashlib
import struct
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.raw_archive import parent_capture as parent
from app.raw_archive.parent_scope import SCOPE, SCOPE_SHA256
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.scientific_capture import HEADER, TRAILER, digest_json


def row(table, number, *, error=b"private-operational-error", null_error=False):
    values = []
    for c in table["columns"]:
        kind, name = c["type"], c["name"]
        if name in {"error", "feature_error"}:
            value = None if null_error else error
        elif not c["not_null"]:
            value = None
        elif kind == "uuid":
            value = uuid.UUID(int=number).bytes
        elif name in {"id", "segment_id", "device_id", "session_id"}:
            value = str(uuid.UUID(int=number)).encode()
        elif kind == "int4":
            value = struct.pack("!i", number)
        elif kind in {"int8", "timestamptz"}:
            value = struct.pack("!q", number)
        elif kind == "float8":
            value = struct.pack("!d", number + 0.5)
        elif kind == "bool":
            value = b"\x01"
        else:
            value = b"fixture"
        values.append(struct.pack("!i", -1 if value is None else len(value)) + (value or b""))
    return struct.pack("!h", len(values)) + b"".join(values)


def fixture_capture(tmp_path, source="gateway", rows=2):
    folder = tmp_path / source
    folder.mkdir(mode=0o700)
    entries = []
    for table in parent.scope(source)["tables"]:
        data = (
            HEADER
            + b"".join(row(table, n + 1, null_error=bool(n % 2)) for n in range(rows))
            + TRAILER
        )
        path = folder / (table["name"] + ".pgcopy")
        path.write_bytes(data)
        path.chmod(0o400)
        entries.append(
            {
                "table": table["name"],
                "file": path.name,
                "columns": table["columns"],
                "rows": rows,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "source_stat": parent.file_identity(path),
            }
        )
    now = datetime.now(UTC)
    proof = {
        "schema": 1,
        "format": "wav_pgcopy_native_v2",
        "kind": source,
        "capture_id": "a" * 32,
        "scope_sha256": SCOPE_SHA256,
        "metadata_sha256": SCOPE["metadata_sha256"],
        "source_identity": {
            **parent.scope(source)["identity"],
            "role": "greenmind_archive_reader_33fe395",
            "session_role": "greenmind_archive_reader_33fe395",
            "role_oid": 499352,
            "role_unprivileged": True,
            "read_only": "on",
            "in_recovery": False,
        },
        "snapshot_sha256": "b" * 64,
        "imported_snapshot_sha256": None,
        "schema_master_sha256": "c" * 64,
        "provider_scope_sha256": "d" * 64,
        "started_at": now.isoformat(),
        "finished_at": (now + timedelta(seconds=1)).isoformat(),
        "files": entries,
        "total_bytes": sum(f["bytes"] for f in entries),
        "source_read_only": True,
        "complete": True,
        "backup_anchor_reference_only": True,
        "backup_snapshot_consistency_proven": False,
        "cross_database_atomic": False,
        "same_snapshot_as_previous_science": False,
        "science_compatibility_proven": False,
    }
    sha = parent._save(folder / "capture.json", proof)
    return folder, proof, sha


@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_exact_full_rosters_native_parts_preserve_errors_nulls_and_retry(
    source, tmp_path, monkeypatch
):
    folder, proof, sha = fixture_capture(tmp_path, source)
    monkeypatch.setattr(parent, "MAX_PART_ROWS", 1)
    prepared = parent.prepare_index(folder, sha, tmp_path / "index.json", checkpoint=lambda: None)
    index = prepared["index"]
    assert len(index["parts"]) == 4
    for position, part in enumerate(index["parts"]):
        destination = tmp_path / f"part-{position}"
        parent.materialize_part(folder, sha, index, position, destination, checkpoint=lambda: None)
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == part["sha256"]
        assert (b"private-operational-error" in destination.read_bytes()) is (position % 2 == 0)
    expected = [25, 6] if source == "gateway" else [13, 7]
    assert [len(f["columns"]) for f in proof["files"]] == expected
    assert (
        not proof["same_snapshot_as_previous_science"] and not proof["science_compatibility_proven"]
    )


@pytest.mark.parametrize(
    "change",
    [
        "drop_table",
        "drop_column",
        "change_source",
        "change_backup",
        "claim_atomic",
        "claim_master_snapshot",
    ],
)
def test_changed_or_incomplete_parent_capture_is_rejected(tmp_path, change):
    _, proof, _ = fixture_capture(tmp_path)
    proof = copy.deepcopy(proof)
    if change == "drop_table":
        proof["files"].pop()
    elif change == "drop_column":
        proof["files"][0]["columns"].pop()
    elif change == "change_source":
        proof["source_identity"]["database_oid"] = 1
    elif change == "change_backup":
        proof["schema_master_sha256"] = None
    elif change == "claim_atomic":
        proof["same_snapshot_as_previous_science"] = True
    else:
        proof["backup_snapshot_consistency_proven"] = True
    with pytest.raises(ArchiveBlocked):
        parent.validate_capture(proof)


def test_part_index_missing_coverage_wrong_digest_and_changed_source_are_rejected(tmp_path):
    folder, proof, sha = fixture_capture(tmp_path)
    index = parent.prepare_index(folder, sha, tmp_path / "index.json", checkpoint=lambda: None)[
        "index"
    ]
    bad = copy.deepcopy(index)
    bad["parts"].pop()
    with pytest.raises(ArchiveBlocked):
        parent.validate_index(proof, bad, sha)
    bad = copy.deepcopy(index)
    bad["parts"][0]["sha256"] = "0" * 64
    with pytest.raises(ArchiveBlocked, match="digest"):
        parent.materialize_part(folder, sha, bad, 0, tmp_path / "bad-part", checkpoint=lambda: None)
    path = folder / proof["files"][0]["file"]
    path.chmod(0o600)
    path.write_bytes(b"changed")
    path.chmod(0o400)
    with pytest.raises(ArchiveBlocked, match="generation"):
        parent.load_capture(folder, sha)


def test_empty_table_parts_are_complete_and_bounded(tmp_path):
    folder, proof, sha = fixture_capture(tmp_path, rows=0)
    index = parent.prepare_index(folder, sha, tmp_path / "index.json", checkpoint=lambda: None)[
        "index"
    ]
    assert len(index["parts"]) == 2 and all(p["rows"] == 0 for p in index["parts"])
    assert all(p["bytes"] == len(HEADER) + len(TRAILER) for p in index["parts"])


def test_scope_is_exact_independent_actual_metadata_roster():
    assert digest_json(SCOPE) == SCOPE_SHA256
    assert (
        SCOPE["metadata_sha256"]
        == "6db7bf0366001367dc8c13240d7c6ad343ac868d4eb5ff83123fa24ec30cab17"
    )
    names = {t["name"] for d in SCOPE["databases"].values() for t in d["tables"]}
    assert names == {"wav_file", "visual_wav", "direct_segment", "direct_visual_segment"}


@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_actual_capture_uses_readonly_rr_and_all_operational_columns(tmp_path, monkeypatch, source):
    calls = []

    class DB:
        connection = SimpleNamespace(
            driver_connection=SimpleNamespace(cursor=lambda: SimpleNamespace(close=lambda: None))
        )

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execution_options(self, **kw):
            assert kw == {"isolation_level": "REPEATABLE READ"}
            return self

        def begin(self):
            return contextlib.nullcontext()

        def execute(self, query, *_):
            calls.append(str(query))
            return SimpleNamespace(scalar_one=lambda: "100:101:")

    monkeypatch.setattr(parent.scientific_capture, "_engine_binding", lambda *_: None)
    monkeypatch.setattr(parent.scientific_capture, "_source_resources", lambda *_: None)
    identity = {
        **parent.scope(source)["identity"],
        "role": "greenmind_archive_reader_33fe395",
        "session_role": "greenmind_archive_reader_33fe395",
        "role_oid": 499352,
        "role_unprivileged": True,
        "read_only": "on",
        "in_recovery": False,
    }
    monkeypatch.setattr(parent, "_identity", lambda *_: identity)
    metadata_calls = []
    monkeypatch.setattr(parent, "_schema", lambda db, table: metadata_calls.append(table["name"]))

    def copy_table(db, cursor, name, fields, sink, body, checkpoint, **kw):
        table = next(t for t in parent.scope(source)["tables"] if t["name"] == name)
        assert fields == [c["name"] for c in table["columns"]]
        checkpoint()
        sink.write(HEADER + row(table, 1) + TRAILER)
        return 1

    monkeypatch.setattr(parent.catalog_capture, "copy_gateway_blocks", copy_table)
    result = parent.capture(
        SimpleNamespace(connect=DB),
        source,
        tmp_path / "capture",
        checkpoint=lambda: None,
        lease_deadline=time.monotonic() + 95,
        schema_master_sha256="c" * 64,
        provider_scope_sha256="d" * 64,
    )
    proof = parent.load_capture(tmp_path / "capture", result["capture_sha256"], verify_files=True)
    assert proof["source_read_only"] and not proof["science_compatibility_proven"]
    assert calls.count("SET TRANSACTION READ ONLY") == 1
    assert len(metadata_calls) == 4
    assert all(s.startswith(("SET ", "SELECT ")) for s in calls)
    assert all(
        b"private-operational-error" in (tmp_path / "capture" / f["file"]).read_bytes()
        for f in proof["files"]
    )
