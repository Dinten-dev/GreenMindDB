"""Live controls remain read-only beside real PostgreSQL raw ingestion."""

import pytest

from tests.direct import test_live as contract

pytest_plugins = ("tests.direct.test_postgres",)
pytestmark = pytest.mark.integration


def test_live_sql_read_only_raw_ack_and_markers(pg_pipeline, monkeypatch):
    live = contract.prepare_live(pg_pipeline, monkeypatch)
    contract.test_preview_does_not_write_sql_and_preserves_raw_samples(live)


def test_live_zone_session_and_device_revocation_on_postgres(pg_pipeline, monkeypatch):
    live = contract.prepare_live(pg_pipeline, monkeypatch)
    contract.test_origin_session_zone_device_and_lease_ownership_are_rechecked(live)
