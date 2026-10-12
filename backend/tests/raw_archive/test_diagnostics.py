"""Diagnostic identity can fetch bucket configuration only."""

import pytest
from botocore.exceptions import ClientError

from app.raw_archive.diagnostics import client_from_environment, inspect_buckets
from app.raw_archive.policy import ArchiveBlocked


def test_missing_identity_never_discovers_admin_credentials(monkeypatch):
    for name in ("ENDPOINT", "ACCESS_KEY", "SECRET_KEY"):
        monkeypatch.delenv("RAW_ARCHIVE_DIAGNOSTIC_" + name, raising=False)
    with pytest.raises(ArchiveBlocked, match="not been provided"):
        client_from_environment()


def test_access_denied_does_not_mean_versioning_is_disabled():
    class Client:
        def get_bucket_versioning(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetBucketVersioning")

        def get_bucket_lifecycle_configuration(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "NoSuchLifecycleConfiguration"}},
                "GetBucketLifecycleConfiguration",
            )

    result = inspect_buckets(Client(), ["isolated"])[0]
    assert result["versioning"] == {"error": "AccessDenied"}
    assert result["lifecycle"] == {"error": "NoSuchLifecycleConfiguration"}
