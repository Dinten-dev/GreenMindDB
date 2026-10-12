import io

import pytest
from botocore.exceptions import ClientError

from app.raw_archive import version_read_probe
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked


def requests():
    result = {}
    for kind, bucket in (
        ("gateway", "greenmind-raw"),
        ("direct", "greenmind-direct-production-hotspot"),
    ):
        result[kind] = {
            "recording": {
                "kind": kind,
                "bucket": bucket,
                "key": kind + ".wav",
                "identity": kind,
                "sha256": "a" * 64,
                "feature_digest": "b" * 64,
                "size": 456044,
                "ended_at": "2026-10-01T00:00:00+00:00",
                "received_at": "2026-10-01T00:00:00+00:00",
            }
        }
    return result


class Healthy:
    last = {"reason": "source_memory"}

    def __init__(self, passed=True):
        self.passed = passed

    def __call__(self):
        return self.passed


class Client:
    def __init__(self, version="null", listing_allowed=False, status="Enabled", range_size=44):
        self.version, self.listing_allowed, self.status, self.range_size = (
            version,
            listing_allowed,
            status,
            range_size,
        )
        self.calls, self.streams = [], []

    def get_bucket_versioning(self, **kwargs):
        self.calls.append(("versioning", kwargs))
        return {"Status": self.status}

    def get_bucket_lifecycle_configuration(self, **kwargs):
        self.calls.append(("lifecycle", kwargs))
        raise ClientError(
            {"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "GetBucketLifecycleConfiguration"
        )

    def list_object_versions(self, **kwargs):
        self.calls.append(("versions", kwargs))
        assert kwargs["MaxKeys"] == 1
        return {"Versions": [{"Key": kwargs["Prefix"], "VersionId": self.version}]}

    def head_object(self, **kwargs):
        self.calls.append(("head", kwargs))
        assert kwargs["VersionId"] == self.version
        return {"ContentLength": 456044}

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        assert kwargs["Range"] == "bytes=0-43" and kwargs["VersionId"] == self.version
        stream = io.BytesIO(b"RIFF" + bytes(4) + b"WAVE" + bytes(32))
        self.streams.append(stream)
        return {"ContentLength": self.range_size, "Body": stream}

    def list_buckets(self):
        self.calls.append(("global_list", {}))
        if not self.listing_allowed:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "ListBuckets")
        return {}


@pytest.mark.parametrize("version", ["null", "actual-version-id"])
def test_specific_version_reads_do_not_accept_deletion(version):
    client = Client(version)
    proof = version_read_probe.inspect(client, requests(), Healthy())
    assert proof["status"] == "passed" and proof["global_list_denied"]
    assert not proof["legacy_null_accepted"] and not proof["deletion_readiness"]
    assert not proof["complete_catalog"] and proof["deleted_files"] == 0
    assert all(not sample["full_wav_decode"] for sample in proof["samples"])
    assert all(stream.closed for stream in client.streams)
    assert {name for name, _ in client.calls} == {
        "versioning",
        "lifecycle",
        "versions",
        "head",
        "get",
        "global_list",
    }


def test_source_pause_opens_no_s3_operation():
    client = Client()
    with pytest.raises(SafetyPause, match="source_memory"):
        version_read_probe.inspect(client, requests(), Healthy(False))
    assert client.calls == []


def test_disabled_versioning_cannot_prove_acceptance():
    client = Client(status="Suspended")
    with pytest.raises(ArchiveBlocked, match="versioning"):
        version_read_probe.inspect(client, requests(), Healthy())
    assert not any(name in {"versions", "head", "get"} for name, _ in client.calls)


def test_broad_listing_permission_is_rejected():
    client = Client(listing_allowed=True)
    with pytest.raises(ArchiveBlocked, match="global listing"):
        version_read_probe.inspect(client, requests(), Healthy())


def test_unbounded_range_body_is_closed_without_reading():
    client = Client(range_size=456044)
    with pytest.raises(ArchiveBlocked, match="Bounded range"):
        version_read_probe.inspect(client, requests(), Healthy())
    assert client.streams[0].closed
