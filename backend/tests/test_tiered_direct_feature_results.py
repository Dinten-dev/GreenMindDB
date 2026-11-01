import copy

import pytest

from app.raw_archive.direct_feature_history import FORMAT, validate_history
from app.raw_archive.policy import ArchiveBlocked
from app.services.signal_feature_contract import canonical_sha256
from tests.feature_contract_fixtures import feature_data as data


def history():
    inputs, payload = data()
    row = dict(
        id="r1",
        segment_id=inputs["segment_id"],
        revision=inputs["revision"],
        manifest_sha256=inputs["manifest_sha256"],
        input_sha256=canonical_sha256(inputs),
        inputs=inputs,
        payload=payload,
        payload_sha256=canonical_sha256(payload),
        created_at=1.0,
        verified_at=2.0,
    )
    revision = {
        "segment_id": inputs["segment_id"],
        "revision": inputs["revision"],
        "manifest_sha256": inputs["manifest_sha256"],
        "manifest": {
            "device_id": inputs["device_id"],
            "session_id": inputs["session_id"],
            "config": {"channel_labels": [inputs["channel_label"]]},
            "runs": [
                {
                    "key": inputs["wav_key"],
                    "sha256": inputs["wav_sha256"],
                    "first_frame": inputs["first_frame"],
                    "frame_count": inputs["frame_count"],
                }
            ],
        },
    }
    body = dict(
        format=FORMAT,
        segment_id=inputs["segment_id"],
        revision=inputs["revision"],
        manifest_sha256=inputs["manifest_sha256"],
        results=[row],
    )
    return {**body, "sha256": canonical_sha256(body)}, revision


def test_history_digest_binds_complete_source_and_results():
    value, revision = history()
    assert validate_history(value, revision) == value["sha256"]
    tampered = copy.deepcopy(value)
    tampered["results"][0]["payload"]["quality"]["data_quality_status"] = "valid"
    with pytest.raises(ArchiveBlocked):
        validate_history(tampered, revision)
    revision["manifest"]["config"]["channel_labels"].append("CH2")
    with pytest.raises(ArchiveBlocked, match="every run and channel"):
        validate_history(value, revision)


def test_new_result_invalidates_previous_scientific_digest():
    value, revision = history()
    new = copy.deepcopy(value)
    new["results"][0]["id"] = "different"
    new["sha256"] = canonical_sha256({k: v for k, v in new.items() if k != "sha256"})
    assert validate_history(new, revision) != validate_history(value, revision)
