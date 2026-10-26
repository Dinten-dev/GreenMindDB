"""Real, disposable PostgreSQL row fencing and lossless archive contracts."""

import os
import shutil
import subprocess
import time
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import parquet
from app.tiered_archive.deletion import evict
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.pipeline import publish, verify_snapshot
from app.tiered_archive.source import Source
from tests.test_tiered_archive import AT, ORG, SENSOR, Box, Provider, permit

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def postgres():
    docker = shutil.which("docker")
    if not docker or os.getenv("SKIP_DOCKER_TESTS") == "1":
        pytest.skip("Isolated Docker required")
    name = "greenmind-tiered-test-" + uuid.uuid4().hex
    password = uuid.uuid4().hex
    subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "--memory",
            "256m",
            "--cpus",
            "0.5",
            "-p",
            "127.0.0.1::5432",
            "-e",
            "POSTGRES_PASSWORD=" + password,
            "-e",
            "POSTGRES_DB=greenmind_tiered_restore_test",
            "postgres:16",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    engine = None
    try:
        address = subprocess.check_output([docker, "port", name, "5432"], text=True).strip()
        engine = create_engine(
            "postgresql+psycopg2://postgres:"
            + password
            + "@"
            + address
            + "/greenmind_tiered_restore_test"
        )
        deadline = time.monotonic() + 45
        while True:
            try:
                with engine.connect() as db:
                    db.execute(text("SELECT 1"))
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        engine._tiered_fixture_container = name
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=15)


@pytest.fixture
def source(postgres, monkeypatch):
    monkeypatch.setenv("TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED", "true")
    with postgres.begin() as db:
        for table in (
            "tiered_visual_reference",
            "tiered_wave_reference",
            "tiered_reading_identity",
            "sensor_reading",
            "wav_file",
            "visual_chunk",
            "visual_reading",
            "sensor",
            "gateway",
            "zone",
        ):
            db.execute(text("DROP TABLE IF EXISTS " + table))
        db.execute(text("CREATE TABLE zone(id uuid PRIMARY KEY,organization_id uuid NOT NULL)"))
        db.execute(text("CREATE TABLE gateway(id uuid PRIMARY KEY,zone_id uuid NOT NULL)"))
        db.execute(text("CREATE TABLE sensor(id uuid PRIMARY KEY,gateway_id uuid NOT NULL)"))
        db.execute(
            text("""CREATE TABLE visual_reading(sensor_id uuid,kind text,bucket timestamptz,seconds int,unit text,
            n bigint,total float8,total2 float8,minimum float8,maximum float8,identities bytea,precise numeric(24,12),
            PRIMARY KEY(sensor_id,kind,bucket,seconds))""")
        )
        db.execute(
            text(
                "CREATE INDEX tiered_visual_reading_scan ON visual_reading(bucket,sensor_id,kind,seconds)"
            )
        )
        db.execute(
            text(
                "CREATE TABLE sensor_reading(timestamp timestamptz,sensor_id uuid,kind text,value float8,unit text,PRIMARY KEY(timestamp,sensor_id,kind))"
            )
        )
        db.execute(
            text(
                "CREATE TABLE wav_file(id uuid PRIMARY KEY,sensor_id uuid,started_at timestamptz,ended_at timestamptz,feature_status text,feature_verified_at timestamptz,raw_deleted_at timestamptz)"
            )
        )
        db.execute(
            text(
                "CREATE TABLE visual_chunk(range_start timestamptz,range_end timestamptz,status text,change_version bigint,archived_version bigint,archive_manifest jsonb)"
            )
        )
        from pathlib import Path

        db.execute(
            text(
                (
                    Path(__file__).resolve().parents[1]
                    / "app/tiered_archive/gateway_references.sql"
                ).read_text()
            )
        )
        db.execute(text("INSERT INTO zone VALUES(:id,:org)"), {"id": ORG, "org": ORG})
        db.execute(text("INSERT INTO gateway VALUES(:id,:zone)"), {"id": ORG, "zone": ORG})
        db.execute(
            text("INSERT INTO sensor VALUES(:sensor,:gateway)"), {"sensor": SENSOR, "gateway": ORG}
        )
        db.execute(
            text(
                """INSERT INTO visual_reading VALUES(:sensor,'temperature',:at,600,'C',2,6,20,2,4,:payload,:amount)"""
            ),
            {
                "sensor": SENSOR,
                "at": AT,
                "payload": b"\x00\xff",
                "amount": Decimal("123.000000000001"),
            },
        )
    return Source(postgres, "gateway", "staging", checkpoint=lambda: None)


def archive(source, tmp_path, dataset="visual_reading"):
    rows, flags = source.export(dataset, ORG, AT, AT + timedelta(days=1))
    path = tmp_path / "original.parquet"
    metadata = dict(parquet.write(path, rows), format="parquet")
    candidate = source.candidate(
        dataset,
        ORG,
        AT,
        AT + timedelta(days=1),
        metadata,
        flags,
        settled_at=AT + timedelta(days=1),
    )
    ledger = Ledger(tmp_path / "state")
    box = Box(tmp_path / "box")
    first = AT + timedelta(days=2)
    now = AT + timedelta(days=100)
    publish(candidate, [(path, metadata)], box, ledger, checkpoint=lambda: None, now=first)
    verify_snapshot(candidate, box, ledger, Provider(first), 3, checkpoint=lambda: None, now=first)
    verify_snapshot(candidate, box, ledger, Provider(now), 4, checkpoint=lambda: None, now=now)
    source.expected = list(parquet.read(path, metadata))
    activation, sha = permit(tmp_path, candidate, now)
    return (
        candidate,
        ledger,
        dict(
            authorization_path=activation,
            authorization_sha256=sha,
            runtime_sha256="a" * 64,
            bindings_sha256="b" * 64,
            checkpoint=lambda: None,
            clock=lambda: now,
        ),
    )


def test_real_sql_types_and_exact_delete_preserve_new_rows(source, tmp_path):
    candidate, ledger, kwargs = archive(source, tmp_path)
    assert source.expected[0]["body"]["row"]["precise"] == Decimal("123.000000000001")
    assert source.expected[0]["body"]["row"]["identities"] == b"\x00\xff"
    with source.engine.begin() as db:
        db.execute(
            text(
                """INSERT INTO visual_reading SELECT sensor_id,'humidity',bucket,seconds,unit,n,total,total2,minimum,maximum,identities,precise FROM visual_reading"""
            )
        )
    try:
        evict(candidate, source, ledger, **kwargs)
        with source.engine.connect() as db:
            assert db.execute(text("SELECT kind FROM visual_reading")).scalars().all() == [
                "humidity"
            ]
        assert ledger.get(candidate.identity)["state"] == "evicted"
    finally:
        ledger.close()


def test_source_update_after_archive_blocks_real_delete(source, tmp_path):
    candidate, ledger, kwargs = archive(source, tmp_path)
    with source.engine.begin() as db:
        db.execute(text("UPDATE visual_reading SET total=9"))
    try:
        with pytest.raises(ArchiveBlocked):
            evict(candidate, source, ledger, **kwargs)
        with source.engine.connect() as db:
            assert db.execute(text("SELECT count(*) FROM visual_reading")).scalar_one() == 1
        assert ledger.get(candidate.identity)["state"] == "verified"
    finally:
        ledger.close()


def test_keyset_pages_do_not_skip_same_timestamp(source):
    with source.engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO visual_reading SELECT sensor_id,'humidity',bucket,seconds,unit,n,total,total2,minimum,maximum,identities,precise FROM visual_reading"
            )
        )
    first, _ = source.export("visual_reading", ORG, AT, AT + timedelta(days=1), limit=1)
    second, _ = source.export(
        "visual_reading", ORG, AT, AT + timedelta(days=1), limit=1, after=source.next_cursor
    )
    assert len(first) == len(second) == 1 and first[0]["identity"] != second[0]["identity"]


def test_changed_organization_blocks_real_delete(source, tmp_path):
    candidate, ledger, kwargs = archive(source, tmp_path)
    with source.engine.begin() as db:
        db.execute(text("UPDATE zone SET organization_id=:org"), {"org": str(uuid.uuid4())})
    try:
        with pytest.raises(ArchiveBlocked):
            evict(candidate, source, ledger, **kwargs)
        with source.engine.connect() as db:
            assert db.execute(text("SELECT count(*) FROM visual_reading")).scalar_one() == 1
    finally:
        ledger.close()


def test_consistent_schema_and_master_dump_against_postgres(source, tmp_path):
    import sys

    from app.tiered_archive.backup import dump

    with source.engine.begin() as db:
        for table in ("organization", "users", "plant"):
            db.execute(
                text("CREATE TABLE IF NOT EXISTS " + table + "(id uuid PRIMARY KEY,label text)")
            )
    wrapper = tmp_path / "fixture-pg-dump"
    wrapper.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import os,subprocess,sys
args=sys.argv[1:]
i=args.index('--file');destination=args[i+1];del args[i:i+2]
command=['docker','exec','-i','-e','PGPORT=5432','-e','PGHOST=127.0.0.1','-e','PGUSER','-e','PGPASSWORD','-e','PGDATABASE',CONTAINER,'pg_dump',*args]
with open(destination,'xb') as out:
    raise SystemExit(subprocess.run(command,stdout=out).returncode)
""".replace("CONTAINER", repr(source.engine._tiered_fixture_container))
    )
    wrapper.chmod(0o700)
    parts = dump(
        source.engine.url.render_as_string(hide_password=False),
        "gateway",
        tmp_path,
        checkpoint=lambda: None,
        executable=str(wrapper),
    )
    assert len(parts) == 2 and all(path.read_bytes().startswith(b"PGDMP") for path in parts)
    docker = shutil.which("docker")
    assert docker
    listing = subprocess.check_output(
        [docker, "exec", "-i", source.engine._tiered_fixture_container, "pg_restore", "--list"],
        input=parts[1].read_bytes(),
    )
    assert b"TABLE DATA" in listing and b"users" in listing and b"sensor" in listing


def test_archived_rows_restore_to_empty_postgres_with_exact_values(source, tmp_path):
    from app.tiered_archive.recovery import restore, restore_database

    candidate, ledger, _ = archive(source, tmp_path)
    try:
        box = Box(tmp_path / "unneeded-empty-box")
        box.root = tmp_path / "box"
        folder = tmp_path / "restored-database"
        report = restore(candidate.identity, folder, ledger, box, checkpoint=lambda: None)
        with source.engine.begin() as db:
            db.execute(text("DELETE FROM visual_reading"))
        result = restore_database(
            folder,
            source.engine.url.render_as_string(hide_password=False),
            report["manifest_sha256"],
            checkpoint=lambda: None,
        )
        assert result == {"restored_rows": 1, "typed_values_verified": True, "source_accesses": 0}
        with source.engine.connect() as db:
            row = db.execute(text("SELECT identities,precise FROM visual_reading")).one()
            assert bytes(row[0]) == b"\x00\xff" and row[1] == Decimal("123.000000000001")
    finally:
        ledger.close()


def test_export_byte_budget_preserves_cursor_without_losing_rows(source):
    with source.engine.begin() as db:
        db.execute(
            text("""INSERT INTO visual_reading SELECT sensor_id,'bulk-'||i,bucket,seconds,unit,n,total,total2,minimum,maximum,
            decode(repeat('ab',20000),'hex'),precise FROM visual_reading CROSS JOIN generate_series(1,499) i""")
        )
    first, _ = source.export("visual_reading", ORG, AT, AT + timedelta(days=1))
    assert source.page_full and 1 < len(first) < 500
    second, _ = source.export(
        "visual_reading", ORG, AT, AT + timedelta(days=1), after=source.next_cursor
    )
    assert len(first) + len(second) == 500
    assert not ({row["identity"] for row in first} & {row["identity"] for row in second})


def test_missing_scan_index_blocks_before_export(source):
    with source.engine.begin() as db:
        db.execute(text("DROP INDEX tiered_visual_reading_scan"))
    with pytest.raises(ArchiveBlocked, match="Missing valid bounded scan index"):
        source.export("visual_reading", ORG, AT, AT + timedelta(days=1))


def test_sql_retention_requires_its_own_acceptance(source, monkeypatch):
    monkeypatch.delenv("TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED")
    rows, flags = source.export("visual_reading", ORG, AT, AT + timedelta(days=1))
    assert len(rows) == 1 and not flags["dependencies_complete"]


def test_gateway_replay_after_raw_eviction_is_still_idempotent(source, tmp_path):
    with source.engine.begin() as db:
        db.execute(
            text("INSERT INTO sensor_reading VALUES(:at,:sensor,'temperature',2,'C')"),
            dict(at=AT, sensor=SENSOR),
        )
        db.execute(
            text("INSERT INTO visual_chunk VALUES(:start,:end,'verified',1,1,'{}')"),
            dict(start=AT, end=AT + timedelta(days=1)),
        )
    candidate, ledger, kwargs = archive(source, tmp_path, "sensor_reading")
    try:
        evict(candidate, source, ledger, **kwargs)
        with source.engine.begin() as db:
            replay = db.execute(
                text(
                    "INSERT INTO sensor_reading VALUES(:at,:sensor,'temperature',2,'C') ON CONFLICT DO NOTHING"
                ),
                dict(at=AT, sensor=SENSOR),
            )
            new = db.execute(
                text(
                    "INSERT INTO sensor_reading VALUES(:at,:sensor,'temperature',4,'C') ON CONFLICT DO NOTHING"
                ),
                dict(at=AT + timedelta(seconds=1), sensor=SENSOR),
            )
            assert replay.rowcount == 0 and new.rowcount == 1
        with source.engine.connect() as db:
            assert db.execute(text("SELECT count(*) FROM sensor_reading")).scalar_one() == 1
    finally:
        ledger.close()


def test_replay_waiting_behind_delete_sees_committed_identity(source):
    import threading

    from app.tiered_archive import codec
    from app.tiered_archive.gateway_references import fence, preserve

    row = dict(timestamp=AT, sensor_id=uuid.UUID(SENSOR), kind="temperature", value=2.0, unit="C")
    results = []
    entered = threading.Event()

    def replay():
        with source.engine.begin() as db:
            entered.set()
            results.append(
                db.execute(
                    text(
                        "INSERT INTO sensor_reading VALUES(:timestamp,:sensor_id,:kind,:value,:unit) ON CONFLICT DO NOTHING"
                    ),
                    row,
                ).rowcount
            )

    with source.engine.begin() as db:
        db.execute(
            text("INSERT INTO sensor_reading VALUES(:timestamp,:sensor_id,:kind,:value,:unit)"), row
        )
    with source.engine.begin() as db:
        fence(db, [row])
        preserve(db, "sensor_reading", [row], "a" * 64)
        db.execute(text("DELETE FROM sensor_reading"))
        thread = threading.Thread(target=replay)
        thread.start()
        assert entered.wait(2)
        time.sleep(0.1)
        assert not results
    thread.join(5)
    assert results == [0]
    with source.engine.connect() as db:
        assert db.execute(text("SELECT identities FROM tiered_reading_identity")).scalar_one() == [
            codec.epoch_us(AT)
        ]


def test_late_aggregate_reuses_archived_identity_and_returns_only_delta(
    source, tmp_path, monkeypatch
):
    from app.tiered_archive.codec import epoch_us
    from app.tiered_archive.gateway_references import previous, relation
    from app.visualization.core import merge_readings, pack_ids
    from app.visualization.worker import save_reading

    monkeypatch.setenv("TIERED_ARCHIVE_READS_ENABLED", "true")
    with source.engine.begin() as db:
        db.execute(
            text("UPDATE visual_reading SET identities=:ids"),
            dict(ids=pack_ids([epoch_us(AT), epoch_us(AT + timedelta(seconds=1))])),
        )
    candidate, ledger, kwargs = archive(source, tmp_path)
    try:
        evict(candidate, source, ledger, **kwargs)
        with source.engine.begin() as db:
            reference = previous(db, SENSOR, "temperature", AT, 600)
            state = merge_readings(
                reference,
                [
                    dict(timestamp=AT, value=2.0),
                    dict(timestamp=AT + timedelta(seconds=2), value=10.0),
                ],
            )
            save_reading(
                db,
                dict(state, sensor_id=SENSOR, kind="temperature", bucket=AT, seconds=600, unit="C"),
            )
            delta = db.execute(
                text("SELECT n,total,total2 FROM " + relation() + " AS readings")
            ).one()
            assert tuple(delta) == (1, 10.0, 100.0)
            assert db.execute(text("SELECT n FROM visual_reading")).scalar_one() == 3
            assert db.execute(text("SELECT n FROM tiered_visual_reference")).scalar_one() == 2
    finally:
        ledger.close()
