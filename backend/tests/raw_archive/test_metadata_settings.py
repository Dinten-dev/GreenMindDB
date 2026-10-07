"""Metadata export must never request or import S3 credentials."""

from app.raw_archive.runner import metadata_source_settings


def test_metadata_only_needs_no_object_credentials(monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_GATEWAY_DATABASE_URL", "postgresql://reader@127.0.0.1/plantdb")
    monkeypatch.setenv("RAW_ARCHIVE_DIRECT_DATABASE_URL", "postgresql://reader@127.0.0.1/direct")
    monkeypatch.setenv("RAW_ARCHIVE_DIRECT_S3_BUCKET", "greenmind-direct-production-hotspot")
    for kind in ("GATEWAY", "DIRECT"):
        for field in ("S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_ENDPOINT"):
            monkeypatch.delenv("RAW_ARCHIVE_" + kind + "_" + field, raising=False)
    assert set(metadata_source_settings("gateway", "production")) == {"database", "bucket"}
    assert metadata_source_settings("direct", "production")["bucket"] == "greenmind-direct-production-hotspot"
