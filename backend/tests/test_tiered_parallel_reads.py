"""Slow archive reads must not exhaust the existing SELECT connection budget."""

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_broker import Handler, Server
from app.tiered_archive import client, reader
from app.tiered_archive.read_service import ReadHandler


@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_four_dashboard_reads_share_one_sql_connection_and_one_archive_worker(
    tmp_path, monkeypatch, source
):
    engine = create_engine(
        "sqlite:///" + str(tmp_path / "source.sqlite3"),
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.5,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as db:
        for statement in (
            "CREATE TABLE zone(id TEXT, organization_id TEXT)",
            "CREATE TABLE gateway(id TEXT, zone_id TEXT)",
            "CREATE TABLE sensor(id TEXT, gateway_id TEXT)",
            "CREATE TABLE direct_device(id TEXT, organization_id TEXT)",
            "INSERT INTO zone VALUES('zone','organization')",
            "INSERT INTO gateway VALUES('gateway','zone')",
            "INSERT INTO sensor VALUES('sensor','gateway')",
            "INSERT INTO direct_device VALUES('sensor','organization')",
        ):
            db.execute(text(statement))
    monkeypatch.setenv("TIERED_ARCHIVE_READS_ENABLED", "true")
    monkeypatch.setenv("TIERED_ARCHIVE_STATE_DIR", str(tmp_path))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    scope = dict(
        source=source,
        organization="organization",
        sensor="sensor",
        start=start.isoformat(),
        end=(start + timedelta(days=1)).isoformat(),
        step=600,
    )
    calls, busy, maximum = [], 0, 0

    def archive(data, destination):
        nonlocal busy, maximum
        assert data == scope
        busy += 1
        maximum = max(maximum, busy)
        try:
            time.sleep(0.04)
            calls.append(data)
            return {"series": [], "waves": []}
        finally:
            busy -= 1

    monkeypatch.setattr(reader, "private_series", archive)
    barrier = threading.Barrier(4)
    request = client.request

    def after_source_queries(*args, **kwargs):
        barrier.wait(timeout=2)
        assert engine.pool.checkedout() == 0
        return request(*args, **kwargs)

    monkeypatch.setattr(client, "request", after_source_queries)
    live = [{"kind": "bio_signal_ch1", "unit": "mV", "data": []}]

    def query():
        with Session(engine) as db:
            result = reader.history(
                copy.deepcopy(live),
                db,
                "sensor",
                start,
                start + timedelta(days=1),
                600,
                source=source,
            )
            assert db.execute(text("SELECT 1")).scalar_one() == 1
            return result

    try:
        with TemporaryDirectory(prefix="gm-parallel-", dir="/private/tmp") as folder:
            path = Path(folder) / "read.sock"
            monkeypatch.setenv("TIERED_ARCHIVE_READ_BROKER_SOCKET", str(path))
            with Server(str(path), None, None, tmp_path, health_check=lambda: None) as server:
                server.RequestHandlerClass = ReadHandler
                thread = threading.Thread(
                    target=server.serve_forever, kwargs={"poll_interval": 0.01}
                )
                thread.start()
                try:
                    with ThreadPoolExecutor(max_workers=4) as executor:
                        results = list(executor.map(lambda _: query(), range(4)))
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
        expected = [
            {
                **live[0],
                "sensor_id": "sensor",
                "aggregation": "24h:1s;7d:1m;older:10m",
                "source": source,
            }
        ]
        assert results == [expected] * 4
        assert len(calls) == 4 and maximum == 1
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()


def test_historical_queue_is_bounded_and_legacy_broker_remains_fail_fast():
    calls = []

    class Busy:
        def acquire(self, **kwargs):
            calls.append(kwargs)
            return False

    for handler_type in (ReadHandler, Handler):
        handler = object.__new__(handler_type)
        handler.server = SimpleNamespace(read_slot=Busy())
        assert not handler.acquire_read_slot()
    assert calls == [{"timeout": 15}, {"blocking": False}]


def test_release_never_discards_pending_orm_changes_or_nested_transaction(db):
    from app.models.user import Organization

    pending = Organization(name="synthetic pending change")
    db.add(pending)
    with pytest.raises(ArchiveBlocked, match="completed read-only"):
        client.release_read_connection(db)
    assert pending in db.new
    db.rollback()
    with db.begin_nested():
        with pytest.raises(ArchiveBlocked, match="completed read-only"):
            client.release_read_connection(db)
        assert db.in_nested_transaction()


def test_detached_read_values_remain_available_and_legacy_auth_releases_pool(tmp_path, monkeypatch):
    from app.models.user import Organization
    from app.visualization import direct_api

    engine = create_engine("sqlite:///" + str(tmp_path / "direct.sqlite3"))
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE direct_device(id TEXT, organization_id TEXT, zone_id TEXT, mode TEXT)"
            )
        )
        connection.execute(text("INSERT INTO direct_device VALUES('device','org','zone','DIRECT')"))
        Organization.__table__.create(connection)
        connection.execute(Organization.__table__.insert().values(name="synthetic loaded value"))
    monkeypatch.setattr(direct_api, "is_platform_admin", lambda _: False)

    def authorize_zone(legacy, user, zone):
        assert zone == "zone" and user.organization_id == "org"
        legacy.execute(text("SELECT 1"))

    monkeypatch.setattr(direct_api, "require_zone_access", authorize_zone)
    monkeypatch.setenv("TIERED_ARCHIVE_READS_ENABLED", "true")
    try:
        with Session(engine) as direct_db, Session(engine) as legacy:
            loaded = legacy.query(Organization).one()
            row = direct_api.authorize(
                direct_db, legacy, SimpleNamespace(organization_id="org"), "device"
            )
            assert not legacy.in_transaction()
            assert loaded.name == "synthetic loaded value" and row["mode"] == "DIRECT"
            assert legacy.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()
