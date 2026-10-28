"""Whole-revision proofs across bounded batches, interruption and changed sources."""

import copy
import hashlib
import io
import wave
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked, Recording
from app.tiered_archive import wav_copy
from app.tiered_archive.codec import digest, epoch_us
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.pipeline import complete_staged_wav
from app.tiered_archive.runtime import candidate_from_json
from app.tiered_archive.wav_source import WavSource, record_json

AT = datetime(2026, 9, 1, tzinfo=UTC)
ORG = "00000000-0000-0000-0000-000000000001"
DEVICE = "00000000-0000-0000-0000-000000000002"
REFERENCE = ["00000000-0000-0000-0000-000000000003", 187]


def fixture(tmp_path, count):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(380)
        output.writeframes(b"\x01\x00" * 380)
    body = buffer.getvalue()
    records = [
        Recording(
            "direct",
            ":".join(map(str, REFERENCE)),
            "greenmind-direct-staging-fixture",
            f"fixture/{index}.wav",
            hashlib.sha256(body).hexdigest(),
            len(body),
            AT + timedelta(minutes=10),
            AT + timedelta(minutes=11),
            "f" * 64,
            AT + timedelta(seconds=index),
            "direct-" + DEVICE,
        )
        for index in range(count)
    ]
    original = {record.key: body for record in records}

    class Objects:
        def snapshot(self, record):
            return {"VersionId": "fixture-1", "key": record.key}

        def download(self, record, snapshot, path):
            assert snapshot == self.snapshot(record)
            path.write_bytes(original[record.key])

    class Adapter:
        kind = "direct"
        environment = "staging"
        objects = Objects()
        version = 1
        complete = True
        candidate = WavSource.candidate

        def metadata(self, reference):
            assert reference == REFERENCE
            envelope = {
                "identity": digest(reference),
                "organization": ORG,
                "sensor": DEVICE,
                "time_us": epoch_us(AT),
                "body": {
                    "reference": reference,
                    "metadata": {"test_only_version": self.version},
                    "originals": [{"recording": record_json(record)} for record in records],
                },
            }
            headers = {
                record.key: {"frames": 380, "rate": 380, "channels": 1, "width": 2}
                for record in records
            }
            return (
                envelope,
                headers,
                dict(
                    complete=self.complete,
                    features_verified=True,
                    projections_verified=True,
                    dependencies_complete=True,
                ),
            )

    class Store:
        identity = "fixture-box"

        def __init__(self):
            self.files = {}
            self.puts = []
            self.crash_after_wav = False

        def publish(self, key, path):
            value = path.read_bytes()
            if key in self.files:
                assert self.files[key] == value
            else:
                self.files[key] = value
                self.puts.append(key)
            if self.crash_after_wav and key.endswith(".wav"):
                self.crash_after_wav = False
                raise KeyboardInterrupt("synthetic crash after verified PUT before journal")

        def download(self, key, path, size, **kwargs):
            value = self.files[key]
            assert len(value) == size
            path.write_bytes(value)

    root = tmp_path / "state"
    ledger = Ledger(root)
    adapter, store = Adapter(), Store()

    def advance(**overrides):
        options = dict(
            bindings="b" * 64, checkpoint=lambda: None, maximum_wavs=10, maximum_bytes=5 * 1024**2
        )
        options.update(overrides)
        return wav_copy.advance(adapter, REFERENCE, store, ledger, **options)

    return SimpleNamespace(
        ledger=ledger,
        adapter=adapter,
        store=store,
        advance=advance,
        originals=original,
        records=records,
    )


def test_187_runs_finish_in_19_bounded_calls_without_partial_published_proofs(tmp_path):
    h = fixture(tmp_path, 187)
    h.adapter.complete = False
    originals = copy.deepcopy(h.originals)
    try:
        for index in range(19):
            old = len([key for key in h.store.puts if key.endswith(".wav")])
            result = h.advance()
            assert result["copied_wavs"] <= 10
            assert result["copied_bytes"] <= 5 * 1024**2
            assert len([key for key in h.store.puts if key.endswith(".wav")]) - old <= 10
            if index < 18:
                assert not result["complete"] and h.ledger.status() == {}
        assert result["complete"] and result["copied_wavs"] == 7
        assert h.ledger.status() == {"published": 1}
        assert len(result["receipt"]["files"]) == 188
        assert "first_verified_at" not in result["receipt"]
        from app.tiered_archive.policy import eligibility

        with pytest.raises(ArchiveBlocked, match="Processing, dependencies"):
            eligibility(
                candidate_from_json(result["receipt"]["source"]),
                result["receipt"],
                AT + timedelta(days=30),
            )
        assert h.ledger.db.execute("SELECT plan,completed FROM wav_copy").fetchall() == [(None, 1)]
        assert h.originals == originals
    finally:
        h.ledger.close()


def test_put_before_journal_crash_never_duplicates_remote_original(tmp_path):
    h = fixture(tmp_path, 2)
    try:
        h.store.crash_after_wav = True
        with pytest.raises(KeyboardInterrupt):
            h.advance(maximum_wavs=1)
        assert h.ledger.status() == {}
        assert not h.advance(maximum_wavs=1)["complete"]
        assert h.advance(maximum_wavs=1)["complete"]
        assert len([key for key in h.store.puts if key.endswith(".wav")]) == 2
    finally:
        h.ledger.close()


def test_completed_copy_can_be_rediscovered_without_reading_deleted_source_versions(tmp_path):
    h = fixture(tmp_path, 1)
    try:
        first = h.advance()

        def forbidden(*args):
            pytest.fail("Completed revision must not re-read removed originals")

        h.adapter.objects.snapshot = forbidden
        second = h.advance()
        assert second["complete"] and second["receipt"] == first["receipt"]
        assert second["copied_wavs"] == 0
        assert second["envelope"] == first["envelope"]
    finally:
        h.ledger.close()


def test_retry_after_legacy_binding_failure_repairs_original_urls(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from app.tiered_archive import runtime

    h = fixture(tmp_path, 11)
    calls = []

    @contextmanager
    def worker():
        yield h.ledger, h.store, lambda: None

    def legacy(envelope, transport, now):
        calls.append(envelope)
        if len(calls) == 1:
            raise OSError("synthetic crash after publication before legacy URL binding")

    h.store.transport = object()
    h.adapter.publish_legacy_receipts = legacy
    monkeypatch.setattr(runtime, "worker", worker)
    monkeypatch.setattr(runtime, "wav_source", lambda *args: h.adapter)
    monkeypatch.setattr(runtime, "bindings_sha256", lambda *args: "b" * 64)
    try:
        options = dict(maximum_wavs=10, maximum_bytes=5 * 1024**2)
        assert not runtime.stage_wav("direct", REFERENCE, **options)["complete"]
        with pytest.raises(OSError, match="legacy URL binding"):
            runtime.stage_wav("direct", REFERENCE, **options)
        h.adapter.objects.snapshot = lambda *args: pytest.fail("Must restore archived metadata")
        assert runtime.stage_wav("direct", REFERENCE, **options)["complete"]
        assert len(calls) == 2 and calls[0] == calls[1]
        assert len([key for key in h.store.puts if key.endswith(".wav")]) == 11
    finally:
        h.ledger.close()


def test_changed_source_during_final_readback_never_publishes(tmp_path):
    h = fixture(tmp_path, 1)
    original = h.adapter.metadata
    calls = []
    try:

        def metadata(reference):
            calls.append(1)
            if len(calls) > 1:
                h.adapter.version = 2
            return original(reference)

        h.adapter.metadata = metadata
        with pytest.raises(ArchiveBlocked, match="Scientific source changed"):
            h.advance()
        assert h.ledger.status() == {}
        assert wav_copy.summary(h.ledger.db)["incomplete_revisions"] == 1
    finally:
        h.ledger.close()


def test_changed_bindings_cannot_reuse_incomplete_transfers(tmp_path):
    h = fixture(tmp_path, 2)
    try:
        assert not h.advance(maximum_wavs=1)["complete"]
        old_puts = list(h.store.puts)
        with pytest.raises(ArchiveBlocked, match="Durable WAV copy plan differs"):
            h.advance(bindings="c" * 64)
        assert h.store.puts == old_puts and h.ledger.status() == {}
    finally:
        h.ledger.close()


def test_incomplete_manifest_is_rejected_even_when_every_listed_file_is_valid(tmp_path):
    h = fixture(tmp_path, 2)
    try:
        result = h.advance()
        receipt = result["receipt"]
        with pytest.raises(ArchiveBlocked, match="incomplete or changed"):
            complete_staged_wav(
                candidate_from_json(receipt["source"]),
                receipt["files"][:-1],
                h.store,
                h.ledger,
                checkpoint=lambda: None,
            )
        assert h.ledger.status() == {"published": 1}
    finally:
        h.ledger.close()


def test_corrupt_staged_original_blocks_final_publication(tmp_path):
    h = fixture(tmp_path, 2)
    try:
        assert not h.advance(maximum_wavs=1)["complete"]
        key = next(key for key in h.store.files if key.endswith(".wav"))
        old = h.store.files[key]
        h.store.files[key] = old[:-1] + bytes([old[-1] ^ 1])
        with pytest.raises(ArchiveBlocked, match="differs from source"):
            h.advance(maximum_wavs=1)
        assert h.ledger.status() == {}
    finally:
        h.ledger.close()


def test_archive_only_catalogue_restore_retains_completed_copy_identity(tmp_path):
    from app.tiered_archive.registry_backup import backup, restore

    h = fixture(tmp_path, 2)
    target = Ledger(tmp_path / "restored")
    try:
        original = h.advance()["receipt"]
        reference = backup(h.ledger, h.store, checkpoint=lambda: None)
        restore(reference, target, h.store, checkpoint=lambda: None, expected_environment="staging")
        assert target.get(original["candidate"]) == original

        def forbidden(*args):
            pytest.fail("Archive-only restored index must not need removed source objects")

        h.adapter.objects.snapshot = forbidden
        resumed = wav_copy.advance(
            h.adapter,
            REFERENCE,
            h.store,
            target,
            bindings="b" * 64,
            checkpoint=lambda: None,
            maximum_wavs=10,
            maximum_bytes=5 * 1024**2,
        )
        assert resumed["complete"] and resumed["copied_wavs"] == 0
    finally:
        h.ledger.close()
        target.close()
