"""S3 SDK contract checks, including exact-version-only deletion and races."""

import hashlib
import io
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from app.raw_archive.policy import ArchiveBlocked, Recording
from app.raw_archive.storage import S3Source

from .test_daily import wav_bytes


@pytest.fixture
def s3_case():
    client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:1",
        aws_access_key_id="isolated-test",
        aws_secret_access_key="isolated-test",
    )
    stub = Stubber(client)
    payload = wav_bytes(3)
    old = datetime.now(UTC) - timedelta(days=2)
    record = Recording(
        "gateway",
        "test",
        "greenmind-raw",
        "test.wav",
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        old,
        old,
        "a" * 64,
        old,
        "mac-14-c1-9f-d9-42-a4",
    )
    snapshot = dict(
        VersionId="version-1",
        ETag='"etag"',
        ContentLength=len(payload),
        LastModified=old.isoformat(),
    )
    stub.activate()
    yield S3Source(client), stub, record, payload, snapshot
    stub.assert_no_pending_responses()
    stub.deactivate()
    client.close()


def test_download_pins_version_etag_and_exact_bytes(s3_case, tmp_path):
    source, stub, record, payload, snapshot = s3_case
    stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(payload), len(payload))},
        dict(Bucket=record.bucket, Key=record.key, VersionId="version-1", IfMatch='"etag"'),
    )
    path = tmp_path / "wav"
    source.download(record, snapshot, path)
    assert path.read_bytes() == payload


def test_download_aborts_declared_size_overrun(s3_case, tmp_path):
    source, stub, record, payload, snapshot = s3_case
    stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(payload), len(payload))},
        dict(Bucket=record.bucket, Key=record.key, VersionId="version-1", IfMatch='"etag"'),
    )
    with pytest.raises(ArchiveBlocked, match="declared length"):
        source.download(replace(record, size=10), snapshot, tmp_path / "wav")


def deletion_prefix(stub, record, snapshot, *, lifecycle=False):
    stub.add_response("get_bucket_versioning", {"Status": "Enabled"}, {"Bucket": record.bucket})
    if lifecycle:
        stub.add_response(
            "get_bucket_lifecycle_configuration",
            {
                "Rules": [
                    {"ID": "danger", "Status": "Enabled", "Prefix": "", "Expiration": {"Days": 90}}
                ]
            },
            {"Bucket": record.bucket},
        )
    else:
        stub.add_client_error(
            "get_bucket_lifecycle_configuration",
            service_error_code="NoSuchLifecycleConfiguration",
            http_status_code=404,
            expected_params={"Bucket": record.bucket},
        )
        stub.add_response(
            "head_object",
            {
                "ETag": snapshot["ETag"],
                "ContentLength": snapshot["ContentLength"],
                "LastModified": datetime.fromisoformat(snapshot["LastModified"]),
            },
            {"Bucket": record.bucket, "Key": record.key, "VersionId": "version-1"},
        )


def test_eviction_uses_only_the_verified_version_and_confirms_removal(s3_case):
    source, stub, record, _, snapshot = s3_case
    deletion_prefix(stub, record, snapshot)
    stub.add_response(
        "list_object_versions",
        {
            "IsTruncated": False,
            "Versions": [dict(Key=record.key, VersionId="version-1", IsLatest=True)],
        },
        {"Bucket": record.bucket, "Prefix": record.key},
    )
    args = {"Bucket": record.bucket, "Key": record.key, "VersionId": "version-1"}
    stub.add_response("delete_object", {}, args)
    stub.add_client_error(
        "head_object",
        service_error_code="NoSuchVersion",
        http_status_code=404,
        expected_params=args,
    )
    source.evict(record, snapshot)


def test_other_versions_block_eviction(s3_case):
    source, stub, record, _, snapshot = s3_case
    deletion_prefix(stub, record, snapshot)
    stub.add_response(
        "list_object_versions",
        {
            "IsTruncated": False,
            "Versions": [dict(Key=record.key, VersionId="version-2", IsLatest=True)],
        },
        {"Bucket": record.bucket, "Prefix": record.key},
    )
    with pytest.raises(ArchiveBlocked, match="Other object versions"):
        source.evict(record, snapshot)


def test_enabled_bucket_lifecycle_blocks_eviction(s3_case):
    source, stub, record, _, snapshot = s3_case
    deletion_prefix(stub, record, snapshot, lifecycle=True)
    with pytest.raises(ArchiveBlocked, match="lifecycle"):
        source.evict(record, snapshot)
