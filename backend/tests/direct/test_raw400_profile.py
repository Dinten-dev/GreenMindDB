"""Raw 400 Hz profiles preserve PCM, gaps, replay identity and legacy WAV rates."""

import hashlib
import io
import json
import struct
import time
import uuid
import wave

import numpy as np
import pytest
from pydantic import ValidationError

from app.direct.assembler import assemble_one
from app.direct.database import transaction
from app.direct.models import Budget, Chunk, Device, Revision, Segment, Session
from app.services.wav_feature_service import _read_pcm_wav
from app.services.wav_service import extract_wav_metadata
from app.visualization.direct import decode


def raw_meta(p, payload, **changes):
    return p.metadata(
        payload,
        **(
            dict(
                sample_rate=400,
                channels=1,
                sample_bits=16,
                frame_count=len(payload) // 2,
                channel_labels=["CH1"],
                calibration_version="unsigned-mv-linear-int16-v1",
                signal_profile="ad8232-raw400-v1",
            )
            | changes
        ),
    )


def manifest(p):
    with transaction(p.engine) as db:
        segment = db.query(Segment).order_by(Segment.bucket).first()
        return db.get(Revision, (segment.id, segment.published_revision)).manifest


def test_raw400_upload_retry_wav_and_millivolt_scaling_keep_both_rails(pipeline):
    p = pipeline
    # Includes values above HPK mV*10's signed overflow boundary, both rails,
    # low bits and nonconstant edges. No filtering, clipping or resampling.
    values = [0, 8, 16383, 32726, 32758, 32767, 16000, 1234] * 50
    payload = struct.pack("<400h", *values)
    meta = raw_meta(p, payload)
    assert p.upload(payload, meta).status_code == 201
    assert p.upload(payload, meta).json()["status"] == "duplicate"
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    data = manifest(p)
    assert data["config"]["signal_profile"] == "ad8232-raw400-v1"
    assert data["config"]["sample_rate"] == 400
    run = data["runs"][0]
    raw = p.store.get(run["key"])
    with wave.open(io.BytesIO(raw), "rb") as wav:
        assert (wav.getframerate(), wav.getnframes(), wav.getsampwidth()) == (400, 400, 2)
        assert wav.readframes(400) == payload
    decoded, unit = decode(payload, data["config"])
    assert unit == "mV"
    assert decoded[0, 0] == 0 and decoded[5, 0] == 3300
    assert decoded[3, 0] > 3276.7 and decoded[3, 0] < decoded[4, 0] < 3300
    assert run["frame_count"] == 400
    with transaction(p.engine) as db:
        assert db.query(Chunk).count() == 1
        assert db.get(Budget, 1).used_bytes == 800


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_rate": 380},
        {"sample_rate": 100},
        {"sample_bits": 24},
        {"channels": 2, "channel_labels": ["CH1", "CH2"]},
        {"calibration_version": "hpk-mv-x10-v1"},
        {"signal_profile": "invented-raw-v9"},
    ],
)
def test_raw_profile_rejects_contradictory_clock_or_encoding(pipeline, changes):
    with pytest.raises(ValidationError):
        raw_meta(pipeline, bytes(800), **changes)


def test_raw400_rejects_signed_negative_samples_without_persistence(pipeline):
    p = pipeline
    payload = struct.pack("<2h", -1, 32767)
    result = p.upload(payload, raw_meta(p, payload))
    assert result.status_code == 422
    assert result.json()["error"] == "raw_profile_pcm_out_of_range"
    with transaction(p.engine) as db:
        assert db.query(Chunk).count() == 0
        assert db.get(Budget, 1).used_bytes == 0


def test_sample_gap_produces_separate_raw_wav_runs(pipeline):
    p = pipeline
    before, after = bytes(800), b"\xff\x7f" * 400
    assert p.upload(before, raw_meta(p, before)).status_code == 201
    assert p.upload(after, raw_meta(p, after, sequence=1, first_frame=420)).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    data = manifest(p)
    assert [400, 420] in data["missing_frame_ranges"]
    assert [r["first_frame"] for r in data["runs"]] == [0, 420]
    assert [r["frame_count"] for r in data["runs"]] == [400, 400]


def test_profile_change_requires_a_new_session(pipeline):
    p = pipeline
    payload = bytes(800)
    meta = raw_meta(p, payload)
    assert p.upload(payload, meta).status_code == 201
    changed = meta.model_copy(update={"signal_profile": None, "sequence": 1, "first_frame": 400})
    result = p.upload(payload, changed)
    assert result.status_code == 409
    assert result.json()["error"] == "session_configuration_conflict"
    new = changed.model_copy(update={"session_id": uuid.uuid4(), "sequence": 0, "first_frame": 0})
    assert p.upload(payload, new).status_code == 201


def test_old_session_config_and_ack_digest_survive_profile_extension(pipeline):
    p = pipeline
    payload = bytes(1200)
    meta = p.metadata(payload)
    old = meta.model_dump(mode="json", exclude={"signal_profile"})
    digest = hashlib.sha256(
        json.dumps(old, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    config = {
        k: v
        for k, v in old.items()
        if k not in {"sequence", "first_frame", "frame_count", "payload_sha256"}
    }
    assert meta.digest() == digest
    assert meta.session_config() == config and "signal_profile" not in config
    assert p.upload(payload, meta).status_code == 201
    with transaction(p.engine) as db:
        assert db.query(Chunk).one().digest == digest
        assert db.query(Session).one().config == config
    assert p.upload(payload, meta).json()["status"] == "duplicate"


def test_dual_mode_keeps_the_existing_380_hz_comparison_contract(pipeline):
    p = pipeline
    with transaction(p.engine) as db:
        db.get(Device, p.device_id).mode = "DUAL"
    payload = bytes(800)
    assert p.upload(payload, raw_meta(p, payload)).status_code == 422
    legacy = raw_meta(p, bytes(760), sample_rate=380, signal_profile=None)
    assert p.upload(bytes(760), legacy).status_code == 201


@pytest.mark.parametrize("rate", [100, 380, 400])
def test_wav_readers_use_the_original_header_rate_and_keep_samples(tmp_path, rate):
    # HPK 100 Hz and existing 380 Hz files are never relabelled as raw400.
    samples = np.array([0, 16000, 32700, 32767] * rate, dtype="<i2")
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    details = extract_wav_metadata(io.BytesIO(data.getvalue()))
    assert details["sample_rate"] == rate and details["duration_seconds"] == 4
    path = tmp_path / f"legacy-{rate}.wav"
    path.write_bytes(data.getvalue())
    read, actual_rate = _read_pcm_wav(path)
    assert actual_rate == rate
    assert np.array_equal(read, samples)
