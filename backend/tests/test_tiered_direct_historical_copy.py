"""Real SQL/Parquet/WAV copies preserve old scientific facts, never old grants."""

import copy
import hashlib
import io
import uuid
import wave
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import load_only

from app.raw_archive.catalog import canonical
from app.raw_archive.direct_copy_catalog import PublishedDirectCopyCatalog
from app.raw_archive.direct_history import COPY_SCOPE
from app.raw_archive.policy import ArchiveBlocked, Recording
from app.raw_archive.policy import Ledger as RawLedger
from app.raw_archive.wav_catalog import COLUMNS
from app.tiered_archive import wav_copy
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.policy import eligibility
from app.tiered_archive.wav_source import WavSource
from tests import test_tiered_wav_metadata_projection as projection_tests

direct_adapter = projection_tests.direct_adapter


def narrow_get(db, model, identity):
    return db.get(
        model,
        identity,
        options=[load_only(*(getattr(model, n) for n in COLUMNS["direct"][model.__tablename__]))],
    )


@pytest.fixture
def historical(direct_adapter):
    from app.direct.features import channel_features
    from app.direct.models import Device, Revision, Segment

    current, reference, sessions, engine = direct_adapter
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(b"\x01\x00" * 380)
    body = output.getvalue()
    with sessions() as db:
        segment = narrow_get(db, Segment, reference[0])
        old = db.get(Revision, tuple(reference))
        manifest = copy.deepcopy(old.manifest)
        manifest["runs"][0]["sha256"] = hashlib.sha256(body).hexdigest()
        manifest["runs"][0]["features"] = channel_features(body[44:], manifest["config"])
        manifest["runs"][0]["features"][0]["extractor_version"] = "historical-fixture-v1"
        old.manifest, old.manifest_sha256 = manifest, canonical(manifest)
        latest = copy.deepcopy(manifest)
        latest["revision"] = 2
        latest["runs"][0]["key"] = "latest.wav"
        db.add(
            Revision(
                segment_id=segment.id,
                revision=2,
                manifest=latest,
                manifest_sha256=canonical(latest),
                verified_at=old.verified_at + 1,
            )
        )
        segment.revision = segment.published_revision = 2
        db.execute(
            __import__("sqlalchemy").text("UPDATE direct_visual_segment SET source_revision=2")
        )
        device = narrow_get(db, Device, segment.device_id)
        device.organization_id = str(uuid.uuid4())
        device.zone_id = str(uuid.uuid4())
        db.commit()
    mutation = [None]

    class Objects:
        version = "v1"

        def snapshot(self, record):
            return {"VersionId": self.version, "ContentLength": len(body)}

        def download(self, record, snapshot, path):
            path.write_bytes(body)
            if mutation[0]:
                mutation[0]()

    class Store:
        identity = "synthetic-box"

        def __init__(self):
            self.files = {}

        def publish(self, key, path):
            raw = path.read_bytes()
            if key in self.files:
                assert self.files[key] == raw
            self.files[key] = raw

        def download(self, key, path, size):
            assert len(self.files[key]) == size
            path.write_bytes(self.files[key])

    adapter = WavSource(
        sessions,
        PublishedDirectCopyCatalog(sessions, current.catalog.bucket),
        Objects(),
        "direct",
        "staging",
        None,
        lambda: None,
        copy_published_history=True,
    )
    yield SimpleNamespace(
        adapter=adapter,
        current=current,
        reference=reference,
        sessions=sessions,
        body=body,
        mutation=mutation,
        store=Store(),
        engine=engine,
    )


def test_older_complete_revision_is_actually_copied_and_quarantined(historical, tmp_path):
    h = historical
    before, _, flags = h.adapter.metadata(h.reference)
    metadata = before["body"]["metadata"]
    assert before["body"]["copy_scope"] == COPY_SCOPE
    assert metadata["capture_assignment"]["status"] == "unknown"
    assert metadata["observed_assignment"]["semantics"] == "observed_at_archive"
    assert "assignment" not in metadata
    assert flags["quarantined"] and not flags["complete"] and not flags["dependencies_complete"]
    assert not flags["projections_verified"] and flags["features_verified"]
    ledger = Ledger(tmp_path / "state")
    try:
        result = wav_copy.advance(
            h.adapter,
            h.reference,
            h.store,
            ledger,
            bindings="b" * 64,
            checkpoint=lambda: None,
            maximum_wavs=10,
            maximum_bytes=5 * 1024**2,
        )
        assert result["complete"] and result["copied_wavs"] == 1
        assert any(raw == h.body for raw in h.store.files.values())
        assert result["envelope"]["body"]["metadata"]["revision"] == metadata["revision"]
        assert (
            result["envelope"]["body"]["metadata"]["session"]["config"]
            == metadata["revision"]["manifest"]["config"]
        )
        candidate = h.adapter.candidate(before, {"content_sha256": "a" * 64}, flags)
        candidate.validate()
        with pytest.raises(ArchiveBlocked, match="incomplete"):
            eligibility(candidate, {}, candidate.end)
        count = len(h.store.files)
        resumed = wav_copy.advance(
            h.adapter,
            h.reference,
            h.store,
            ledger,
            bindings="b" * 64,
            checkpoint=lambda: None,
            maximum_wavs=10,
            maximum_bytes=5 * 1024**2,
        )
        assert resumed["complete"] and resumed["copied_wavs"] == 0
        assert len(h.store.files) == count
        h.adapter.raw_config = SimpleNamespace(root=tmp_path / "legacy")
        h.adapter.publish_legacy_receipts(resumed["envelope"], h.store, datetime.now(UTC))
        raw = RawLedger(h.adapter.raw_config.root)
        try:
            recording = Recording.from_receipt(
                resumed["envelope"]["body"]["originals"][0]["recording"]
            )
            state, receipt = raw.load(recording)
            assert state == "verified" and h.store.files[receipt["remote_key"]] == h.body
            from app.raw_archive.deletion import verify_deletion_evidence
            from app.raw_archive.wav_context import RECEIPT_FORMAT

            assert receipt["source_identity"]["format"] == RECEIPT_FORMAT
            with pytest.raises(ArchiveBlocked, match="COPY-only"):
                verify_deletion_evidence(None, recording, receipt, None, datetime.now(UTC))
            assert receipt["recording"]["key"] == "original.wav"
        finally:
            raw.close()

    finally:
        ledger.close()


def test_latest_strict_read_retained_but_copy_context_is_explicitly_unknown(historical):
    from app.raw_archive.wav_context import DIRECT_COPY_SCOPE_V2, UNKNOWN, copy_flags

    h = historical
    copied, _, flags = h.adapter.metadata([h.reference[0], 2])
    strict, _, strict_flags = h.current.metadata([h.reference[0], 2])
    assert copied["body"]["copy_scope"] == DIRECT_COPY_SCOPE_V2 and flags == copy_flags()
    assert copied["body"]["metadata"]["capture_assignment"] == UNKNOWN
    assert all(strict_flags.values()) and "copy_scope" not in strict["body"]
    for field in ("segment", "revision", "session"):
        assert copied["body"]["metadata"][field] == strict["body"]["metadata"][field]


def test_historical_metadata_read_race_blocks_before_any_source_body(historical, monkeypatch):
    h = historical
    original = h.adapter.catalog.historical_metadata
    calls = []

    def changed(reference):
        result = original(reference)
        calls.append(True)
        if len(calls) > 1:
            result["revision"]["manifest_sha256"] = "f" * 64
        return result

    monkeypatch.setattr(h.adapter.catalog, "historical_metadata", changed)
    with pytest.raises(ArchiveBlocked, match="between metadata reads"):
        h.adapter.metadata(h.reference)
    assert not h.store.files


def test_history_scope_cannot_construct_a_delete_source():
    from app.tiered_archive import runtime

    with pytest.raises(ArchiveBlocked, match="deletion source"):
        runtime.wav_source("direct", lambda: None, writing=True, copy_published_history=True)


@pytest.mark.parametrize(
    "change", ["partial", "gap", "features", "missing_feature", "frames", "checksum"]
)
def test_incomplete_or_manipulated_historical_revision_is_blocked(historical, change):
    from app.direct.models import Revision

    h = historical
    with h.sessions() as db:
        old = db.get(Revision, tuple(h.reference))
        manifest = copy.deepcopy(old.manifest)
        if change == "partial":
            manifest["status"] = "partial"
        if change == "gap":
            manifest["missing_frame_ranges"] = [[0, 1]]
        if change == "features":
            manifest["runs"][0]["features"][0]["sample_count"] = 1
        if change == "missing_feature":
            manifest["runs"][0]["features"][0].pop("rms")
        if change == "frames":
            manifest["end_frame"] += 1
        if change == "checksum":
            manifest["runs"][0]["features"][0]["mean"] = 123
        old.manifest = manifest
        if change != "checksum":
            old.manifest_sha256 = canonical(manifest)
        db.commit()
    with pytest.raises(ArchiveBlocked):
        h.adapter.metadata(h.reference)
    assert not h.store.files


def test_current_device_and_session_are_observations_not_original_facts(historical):
    from app.direct.models import Device
    from app.direct.models import Session as CaptureSession

    h = historical
    before, _, _ = h.adapter.metadata(h.reference)
    with h.sessions() as db:
        device = (
            db.query(Device)
            .options(load_only(*(getattr(Device, n) for n in COLUMNS["direct"]["direct_device"])))
            .one()
        )
        device.organization_id = str(uuid.uuid4())
        session = db.query(CaptureSession).one()
        session.config = dict(session.config, calibration_version="changed-today")
        db.commit()
    after, _, _ = h.adapter.metadata(h.reference)
    a, b = before["body"]["metadata"], after["body"]["metadata"]
    assert a["revision"] == b["revision"] and a["session"] == b["session"]
    assert a["observed_assignment"] != b["observed_assignment"]
    assert a["capture_assignment"] == b["capture_assignment"]
    assert b["session"]["config"]["calibration_version"] == "unsigned-mv-linear-int16-v1"


@pytest.mark.parametrize("change", ["source", "object"])
def test_source_change_during_historical_copy_prevents_publication(historical, tmp_path, change):
    from app.direct.models import Segment

    h = historical

    def mutate():
        if change == "object":
            h.adapter.objects.version = "v2"
        else:
            with h.sessions() as db:
                segment = narrow_get(db, Segment, h.reference[0])
                segment.updated_at += 1
                db.commit()

    h.mutation[0] = mutate
    with pytest.raises(ArchiveBlocked, match="changed during"):
        h.adapter.export(h.reference, tmp_path)


def test_copy_only_proof_is_explicitly_rejected_by_all_delete_paths(historical):
    h = historical
    envelope, _, flags = h.adapter.metadata(h.reference)
    writer = h.current
    writer.expected = [envelope]
    candidate = h.adapter.candidate(envelope, {"content_sha256": "a" * 64}, flags)
    for method in (writer.absent, writer.inspect, writer.delete, writer.complete_eviction):
        with pytest.raises(ArchiveBlocked, match="COPY-only"):
            method(candidate)
    with pytest.raises(ArchiveBlocked, match="COPY-only"), writer.exclusive(candidate):
        pytest.fail("delete transaction entered")
    with pytest.raises(ArchiveBlocked, match="sealed"):
        writer.catalog.resolve(h.reference)
