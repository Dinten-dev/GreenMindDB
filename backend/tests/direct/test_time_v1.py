import hashlib
import io
import time
import wave
from datetime import UTC, datetime

import pytest

from app.direct.assembler import assemble_one, build_manifest
from app.direct.database import transaction
from app.direct.models import Revision, Segment
from app.direct.storage import KEY_RE
from app.raw_archive import wav_catalog


def test_v1_timestamps_sequence_gaps_retry_and_unchanged_pcm(pipeline, monkeypatch):
    p = pipeline
    p.cfg.timestamp_object_keys_enabled = True
    p.start_us = int(datetime(2026, 11, 6, 21, tzinfo=UTC).timestamp()) * 1_000_000
    monkeypatch.setattr(time, "time", lambda: p.start_us / 1_000_000 + 1800)
    payload = b"\x01\x00" * 380
    config = dict(
        sample_rate=380,
        channels=1,
        sample_bits=16,
        frame_count=380,
        channel_labels=["CH1"],
        calibration_version="unsigned-mv-linear-int16-v1",
    )
    first = p.metadata(payload, sequence=7, first_frame=380, **config)
    assert p.upload(payload, first).status_code == 201
    assert p.upload(payload, first).json()["status"] == "duplicate"
    assert (
        p.upload(payload, p.metadata(payload, sequence=9, first_frame=1140, **config)).status_code
        == 201
    )
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        segment = db.query(Segment).one()
        revision = db.get(Revision, (segment.id, segment.published_revision))
        manifest = revision.manifest
        assert [r["key"] for r in build_manifest(db, segment, p.store)["runs"]] == [
            r["key"] for r in manifest["runs"]
        ]
        wav_catalog.validate_row(
            "direct",
            "direct_revision",
            dict(
                segment_id=segment.id,
                revision=revision.revision,
                manifest=manifest,
                manifest_sha256=revision.manifest_sha256,
                verified_at=revision.verified_at,
                raw_deleted_at=None,
            ),
        )
    assert manifest["protocol_version"] == 1
    assert manifest["missing_frame_ranges"][:2] == [[0, 380], [760, 1140]]
    for run, seq, frame, second in zip(manifest["runs"], [7, 9], [380, 1140], [1, 3], strict=True):
        key = run["key"]
        assert (
            f"/20261106T21000{second}000Z_e{p.start_us // 1000 + second * 1000}_q{seq}_f{frame}_"
            in key
        )
        assert "_b" not in key.split("/")[-1].rsplit("_", 1)[0]
        assert KEY_RE.fullmatch(key)
        assert run["started_at_us"] == p.start_us + second * 1_000_000
        assert "chunks" not in run  # Keep the version-1 archive schema unchanged.
        data = p.store.get(key)
        assert hashlib.sha256(data).hexdigest() == run["sha256"]
        with wave.open(io.BytesIO(data), "rb") as wav:
            assert (wav.getframerate(), wav.getnframes()) == (380, 380)
            assert wav.readframes(380) == payload


@pytest.mark.parametrize("protocol_version", [1, 2])
def test_timestamp_flag_disabled_keeps_hash_keys(pipeline, protocol_version):
    from tests.direct.test_time_v2 import meta

    p = pipeline
    payload = bytes(1200)
    metadata = p.metadata(payload) if protocol_version == 1 else meta(p, payload)
    assert p.upload(payload, metadata).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        segment = db.query(Segment).one()
        manifest = db.get(Revision, (segment.id, segment.published_revision)).manifest
    filename = manifest["runs"][0]["key"].split("/")[-1]
    assert len(filename) == 68 and KEY_RE.fullmatch(manifest["runs"][0]["key"])
    p.cfg.timestamp_object_keys_enabled = True
    assert p.store.get(manifest["runs"][0]["key"])


def test_v1_boundary_uses_piece_start_and_original_chunk_sequence(pipeline):
    p = pipeline
    p.cfg.timestamp_object_keys_enabled = True
    p.start_us = (int(time.time()) // 600 - 2) * 600_000_000 - 100_000
    payload = bytes(152)
    metadata = p.metadata(
        payload,
        sequence=6,
        sample_rate=380,
        channels=1,
        sample_bits=16,
        frame_count=76,
        channel_labels=["CH1"],
        calibration_version="unsigned-mv-linear-int16-v1",
    )
    assert p.upload(payload, metadata).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        runs = [
            db.get(Revision, (segment.id, segment.published_revision)).manifest["runs"][0]
            for segment in db.query(Segment).order_by(Segment.bucket)
        ]
    assert [r["frame_count"] for r in runs] == [38, 38]
    assert [r["started_at_us"] for r in runs] == [p.start_us, p.start_us + 100_000]
    assert "_q6_f0_" in runs[0]["key"] and "_q6_f38_" in runs[1]["key"]
    pcm = bytearray()
    for run in runs:
        with wave.open(io.BytesIO(p.store.get(run["key"])), "rb") as wav:
            pcm.extend(wav.readframes(wav.getnframes()))
    assert pcm == payload
