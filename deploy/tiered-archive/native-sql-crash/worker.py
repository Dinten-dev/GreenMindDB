"""Real child-process crashes, real PostgreSQL and isolated artificial proof fixtures."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.raw_archive.health import HealthProbe
from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import codec, parquet, runtime
from app.tiered_archive.latency import LatencyGuard
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.pipeline import candidate_json, publish, verify_snapshot
from app.tiered_archive.policy import POLICY_SHA256
from app.tiered_archive.resources import read_cgroup, reasons
from app.tiered_archive.source import Source
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

AT = datetime(2026, 10, 1, tzinfo=UTC)
NOW = AT + timedelta(days=100)
ORG = "11111111-1111-4111-8111-111111111111"
SENSOR = "22222222-2222-4222-8222-222222222222"
SEGMENT = "33333333-3333-4333-8333-333333333333"
RUNTIME = "55c067b4c8e5d7aa5bf737a0d2e3299548ce620a841793b0ac8bab65044cf5f4"

SCHEMA = [
    "CREATE TABLE zone(id uuid PRIMARY KEY,organization_id uuid NOT NULL)",
    "CREATE TABLE gateway(id uuid PRIMARY KEY,zone_id uuid NOT NULL)",
    "CREATE TABLE sensor(id uuid PRIMARY KEY,gateway_id uuid NOT NULL)",
    """CREATE TABLE visual_reading(sensor_id uuid,kind text,bucket timestamptz,seconds int,unit text,
       n bigint,total float8,total2 float8,minimum float8,maximum float8,identities bytea,
       precise numeric(24,12),PRIMARY KEY(sensor_id,kind,bucket,seconds))""",
    "CREATE INDEX tiered_visual_reading_scan ON visual_reading(bucket,sensor_id,kind,seconds)",
    """CREATE TABLE sensor_reading(timestamp timestamptz,sensor_id uuid,kind text,value float8,
       unit text,PRIMARY KEY(timestamp,sensor_id,kind))""",
    """CREATE TABLE wav_file(id uuid PRIMARY KEY,sensor_id uuid,started_at timestamptz,ended_at timestamptz,
       feature_status text,feature_verified_at timestamptz,raw_deleted_at timestamptz)""",
    """CREATE TABLE visual_chunk(range_start timestamptz,range_end timestamptz,status text,
       change_version bigint,archived_version bigint,archive_manifest jsonb)""",
    "CREATE TABLE direct_device(id uuid PRIMARY KEY,organization_id uuid)",
    "CREATE TABLE direct_segment(id uuid PRIMARY KEY,sealed bool,revision int,published_revision int)",
    "CREATE TABLE direct_revision(segment_id uuid,revision int,manifest jsonb)",
    "CREATE TABLE direct_visual_segment(segment_id uuid,source_revision int,error text,source text)",
    """CREATE TABLE direct_visual_point(segment_id uuid,device_id uuid,channel int,bucket float8,
       seconds int,n bigint,total float8,total2 float8,minimum float8,maximum float8,
       PRIMARY KEY(segment_id,channel,bucket))""",
    "CREATE INDEX tiered_direct_visual_point_scan ON direct_visual_point(bucket,segment_id,channel)",
    "CREATE TABLE fixture_deletes(dataset text)",
    """CREATE FUNCTION fixture_audit_delete() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN INSERT INTO fixture_deletes VALUES(TG_TABLE_NAME);RETURN OLD;END $$""",
    "CREATE TRIGGER fixture_delete AFTER DELETE ON visual_reading FOR EACH ROW EXECUTE FUNCTION fixture_audit_delete()",
    "CREATE TRIGGER fixture_delete AFTER DELETE ON direct_visual_point FOR EACH ROW EXECUTE FUNCTION fixture_audit_delete()",
]


class FixtureBox:
    identity = "isolated-synthetic-storagebox-fixture"

    def __init__(self, root):
        self.root = root
        root.mkdir()

    def publish(self, key, path):
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)

    def download(self, key, path, size, *, snapshot=None):
        assert snapshot in ("fixture-snapshot", None)
        shutil.copyfile(self.root / key, path)
        assert path.stat().st_size == size


class FixtureProvider:
    def __init__(self, created):
        self.created = created

    def read(self, identity):
        return {
            "id": identity,
            "storage_box": 4,
            "name": "fixture-snapshot",
            "created": self.created.isoformat(),
        }


def write_private(path, raw):
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def fixture_permit(candidate):
    return {
        "test_fixture": True,
        "not_live_acceptance": True,
        "policy": POLICY_SHA256,
        "environment": "staging",
        "mode": "pilot",
        "kinds": [candidate.kind],
        "candidates": [candidate.identity],
        "authorized_at": (NOW - timedelta(hours=1)).isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "runtime_sha256": RUNTIME,
        "bindings_sha256": "b" * 64,
        "acceptance": {
            "policy": POLICY_SHA256,
            "environment": "staging",
            "runtime_sha256": RUNTIME,
            "bindings_sha256": "b" * 64,
            "archive_only_restore": True,
            "dashboard_parity": True,
            "tenant_isolation": True,
            "rollback_passed": True,
            "backlog_reconciled": True,
            "deployed_at": (NOW - timedelta(days=2)).isoformat(),
            "observation": {
                "test_fixture": True,
                "started_at": (NOW - timedelta(days=2)).isoformat(),
                "finished_at": (NOW - timedelta(days=1)).isoformat(),
                "samples": 288,
                "maximum_gap_seconds": 300,
                "errors": 0,
                "progress": {"gateway": True, "direct": True},
            },
        },
    }


def main():
    root = Path(sys.argv[1])
    state = root / "state"
    credentials = Path(os.environ["CREDENTIALS_DIRECTORY"])
    binding = json.loads((root / "test-binding.json").read_text())
    name, token = binding["database"], binding["token"]
    assert re.fullmatch(r"gm_tiered_accept_[a-f0-9]{24}", name)
    assert re.fullmatch(r"[a-f0-9]{32}", token)
    baseline = credentials / "baseline"
    urls = [row["url"] for row in json.loads(baseline.read_text())["endpoints"]]
    guard = LatencyGuard(
        baseline, checksum(baseline), urls, state, state / "control", "copy"
    )
    health = HealthProbe(
        urls,
        [state],
        1024,
        min(2, 0.5 * (os.cpu_count() or 1)),
        on_sample=guard.observe,
    )
    started = time.monotonic()
    stage = "initial"
    engine = maintenance = None
    created = False
    report = {
        "passed": False,
        "original_source_accesses": 0,
        "original_source_writes": 0,
        "box_accesses": 0,
        "operational_activation_files_created": 0,
        "operational_deletion_flags_changed": False,
        "artificial_proof_and_clock_fixtures": True,
        "fixture_sql_retention_acceptance_only_in_child": True,
        "real_24h_observation": False,
        "cases": [],
    }

    def checkpoint():
        if time.monotonic() - started > 150 or not health() or reasons(read_cgroup()):
            raise ArchiveBlocked("resource_or_health_pause")

    def sql(statement, parameters=None):
        checkpoint()
        with engine.begin() as db:
            assert db.execute(text("SELECT current_database()")).scalar_one() == name
            result = db.execute(text(statement), parameters or {})
            return result.fetchall() if result.returns_rows else result.rowcount

    def prepare_case(dataset="visual_reading", *, late=False):
        sql(
            "TRUNCATE fixture_deletes,visual_reading,tiered_visual_reference,direct_visual_point"
        )
        sql("UPDATE direct_segment SET revision=1")
        sql("UPDATE zone SET organization_id=:org", {"org": ORG})
        sql(
            "INSERT INTO visual_reading VALUES(:sensor,'temperature',:at,600,'C',2,6,20,2,4,:payload,:precise)",
            {
                "sensor": SENSOR,
                "at": AT,
                "payload": b"\x00\xff",
                "precise": Decimal("123.000000000001"),
            },
        )
        sql(
            "INSERT INTO direct_visual_point VALUES(:segment,:sensor,0,:at,600,2,6,20,2,4)",
            {"segment": SEGMENT, "sensor": SENSOR, "at": AT.timestamp()},
        )
        case = state / "cases" / uuid.uuid4().hex
        case.mkdir(mode=0o700, parents=True)
        adapter = Source(
            engine,
            "direct" if dataset.startswith("direct") else "gateway",
            "staging",
            checkpoint=checkpoint,
        )
        rows, flags = adapter.export(dataset, ORG, AT, AT + timedelta(days=1))
        assert len(rows) == 1 and not flags["dependencies_complete"]
        path = case / "rows.parquet"
        metadata = dict(
            parquet.write(path, rows, checkpoint=checkpoint), format="parquet"
        )
        candidate = adapter.candidate(
            dataset,
            ORG,
            AT,
            AT + timedelta(days=1),
            metadata,
            flags,
            settled_at=AT + timedelta(days=1),
        )
        candidate = replace(
            candidate,
            dependencies_complete=True,
            late_until=NOW + timedelta(hours=1) if late else candidate.late_until,
        )
        ledger = Ledger(case / "ledger")
        try:
            box = FixtureBox(case / "box")
            first = AT + timedelta(days=2)
            publish(
                candidate,
                [(path, metadata)],
                box,
                ledger,
                checkpoint=checkpoint,
                now=first,
            )
            verify_snapshot(
                candidate,
                box,
                ledger,
                FixtureProvider(first),
                3,
                checkpoint=checkpoint,
                now=first,
            )
            verify_snapshot(
                candidate,
                box,
                ledger,
                FixtureProvider(NOW),
                4,
                checkpoint=checkpoint,
                now=NOW,
            )
        finally:
            ledger.close()
        write_private(
            case / "fixture-permit.json", json.dumps(fixture_permit(candidate)).encode()
        )
        write_private(
            case / "context.json",
            codec.encode(
                {
                    "test_fixture": True,
                    "database": name,
                    "candidate": candidate_json(candidate),
                    "expected": list(parquet.read(path, metadata)),
                    "now": NOW,
                    "runtime_sha256": RUNTIME,
                    "bindings_sha256": "b" * 64,
                }
            ),
        )
        return case, candidate

    def child(case, mode):
        checkpoint()
        process = subprocess.run(
            [sys.executable, str(root / "child.py"), str(root), str(case), mode],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        deadline = time.monotonic() + 3
        while sql(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=:database AND application_name=:app",
            {"database": name, "app": "gm-crash-" + case.name},
        )[0][0]:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        return process

    def receipt(case, candidate):
        ledger = Ledger(case / "ledger")
        try:
            return ledger.get(candidate.identity)
        finally:
            ledger.close()

    def completed(label, **details):
        item = {"case": label, "passed": True, **details}
        report["cases"].append(item)
        with (state / "progress.jsonl").open("a") as journal:
            journal.write(json.dumps(item) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

    try:
        checkpoint()
        assert all(
            os.getenv(key, "false") == "false"
            for key in (
                "TIERED_ARCHIVE_DELETE_ENABLED",
                "RAW_ARCHIVE_DELETE_ENABLED",
                "RETENTION_ENABLED",
                "DIRECT_RETENTION_ENABLED",
                "TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED",
            )
        )
        report["runtime_sha256"] = runtime.runtime_sha256()
        assert report["runtime_sha256"] == RUNTIME
        admin = json.loads((credentials / "test-db-admin").read_text())
        url = URL.create(
            "postgresql+psycopg2",
            username=admin["user"],
            password=admin["password"],
            host="127.0.0.1",
            port=5433,
            database="postgres",
        )
        maintenance = create_engine(
            url,
            poolclass=NullPool,
            isolation_level="AUTOCOMMIT",
            connect_args={
                "options": "-c statement_timeout=8000 -c lock_timeout=150 -c work_mem=4096"
            },
        )
        with maintenance.connect() as db:
            assert (
                db.execute(
                    text("SELECT 1 FROM pg_database WHERE datname=:name"),
                    {"name": name},
                ).first()
                is None
            )
            db.execute(
                text(
                    "CREATE DATABASE " + name + " TEMPLATE template0 CONNECTION LIMIT 3"
                )
            )
            created = True
            db.execute(
                text(
                    "COMMENT ON DATABASE "
                    + name
                    + " IS 'greenmind-synthetic-test:"
                    + token
                    + "'"
                )
            )
        engine = create_engine(
            url.set(database=name),
            poolclass=NullPool,
            connect_args={
                "options": "-c statement_timeout=3000 -c lock_timeout=150 -c work_mem=4096"
            },
        )
        report["database"] = name
        stage = "schema"
        for statement in SCHEMA:
            sql(statement)
        sql((Path(runtime.__file__).parent / "gateway_references.sql").read_text())
        for table, values in (
            ("zone", (ORG, ORG)),
            ("gateway", (ORG, ORG)),
            ("sensor", (SENSOR, ORG)),
        ):
            sql(
                "INSERT INTO " + table + " VALUES(:a,:b)",
                {"a": values[0], "b": values[1]},
            )
        sql(
            "INSERT INTO direct_device VALUES(:sensor,:org)",
            {"sensor": SENSOR, "org": ORG},
        )
        sql(
            "INSERT INTO direct_segment VALUES(:segment,true,1,1)", {"segment": SEGMENT}
        )
        sql(
            "INSERT INTO direct_visual_segment VALUES(:segment,1,NULL,'wav')",
            {"segment": SEGMENT},
        )
        sql(
            'INSERT INTO direct_revision VALUES(:segment,1,\'{"status":"complete"}\')',
            {"segment": SEGMENT},
        )

        for mode, code, before_rows, before_audit, before_state in (
            ("before_source_delete", 71, 1, 0, "deleting"),
            ("before_sql_commit", 72, 1, 0, "deleting"),
            ("after_sql_commit", 73, 0, 1, "deleting"),
            ("after_local_completion", 74, 0, 1, "evicted"),
        ):
            stage = mode
            case, candidate = prepare_case()
            original_archive = {
                str(p.relative_to(case / "box")): checksum(p)
                for p in (case / "box").rglob("*")
                if p.is_file()
            }
            process = child(case, mode)
            assert process.returncode == code, "child_did_not_exit_at_expected_phase"
            marker = json.loads((case / "crash.json").read_text())
            assert marker["database"] == name and marker["test_fixture"] is True
            assert sql("SELECT count(*) FROM visual_reading")[0][0] == before_rows
            assert sql("SELECT count(*) FROM fixture_deletes")[0][0] == before_audit
            assert receipt(case, candidate)["state"] == before_state
            for _ in range(2):
                resumed = child(case, "resume")
                assert (
                    resumed.returncode == 0
                    and json.loads(resumed.stdout)["state"] == "evicted"
                )
            assert sql("SELECT count(*) FROM visual_reading")[0][0] == 0
            assert sql("SELECT count(*) FROM fixture_deletes")[0][0] == 1
            assert sql("SELECT count(*) FROM tiered_visual_reference")[0][0] == 1
            assert receipt(case, candidate)["state"] == "evicted"
            assert original_archive == {
                str(p.relative_to(case / "box")): checksum(p)
                for p in (case / "box").rglob("*")
                if p.is_file()
            }
            completed(
                mode,
                exit_code=code,
                crash_marker=marker["phase"],
                committed_deletes=1,
                successful_retries=2,
                archive_unchanged=True,
            )

        for label, dataset in (
            ("changed_source", "visual_reading"),
            ("changed_organization", "visual_reading"),
            ("stale_proof", "visual_reading"),
            ("late_direct_window", "direct_visual_point"),
            ("changed_direct_revision", "direct_visual_point"),
        ):
            stage = label
            case, candidate = prepare_case(dataset, late=label == "late_direct_window")
            if label == "changed_source":
                sql("UPDATE visual_reading SET total=9")
            elif label == "changed_organization":
                sql("UPDATE zone SET organization_id=:org", {"org": str(uuid.uuid4())})
            elif label == "changed_direct_revision":
                sql("UPDATE direct_segment SET revision=2")
            elif label == "stale_proof":
                ledger = Ledger(case / "ledger")
                try:
                    stale = ledger.get(candidate.identity)
                    stale["verified_at"] = (NOW - timedelta(hours=25)).isoformat()
                    stale["snapshot"]["created_at"] = stale["verified_at"]
                    ledger.save(candidate, "verified", stale, NOW)
                finally:
                    ledger.close()
            process = child(case, "reject")
            assert process.returncode == 0
            rejection = json.loads(process.stdout)
            assert (
                rejection.get("blocked")
                and receipt(case, candidate)["state"] == "verified"
            )
            assert sql("SELECT count(*) FROM " + dataset)[0][0] == 1
            assert sql("SELECT count(*) FROM fixture_deletes")[0][0] == 0
            completed(label, blocked=rejection["blocked"], committed_deletes=0)
        report["database_size_bytes"] = sql(
            "SELECT pg_database_size(current_database())"
        )[0][0]
        assert report["database_size_bytes"] < 32 * 1024**2
        checkpoint()
        report["passed"] = len(report["cases"]) == 9
    except Exception as error:  # noqa: BLE001 - Persist a sanitized result before mandatory cleanup.
        report["failure"] = {
            "stage": stage,
            "type": type(error).__name__,
            "frames": [
                {"file": Path(f.filename).name, "line": f.lineno}
                for f in traceback.extract_tb(error.__traceback__)
            ],
        }
        if isinstance(error, ArchiveBlocked):
            report["failure"]["reason"] = str(error)
        if (
            isinstance(error, AssertionError)
            and error.args
            and isinstance(error.args[0], str)
        ):
            report["failure"]["assertion"] = error.args[0][:120]
        if getattr(getattr(error, "orig", None), "pgcode", None):
            report["failure"]["sqlstate"] = error.orig.pgcode
    finally:
        if engine is not None:
            engine.dispose()
        if created and maintenance is not None:
            try:
                with maintenance.connect() as db:
                    assert (
                        db.execute(
                            text(
                                "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=:name"
                            ),
                            {"name": name},
                        ).scalar_one()
                        == "greenmind-synthetic-test:" + token
                    )
                    db.execute(text("DROP DATABASE " + name + " WITH (FORCE)"))
                    report["own_database_removed"] = (
                        db.execute(
                            text("SELECT 1 FROM pg_database WHERE datname=:name"),
                            {"name": name},
                        ).first()
                        is None
                    )
            except Exception as error:  # noqa: BLE001 - Preserve cleanup failures without exposing credentials.
                report["database_cleanup_failure"] = type(error).__name__
        if maintenance is not None:
            maintenance.dispose()
        health.close()
        report["seconds"] = round(time.monotonic() - started, 3)
        report["resources"] = read_cgroup()
        print(json.dumps(report))


if __name__ == "__main__":
    main()
