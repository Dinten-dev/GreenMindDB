"""Exact-version contract on the production MinIO RELEASE, in an ephemeral local container."""

import hashlib
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from app.raw_archive.policy import ArchiveBlocked, Recording
from app.raw_archive.storage import S3Source

pytestmark = pytest.mark.integration
IMAGE = "minio/minio:RELEASE.2025-09-07T16-13-09Z"


@pytest.fixture
def minio():
    if os.environ.get("SKIP_DOCKER_TESTS") == "1":
        pytest.skip("Explicitly disabled local Docker tests")
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Local Docker executable unavailable")
    name = "greenmind-delete-fixture-" + uuid.uuid4().hex
    # These credentials belong exclusively to this empty ephemeral fixture.
    username, password = "isolated-fixture", uuid.uuid4().hex
    subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "-d",
            "--platform",
            "linux/amd64",
            "--name",
            name,
            "--label",
            "greenmind.deletion-test=true",
            "--memory",
            "256m",
            "--cpus",
            "0.5",
            "-p",
            "127.0.0.1::9000",
            "-e",
            "MINIO_ROOT_USER=" + username,
            "-e",
            "MINIO_ROOT_PASSWORD=" + password,
            IMAGE,
            "server",
            "/data",
        ],
        check=True,
        capture_output=True,
        timeout=240,
    )
    try:
        port = subprocess.check_output(
            [docker, "port", name, "9000"], text=True, timeout=10
        ).strip()
        client = boto3.client(
            "s3",
            endpoint_url="http://" + port,
            aws_access_key_id=username,
            aws_secret_access_key=password,
            config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 0}),
        )
        deadline = time.monotonic() + 45
        while True:
            try:
                client.list_buckets()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        client.create_bucket(Bucket="isolated-originals")
        yield client
    finally:
        label = subprocess.check_output(
            [
                docker,
                "inspect",
                "--format",
                '{{index .Config.Labels "greenmind.deletion-test"}}',
                name,
            ],
            text=True,
            timeout=10,
        ).strip()
        assert label == "true"
        subprocess.run([docker, "rm", "-f", name], check=True, capture_output=True, timeout=20)


def test_existing_null_version_can_be_addressed_without_reupload(minio, monkeypatch):
    payload, bucket, key = b"existing-original", "isolated-originals", "old.wav"
    minio.put_object(Bucket=bucket, Key=key, Body=payload)
    original = minio.head_object(Bucket=bucket, Key=key)
    minio.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    assert minio.get_object(Bucket=bucket, Key=key, VersionId="null")["Body"].read() == payload
    old = original["LastModified"]

    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return old + timedelta(days=9)

    monkeypatch.setattr("app.raw_archive.storage.datetime", FutureClock)
    record = Recording(
        "gateway",
        "isolated",
        bucket,
        key,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        old,
        old,
        "a" * 64,
        old,
        "mac-14-c1-9f-d9-42-a4",
    )
    snapshot = {
        "VersionId": None,
        "ETag": original["ETag"],
        "ContentLength": len(payload),
        "LastModified": original["LastModified"].isoformat(),
    }
    # Clock is controlled in the fixture solely to isolate version addressing;
    # the outer deletion gate separately tests the seven-day real age rule.
    source = S3Source(minio, allow_legacy_null=True)
    source.evict(record, snapshot)
    with pytest.raises(ClientError) as error:
        minio.get_object(Bucket=bucket, Key=key, VersionId="null")
    assert error.value.response["Error"]["Code"] in {"NoSuchKey", "NoSuchVersion", "404"}


def test_explicit_null_removal_never_removes_a_concurrent_new_version(minio, monkeypatch):
    bucket, key = "isolated-originals", "race.wav"
    minio.put_object(Bucket=bucket, Key=key, Body=b"old")
    original = minio.head_object(Bucket=bucket, Key=key)
    minio.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    old = original["LastModified"]

    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return old + timedelta(days=9)

    monkeypatch.setattr("app.raw_archive.storage.datetime", FutureClock)
    record = Recording(
        "gateway",
        "isolated-race",
        bucket,
        key,
        hashlib.sha256(b"old").hexdigest(),
        3,
        old,
        old,
        "a" * 64,
        old,
        "mac-14-c1-9f-d9-42-a4",
    )
    snapshot = {
        "VersionId": None,
        "ETag": original["ETag"],
        "ContentLength": 3,
        "LastModified": old.isoformat(),
    }
    with pytest.raises(ArchiveBlocked, match="versioning"):
        S3Source(minio).evict(record, snapshot)
    newer = {}

    class RaceAfterList:
        def __getattr__(self, name):
            return getattr(minio, name)

        def list_object_versions(self, **arguments):
            result = minio.list_object_versions(**arguments)
            newer.update(minio.put_object(Bucket=bucket, Key=key, Body=b"new"))
            return result

    S3Source(RaceAfterList(), allow_legacy_null=True, diagnostic_client=minio).evict(
        record, snapshot
    )
    assert minio.get_object(Bucket=bucket, Key=key)["Body"].read() == b"new"
    assert minio.head_object(Bucket=bucket, Key=key)["VersionId"] == newer["VersionId"]
