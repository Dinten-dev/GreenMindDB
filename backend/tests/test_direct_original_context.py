"""Local-only Direct closure tests; no Source, server or provider operations."""

import hashlib
import io
import json
from pathlib import Path

import pytest

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import codec
from app.tiered_archive import direct_original_context as direct
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.storage import Store


def encoded(items):
    return b"".join(codec.encode(item) + b"\n" for item in items)


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    root = tmp_path / "capture"
    evidence = root / "evidence"
    evidence.mkdir(parents=True)
    parent = tmp_path / "work"
    parent.mkdir(mode=0o700)
    organization = "11111111-1111-4111-8111-111111111111"
    rows = [{"identity": f"{i:064x}", "organization": organization, "sensor": "device-fixture", "time_us": 1793520000000000 + i, "body": {"row": {"id": i}, "context": {"original_payload": b"a" * 760}}} for i in range(601)]
    digest = hashlib.sha256()
    for row in rows:
        value = codec.encode(row)
        digest.update(len(value).to_bytes(8, "big") + value)
    manifest = {"runs": [], "fixture": True}
    revisions = [{"reference": ["fixture", 1], "record": {"id": 1}}]
    wavs = {f"direct/device/session/{n}.wav": b"RIFF-synthetic-" + bytes([n]) for n in range(3)}
    pins = {k: {"bytes": len(v), "sha256": hashlib.sha256(v).hexdigest()} for k, v in wavs.items()}
    values = {
        "original-context.jsonl": encoded(rows),
        "manifest.jsonl": encoded([manifest]),
        "dependency-pins.jsonl": encoded([pins]),
        "detached-reconstruction-candidates.jsonl": encoded([{"candidate": i} for i in range(601)]),
        "dependency-revisions.jsonl": encoded(revisions),
    }
    for name, value in values.items():
        (evidence / name).write_bytes(value)
        (evidence / name).chmod(0o600)
    monkeypatch.setattr(direct, "CAPTURE_ROOT", root)
    monkeypatch.setattr(direct, "CAPTURE_FILES", {k: (len(v), hashlib.sha256(v).hexdigest()) for k, v in values.items()})
    monkeypatch.setattr(direct, "SOURCE_ROWS_SHA", digest.hexdigest())
    monkeypatch.setattr(direct, "REVISION_SHA", hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    monkeypatch.setattr(direct, "DEPENDENCY_SHA", codec.digest(revisions))
    monkeypatch.setattr(direct, "private_dir", lambda path: Path(path))
    monkeypatch.setattr(direct, "_runtime_guard", lambda *_: None)
    monkeypatch.setattr(direct, "require_bound_source_lease", lambda: None)

    def read_file(path, size, sha, *, checkpoint):
        raw = path.read_bytes()
        checkpoint()
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
            raise ArchiveBlocked("capture differs")
        return raw

    monkeypatch.setattr(direct, "_read_exact", read_file)

    class Client:
        gets = 0
        closed = False

        def head_object(self, *, Bucket, Key):
            return {"ContentLength": len(wavs[Key]), "ETag": "fixed", "VersionId": "v1", "LastModified": "same"}

        def get_object(self, *, Bucket, Key, VersionId):
            self.gets += 1
            assert VersionId == "v1"
            return {"Body": io.BytesIO(wavs[Key])}

        def close(self):
            self.closed = True

    client = Client()
    monkeypatch.setattr(direct, "_source_client", lambda: (client, "fixture"))
    return root, parent, client, wavs


def test_complete_encrypted_quarantined_bundle_with_three_gets_and_resume(synthetic, tmp_path, monkeypatch):
    _root, parent, client, wavs = synthetic
    key = bytes(range(32))
    staged = direct.stage(parent, key, checkpoint=lambda: None)
    assert staged["quarantined"] is True and staged["published"] is False and client.gets == 3 and client.closed
    directory = Path(staged["stage"])
    assert sorted(p.name for p in directory.iterdir()) == ["0000.parquet", *[f"{i:04d}.bin" for i in range(1, 9)], "stage.json"]
    assert all(value not in (directory / "stage.json").read_bytes() for value in wavs.values())
    ledger = Ledger(tmp_path / "ledger")
    store = object.__new__(Store)
    monkeypatch.setattr(direct, "_check_staged_file", lambda path, ref: checksum(path) == ref["sha256"] or (_ for _ in ()).throw(ArchiveBlocked("changed")))
    published = []

    def publish(candidate, files, _store, _ledger, *, checkpoint):
        published.append((candidate, files))
        return {"state": "published", "source": {"quarantined": candidate.quarantined}}

    monkeypatch.setattr(direct.pipeline, "publish", publish)
    try:
        result = direct.publish_stage(directory, store, ledger, checkpoint=lambda: None)
        assert result["quarantined"] is True and result["archive_losslessness_proven"] is False
        assert published[0][0].dataset == "direct_original_context" and published[0][0].kind == "direct_payload"
        direct.publish_stage(directory, store, ledger, checkpoint=lambda: None)
        assert client.gets == 3
        (directory / "0008.bin").write_bytes(b"changed")
        with pytest.raises(ArchiveBlocked, match="changed"):
            direct.publish_stage(directory, store, ledger, checkpoint=lambda: None)
    finally:
        ledger.close()


def test_source_get_changed_during_read_holds_without_publication(synthetic, tmp_path, monkeypatch):
    _root, parent, client, _wavs = synthetic
    before = client.head_object
    calls = []

    def changed(**kwargs):
        calls.append(1)
        value = before(**kwargs)
        if len(calls) == 2:
            value["ETag"] = "changed"
        return value

    monkeypatch.setattr(client, "head_object", changed)
    with pytest.raises(ArchiveBlocked, match="changed"):
        direct.stage(parent, bytes(range(32)), checkpoint=lambda: None)
    assert client.gets == 1
    stages = list(parent.iterdir())
    assert len(stages) == 1 and not (stages[0] / "stage.json").exists()


def test_capture_digest_change_fails_before_source_get(synthetic):
    root, parent, client, _ = synthetic
    path = root / "evidence" / "manifest.jsonl"
    path.write_bytes(path.read_bytes() + b"corruption")
    with pytest.raises(ArchiveBlocked, match="capture differs"):
        direct.stage(parent, bytes(range(32)), checkpoint=lambda: None)
    assert client.gets == 0


def test_independent_detached_reconstruction_uses_only_three_archived_wavs(tmp_path, monkeypatch):
    import copy
    import importlib.util

    from sqlalchemy import select

    from app.direct.models import Revision, Segment

    project = Path(__file__).resolve().parents[4]
    fixture_root = project / "outputs/greenmind-approved-direct-original-controller-20261101"
    monkeypatch.syspath_prepend(str(fixture_root))
    spec = importlib.util.spec_from_file_location("direct_native_fixture", fixture_root / "test_derived_reference.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    import derived_reference

    engine, db, source_rows, contexts, waves, _statements = fixture.fixture()
    try:
        archived, candidates, pairs = [], [], set()
        for (row, payload), context in zip(source_rows, contexts, strict=True):
            candidate = derived_reference.prepare(db, row, context, lambda: None)
            candidates.append(candidate)
            archived.append({"body": {"row": copy.deepcopy(row), "context": {**copy.deepcopy(context), "original_payload": payload}}})
            pairs.update((p["segment_id"], p["revision"]) for p in candidate["candidate"]["parts"])
        revisions = []
        for segment, revision in sorted(pairs):
            record = dict(db.execute(select(Segment.device_id, Segment.session_id, Revision.manifest_sha256, Revision.verified_at, Revision.raw_deleted_at, Revision.manifest).join(Revision, Revision.segment_id == Segment.id).where(Segment.id == segment, Revision.revision == revision)).mappings().one())
            revisions.append({"reference": [segment, revision], "record": record})
        manifest = revisions[0]["record"]["manifest"]
        monkeypatch.setattr(direct, "REVISION_SHA", hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        monkeypatch.setattr(direct, "DEPENDENCY_SHA", codec.digest(revisions))
        monkeypatch.setattr(direct, "SOURCE_CHUNKS", 2)
        monkeypatch.setattr(direct, "SOURCE_ORIGINAL_BYTES", 8)
        digest = hashlib.sha256()
        for row in archived:
            raw = codec.encode(row)
            digest.update(len(raw).to_bytes(8, "big") + raw)
        monkeypatch.setattr(direct, "SOURCE_ROWS_SHA", digest.hexdigest())
        pins = {name: {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()} for name, value in waves.items()}
        paths = {}
        for index, (name, value) in enumerate(waves.items()):
            path = tmp_path / f"{index}.wav"
            path.write_bytes(value)
            paths[name] = path
        result = direct._verify_archived_closure(archived, manifest, pins, candidates, revisions, paths, checkpoint=lambda: None)
        assert result["chunks"] == 2 and result["independently_restored_wavs"] == 3
        mutated_rows = copy.deepcopy(archived)
        mutated_rows[0]["body"]["context"]["original_payload"] = b"bad"
        with pytest.raises(ArchiveBlocked, match="original bytes differ"):
            direct._verify_archived_closure(mutated_rows, manifest, pins, candidates, revisions, paths, checkpoint=lambda: None)
        changed_reference = copy.deepcopy(archived)
        changed_reference[0]["body"]["context"]["payload_reference"] = {"version": 1}
        with pytest.raises(ArchiveBlocked, match="NULL"):
            direct._verify_archived_closure(changed_reference, manifest, pins, candidates, revisions, paths, checkpoint=lambda: None)
        altered = copy.deepcopy(candidates)
        altered[0]["original_metadata_sha256"] = "0" * 64
        with pytest.raises(ArchiveBlocked, match="provenance"):
            direct._verify_archived_closure(archived, manifest, pins, altered, revisions, paths, checkpoint=lambda: None)
        next(iter(paths.values())).write_bytes(b"corrupt")
        with pytest.raises(ArchiveBlocked, match="WAV differs"):
            direct._verify_archived_closure(archived, manifest, pins, candidates, revisions, paths, checkpoint=lambda: None)
    finally:
        db.close()
        engine.dispose()


def test_independent_readback_authenticates_every_part_without_source_fallback(tmp_path, monkeypatch):
    import copy
    import importlib.util
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.direct.models import Revision, Segment
    from app.raw_archive.read_only_box import CatalogReadOnlyBox
    from app.tiered_archive import scientific_registry_import

    project = Path(__file__).resolve().parents[4]
    fixture_root = project / "outputs/greenmind-approved-direct-original-controller-20261101"
    monkeypatch.syspath_prepend(str(fixture_root))
    spec = importlib.util.spec_from_file_location("direct_readback_fixture", fixture_root / "test_derived_reference.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    import derived_reference

    engine, db, source_rows, contexts, waves, _statements = fixture.fixture()
    try:
        archived, candidates, pairs = [], [], set()
        for index, ((row, payload), context) in enumerate(zip(source_rows, contexts, strict=True)):
            candidate = derived_reference.prepare(db, row, context, lambda: None)
            candidates.append(candidate)
            archived.append({"identity": f"{index:064x}", "organization": "11111111-1111-4111-8111-111111111111", "sensor": row["device_id"], "time_us": 1793520000000000 + index, "body": {"row": copy.deepcopy(row), "context": {**copy.deepcopy(context), "original_payload": payload}}})
            pairs.update((part["segment_id"], part["revision"]) for part in candidate["candidate"]["parts"])
        revisions = []
        for segment, revision in sorted(pairs):
            record = dict(db.execute(select(Segment.device_id, Segment.session_id, Revision.manifest_sha256, Revision.verified_at, Revision.raw_deleted_at, Revision.manifest).join(Revision, Revision.segment_id == Segment.id).where(Segment.id == segment, Revision.revision == revision)).mappings().one())
            revisions.append({"reference": [segment, revision], "record": record})
        manifest = revisions[0]["record"]["manifest"]
        pins = {name: {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()} for name, value in waves.items()}
        files = {"original-context.jsonl": encoded(archived), "manifest.jsonl": encoded([manifest]), "dependency-pins.jsonl": encoded([pins]), "detached-reconstruction-candidates.jsonl": encoded(candidates), "dependency-revisions.jsonl": encoded(revisions)}
        root = tmp_path / "capture"
        evidence = root / "evidence"
        evidence.mkdir(parents=True)
        parent = tmp_path / "work"
        parent.mkdir(mode=0o700)
        for name, raw in files.items():
            (evidence / name).write_bytes(raw)
        digest = hashlib.sha256()
        for item in archived:
            raw = codec.encode(item)
            digest.update(len(raw).to_bytes(8, "big") + raw)
        monkeypatch.setattr(direct, "CAPTURE_ROOT", root)
        monkeypatch.setattr(direct, "CAPTURE_FILES", {name: (len(raw), hashlib.sha256(raw).hexdigest()) for name, raw in files.items()})
        monkeypatch.setattr(direct, "SOURCE_CHUNKS", 2)
        monkeypatch.setattr(direct, "SOURCE_ORIGINAL_BYTES", 8)
        monkeypatch.setattr(direct, "SOURCE_ROWS_SHA", digest.hexdigest())
        monkeypatch.setattr(direct, "DEPENDENCY_SHA", codec.digest(revisions))
        monkeypatch.setattr(direct, "REVISION_SHA", hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        monkeypatch.setattr(direct, "private_dir", lambda path: Path(path))
        monkeypatch.setattr(direct, "_runtime_guard", lambda *_: None)
        monkeypatch.setattr(direct, "require_bound_source_lease", lambda: None)
        monkeypatch.setattr(direct, "_read_exact", lambda path, size, sha, **_: path.read_bytes() if len(path.read_bytes()) == size and checksum(path) == sha else (_ for _ in ()).throw(ArchiveBlocked("source changed")))
        monkeypatch.setattr(direct, "_check_staged_file", lambda path, ref: None if checksum(path) == ref["sha256"] else (_ for _ in ()).throw(ArchiveBlocked("stage changed")))

        class S3:
            def head_object(self, *, Bucket, Key):
                return {"ContentLength": len(waves[Key]), "ETag": "fixed"}

            def get_object(self, *, Bucket, Key, **_):
                return {"Body": io.BytesIO(waves[Key])}

            def close(self):
                pass

        monkeypatch.setattr(direct, "_source_client", lambda: (S3(), "fixture"))
        key = bytes(range(32))
        stage = direct.stage(parent, key, checkpoint=lambda: None)
        monkeypatch.setattr(direct, "_source_client", lambda: (_ for _ in ()).throw(AssertionError("source fallback")))
        archive = {}
        publisher = Store(SimpleNamespace(identity="synthetic-box"))
        ro_transport = object.__new__(CatalogReadOnlyBox)
        ro_transport.identity = "synthetic-box"
        verifier = Store(ro_transport)
        monkeypatch.setattr(Store, "publish", lambda self, object_key, path: archive.__setitem__(object_key, path.read_bytes()))
        monkeypatch.setattr(Store, "download", lambda self, object_key, path, size, **_: path.write_bytes(archive[object_key]))
        monkeypatch.setattr(scientific_registry_import, "_provider_proof", lambda *_: None)
        ledger = Ledger(tmp_path / "ledger")
        try:
            published = direct.publish_stage(stage["stage"], publisher, ledger, checkpoint=lambda: None)
            with pytest.raises(ArchiveBlocked, match="Independent matching"):
                direct.independent_readback(published["candidate"], ledger, publisher, publisher, key, provider_proof_path="/fixture", provider_proof_sha256="a" * 64, provider_terminal_path="/fixture", provider_terminal_sha256="b" * 64, checkpoint=lambda: None)
            result = direct.independent_readback(published["candidate"], ledger, publisher, verifier, key, provider_proof_path="/fixture", provider_proof_sha256="a" * 64, provider_terminal_path="/fixture", provider_terminal_sha256="b" * 64, checkpoint=lambda: None)
            assert result["chunks"] == 2 and result["independently_restored_wavs"] == 3
            assert result["quarantined"] is True and result["snapshot_accepted"] is False and result["source_data_accesses"] == 0
            receipt = ledger.get(published["candidate"])
            archive[receipt["files"][8]["key"]] = b"tampered"
            with pytest.raises(ArchiveBlocked, match="ciphertext differs"):
                direct.independent_readback(published["candidate"], ledger, publisher, verifier, key, provider_proof_path="/fixture", provider_proof_sha256="a" * 64, provider_terminal_path="/fixture", provider_terminal_sha256="b" * 64, checkpoint=lambda: None)
            assert ledger.get(published["candidate"])["state"] == "published"
        finally:
            ledger.close()
    finally:
        db.close()
        engine.dispose()
