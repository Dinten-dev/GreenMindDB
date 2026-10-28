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
        ObjectHeaders={},
    )
    stub.activate()
    yield S3Source(client), stub, record, payload, snapshot
    stub.assert_no_pending_responses()
    stub.deactivate()
    client.close()


def head(snapshot):
    return {
        "ETag": snapshot["ETag"],
        "ContentLength": snapshot["ContentLength"],
        "LastModified": datetime.fromisoformat(snapshot["LastModified"]),
        "VersionId": snapshot["VersionId"],
        **snapshot["ObjectHeaders"],
    }


def test_download_pins_version_etag_and_exact_bytes(s3_case, tmp_path):
    source, stub, record, payload, snapshot = s3_case
    stub.add_response(
        "get_object",
        dict(head(snapshot), Body=StreamingBody(io.BytesIO(payload), len(payload))),
        dict(Bucket=record.bucket, Key=record.key, VersionId="version-1", IfMatch='"etag"'),
    )
    stub.add_response(
        "head_object",
        head(snapshot),
        dict(Bucket=record.bucket, Key=record.key, VersionId="version-1"),
    )
    path = tmp_path / "wav"
    source.download(record, snapshot, path)
    assert path.read_bytes() == payload


def test_download_aborts_declared_size_overrun(s3_case, tmp_path):
    source, stub, record, payload, snapshot = s3_case
    stub.add_response(
        "get_object",
        dict(head(snapshot), Body=StreamingBody(io.BytesIO(payload), len(payload))),
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
            head(snapshot),
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
        {"Bucket": record.bucket, "Prefix": record.key, "MaxKeys": 2},
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
        {"Bucket": record.bucket, "Prefix": record.key, "MaxKeys": 2},
    )
    with pytest.raises(ArchiveBlocked, match="Other object versions"):
        source.evict(record, snapshot)


def test_dry_target_uses_all_real_eviction_checks_without_delete(s3_case):
    source, stub, record, _, snapshot = s3_case
    deletion_prefix(stub, record, snapshot)
    stub.add_response(
        "list_object_versions",
        {
            "IsTruncated": False,
            "Versions": [dict(Key=record.key, VersionId="version-1", IsLatest=True)],
        },
        {"Bucket": record.bucket, "Prefix": record.key, "MaxKeys": 2},
    )
    assert source.deletion_target(record, snapshot) == {
        "Bucket": record.bucket,
        "Key": record.key,
        "VersionId": "version-1",
    }
    # Any delete/extra request would raise because no such response is stubbed.


def test_enabled_bucket_lifecycle_blocks_eviction(s3_case):
    source, stub, record, _, snapshot = s3_case
    deletion_prefix(stub, record, snapshot, lifecycle=True)
    with pytest.raises(ArchiveBlocked, match="lifecycle"):
        source.evict(record, snapshot)


def test_snapshot_retains_headers_and_custom_metadata(s3_case):
    import json

    source, stub, record, _, snapshot = s3_case
    headers = {
        "ContentType": "audio/wav",
        "ContentEncoding": "identity",
        "ContentLanguage": "de",
        "ContentDisposition": 'attachment; filename="sensor.wav"',
        "CacheControl": "private, max-age=0",
        "WebsiteRedirectLocation": "/original",
        "Expires": datetime(2030, 1, 1, tzinfo=UTC),
        "Metadata": {"calibration": "v3", "sensor": "original"},
    }
    stub.add_response(
        "head_object", head(snapshot) | headers, dict(Bucket=record.bucket, Key=record.key)
    )
    captured = source.snapshot(record)
    decoded = json.loads(json.dumps(captured))
    assert decoded["ObjectHeaders"] == headers | {"Expires": "2030-01-01T00:00:00+00:00"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("Metadata", {"calibration": "changed"}),
        ("ContentType", "application/octet-stream"),
        ("VersionId", "other-version"),
        ("ETag", "other-etag"),
        ("ContentLength", 0),
        ("LastModified", datetime(2000, 1, 1, tzinfo=UTC)),
    ],
)
def test_get_metadata_change_blocks_before_creating_file(s3_case, tmp_path, field, value):
    source, stub, record, payload, snapshot = s3_case
    stream = io.BytesIO(payload)
    stub.add_response(
        "get_object",
        head(snapshot) | {field: value, "Body": StreamingBody(stream, len(payload))},
        dict(Bucket=record.bucket, Key=record.key, VersionId="version-1", IfMatch='"etag"'),
    )
    path = tmp_path / "wav"
    with pytest.raises(ArchiveBlocked, match="metadata changed"):
        source.download(record, snapshot, path)
    assert not path.exists() and stream.closed


def test_metadata_change_after_download_is_rejected(s3_case, tmp_path):
    source, stub, record, payload, snapshot = s3_case
    stub.add_response(
        "get_object",
        head(snapshot) | {"Body": StreamingBody(io.BytesIO(payload), len(payload))},
        dict(Bucket=record.bucket, Key=record.key, VersionId="version-1", IfMatch='"etag"'),
    )
    stub.add_response(
        "head_object",
        head(snapshot) | {"Metadata": {"late": "update"}},
        dict(Bucket=record.bucket, Key=record.key, VersionId="version-1"),
    )
    with pytest.raises(ArchiveBlocked, match="metadata changed"):
        source.download(record, snapshot, tmp_path / "wav")


def test_old_snapshot_still_downloads_but_cannot_authorize_deletion(s3_case, tmp_path):
    source, stub, record, payload, snapshot = s3_case
    snapshot.pop("ObjectHeaders")
    stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(payload), len(payload))},
        dict(Bucket=record.bucket, Key=record.key, VersionId="version-1", IfMatch='"etag"'),
    )
    path = tmp_path / "wav"
    source.download(record, snapshot, path)
    assert path.read_bytes() == payload
    with pytest.raises(ArchiveBlocked, match="metadata archive proof"):
        source.evict(record, snapshot)


def test_silent_short_body_is_rejected(s3_case, tmp_path):
    source, stub, record, payload, snapshot = s3_case
    stub.add_response(
        "get_object",
        head(snapshot) | {"Body": StreamingBody(io.BytesIO(payload[:-1]), len(payload) - 1)},
        dict(Bucket=record.bucket, Key=record.key, VersionId="version-1", IfMatch='"etag"'),
    )
    with pytest.raises(ArchiveBlocked, match="shorter than declared"):
        source.download(record, snapshot, tmp_path / "wav")


def test_incomplete_metadata_is_not_archived(s3_case):
    source, stub, record, _, snapshot = s3_case
    stub.add_response(
        "head_object",
        head(snapshot) | {"MissingMeta": 1},
        dict(Bucket=record.bucket, Key=record.key),
    )
    with pytest.raises(ArchiveBlocked, match="metadata is incomplete"):
        source.snapshot(record)


def test_metadata_must_remain_exact_before_eviction(s3_case):
    source, stub, record, _, snapshot = s3_case
    changed = snapshot | {"ObjectHeaders": {"Metadata": {"calibration": "changed"}}}
    deletion_prefix(stub, record, changed)
    with pytest.raises(ArchiveBlocked, match="metadata changed"):
        source.evict(record, snapshot)
