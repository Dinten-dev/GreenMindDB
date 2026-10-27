"""Read an existing Staging copy; encrypted local full restore into own marked DB."""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

root = pathlib.Path(sys.argv[1])
state = root / "state"
sys.path.insert(
    0, "/opt/greenmind/tiered-archive/overlays/staging-credentials-83fd3279eea3/backend"
)
from app.raw_archive.health import HealthProbe
from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import backup
from app.tiered_archive.latency import LatencyGuard
from app.tiered_archive.resources import read_cgroup

credentials = pathlib.Path(os.environ["CREDENTIALS_DIRECTORY"])
profile = json.loads((credentials / "database").read_text())
source_database = profile["source_database"]
assert source_database in {"plantdb_staging", "greenmind_direct_staging"}
baseline = credentials / "baseline"
urls = [x["url"] for x in json.loads(baseline.read_text())["endpoints"]]
guard = LatencyGuard(
    baseline, checksum(baseline), urls, state, state / "control", "copy"
)
health = HealthProbe(
    urls, [state], 1024, min(2, 0.5 * (os.cpu_count() or 1)), on_sample=guard.observe
)
started = time.monotonic()
report = {"passed": False, "source_writes": 0, "box_accesses": 0, "cases": []}
engines = []
MAX = 64 * 1024**2
os.umask(0o077)


def check():
    r = read_cgroup()
    if (
        time.monotonic() - started > 120
        or not health()
        or not r["available"]
        or r["peak_bytes"] > 160 * 1024**2
        or any(r["events"][k] for k in ("high", "max", "oom", "oom_kill"))
    ):
        raise ArchiveBlocked("Native full restore resource guard")
    for p in state.glob("*.dump"):
        if p.stat().st_size > MAX:
            raise ArchiveBlocked("Native dump exceeds budget")


def engine(kind):
    from app.tiered_archive.recovery import restore_url

    u = (
        make_url(profile[kind]["url"])
        if kind == "source"
        else restore_url(profile[kind]["url"])
    )
    assert (
        u.username == "greenmind_archive_staging_ro"
        and u.host == "127.0.0.1"
        and u.port == 5433
        and not u.query
    )
    assert u.database == (
        source_database if kind == "source" else profile[kind]["name"]
    )
    options = (
        "-c default_transaction_read_only="
        + ("on" if kind == "source" else "off")
        + " -c statement_timeout=8000 -c lock_timeout=150"
    )
    e = create_engine(
        u,
        pool_size=1,
        max_overflow=0,
        connect_args={"connect_timeout": 3, "options": options},
    )
    engines.append(e)
    with e.connect() as db:
        identity = db.execute(
            text(
                "SELECT oid::text,shobj_description(oid,'pg_database'),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
            )
        ).one()
        if kind == "target":
            assert list(identity) == [
                profile[kind]["oid"],
                profile["marker"],
                "greenmind_archive_staging_ro",
            ]
        else:
            assert db.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
            assert (
                db.execute(
                    text("SELECT pg_database_size(current_database())")
                ).scalar_one()
                < MAX
            )
        assert not any(
            db.execute(
                text(
                    "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
            ).one()
        )
    return e, u


def client(kind, args):
    u = make_url(profile[kind]["url"])
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PGHOST": u.host,
        "PGPORT": str(u.port),
        "PGDATABASE": u.database,
        "PGUSER": u.username,
        "PGPASSWORD": u.password,
        "PGCONNECT_TIMEOUT": "3",
        "PGOPTIONS": "-c default_transaction_read_only="
        + ("on" if kind == "source" else "off")
        + " -c statement_timeout=8000 -c lock_timeout=150",
    }
    with subprocess.Popen(
        args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ) as p:
        try:
            while p.poll() is None:
                check()
                time.sleep(0.1)
            check()
            if p.returncode:
                raise ArchiveBlocked("Native full client failed")
        except BaseException:
            if p.poll() is None:
                p.kill()
            p.wait()
            raise


class Sink:
    def __init__(self, budget):
        self.h = hashlib.sha256()
        self.bytes = 0
        self.budget = budget
        self.checked_bytes = 0
        self.checked_at = time.monotonic()

    def write(self, b):
        self.bytes += len(b)
        self.budget[0] += len(b)
        if self.budget[0] > MAX:
            raise ArchiveBlocked("Full binary content proof exceeds budget")
        now = time.monotonic()
        if self.bytes - self.checked_bytes >= 65536 or now - self.checked_at >= 0.25:
            check()
            self.checked_bytes, self.checked_at = self.bytes, now
        self.h.update(b)
        return len(b)

    def digest(self):
        check()
        return self.h.hexdigest()


def sequence_proof(db):
    quote = db.dialect.identifier_preparer.quote
    rows = db.execute(
        text(
            "SELECT n.nspname,c.relname,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2"
        )
    ).all()
    values = {}
    for schema, name, *settings in rows:
        check()
        last, called = db.execute(
            text(
                "SELECT last_value,is_called FROM " + quote(schema) + "." + quote(name)
            )
        ).one()
        values[schema + "." + name] = {
            "settings": settings,
            "last_value": last,
            "is_called": called,
        }
    return values


def content_proof(db):
    db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    db.execute(text("SET LOCAL extra_float_digits=3"))
    names = (
        db.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )
        )
        .scalars()
        .all()
    )
    assert 1 <= len(names) <= 128
    quote = db.dialect.identifier_preparer.quote
    tables = {}
    budget = [0]
    for name in names:
        check()
        keys = (
            db.execute(
                text(
                    "SELECT a.attname FROM pg_index i CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum WHERE i.indrelid=to_regclass(:name) AND i.indisprimary ORDER BY k.ord"
                ),
                {"name": "public." + quote(name)},
            )
            .scalars()
            .all()
        )
        if not keys:
            raise ArchiveBlocked(
                "Full proof requires primary key for every public table"
            )
        count = db.execute(
            text("SELECT count(*) FROM public." + quote(name))
        ).scalar_one()
        sink = Sink(budget)
        with db.connection.driver_connection.cursor() as cursor:
            cursor.copy_expert(
                "COPY (SELECT * FROM public."
                + quote(name)
                + " ORDER BY "
                + ",".join(quote(k) for k in keys)
                + ") TO STDOUT WITH (FORMAT binary)",
                sink,
            )
        tables[name] = {
            "rows": count,
            "binary_sha256": sink.digest(),
            "bytes": sink.bytes,
        }

    def hash_query(query):
        sink = Sink(budget)
        with db.connection.driver_connection.cursor() as cursor:
            cursor.copy_expert(
                "COPY (" + query + ") TO STDOUT WITH (FORMAT binary)", sink
            )
        return sink.digest()

    definitions = {
        "columns": "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "constraints": "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY 1,2",
        "indexes": "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2",
        "views": "SELECT viewname,definition FROM pg_views WHERE schemaname='public' ORDER BY 1",
        "extensions": "SELECT e.extname,e.extversion,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY 1",
        "hypertables": "SELECT hypertable_schema,hypertable_name,num_dimensions,compression_enabled FROM timescaledb_information.hypertables ORDER BY 1,2",
        "chunks": "SELECT hypertable_schema,hypertable_name,chunk_schema,chunk_name,is_compressed,range_start,range_end FROM timescaledb_information.chunks ORDER BY 1,2,3,4",
        "dimensions": "SELECT hypertable_schema,hypertable_name,column_name,column_type::text,dimension_type,time_interval,integer_interval,num_partitions FROM timescaledb_information.dimensions ORDER BY hypertable_schema,hypertable_name,dimension_number",
        "compression_settings": "SELECT hypertable_schema,hypertable_name,attname,segmentby_column_index,orderby_column_index,orderby_asc,orderby_nullsfirst FROM timescaledb_information.compression_settings ORDER BY hypertable_schema,hypertable_name,attname",
    }
    shapes = {name: hash_query(query) for name, query in definitions.items()}
    return {
        "public_tables": tables,
        "structure": shapes,
        "proof_bytes": budget[0],
        "sequences": sequence_proof(db),
    }


def jobs_proof(db):
    rows = db.execute(
        text(
            "SELECT job_id,application_name,proc_schema,proc_name,schedule_interval::text,max_runtime::text,max_retries,retry_period::text,owner::text,scheduled,fixed_schedule,config::text FROM timescaledb_information.jobs WHERE job_id>=1000 ORDER BY job_id"
        )
    ).all()
    return hashlib.sha256(
        json.dumps([list(r) for r in rows], separators=(",", ":")).encode()
    ).hexdigest()


stage = "identity"
try:
    check()
    source, surl = engine("source")
    target, turl = engine("target")
    key_path = credentials / "backup-key"
    assert checksum(key_path) == profile["key_sha256"]
    key = backup.key_from_file(key_path)
    dump = state / "full.dump"
    stage = "consistent_full_capture"
    with (
        source.connect().execution_options(isolation_level="REPEATABLE READ") as db,
        db.begin(),
    ):
        db.execute(text("SET TRANSACTION READ ONLY"))
        snapshot = db.execute(text("SELECT pg_export_snapshot()")).scalar_one()
        expected = content_proof(db)
        expected_jobs = jobs_proof(db)
        client(
            "source",
            [
                *backup.client_command("pg_dump"),
                "--no-password",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--snapshot=" + snapshot,
                "--file=" + str(dump),
            ],
        )
        if sequence_proof(db) != expected["sequences"]:
            raise ArchiveBlocked("Source sequence changed during consistent capture")
    assert 0 < dump.stat().st_size <= MAX
    report["dump_bytes"] = dump.stat().st_size
    encrypted = state / "full.bin"
    reference = backup.encrypt(dump, encrypted, key, checkpoint=check)
    dump.unlink()
    source.dispose()
    metadata = state / "capture.json"
    metadata.write_text(
        json.dumps(
            {
                "version": 1,
                "source_database": source_database,
                "dump_plaintext_sha256": reference["plaintext_sha256"],
                "proof": expected,
                "jobs_proof": expected_jobs,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    sealed_metadata = state / "capture.bin"
    metadata_reference = backup.encrypt(
        metadata, sealed_metadata, key, checkpoint=check
    )
    metadata.unlink()
    stage = "authenticated_decryption"
    backup.decrypt(
        sealed_metadata,
        metadata,
        key,
        metadata_reference["plaintext_sha256"],
        checkpoint=check,
    )
    recovered = json.loads(metadata.read_text())
    metadata.unlink()
    assert recovered["version"] == 1 and recovered["source_database"] == source_database
    assert recovered["dump_plaintext_sha256"] == reference["plaintext_sha256"]
    expected, expected_jobs = recovered["proof"], recovered["jobs_proof"]
    report["encrypted_parts"] = [
        {k: part[k] for k in ("bytes", "sha256", "plaintext_sha256")}
        for part in (reference, metadata_reference)
    ]
    backup.decrypt(
        encrypted, dump, key, reference["plaintext_sha256"], checkpoint=check
    )
    report["encrypted_bytes"] = encrypted.stat().st_size
    report["cases"].append("consistent_full_dump_encrypted_authenticated_existing_key")
    with target.connect() as db:
        assert db.execute(text("SHOW timescaledb.restoring")).scalar_one() == "on"
    stage = "native_pg_restore"
    client(
        "target",
        [
            *backup.client_command("pg_restore"),
            "--no-password",
            "--no-owner",
            "--no-acl",
            "--exit-on-error",
            "--dbname=" + turl.database,
            str(dump),
        ],
    )
    dump.unlink()
    stage = "restore_authenticated_sequence_state"
    with target.begin() as db:
        existing = sequence_proof(db)
        captured = expected["sequences"]
        assert set(existing) == set(captured)
        for name, item in captured.items():
            assert existing[name]["settings"] == item["settings"]
            assert type(item["last_value"]) is int and type(item["is_called"]) is bool
            db.execute(
                text("SELECT setval(CAST(:name AS regclass), :value, :called)"),
                {
                    "name": name,
                    "value": item["last_value"],
                    "called": item["is_called"],
                },
            )
    report["cases"].append("all_sequence_states_restored_from_authenticated_metadata")
    with target.connect() as db:
        assert jobs_proof(db) == expected_jobs
    report["cases"].append("restored_compression_policy_matches_before_quarantine")
    stage = "supervisor_post_restore"
    request = state / "post-restore-request"
    request.write_text(profile["marker"])
    request.chmod(0o600)
    while not (state / "post-restore-done").is_file():
        check()
        time.sleep(0.1)
    assert (state / "post-restore-done").read_text() == profile["marker"]
    target.dispose()
    stage = "all_public_contents_and_timescale_structure"
    with (
        target.connect().execution_options(isolation_level="REPEATABLE READ") as db,
        db.begin(),
    ):
        db.execute(text("SET TRANSACTION READ ONLY"))
        assert db.execute(text("SHOW timescaledb.restoring")).scalar_one() == "off"
        assert (
            db.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.jobs WHERE scheduled"
                )
            ).scalar_one()
            == 0
        )
        actual = content_proof(db)
    report["table_differences"] = [
        name
        for name in sorted(
            set(actual["public_tables"]) | set(expected["public_tables"])
        )
        if actual["public_tables"].get(name) != expected["public_tables"].get(name)
    ]
    report["structure_differences"] = [
        name
        for name in expected["structure"]
        if actual["structure"][name] != expected["structure"][name]
    ]
    report["sequence_differences"] = [
        name
        for name in sorted(set(actual["sequences"]) | set(expected["sequences"]))
        if actual["sequences"].get(name) != expected["sequences"].get(name)
    ]
    assert actual == expected
    report["content_proof_sha256"] = hashlib.sha256(
        json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    report["cases"].append("all_public_binary_values_and_timescale_shapes_exact")
    report.update(
        passed=True,
        sequences_verified=len(actual["sequences"]),
        public_tables=len(actual["public_tables"]),
        public_rows=sum(t["rows"] for t in actual["public_tables"].values()),
        row_counts={n: t["rows"] for n, t in actual["public_tables"].items()},
        proof_bytes=actual["proof_bytes"],
        restored_jobs_disabled=True,
    )
except Exception as e:  # noqa: BLE001 -- sanitize diagnostics; retain failed tests
    report.update(stage=stage, error_type=type(e).__name__)
    if isinstance(e, ArchiveBlocked):
        report["blocked_reason"] = str(e)
        report["last_health"] = health.last
    orig = getattr(e, "orig", None)
    if orig:
        report["sqlstate"] = getattr(orig, "pgcode", None)
    # Never emit actual row values or driver exception strings.
finally:
    for e in engines:
        e.dispose()
    report.update(seconds=round(time.monotonic() - started, 3), resources=read_cgroup())
    print(json.dumps(report))
