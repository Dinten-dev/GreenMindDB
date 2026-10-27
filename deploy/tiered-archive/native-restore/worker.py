"""Read an existing Staging copy; encrypted local full restore into own marked DB."""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
from contextlib import ExitStack

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

root = pathlib.Path(sys.argv[1])
state = root / "state"
sys.path.insert(
    0, "/opt/greenmind/tiered-archive/overlays/staging-native-76c7fa172272/backend"
)
import app.tiered_archive
from app.raw_archive.health import HealthProbe
from app.raw_archive.policy import ArchiveBlocked, checksum

# Only the hash-pinned module shipped beside this temporary worker is overlaid.
app.tiered_archive.__path__.insert(0, str(root))
from app.tiered_archive import backup
from app.tiered_archive import native_backup as native
from app.tiered_archive.latency import LatencyGuard
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.pipeline import publish, verify_file
from app.tiered_archive.resources import read_cgroup
from app.tiered_archive.storage import validate_key

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


class LocalStore:
    """Exercise the real publication format without any Storage Box connection."""

    identity = "isolated-native-format-probe"

    def __init__(self, directory):
        self.root = directory
        directory.mkdir(mode=0o700)

    def publish(self, key, source):
        validate_key(key)
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with source.open("rb") as src, target.open("xb") as dst:
            while block := src.read(65536):
                check()
                dst.write(block)

    def readback(self, reference, target):
        validate_key(reference["key"])
        with (self.root / reference["key"]).open("rb") as src, target.open("xb") as dst:
            while block := src.read(65536):
                check()
                dst.write(block)
        verify_file(target, reference, check)


def sequence_proof(db):
    return native.sequence_proof(db, checkpoint=check)


def content_proof(db):
    return native.content_proof(db, checkpoint=check)


def jobs_proof(db):
    return native.jobs_proof(db)


contexts = ExitStack()

stage = "identity"
try:
    check()
    source, surl = engine("source")
    target, turl = engine("target")
    key_path = credentials / "backup-key"
    assert checksum(key_path) == profile["key_sha256"]
    key = backup.key_from_file(key_path)
    source.dispose()
    capture_folder = state / "capture"
    capture_folder.mkdir(mode=0o700)
    stage = "native_archive_capture"
    candidate, files = native.capture(
        profile["source"]["url"],
        "gateway" if source_database == "plantdb_staging" else "direct",
        capture_folder,
        key,
        environment="staging",
        checkpoint=check,
    )
    assert not (capture_folder / "native-full.dump").exists()
    assert not (capture_folder / "native-metadata.json").exists()
    stage = "local_archive_publication_and_readback"
    store = LocalStore(state / "local-store")
    ledger = Ledger(state / "local-ledger")
    try:
        receipt = publish(candidate, files, store, ledger, checkpoint=check)
        assert receipt["state"] == "published"  # No invented snapshot proof.
        report["archive_identity"] = candidate.identity
        report["manifest_sha256"] = receipt["manifest"]["sha256"]
        report["archive_bytes"] = (
            sum(p["bytes"] for p in receipt["files"]) + receipt["manifest"]["bytes"]
        )
        recovered_folder = state / "recovered"
        recovered_folder.mkdir(mode=0o700)
        store.readback(receipt["manifest"], recovered_folder / "manifest.json")
        for number, part in enumerate(receipt["files"]):
            store.readback(part, recovered_folder / f"{number:04d}.{part['format']}")
    finally:
        ledger.close()
    # Drop all capture inputs before database replay. Only readback artifacts remain.
    for path, _ in files:
        path.unlink()
    capture_folder.rmdir()
    stage = "authenticated_native_archive"
    dump, recovered = contexts.enter_context(
        native.authenticated_bundle(
            recovered_folder,
            receipt["manifest"]["sha256"],
            key,
            checkpoint=check,
        )
    )
    expected, expected_jobs = recovered["proof"], recovered["jobs_proof"]
    report["backup_format"] = native.FORMAT
    report["dump_bytes"] = dump.stat().st_size
    report["encrypted_parts"] = [
        {k: part[k] for k in ("bytes", "sha256", "plaintext_sha256")}
        for part in receipt["files"][1:]
    ]
    report["encrypted_bytes"] = sum(p["bytes"] for p in receipt["files"][1:])
    report["cases"].extend(
        [
            "consistent_native_archive_capture",
            "real_pipeline_local_publication_and_readback",
            "original_capture_artifacts_removed_before_replay",
            "all_encrypted_parts_authenticated_before_replay",
        ]
    )
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
    contexts.close()
    for e in engines:
        e.dispose()
    report.update(seconds=round(time.monotonic() - started, 3), resources=read_cgroup())
    print(json.dumps(report))
