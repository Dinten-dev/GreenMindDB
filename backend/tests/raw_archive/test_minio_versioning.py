"""Both-bucket preflight and absent lifecycle are mandatory before any write."""

import pytest
from botocore.exceptions import ClientError

from app.raw_archive.minio_versioning import BUCKETS, enable_reviewed
from app.raw_archive.policy import ArchiveBlocked


class Client:
    def __init__(self):
        self.states = dict.fromkeys(BUCKETS)
        self.lifecycle = {}
        self.changed = []

    def get_bucket_versioning(self, *, Bucket):
        return {"Status": self.states[Bucket]} if self.states[Bucket] else {}

    def get_bucket_lifecycle_configuration(self, *, Bucket):
        if Bucket in self.lifecycle:
            if isinstance(self.lifecycle[Bucket], str):
                raise ClientError({"Error": {"Code": self.lifecycle[Bucket]}}, "GetBucketLifecycle")
            return self.lifecycle[Bucket]
        raise ClientError({"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "GetBucketLifecycle")

    def put_bucket_versioning(self, *, Bucket, VersioningConfiguration):
        assert VersioningConfiguration == {"Status": "Enabled"}
        self.changed.append(Bucket)
        self.states[Bucket] = "Enabled"


def test_enable_only_exact_buckets_and_read_back():
    client = Client()
    result = enable_reviewed(client)
    assert client.changed == list(BUCKETS)
    assert result["original_objects_rewritten"] == result["deleted_files"] == 0
    assert not result["legacy_null_deletion_enabled"]
    assert all(row["versioning"] == "Enabled" for row in result["after"])
    assert enable_reviewed(client)["changed"] == []


@pytest.mark.parametrize("state", ["Suspended", "unknown"])
def test_unreviewed_second_bucket_prevents_first_change(state):
    client = Client()
    client.states[BUCKETS[1]] = state
    with pytest.raises(ArchiveBlocked):
        enable_reviewed(client)
    assert not client.changed


@pytest.mark.parametrize(
    "lifecycle", [{"Rules": []}, {"Rules": [{"Status": "Enabled"}]}, "AccessDenied"]
)
def test_lifecycle_or_denied_metadata_prevents_all_writes(lifecycle):
    client = Client()
    client.lifecycle[BUCKETS[1]] = lifecycle
    with pytest.raises((ArchiveBlocked, ClientError)):
        enable_reviewed(client)
    assert not client.changed


def test_resource_pause_never_suspends_or_deletes_versions():
    client = Client()
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ArchiveBlocked("Preserve receiver headroom")

    with pytest.raises(ArchiveBlocked):
        enable_reviewed(client, checkpoint=checkpoint)
    assert client.changed == [BUCKETS[0]]
    assert client.states[BUCKETS[0]] == "Enabled"
