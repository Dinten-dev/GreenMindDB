import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.raw_archive import feature_backup
from app.raw_archive import wav_catalog as catalog
from app.raw_archive.deletion import verify_feature_v2
from app.raw_archive.direct_feature_history import (
    FORMAT,
    scientific_descriptor,
    verify_scientific_snapshot,
)
from app.raw_archive.policy import ArchiveBlocked
from app.services.signal_feature_contract import canonical_sha256
from tests.feature_contract_fixtures import feature_data


def test_scientific_gate_downloads_and_restores_every_part(tmp_path):
    inputs, payload = feature_data()
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
    history_body = dict(
        format=FORMAT,
        segment_id=inputs["segment_id"],
        revision=inputs["revision"],
        manifest_sha256=inputs["manifest_sha256"],
        results=[row],
    )
    history = dict(history_body, sha256=canonical_sha256(history_body))
    source = tmp_path / "source"
    source.mkdir()
    parts = catalog.Parts(source, "direct", lambda: None)
    parts.write(feature_backup.validate_v2_row("direct", "direct_feature_result", row))
    parts.finish()
    remote_parts = [
        dict(item, remote_key=f"production/catalog-backup/{item['sha256']}.jsonl.gz")
        for item in parts.files
    ]
    counts = {"direct": {"direct_feature_result": 1}}
    manifest = dict(
        schema=3,
        format=feature_backup.FORMAT_DIRECT_REVISION,
        environment="production",
        destination="test-box",
        allowlist_sha256=feature_backup.DIRECT_REVISION_ALLOWLIST_SHA256,
        scientific_coverage={
            key: inputs[key] for key in ("segment_id", "revision", "manifest_sha256")
        },
        tables=counts,
        files=remote_parts,
    )
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_ref = dict(
        key=f"production/catalog-backup/{digest}.json", sha256=digest, size=len(manifest_bytes)
    )
    now = datetime.now(UTC)
    snapshot = dict(
        name="snapshot-1",
        created_at=(now - timedelta(minutes=1)).isoformat(),
        provider_id=1,
        storage_box_id=2,
    )
    proof = dict(
        format="direct-feature-proof-v2",
        source="direct",
        revision=f"{inputs['segment_id']}:{inputs['revision']}",
        manifest_sha256=inputs["manifest_sha256"],
        history_sha256=history["sha256"],
        restored_history_sha256=history["sha256"],
        checked_at=(now - timedelta(seconds=30)).isoformat(),
        provider_snapshot={
            "provider_id": snapshot["provider_id"],
            "box_id": snapshot["storage_box_id"],
            "name": snapshot["name"],
            "created_at": snapshot["created_at"],
        },
        manifest=manifest_ref,
        parts=[
            dict(file=e["file"], key=e["remote_key"], size=e["bytes"], sha256=e["sha256"])
            for e in remote_parts
        ],
    )
    recording = SimpleNamespace(
        identity=proof["revision"], feature_digest=inputs["manifest_sha256"]
    )

    class Reader:
        identity = "test-box"

        def __init__(self):
            self.reads = []

        def download_catalog(self, key, path, limit, *, snapshot):
            self.reads.append((key, snapshot))
            data = (
                manifest_bytes
                if key == manifest_ref["key"]
                else (source / remote_parts[0]["file"]).read_bytes()
            )
            path.write_bytes(data)

    reader = Reader()
    descriptor = scientific_descriptor(manifest, history, manifest_ref)
    assert descriptor == {
        key: value
        for key, value in proof.items()
        if key not in {"checked_at", "provider_snapshot", "restored_history_sha256"}
    }
    constructed_output = tmp_path / "constructed-readback"
    constructed_output.mkdir()
    constructed = verify_scientific_snapshot(
        descriptor,
        recording,
        reader,
        snapshot,
        now,
        constructed_output,
        lambda _: (history, revision),
    )
    assert constructed["restored_history_sha256"] == history["sha256"]
    reader.reads.clear()
    output = tmp_path / "readback"
    output.mkdir()
    assert (
        verify_feature_v2(
            proof, recording, reader, snapshot, now, output, lambda _: (history, revision)
        )
        == history["sha256"]
    )
    assert len(reader.reads) == 1 + len(remote_parts)
    changed = dict(proof, restored_history_sha256="0" * 64)
    with pytest.raises(ArchiveBlocked):
        verify_feature_v2(
            changed, recording, reader, snapshot, now, output, lambda _: (history, revision)
        )
    stale = dict(proof, checked_at=(now - timedelta(days=2)).isoformat())
    with pytest.raises(ArchiveBlocked, match="stale"):
        verify_feature_v2(
            stale, recording, reader, snapshot, now, output, lambda _: (history, revision)
        )
    renewed_snapshot = dict(snapshot, checked_at=now.isoformat())
    renewed_output = tmp_path / "renewed-readback"
    renewed_output.mkdir()
    assert (
        verify_feature_v2(
            proof,
            recording,
            reader,
            renewed_snapshot,
            now,
            renewed_output,
            lambda _: (history, revision),
        )
        == history["sha256"]
    )


def test_scoped_bundle_rejects_global_or_foreign_coverage(tmp_path):
    inputs, payload = feature_data()
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
    parts = catalog.Parts(tmp_path, "direct", lambda: None)
    parts.write(feature_backup.validate_v2_row("direct", "direct_feature_result", row))
    parts.finish()
    manifest = dict(
        schema=3,
        format=feature_backup.FORMAT_DIRECT_REVISION,
        environment="production",
        allowlist_sha256=feature_backup.DIRECT_REVISION_ALLOWLIST_SHA256,
        scientific_coverage={
            "segment_id": "other",
            "revision": inputs["revision"],
            "manifest_sha256": inputs["manifest_sha256"],
        },
        tables={"direct": {"direct_feature_result": 1}},
        files=parts.files,
    )
    with pytest.raises(ArchiveBlocked, match="outside scoped"):
        feature_backup.restore(
            manifest,
            {entry["file"]: tmp_path / entry["file"] for entry in parts.files},
            tmp_path / "out.sqlite3",
        )
    manifest["tables"]["gateway"] = {}
    with pytest.raises(ArchiveBlocked):
        feature_backup.check_manifest(manifest)
