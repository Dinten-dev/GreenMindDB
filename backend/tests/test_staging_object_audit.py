"""Whole-bucket verification must not mistake truncated/unstable scopes for success."""

import io
import runpy
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.raw_archive.policy import ArchiveBlocked

AUDIT = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "deploy/tiered-archive/object-audit.py")
)
AT = datetime(2026, 10, 28, tzinfo=UTC)


def row(key="one.wav"):
    return dict(Key=key, VersionId="null", IsLatest=True, LastModified=AT, ETag='"same"', Size=50)


class Pages:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def list_object_versions(self, **args):
        self.calls.append(args)
        return next(self.pages)


def test_inventory_consumes_all_pages_and_preserves_markers():
    client = Pages(
        [
            dict(
                Versions=[row()],
                IsTruncated=True,
                NextKeyMarker="one.wav",
                NextVersionIdMarker="null",
            ),
            dict(Versions=[row("two.wav")], DeleteMarkers=[dict(Key="old", VersionId="deleted")]),
        ]
    )
    result = AUDIT["inventory"](client, "staging", lambda: None)
    assert len(result["versions"]) == 2 and len(result["delete_markers"]) == 1
    assert client.calls[1]["KeyMarker"] == "one.wav"
    assert client.calls[1]["VersionIdMarker"] == "null"


def test_inventory_rejects_duplicate_versions():
    with pytest.raises(ArchiveBlocked, match="Duplicate version"):
        AUDIT["inventory"](Pages([dict(Versions=[row(), row()])]), "staging", lambda: None)


def test_inventory_rejects_stalled_cursor():
    client = Pages(
        [
            dict(IsTruncated=True, NextKeyMarker="one", NextVersionIdMarker="null"),
            dict(IsTruncated=True, NextKeyMarker="one", NextVersionIdMarker="null"),
        ]
    )
    with pytest.raises(ArchiveBlocked, match="continuation"):
        AUDIT["inventory"](client, "staging", lambda: None)


def test_inventory_rejects_bytes_before_any_get():
    item = row()
    item["Size"] = 17 * 1024**2
    with pytest.raises(ArchiveBlocked, match="body budget"):
        AUDIT["inventory"](Pages([dict(Versions=[item])]), "staging", lambda: None)


def test_decoder_rejects_declared_but_missing_pcm(tmp_path):
    path = tmp_path / "short.wav"
    b = io.BytesIO()
    with wave.open(b, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(b"\x00\x00" * 20)
    path.write_bytes(b.getvalue()[:-2])
    with pytest.raises(ArchiveBlocked, match="Incomplete WAV"):
        AUDIT["decode_wav"](path, lambda: None)


def test_decoder_propagates_health_pause(tmp_path):
    path = tmp_path / "healthy.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(b"\x00\x00" * 20)

    def pause():
        raise ArchiveBlocked("health_pause")

    with pytest.raises(ArchiveBlocked, match="health_pause"):
        AUDIT["decode_wav"](path, pause)


def test_nonempty_scratch_rejected_before_any_source_read(tmp_path):
    (tmp_path / "original.wav").write_bytes(b"keep")
    with pytest.raises(ArchiveBlocked, match="empty private"):
        AUDIT["audit_bucket"](None, "staging", tmp_path, checkpoint=lambda: None)
    assert (tmp_path / "original.wav").read_bytes() == b"keep"


class ObjectClient:
    def __init__(self, body):
        self.body = body
        self.reads = []

    def list_object_versions(self, **args):
        entry = row()
        entry.update(VersionId="older-version", IsLatest=False, Size=len(self.body))
        return {"Versions": [entry]}

    def get_bucket_versioning(self, **args):
        return {"Status": "Enabled"}

    def head_object(self, **args):
        assert args["VersionId"] == "older-version"
        self.reads.append(("head", args["VersionId"]))
        return dict(
            VersionId="older-version",
            ContentLength=len(self.body),
            ETag='"same"',
            LastModified=AT,
            ContentType="audio/wav",
            Metadata={},
        )

    def get_object(self, **args):
        head = self.head_object(**args)
        self.reads.append(("get", args["VersionId"]))
        return head | {"Body": io.BytesIO(self.body)}


def wav_bytes():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(b"\x01\x00" * 20)
    return stream.getvalue()


def test_full_body_audit_reads_older_exact_version_and_cleans_own_scratch(tmp_path):
    client = ObjectClient(wav_bytes())
    result = AUDIT["audit_bucket"](client, "staging", tmp_path, checkpoint=lambda: None)
    assert result["passed"] and result["stable_inventory"]
    assert result["objects"][0]["wav"]["frames"] == 20
    assert result["objects"][0]["version_id"] == "older-version"
    assert result["read_bytes"] == len(client.body)
    assert ("get", "older-version") in client.reads
    assert not list(tmp_path.iterdir())


def test_corrupt_audio_is_reported_without_claiming_full_acceptance(tmp_path):
    client = ObjectClient(wav_bytes()[:-2])
    result = AUDIT["audit_bucket"](client, "staging", tmp_path, checkpoint=lambda: None)
    assert not result["passed"] and result["stable_inventory"]
    assert not result["objects"][0]["decodable"]
    assert not list(tmp_path.iterdir())


def test_list_milliseconds_and_http_head_seconds_refer_to_same_object(tmp_path):
    class Milliseconds(ObjectClient):
        def list_object_versions(self, **args):
            value = super().list_object_versions(**args)
            value["Versions"][0]["LastModified"] = AT.replace(microsecond=950000)
            return value

    result = AUDIT["audit_bucket"](
        Milliseconds(wav_bytes()), "staging", tmp_path, checkpoint=lambda: None
    )
    assert result["passed"] and result["stable_inventory"]
    assert result["objects"][0]["listed_last_modified"].endswith(".950000+00:00")
    assert result["objects"][0]["head_last_modified"] == AT.isoformat()


def test_different_modification_second_blocks_before_body_get(tmp_path):
    from datetime import timedelta

    class Changed(ObjectClient):
        def list_object_versions(self, **args):
            value = super().list_object_versions(**args)
            value["Versions"][0]["LastModified"] = AT - timedelta(seconds=1)
            return value

    client = Changed(wav_bytes())
    with pytest.raises(ArchiveBlocked, match="version changed"):
        AUDIT["audit_bucket"](client, "staging", tmp_path, checkpoint=lambda: None)
    assert not any(operation == "get" for operation, _ in client.reads)


def test_unversioned_minio_allows_empty_version_cursor_when_key_advances():
    client = Pages(
        [
            dict(
                Versions=[row()], IsTruncated=True, NextKeyMarker="one.wav", NextVersionIdMarker=""
            ),
            dict(Versions=[row("two.wav")]),
        ]
    )
    result = AUDIT["inventory"](client, "staging", lambda: None)
    assert len(result["versions"]) == 2
    assert client.calls[1]["KeyMarker"] == "one.wav"
    assert client.calls[1]["VersionIdMarker"] == ""
