"""Existing Staging reuse must never turn into production/write access."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "reuse", ROOT / "deploy/tiered-archive/validate-existing-staging.py"
)
reuse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reuse)


def profiles():
    writer = dict(
        line.split("=", 1)
        for line in (ROOT / "deploy/tiered-archive/staging-copy.env.example")
        .read_text()
        .splitlines()
        if line and not line.startswith("#")
    )
    writer.update({k: "false" for k in reuse.FLAGS})
    writer["PYTHONPATH"] = reuse.BACKEND
    writer["TIERED_ARCHIVE_PG_CLIENT_DIR"] = reuse.CLIENT_DIR
    writer["TIERED_ARCHIVE_PG_CLIENT_MANIFEST_SHA256"] = reuse.CLIENT_MANIFEST
    for key in reuse.CREDENTIAL_FIELDS:
        writer.pop(key, None)
    writer.update(
        TIERED_ARCHIVE_SOURCE_ORIGIN="existing_archive_host_staging_copy",
        RAW_ARCHIVE_WORKER_ROLE="existing-staging",
        RAW_ARCHIVE_SFTP_USER="u676312",
        RAW_ARCHIVE_SFTP_HOST="u676312.your-storagebox.de",
        RAW_ARCHIVE_SFTP_ROOT="greenmind-raw",
        RAW_ARCHIVE_HEALTH_URLS="http://127.0.0.1:8001/health,http://127.0.0.1:8002/health,http://127.0.0.1:9002/minio/health/ready",
        TIERED_ARCHIVE_BACKUP_KEY_SHA256="a" * 64,
    )
    for kind, db, bucket in [
        ("GATEWAY", "plantdb_staging", "greenmind-raw"),
        ("DIRECT", "greenmind_direct_staging", "greenmind-direct-staging-hotspot"),
    ]:
        writer["RAW_ARCHIVE_" + kind + "_DATABASE_URL"] = (
            "postgresql+psycopg2://greenmind_archive_staging_ro:test@127.0.0.1:5433/" + db
        )
        writer["RAW_ARCHIVE_" + kind + "_S3_ENDPOINT"] = "http://127.0.0.1:9002"
        writer["RAW_ARCHIVE_" + kind + "_S3_BUCKET"] = bucket
        writer["RAW_ARCHIVE_" + kind + "_S3_ACCESS_KEY"] = "gm-tiered-staging-ro"
        writer["RAW_ARCHIVE_" + kind + "_S3_SECRET_KEY"] = "synthetic-test-only"
    reader = {k: "false" for k in reuse.FLAGS}
    reader["PYTHONPATH"] = reuse.BACKEND
    reader.update(
        RAW_ARCHIVE_ENVIRONMENT="staging",
        TIERED_ARCHIVE_ENVIRONMENT="staging",
        TIERED_ARCHIVE_STATE_DIR=reuse.STATE,
        RAW_ARCHIVE_STATE_DIR=reuse.STATE + "/raw",
        RAW_ARCHIVE_READ_ONLY_SFTP_USER="u676312-sub1",
        RAW_ARCHIVE_READ_ONLY_SFTP_HOST="u676312-sub1.your-storagebox.de",
        TIERED_ARCHIVE_BACKUP_KEY_SHA256="a" * 64,
    )
    for profile in (writer, reader):
        profile.update(
            {
                key: writer[key]
                for key in (
                    "RAW_ARCHIVE_HEALTH_URLS",
                    "RAW_ARCHIVE_WORKER_ROLE",
                    "RAW_ARCHIVE_SOURCE_MOUNTS",
                    "RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB",
                    "RAW_ARCHIVE_MAX_HOST_LOAD",
                    "RAW_ARCHIVE_MAX_LOAD_PER_CPU",
                )
            }
        )
        profile["TIERED_ARCHIVE_LATENCY_BASELINE_SHA256"] = "b" * 64
    return (
        writer,
        reader,
        dict(
            schema=1,
            environment="staging",
            wav_sources=["gateway", "direct"],
            scopes=[],
            backups=[],
            scheduled_snapshots=False,
        ),
    )


def test_existing_sources_and_box_pass():
    result = reuse.validate(*profiles())
    assert result["source_ports"] == [5433, 9002]
    assert not result["activation_authorized"] and not result["deletion_enabled"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("TIERED_ARCHIVE_PG_CLIENT_DIR", "/tmp/unreviewed-clients"),  # noqa: S108 - intentionally rejected fixture path
        ("TIERED_ARCHIVE_PG_CLIENT_MANIFEST_SHA256", "f" * 64),
        ("RAW_ARCHIVE_ENVIRONMENT", "production"),
        ("TIERED_ARCHIVE_ENVIRONMENT", "production"),
        (
            "RAW_ARCHIVE_GATEWAY_DATABASE_URL",
            "postgresql+psycopg2://greenmind_archive_staging_ro:test@188.245.247.156:5433/plantdb_staging",
        ),
        (
            "RAW_ARCHIVE_GATEWAY_DATABASE_URL",
            "postgresql+psycopg2://plantuser:test@127.0.0.1:5433/plantdb_staging",
        ),
        (
            "RAW_ARCHIVE_GATEWAY_DATABASE_URL",
            "postgresql+psycopg2://greenmind_archive_staging_ro:test@127.0.0.1:5433/plantdb_staging?host=188.245.247.156",
        ),
        ("RAW_ARCHIVE_DIRECT_S3_ENDPOINT", "http://188.245.247.156:9002"),
        ("RAW_ARCHIVE_DIRECT_S3_BUCKET", "greenmind-direct-production"),
        ("RAW_ARCHIVE_GATEWAY_S3_ACCESS_KEY", "minioadmin"),
        ("RAW_ARCHIVE_SFTP_USER", "u999999"),
        ("RAW_ARCHIVE_SFTP_ROOT", "production"),
        ("TIERED_ARCHIVE_STATE_DIR", "/var/lib/greenmind/archive-copy-20261010"),
        ("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "512"),
        ("RAW_ARCHIVE_MAX_LOAD_PER_CPU", "1"),
        ("TIERED_ARCHIVE_MAX_WAV_BUNDLE_BYTES", str(64 * 1024**2)),
        ("RAW_ARCHIVE_SOURCE_PROBE_URL", "http://127.0.0.1:28143/source-health"),
        ("TIERED_ARCHIVE_GATEWAY_WRITER_DATABASE_URL", "forbidden"),
    ]
    + [(flag, "true") for flag in reuse.FLAGS],
)
def test_unsafe_writer_rejected(field, value):
    writer, reader, schedule = profiles()
    writer[field] = value
    with pytest.raises(ValueError):
        reuse.validate(writer, reader, schedule)


@pytest.mark.parametrize(
    "field,value",
    [
        ("RAW_ARCHIVE_SFTP_KEY", "/upload"),
        ("DATABASE_URL", "forbidden"),
        ("RAW_ARCHIVE_READ_ONLY_SFTP_USER", "u676312"),
        ("RAW_ARCHIVE_READ_ONLY_SFTP_ROOT", "greenmind-staging"),
        ("TIERED_ARCHIVE_DELETE_ENABLED", "true"),
    ],
)
def test_reader_cannot_inherit_upload_or_change_root(field, value):
    writer, reader, schedule = profiles()
    reader[field] = value
    with pytest.raises(ValueError):
        reuse.validate(writer, reader, schedule)


@pytest.mark.parametrize("profile_index", [0, 1])
@pytest.mark.parametrize("field", ["PYTHONPATH", *reuse.CREDENTIAL_FIELDS])
def test_environment_file_cannot_override_runtime_or_credentials(profile_index, field):
    writer, reader, schedule = profiles()
    (writer, reader)[profile_index][field] = "/old-release-or-private-key"
    with pytest.raises(ValueError):
        reuse.validate(writer, reader, schedule)


@pytest.mark.parametrize(
    "field,value",
    [
        ("RAW_ARCHIVE_HEALTH_URLS", "http://127.0.0.1:8120/health"),
        ("RAW_ARCHIVE_WORKER_ROLE", "archive"),
        ("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "512"),
        ("RAW_ARCHIVE_MAX_LOAD_PER_CPU", "1"),
        ("TIERED_ARCHIVE_LATENCY_BASELINE_SHA256", ""),
        ("TIERED_ARCHIVE_LATENCY_BASELINE_SHA256", "c" * 64),
    ],
)
def test_reader_cannot_bypass_staging_latency_guard(field, value):
    writer, reader, schedule = profiles()
    reader[field] = value
    with pytest.raises(ValueError):
        reuse.validate(writer, reader, schedule)


@pytest.mark.parametrize("source", ["GATEWAY", "DIRECT"])
def test_backup_binding_reuses_exact_existing_readonly_source(source):
    writer, reader, schedule = profiles()
    writer["TIERED_ARCHIVE_" + source + "_BACKUP_DATABASE_URL"] = writer[
        "RAW_ARCHIVE_" + source + "_DATABASE_URL"
    ]
    result = reuse.validate(writer, reader, schedule)
    assert not result["activation_authorized"] and not result["deletion_enabled"]


@pytest.mark.parametrize("source", ["GATEWAY", "DIRECT"])
@pytest.mark.parametrize(
    "change", ["owner", "production", "query", "other_database", "password", "empty"]
)
def test_backup_binding_cannot_expand_source_or_privileges(source, change):
    writer, reader, schedule = profiles()
    url = writer["RAW_ARCHIVE_" + source + "_DATABASE_URL"]
    mutations = {
        "owner": url.replace("greenmind_archive_staging_ro", "postgres"),
        "production": url.replace("127.0.0.1", "188.245.247.156"),
        "query": url + "?host=188.245.247.156",
        "other_database": url.replace("_staging", "_production"),
        "password": url.replace(":test@", ":different@"),
        "empty": "",
    }
    writer["TIERED_ARCHIVE_" + source + "_BACKUP_DATABASE_URL"] = mutations[change]
    with pytest.raises(ValueError, match="exact existing read-only"):
        reuse.validate(writer, reader, schedule)
