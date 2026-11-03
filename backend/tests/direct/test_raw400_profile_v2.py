"""V2 keeps nullable uncertainty and existing identity hashes unchanged."""

import hashlib
import json
import uuid

import pytest

from app.direct.protocol import ChunkMetadataV2


@pytest.mark.parametrize("profile", [None, "ad8232-raw400-v1"])
def test_v2_profile_preserves_time_uncertainty_and_durable_ack(pipeline, profile):
    p = pipeline
    payload = bytes(800)
    data = p.metadata(
        payload,
        sample_rate=400,
        channels=1,
        sample_bits=16,
        frame_count=400,
        channel_labels=["CH1"],
        calibration_version="unsigned-mv-linear-int16-v1",
        signal_profile=profile,
    ).model_dump(mode="json")
    data.update(
        protocol_version=2,
        boot_counter=1,
        counter_generation=str(uuid.uuid4()),
        timing={
            "source": "ntp",
            "sync_epoch_us": p.start_us - 1_000_000,
            "time_reference": "nominal_sample_clock",
            "uncertainty_us": None,
        },
    )
    meta = ChunkMetadataV2.model_validate(data)
    assert "uncertainty_us" in meta.session_config()["timing"]
    assert meta.session_config()["timing"]["uncertainty_us"] is None
    if profile is None:
        old = {k: v for k, v in data.items() if k != "signal_profile"}
        digest = hashlib.sha256(
            json.dumps(old, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert meta.digest() == digest
    assert p.upload(payload, meta).status_code == 201
    assert p.upload(payload, meta).json()["status"] == "duplicate"
