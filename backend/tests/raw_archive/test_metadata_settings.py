"""Metadata export must never request or import S3 credentials."""

import sys

import pytest

from app.raw_archive import isolated_catalog
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.runner import metadata_source_settings


def test_metadata_only_needs_no_object_credentials(monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_GATEWAY_DATABASE_URL", "postgresql://reader@127.0.0.1/plantdb")
    monkeypatch.setenv("RAW_ARCHIVE_DIRECT_DATABASE_URL", "postgresql://reader@127.0.0.1/direct")
    monkeypatch.setenv("RAW_ARCHIVE_DIRECT_S3_BUCKET", "greenmind-direct-production-hotspot")
    for kind in ("GATEWAY", "DIRECT"):
        for field in ("S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_ENDPOINT"):
            monkeypatch.delenv("RAW_ARCHIVE_" + kind + "_" + field, raising=False)
    assert set(metadata_source_settings("gateway", "production")) == {"database", "bucket"}
    assert (
        metadata_source_settings("direct", "production")["bucket"]
        == "greenmind-direct-production-hotspot"
    )


@pytest.mark.parametrize("session", ["not-hex", "A" * 24, "a" * 25, "a;hostname"])
def test_isolated_catalog_rejects_remote_command_input_before_credentials(session, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "catalog",
            "--session",
            session,
            "--credentials",
            "/absent",
            "--ledger",
            "/absent",
            "--output",
            "/absent",
        ],
    )
    monkeypatch.setattr(
        isolated_catalog, "load_credentials", lambda _: pytest.fail("No access before validation")
    )
    with pytest.raises(ArchiveBlocked, match="session"):
        isolated_catalog.main()
