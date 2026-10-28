"""Reuse the staging SELECT/GET identities without moving upload or JWT secrets."""

import runpy
from pathlib import Path

import pytest
from sqlalchemy.engine import URL, make_url

MODULE = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "deploy/tiered-archive/staging-api-profile.py")
)


@pytest.fixture
def profiles():
    existing = dict(
        SERVICE_ROLE="visualization",
        DIRECT_ENVIRONMENT="staging",
        VISUAL_DIRECT_ENABLED="true",
        DATABASE_URL="postgresql+psycopg2://old:old@postgres:5432/plantdb_staging",
        DIRECT_DATABASE_URL="postgresql+psycopg2://old:old@postgres:5432/greenmind_direct_staging",
        S3_ENDPOINT="http://minio:9000",
        DIRECT_S3_ENDPOINT_URL="https://test.green-mind.ch:9443",
        DIRECT_S3_BUCKET="greenmind-direct-staging-hotspot",
        JWT_SECRET_KEY="synthetic-existing-auth-key",
    )
    source = {key: "false" for key in MODULE["FLAGS"]}
    source.update(
        TIERED_ARCHIVE_SOURCE_ORIGIN="existing_archive_host_staging_copy",
        RAW_ARCHIVE_ENVIRONMENT="staging",
        TIERED_ARCHIVE_ENVIRONMENT="staging",
        RAW_ARCHIVE_SFTP_USER="synthetic-upload-account",
        RAW_ARCHIVE_SFTP_KEY="/not-an-api-key",
    )
    for kind, database, bucket in [
        ("GATEWAY", "plantdb_staging", "greenmind-raw"),
        ("DIRECT", "greenmind_direct_staging", "greenmind-direct-staging-hotspot"),
    ]:
        prefix = "RAW_ARCHIVE_" + kind + "_"
        source[prefix + "DATABASE_URL"] = URL.create(
            "postgresql+psycopg2",
            username="greenmind_archive_staging_ro",
            password="synthetic:@/#%",
            host="127.0.0.1",
            port=5433,
            database=database,
        ).render_as_string(hide_password=False)
        for name, value in dict(
            ENDPOINT="http://127.0.0.1:9002",
            BUCKET=bucket,
            ACCESS_KEY="gm-tiered-staging-ro",
            SECRET_KEY="synthetic-get-key",
        ).items():
            source[prefix + "S3_" + name] = value
    return existing, source


def test_existing_auth_and_endpoints_survive_ro_rebinding_without_credential_spill(profiles):
    existing, source = profiles
    before = dict(existing), dict(source)
    profile = MODULE["prepare"](existing, source)
    assert (existing, source) == before
    assert profile["JWT_SECRET_KEY"] == existing["JWT_SECRET_KEY"]
    assert profile["DIRECT_S3_ENDPOINT_URL"] == existing["DIRECT_S3_ENDPOINT_URL"]
    assert profile["S3_ENDPOINT"] == existing["S3_ENDPOINT"]
    assert not any(name.startswith("RAW_ARCHIVE_SFTP_") for name in profile)
    for kind, target in [("GATEWAY", "DATABASE_URL"), ("DIRECT", "DIRECT_DATABASE_URL")]:
        previous = make_url(existing[target])
        current = make_url(profile[target])
        assert (current.host, current.port, current.database) == (
            previous.host,
            previous.port,
            previous.database,
        )
        assert current.username == "greenmind_archive_staging_ro"
        assert (
            current.password == make_url(source["RAW_ARCHIVE_" + kind + "_DATABASE_URL"]).password
        )
    assert all(profile[key] == "false" for key in MODULE["FLAGS"])


@pytest.mark.parametrize(
    "key,value",
    [
        ("SERVICE_ROLE", "application"),
        ("DIRECT_ENVIRONMENT", "production"),
        ("DIRECT_S3_ENDPOINT_URL", "http://test.green-mind.ch:9443"),
        ("DIRECT_S3_ENDPOINT_URL", "https://another.example:9443"),
        ("DIRECT_S3_BUCKET", "greenmind-direct-production-hotspot"),
        ("RAW_ARCHIVE_SFTP_KEY", "/synthetic-upload-key"),
        ("ARCHIVE_REMOTE_AUTH_ENABLED", "true"),
        ("TIERED_ARCHIVE_DELETE_ENABLED", "true"),
    ],
)
def test_receiver_production_endpoint_or_upload_profiles_are_not_repurposed(profiles, key, value):
    existing, source = profiles
    with pytest.raises(ValueError):
        MODULE["prepare"]({**existing, key: value}, source)


@pytest.mark.parametrize(
    "key,value",
    [
        ("RAW_ARCHIVE_ENVIRONMENT", "production"),
        (
            "RAW_ARCHIVE_DIRECT_DATABASE_URL",
            "postgresql+psycopg2://writer:synthetic@127.0.0.1:5433/greenmind_direct_staging",
        ),
        (
            "RAW_ARCHIVE_DIRECT_DATABASE_URL",
            "postgresql+psycopg2://greenmind_archive_staging_ro:synthetic@127.0.0.1:5433/greenmind_direct_production",
        ),
        ("RAW_ARCHIVE_GATEWAY_S3_ENDPOINT", "https://another.example"),
        ("RAW_ARCHIVE_GATEWAY_S3_ACCESS_KEY", "minioadmin"),
        ("RAW_ARCHIVE_GATEWAY_S3_BUCKET", "another-bucket"),
        ("RETENTION_ENABLED", "true"),
    ],
)
def test_unreviewed_source_identities_and_destructive_settings_are_rejected(profiles, key, value):
    existing, source = profiles
    with pytest.raises(ValueError):
        MODULE["prepare"](existing, {**source, key: value})
