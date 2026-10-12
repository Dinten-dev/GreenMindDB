"""Reproduce spool exhaustion and prove lossless recovery, including late repair."""

import hashlib
import io
import time
import tracemalloc
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.direct.assembler import assemble_one
from app.direct.database import transaction
from app.direct.models import Budget, Chunk, Device, Heartbeat, PayloadReference, Revision, Segment
from app.direct.models import Session as CaptureSession
from app.direct.payloads import WavReader, offload_verified_chunks, read_payload
from app.direct.retention import run_retention


def archive_copy(p, folder):
    with transaction(p.engine) as db:
        keys = {run["key"] for rev in db.query(Revision).all() for run in rev.manifest["runs"]}
    for key in keys:
        path = folder / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(p.store.get(key))
    return lambda key: (folder / key).read_bytes()


def publish(p):
    while assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2):
        pass


def test_missing_source_cannot_be_hidden_by_archive_fallback(pipeline, tmp_path, monkeypatch):
    p = pipeline
    payload = bytes(1200)
    assert p.upload(payload).status_code == 201
    publish(p)
    archived = archive_copy(p, tmp_path / "independent-archive")
    p.cfg.archive_broker_socket = Path("/private/synthetic.sock")

    def unavailable(_key):
        raise FileNotFoundError("synthetic source missing")

    monkeypatch.setattr(p.store, "get_local", unavailable)
    monkeypatch.setattr(p.store, "get_archive", archived)
    with pytest.raises(FileNotFoundError):
        offload_verified_chunks(p.engine, p.store, archived)
    with transaction(p.engine) as db:
        assert db.query(Chunk).one().payload == payload
        assert db.query(PayloadReference).count() == 0
        assert db.get(Budget, 1).used_bytes == len(payload)
        assert db.get(Device, p.device_id).spool_bytes == len(payload)


def test_s3_source_verification_never_uses_generic_archive_fallback(
    pipeline, tmp_path, monkeypatch
):
    from botocore.exceptions import ClientError

    from app.direct.storage import archive_reader

    p = pipeline
    payload = bytes(1200)
    assert p.upload(payload).status_code == 201
    publish(p)
    archived = archive_copy(p, tmp_path / "s3-independent-archive")
    missing = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    class MissingSource:
        def get_object(self, **_kwargs):
            raise missing

    restored = []

    def restore(*, kind, bucket, key):
        restored.append(key)
        return {"Body": io.BytesIO(archived(key))}

    monkeypatch.setenv("RAW_ARCHIVE_READS_ENABLED", "true")
    monkeypatch.setattr(archive_reader, "restore_object", restore)
    p.store.client = MissingSource()
    with transaction(p.engine) as db:
        key = db.query(Revision).one().manifest["runs"][0]["key"]
    assert p.store.get(key) == archived(key)
    assert restored == [key]
    restored.clear()
    with pytest.raises(ClientError):
        offload_verified_chunks(p.engine, p.store, archived)
    assert restored == []
    with transaction(p.engine) as db:
        assert db.query(Chunk).one().payload == payload
        assert db.query(PayloadReference).count() == 0
        assert db.get(Budget, 1).used_bytes == len(payload)


def test_full_partial_spool_recovers_without_losing_late_frames(pipeline, tmp_path):
    p = pipeline
    p.cfg.max_spool_bytes = 2400
    one, gap, three = (bytes([value]) * 1200 for value in (1, 2, 3))
    assert p.upload(one).status_code == 201
    assert p.upload(three, p.metadata(three, sequence=2, first_frame=200)).status_code == 201
    publish(p)
    assert p.upload(gap, p.metadata(gap, sequence=1, first_frame=100)).status_code == 503
    health = p.client.get("/health")
    assert health.status_code == 200  # Liveness must not trigger a restart loop.
    assert health.json()["direct_ingest"] == "healthy"
    assert health.json()["accepting_max_size_chunks"] is False
    assert p.client.get("/readiness").json()["direct_ingest"] == "capacity_limited"
    assert p.client.get("/readiness").status_code == 503
    get_archive = archive_copy(p, tmp_path / "box")
    assert offload_verified_chunks(p.engine, p.store, get_archive) == 2400
    assert offload_verified_chunks(p.engine, p.store, get_archive) == 0
    with transaction(p.engine) as db:
        assert db.get(Budget, 1).used_bytes == db.get(Device, p.device_id).spool_bytes == 0
        assert db.query(PayloadReference).count() == 2
        assert db.query(Segment).one().sealed is False
    p.engine.dispose()
    assert p.upload(one).json()["status"] == "duplicate"
    assert p.upload(bytes(1200)).status_code == 409
    assert p.upload(gap, p.metadata(gap, sequence=1, first_frame=100)).status_code == 201
    publish(p)
    with transaction(p.engine) as db:
        segment = db.query(Segment).one()
        run = db.get(Revision, (segment.id, segment.published_revision)).manifest["runs"][0]
    with wave.open(io.BytesIO(p.store.get(run["key"])), "rb") as wav:
        assert wav.readframes(wav.getnframes()) == one + gap + three
    assert p.client.get("/health").json()["spool_bytes"] == 1200


def test_cross_boundary_reconstructs_both_wav_parts(pipeline, tmp_path):
    p = pipeline
    payload = b"".join(value.to_bytes(3, "little", signed=True) for value in range(800))
    assert p.upload(payload, p.metadata(payload, first_frame=299900)).status_code == 201
    publish(p)
    assert offload_verified_chunks(p.engine, p.store, archive_copy(p, tmp_path / "box")) == 2400
    with transaction(p.engine) as db:
        chunk = db.query(Chunk).one()
        config = db.get(CaptureSession, (chunk.device_id, chunk.session_id)).config
        assert len(db.get(PayloadReference, chunk.id).parts) == 2
        assert read_payload(db, chunk, config, WavReader(p.store.get)) == payload


def test_late_ingest_during_archive_read_keeps_immutable_reference(pipeline, tmp_path):
    p = pipeline
    first, late = bytes([1]) * 1200, bytes([2]) * 1200
    p.upload(first)
    publish(p)
    archived = archive_copy(p, tmp_path / "box")

    def arrive_during_read(key):
        assert p.upload(late, p.metadata(late, sequence=1, first_frame=100)).status_code == 201
        return archived(key)

    assert offload_verified_chunks(p.engine, p.store, arrive_during_read) == 1200
    publish(p)
    with transaction(p.engine) as db:
        segment = db.query(Segment).one()
        run = db.get(Revision, (segment.id, segment.published_revision)).manifest["runs"][0]
    with wave.open(io.BytesIO(p.store.get(run["key"])), "rb") as wav:
        assert wav.readframes(wav.getnframes()) == first + late


@pytest.mark.parametrize("failure", ["missing", "corrupt", "format"])
def test_archive_failure_never_releases_original(pipeline, tmp_path, failure):
    p = pipeline
    payload = bytes(1200)
    p.upload(payload)
    publish(p)
    archived = archive_copy(p, tmp_path / "box")

    def failed_read(key):
        if failure == "missing":
            raise FileNotFoundError("injected")
        value = archived(key)
        return value[:-1] + b"\xff" if failure == "corrupt" else b"invalid wav"

    with pytest.raises((ValueError, OSError)):
        offload_verified_chunks(p.engine, p.store, failed_read)
    with transaction(p.engine) as db:
        assert db.query(Chunk).one().payload == payload
        assert db.query(PayloadReference).count() == 0
        assert db.get(Budget, 1).used_bytes == 1200


def test_offload_commit_failure_is_atomic(pipeline, tmp_path):
    p = pipeline
    p.upload(bytes(1200))
    publish(p)
    archived = archive_copy(p, tmp_path / "box")

    def fail_reference_commit(connection):
        if connection.execute(PayloadReference.__table__.select()).first():
            raise OperationalError("commit", {}, RuntimeError("injected"))

    event.listen(p.engine, "commit", fail_reference_commit)
    try:
        with pytest.raises(OperationalError):
            offload_verified_chunks(p.engine, p.store, archived)
    finally:
        event.remove(p.engine, "commit", fail_reference_commit)
    with transaction(p.engine) as db:
        assert db.query(Chunk).one().payload == bytes(1200)
        assert db.query(PayloadReference).count() == 0
        assert db.get(Budget, 1).used_bytes == 1200
    assert offload_verified_chunks(p.engine, p.store, archived) == 1200


def test_concurrent_offload_accounts_once(pipeline, tmp_path):
    p = pipeline
    p.upload(bytes(1200))
    publish(p)
    archived = archive_copy(p, tmp_path / "box")
    with ThreadPoolExecutor(max_workers=2) as pool:
        released = list(
            pool.map(lambda _: offload_verified_chunks(p.engine, p.store, archived), range(2))
        )
    assert sum(released) == 1200
    with transaction(p.engine) as db:
        assert db.query(PayloadReference).count() == 1
        assert db.get(Budget, 1).used_bytes == db.get(Device, p.device_id).spool_bytes == 0


def test_limits_and_reference_pinning(pipeline, tmp_path):
    p = pipeline
    for sequence in range(3):
        p.upload(
            bytes([sequence]) * 1200,
            p.metadata(bytes([sequence]) * 1200, sequence=sequence, first_frame=100 * sequence),
        )
    publish(p)
    archived = archive_copy(p, tmp_path / "box")
    assert offload_verified_chunks(p.engine, p.store, archived, max_bytes=2400) == 2400
    with transaction(p.engine) as db:
        assert db.get(Budget, 1).used_bytes == 1200
        db.query(Segment).one().sealed = True
    p.cfg.retention_enabled, p.cfg.retention_dry_run = True, False
    assert run_retention(p.engine, p.cfg, p.store, now=time.time() + 800 * 86400)["segments"] == 0
    with transaction(p.engine) as db:
        assert db.query(PayloadReference).count() == 2
        assert db.query(Chunk).count() == 3
    assert offload_verified_chunks(p.engine, p.store, archived) == 1200


def test_reference_tampering_and_archived_only_read(pipeline, tmp_path, monkeypatch):
    p = pipeline
    p.upload(bytes(1200))
    publish(p)
    archived = archive_copy(p, tmp_path / "box")
    assert offload_verified_chunks(p.engine, p.store, archived) == 1200
    monkeypatch.setattr(p.store, "get_archive", archived)
    p.cfg.archive_broker_socket = tmp_path / "test-socket"
    with transaction(p.engine) as db:
        ref = db.query(PayloadReference).one()
        p.store._path(ref.parts[0]["key"]).unlink()
        chunk = db.query(Chunk).one()
        config = db.get(CaptureSession, (chunk.device_id, chunk.session_id)).config
        assert read_payload(db, chunk, config, WavReader(p.store.get)) == bytes(1200)
        ref.parts = [dict(ref.parts[0], run_offset=1)]
    with transaction(p.engine) as db, pytest.raises(ValueError):
        read_payload(db, db.query(Chunk).one(), config, WavReader(p.store.get))


def test_readiness_reports_persisted_progress(pipeline):
    p = pipeline
    p.upload(bytes(1200))
    with transaction(p.engine) as db:
        db.add(Heartbeat(name="assembler", status="healthy", updated_at=time.time()))
    response = p.client.get("/readiness")
    assert response.status_code == 200
    assert response.json()["last_persisted_at"] > time.time() - 60
    assert response.json()["spool_free_bytes"] == p.cfg.max_spool_bytes - 1200


def test_missing_archive_candidate_does_not_block_verified_candidate(pipeline, tmp_path):
    p = pipeline
    first, second = bytes([1]) * 1200, bytes([2]) * 1200
    p.upload(first)
    p.upload(second, p.metadata(second, sequence=1, first_frame=300000))
    publish(p)
    archived = archive_copy(p, tmp_path / "box")
    with transaction(p.engine) as db:
        revisions = db.query(Revision).join(Segment).order_by(Segment.bucket).all()
        available_key = revisions[1].manifest["runs"][0]["key"]
    requests = []

    def available(keys):
        assert available_key in keys and len(keys) == 2
        return {available_key}

    def read(key):
        requests.append(key)
        return archived(key)

    assert offload_verified_chunks(p.engine, p.store, read, archive_available=available) == 1200
    assert requests == [available_key]
    with transaction(p.engine) as db:
        assert db.get(Budget, 1).used_bytes == 1200
        assert db.query(Chunk).filter_by(sequence=0).one().payload == first
        assert db.query(PayloadReference).count() == 1


def test_availability_never_replaces_archive_readback(pipeline, tmp_path):
    p = pipeline
    p.upload(bytes(1200))
    publish(p)
    archive_copy(p, tmp_path / "box")

    def missing(key):
        raise FileNotFoundError(key)

    with pytest.raises(FileNotFoundError):
        offload_verified_chunks(p.engine, p.store, missing, archive_available=set)
    with transaction(p.engine) as db:
        assert db.query(Chunk).one().payload == bytes(1200)
        assert db.get(Budget, 1).used_bytes == 1200
        assert db.query(PayloadReference).count() == 0


def test_large_wav_reconstruction_does_not_duplicate_entire_pcm(pipeline):
    p = pipeline
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(bytes(4 * 1024**2))
    data = output.getvalue()
    run = dict(
        key=f"direct/{p.device_id}/{p.session_id}/{'a' * 64}.wav",
        sha256=hashlib.sha256(data).hexdigest(),
        frame_count=2 * 1024**2,
    )
    config = dict(channels=1, sample_bits=16, sample_rate=380)
    reader = WavReader(lambda _: data, cache_bytes=1024)
    tracemalloc.start()
    try:
        assert reader.read_range(run, config, p.device_id, p.session_id, 100, 380) == bytes(760)
        assert tracemalloc.get_traced_memory()[1] < 512 * 1024
    finally:
        tracemalloc.stop()
    assert reader.cached_bytes == 0
