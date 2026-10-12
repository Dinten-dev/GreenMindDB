"""Actual target checks cannot call mutation APIs or bypass live guards."""

from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from app.raw_archive import target_read_probe
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked

from .test_version_read_probe import Healthy
from .test_version_read_probe import requests as unpinned_requests


class Client:
    def __init__(self, version="null", additional=False, truncated=False):
        self.version, self.additional, self.truncated = version, additional, truncated
        self.calls = []
        self.modified = datetime.now(UTC) - timedelta(days=9)

    def head_object(self, **arguments):
        self.calls.append(("head", arguments))
        return {
            "VersionId": self.version,
            "ETag": "original",
            "ContentLength": 456044,
            "LastModified": self.modified,
        }

    def get_bucket_versioning(self, **arguments):
        self.calls.append(("versioning", arguments))
        return {"Status": "Enabled"}

    def get_bucket_lifecycle_configuration(self, **arguments):
        self.calls.append(("lifecycle", arguments))
        raise ClientError({"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "lifecycle")

    def list_object_versions(self, **arguments):
        self.calls.append(("versions", arguments))
        assert arguments["MaxKeys"] == 2
        found = [{"Key": arguments["Prefix"], "VersionId": self.version, "IsLatest": True}]
        if self.additional:
            found.append({"Key": arguments["Prefix"], "VersionId": "second", "IsLatest": False})
        return {"Versions": found, "IsTruncated": self.truncated}

    def delete_object(self, **arguments):
        pytest.fail("Read-only preflight must never delete")


def requests(client):
    data = unpinned_requests()
    for sample in data.values():
        sample["snapshot"] = {
            "VersionId": client.version,
            "ETag": "original",
            "ContentLength": 456044,
            "LastModified": client.modified.isoformat(),
        }
    return data


@pytest.fixture(autouse=True)
def disabled(monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_DELETE_ENABLED", "false")


@pytest.mark.parametrize("version", ["null", "exact-version"])
def test_exact_target_check_never_activates_policy(version):
    client = Client(version)
    result = target_read_probe.inspect(client, requests(client), Healthy())
    assert result["status"] == "approved_target_preflight_passed"
    assert all(r["version_id"] == version for r in result["samples"])
    assert not result["legacy_null_enabled"] and not result["deletion_readiness"]
    assert not result["complete_cohort"] and result["deleted_files"] == 0
    assert {method for method, _ in client.calls} == {"head", "versioning", "lifecycle", "versions"}


def test_source_pause_opens_no_api():
    client = Client()
    with pytest.raises(SafetyPause, match="source_memory"):
        target_read_probe.inspect(client, requests(client), Healthy(False))
    assert client.calls == []


@pytest.mark.parametrize("additional,truncated", [(True, False), (False, True)])
def test_other_or_unknown_versions_reject_preflight(additional, truncated):
    client = Client(additional=additional, truncated=truncated)
    with pytest.raises(ArchiveBlocked, match="Other object versions"):
        target_read_probe.inspect(client, requests(client), Healthy())


def test_enabled_policy_rejected_before_requests(monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_DELETE_ENABLED", "true")
    client = Client()
    with pytest.raises(ArchiveBlocked, match="deletion disabled"):
        target_read_probe.inspect(client, requests(client), Healthy())
    assert client.calls == []


def test_foreign_bucket_rejected_before_requests():
    client = Client()
    data = requests(client)
    data["direct"]["recording"]["bucket"] = "foreign"
    with pytest.raises(ArchiveBlocked, match="production sample"):
        target_read_probe.inspect(client, data, Healthy())
    assert client.calls == []


def test_changed_original_cannot_be_adopted():
    client = Client()
    data = requests(client)
    data["gateway"]["snapshot"]["ETag"] = "different-original"
    with pytest.raises(ArchiveBlocked, match="snapshot changed"):
        target_read_probe.inspect(client, data, Healthy())
    assert not any(method == "versions" for method, _ in client.calls)


def test_missing_original_snapshot_opens_no_api():
    client = Client()
    with pytest.raises(ArchiveBlocked, match="original snapshot"):
        target_read_probe.inspect(client, unpinned_requests(), Healthy())
    assert client.calls == []
