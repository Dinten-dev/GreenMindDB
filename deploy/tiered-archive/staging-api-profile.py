"""Prepare one existing staging reader profile without IO or new credentials.

The applying operator must first verify container/image/network identities and
keep this returned environment server-local and root-private. No CLI prints it.
"""

from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

FLAGS = (
    "TIERED_ARCHIVE_DELETE_ENABLED",
    "RAW_ARCHIVE_DELETE_ENABLED",
    "RETENTION_ENABLED",
    "DIRECT_RETENTION_ENABLED",
    "VISUAL_PRUNE_ENABLED",
    "TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED",
    "TIERED_ARCHIVE_DIRECT_LOCATORS_ACCEPTED",
    "TIERED_ARCHIVE_FEATURE_READS_ACCEPTED",
)
SOCKET = "/run/greenmind-tiered-archive-staging/history.sock"
PROFILE = "/etc/greenmind/tiered-archive/staging.env"


def prepare(existing, source):
    if (
        existing.get("SERVICE_ROLE") != "visualization"
        or existing.get("DIRECT_ENVIRONMENT") != "staging"
        or existing.get("VISUAL_DIRECT_ENABLED") != "true"
        or existing.get("ARCHIVE_REMOTE_AUTH_ENABLED", "false") != "false"
        or existing.get("GREENMIND_TRANSACTION_POOL_READS", "false") != "false"
        or source.get("TIERED_ARCHIVE_SOURCE_ORIGIN")
        != "existing_archive_host_staging_copy"
        or any(
            source.get(key) != "staging"
            for key in ("RAW_ARCHIVE_ENVIRONMENT", "TIERED_ARCHIVE_ENVIRONMENT")
        )
        or any(source.get(key) != "false" for key in FLAGS)
        or any(existing.get(key, "false").lower() != "false" for key in FLAGS)
    ):
        raise ValueError(
            "Only the existing non-destructive staging reader may be prepared"
        )
    if any(key.startswith("RAW_ARCHIVE_SFTP_") or "WRITER_" in key for key in existing):
        raise ValueError("No source writer or Box upload access in the API")
    endpoints = {
        "S3_ENDPOINT": ("http", "minio", 9000),
        "DIRECT_S3_ENDPOINT_URL": ("https", "test.green-mind.ch", 9443),
    }
    for key, expected in endpoints.items():
        url = urlsplit(existing.get(key, ""))
        if (
            (url.scheme, url.hostname, url.port) != expected
            or url.path not in ("", "/")
            or any((url.username, url.password, url.query, url.fragment))
        ):
            raise ValueError("Retain the inventoried staging object endpoints")
    result = dict(existing)
    for kind, database in (
        ("GATEWAY", "plantdb_staging"),
        ("DIRECT", "greenmind_direct_staging"),
    ):
        target = "DATABASE_URL" if kind == "GATEWAY" else "DIRECT_DATABASE_URL"
        current = make_url(existing[target])
        ro = make_url(source["RAW_ARCHIVE_" + kind + "_DATABASE_URL"])
        if (
            (current.drivername, current.host, current.port, current.database)
            != ("postgresql+psycopg2", "postgres", 5432, database)
            or (ro.drivername, ro.host, ro.port, ro.database, ro.username)
            != (
                "postgresql+psycopg2",
                "127.0.0.1",
                5433,
                database,
                "greenmind_archive_staging_ro",
            )
            or not ro.password
            or ro.query
            or current.query
        ):
            raise ValueError(
                "Rebind only the inventoried local SELECT role and databases"
            )
        result[target] = ro.set(host=current.host, port=current.port).render_as_string(
            hide_password=False
        )
        prefix = "RAW_ARCHIVE_" + kind + "_S3_"
        bucket = (
            "greenmind-raw" if kind == "GATEWAY" else "greenmind-direct-staging-hotspot"
        )
        if (
            source.get(prefix + "ENDPOINT") != "http://127.0.0.1:9002"
            or source.get(prefix + "BUCKET") != bucket
            or source.get(prefix + "ACCESS_KEY") != "gm-tiered-staging-ro"
            or not source.get(prefix + "SECRET_KEY")
        ):
            raise ValueError("Reuse the existing staging GET-only object account")
        if kind == "DIRECT" and existing.get("DIRECT_S3_BUCKET") != bucket:
            raise ValueError("Do not redirect Direct to another bucket")
        access, secret = (
            ("S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
            if kind == "GATEWAY"
            else ("DIRECT_S3_ACCESS_KEY", "DIRECT_S3_SECRET_KEY")
        )
        result[access], result[secret] = (
            source[prefix + "ACCESS_KEY"],
            source[prefix + "SECRET_KEY"],
        )
    if not existing.get("JWT_SECRET_KEY"):
        raise ValueError("Preserve the existing API authentication secret")
    result.update({key: "false" for key in FLAGS})
    result.update(
        ENVIRONMENT="staging",
        COOKIE_SECURE="true",
        S3_READ_ONLY="true",
        TIERED_ARCHIVE_READS_ENABLED="true",
        TIERED_ARCHIVE_ENVIRONMENT="staging",
        TIERED_ARCHIVE_READ_BROKER_SOCKET=SOCKET,
        RAW_ARCHIVE_READ_BROKER_SOCKET=SOCKET,
        RAW_ARCHIVE_READS_ENABLED="true",
        RAW_ARCHIVE_ENVIRONMENT="staging",
        ARCHIVE_COMPAT_READS_ENABLED="true",
        DIRECT_ARCHIVE_BROKER_SOCKET=SOCKET,
        DIRECT_INGEST_ENABLED="false",
        DIRECT_RETENTION_ENABLED="false",
        GUNICORN_WORKERS="1",
        PYTHONPATH="/app/archive-dependencies:/app",
    )
    assert result["JWT_SECRET_KEY"] == existing["JWT_SECRET_KEY"]
    return result
