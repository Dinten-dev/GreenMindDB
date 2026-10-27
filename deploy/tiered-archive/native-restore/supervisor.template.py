import base64
import datetime
import hashlib
import json
import os
import pathlib
import pwd
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.parse
import uuid

PAYLOAD = None
SOURCE = None
SOURCE_DATABASE = {"gateway": "plantdb_staging", "direct": "greenmind_direct_staging"}[
    SOURCE
]
S = pathlib.Path("/var/lib/greenmind/tiered-archive/staging")
C = pathlib.Path("/etc/greenmind/tiered-archive")
container = "gm-staging-postgres-1"
units = [
    "greenmind-tiered-archive-staging.service",
    "greenmind-tiered-read-staging.service",
    "greenmind-tiered-archive-staging.timer",
]
root = None
before = None
unit = None
created = []
identities = {}
stopped = True
marker = "greenmind-native-restore:" + uuid.uuid4().hex
out = {
    "utc": datetime.datetime.now(datetime.UTC).isoformat(),
    "original_changes": 0,
    "worker_sha256": PAYLOAD["sha256"],
    "native_sha256": PAYLOAD["native_sha256"],
    "existing_staging_source_read_allowed": True,
    "box_accesses": 0,
    "archive_services_started": False,
    "source_database": SOURCE_DATABASE,
    "memory_max_bytes": 192 * 1024**2,
    "cpu_quota_percent": 25,
}


def run(args, body=None, timeout=12):
    p = subprocess.run(
        args, input=body, capture_output=True, text=True, timeout=timeout, check=False
    )
    if p.returncode:
        raise RuntimeError("bounded_command_failed")
    return p.stdout


def sql(body, database="postgres"):
    return run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            database,
        ],
        body,
    ).strip()


def stopped_units():
    result = {
        u: run(["systemctl", "show", u, "--property=ActiveState,UnitFileState"]).strip()
        for u in units
    }
    assert all(
        "ActiveState=inactive" in s and "UnitFileState=disabled" in s
        for s in result.values()
    )
    return result


def retain_encrypted():
    """Preserve only the four hash-pinned archive parts after a completed restore."""
    test = out["test"]
    assert test["passed"] and test["backup_format"] == "postgres-full-v1"
    assert out["returncode"] == 0 and stopped
    assert test["box_accesses"] == 0 and test["source_writes"] == 0
    folder = root / "state/recovered"
    manifest_path = folder / "manifest.json"
    assert not manifest_path.is_symlink() and manifest_path.stat().st_size < 65536
    assert (
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        == test["manifest_sha256"]
    )
    manifest = json.loads(manifest_path.read_text())
    identity = manifest["identity"]
    assert (
        re.fullmatch(r"[a-f0-9]{64}", identity) and identity == test["archive_identity"]
    )
    candidate = manifest["candidate"]
    assert candidate["source"] == SOURCE and candidate["environment"] == "staging"
    assert candidate["dataset"] == "database_backup"
    assert candidate["organization"] == "00000000-0000-0000-0000-000000000000"
    parts = manifest["files"]
    assert [p["format"] for p in parts] == ["parquet", "bin", "bin"]
    assert parts[0]["backup_format"] == "postgres-full-v1"
    assert 0 < test["archive_bytes"] <= 5 * 1024**2
    refs = [{"name": f"{i:04d}.{p['format']}", **p} for i, p in enumerate(parts)]
    manifest_ref = {
        "name": "manifest.json",
        "sha256": test["manifest_sha256"],
        "bytes": manifest_path.stat().st_size,
        "key": f"staging/tiered/{candidate['organization']}/database_backup/{candidate['start'][:10]}/{test['manifest_sha256']}.json",
    }
    refs.append(manifest_ref)
    assert sum(p["bytes"] for p in refs) == test["archive_bytes"]
    for part in refs:
        path = folder / part["name"]
        st = path.lstat()
        assert stat.S_ISREG(st.st_mode) and st.st_size == part["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == part["sha256"]
    target = S / ("prepared-native-" + SOURCE + "-" + identity[:16])
    target.mkdir(mode=0o700)  # Never overwrite any previous proposal.
    out["preparation_directory"] = str(target)
    for part in refs:
        source = folder / part["name"]
        dest = target / part["name"]
        with source.open("rb") as src, dest.open("xb") as dst:
            shutil.copyfileobj(src, dst, 65536)
            dst.flush()
            os.fsync(dst.fileno())
        assert hashlib.sha256(dest.read_bytes()).hexdigest() == part["sha256"]
        dest.chmod(0o400)
    approval = {
        "version": 1,
        "source": SOURCE,
        "identity": identity,
        "manifest_sha256": test["manifest_sha256"],
        "bytes": test["archive_bytes"],
        "files": refs,
        "root": str(target),
        "uploaded": False,
        "native_sha256": PAYLOAD["native_sha256"],
        "worker_sha256": PAYLOAD["sha256"],
    }
    path = target / "approval-scope.json"
    path.write_text(json.dumps(approval, sort_keys=True, indent=2))
    path.chmod(0o400)
    with path.open("rb") as f:
        os.fsync(f.fileno())
    target.chmod(0o500)
    out["prepared_archive"] = dict(
        approval, approval_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )


try:
    out["units_before"] = stopped_units()
    assert (S / "control/PAUSE").is_file() and not (C / "INSTALLATION_READY").exists()
    original_hashes = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            C / "staging.env",
            C / "staging-read.env",
            C / "staging-schedule.json",
            S / "control/PAUSE",
            S / "tiered.sqlite3",
        ]
    }
    available = next(
        int(l.split()[1])
        for l in pathlib.Path("/proc/meminfo").read_text().splitlines()
        if l.startswith("MemAvailable:")
    )
    out["headroom"] = {
        "available_memory_kib": available,
        "free_disk_bytes": shutil.disk_usage(S).free,
        "load": os.getloadavg()[0],
        "load_limit": min(2, 0.5 * (os.cpu_count() or 1)),
    }
    if out["headroom"]["load"] > out["headroom"]["load_limit"]:
        out["pause_reason"] = "host_load"
    assert (
        available > 1024**2
        and out["headroom"]["free_disk_bytes"] > 2 * 1024**3
        and out["headroom"]["load"] <= out["headroom"]["load_limit"]
    )
    before = json.loads(run(["docker", "inspect", container]))[0]
    assert (
        before["Id"]
        == "14c10dd6db6b978676b11ef50fb5a2e17d649b6eff5d67d95808131896415a2c"
        and before["State"]["Running"]
        and before["HostConfig"]["Memory"] == 335544320
    )
    user = dict(x.split("=", 1) for x in before["Config"]["Env"] if "=" in x).get(
        "POSTGRES_USER", "postgres"
    )
    ro = dict(
        l.split("=", 1)
        for l in (C / "staging.env").read_text().splitlines()
        if l and not l.startswith("#")
    )
    selected = urllib.parse.urlsplit(
        ro["RAW_ARCHIVE_" + SOURCE.upper() + "_DATABASE_URL"]
    )
    assert (
        selected.username == "greenmind_archive_staging_ro"
        and selected.hostname == "127.0.0.1"
        and selected.port == 5433
    )
    root = pathlib.Path(tempfile.mkdtemp(prefix="timescale-full-", dir=S))
    root.chmod(0o755)
    unit = "gm-timescale-full-" + root.name.removeprefix("timescale-full-")
    account = pwd.getpwnam("greenmind-archive")
    state = root / "state"
    state.mkdir(mode=0o700)
    os.chown(state, account.pw_uid, account.pw_gid)
    (state / "control").mkdir(mode=0o700)
    os.chown(state / "control", account.pw_uid, account.pw_gid)
    body = base64.b64decode(PAYLOAD["data"])
    assert hashlib.sha256(body).hexdigest() == PAYLOAD["sha256"]
    (root / "worker.py").write_bytes(body)
    (root / "worker.py").chmod(0o444)
    module = base64.b64decode(PAYLOAD["native_data"])
    assert hashlib.sha256(module).hexdigest() == PAYLOAD["native_sha256"]
    (root / "native_backup.py").write_bytes(module)
    (root / "native_backup.py").chmod(0o444)
    profile = {
        "marker": marker,
        "source_database": SOURCE_DATABASE,
        "source": {"url": ro["RAW_ARCHIVE_" + SOURCE.upper() + "_DATABASE_URL"]},
        "key_sha256": ro["TIERED_ARCHIVE_BACKUP_KEY_SHA256"],
    }
    assert selected.path == "/" + SOURCE_DATABASE
    for kind in ("target",):
        name = "gm_restore_probe_" + uuid.uuid4().hex[:16]
        assert (
            sql("SELECT count(*) FROM pg_database WHERE datname='" + name + "';") == "0"
        )
        journal = root / "intent.json"
        journal.write_text(
            json.dumps({"marker": marker, "databases": identities, "pending": name})
        )
        journal.chmod(0o600)
        with journal.open("rb") as f:
            os.fsync(f.fileno())
        sql(
            'CREATE DATABASE "'
            + name
            + '" OWNER greenmind_archive_staging_ro TEMPLATE template0 CONNECTION LIMIT 3;'
        )
        created.append(name)
        sql('COMMENT ON DATABASE "' + name + "\" IS '" + marker + "';")
        oid = sql("SELECT oid::text FROM pg_database WHERE datname='" + name + "';")
        identities[name] = oid
        # Extension setup and extra grants are restricted to these newly created,
        # UUID-marked synthetic databases. No global privileges or source grants.
        sql(
            "SET ROLE greenmind_archive_staging_ro; SET default_transaction_read_only=off; CREATE EXTENSION timescaledb VERSION '2.17.2';",
            name,
        )
        if kind == "target":
            schemas = json.loads(
                sql(
                    "SELECT coalesce(json_agg(nspname ORDER BY nspname),'[]'::json) FROM pg_namespace WHERE nspname LIKE '_timescaledb_%';",
                    name,
                )
            )
            assert set(schemas) <= {
                "_timescaledb_catalog",
                "_timescaledb_config",
                "_timescaledb_internal",
                "_timescaledb_functions",
                "_timescaledb_cache",
                "_timescaledb_debug",
            }
            for schema in schemas:
                sql(
                    'GRANT USAGE,CREATE ON SCHEMA "'
                    + schema
                    + '" TO greenmind_archive_staging_ro; GRANT ALL ON ALL TABLES IN SCHEMA "'
                    + schema
                    + '" TO greenmind_archive_staging_ro; GRANT ALL ON ALL SEQUENCES IN SCHEMA "'
                    + schema
                    + '" TO greenmind_archive_staging_ro;',
                    name,
                )
        if kind == "target":
            sql("SELECT timescaledb_pre_restore();", name)
        profile[kind] = {
            "name": name,
            "oid": oid,
            "url": urllib.parse.urlunsplit(selected._replace(path="/" + name)),
        }
    journal.write_text(json.dumps({"marker": marker, "databases": identities}))
    journal.chmod(0o600)
    credential = root / "database.json"
    fd = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(profile, f)
    out["test_databases"] = identities
    out["unit"] = unit
    args = [
        "systemd-run",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--unit=" + unit,
        "-p",
        "User=greenmind-archive",
        "-p",
        "MemoryMax=192M",
        "-p",
        "MemoryHigh=160M",
        "-p",
        "MemorySwapMax=0",
        "-p",
        "CPUQuota=25%",
        "-p",
        "RuntimeMaxSec=150",
        "-p",
        "TimeoutStopSec=5",
        "-p",
        "NoNewPrivileges=yes",
        "-p",
        "ProtectSystem=strict",
        "-p",
        "ProtectHome=yes",
        "-p",
        "PrivateTmp=yes",
        "-p",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "-p",
        "IPAddressDeny=any",
        "-p",
        "IPAddressAllow=localhost",
        "-p",
        "ReadWritePaths=" + str(state),
        "-p",
        "InaccessiblePaths=/etc/greenmind",
        "-p",
        "LoadCredential=database:" + str(credential),
        "-p",
        "LoadCredential=backup-key:" + str(C / "staging-backup.key"),
        "-p",
        "LoadCredential=baseline:" + str(C / "staging-latency-baseline.json"),
        "-p",
        "Environment=TIERED_ARCHIVE_PG_CLIENT_DIR=/opt/greenmind/tiered-archive/clients/postgresql-15.10-55bb10b9c0ca",
        "-p",
        "Environment=TIERED_ARCHIVE_PG_CLIENT_MANIFEST_SHA256=55bb10b9c0cac4be01ef2d14ca03e51e3ccd837fb7ec83c2a46d4359d4b0e6f0",
        "-p",
        "Environment=PYTHONDONTWRITEBYTECODE=1",
        "-p",
        "Environment=OPENBLAS_NUM_THREADS=1",
        "-p",
        "Environment=OMP_NUM_THREADS=1",
        "/opt/greenmind/tiered-archive/releases/5be542bdd4b44106b232677c53f507ca0e62e88a/python/bin/python",
        str(root / "worker.py"),
        str(root),
    ]
    stopped = False
    with subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as p:
        deadline = time.monotonic() + 170
        post_done = False
        try:
            while p.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError("test_deadline")
                request = state / "post-restore-request"
                if request.exists() and not post_done:
                    assert (
                        not request.is_symlink()
                        and request.stat().st_size < 100
                        and request.read_text() == marker
                    )
                    target = profile["target"]
                    name = target["name"]
                    identity = json.loads(
                        sql(
                            "SELECT json_build_object('oid',oid::text,'marker',shobj_description(oid,'pg_database'),'owner',pg_get_userbyid(datdba)) FROM pg_database WHERE datname='"
                            + name
                            + "';"
                        )
                    )
                    assert identity == {
                        "oid": target["oid"],
                        "marker": marker,
                        "owner": "greenmind_archive_staging_ro",
                    }
                    sql(
                        "UPDATE _timescaledb_config.bgw_job SET scheduled=false WHERE scheduled;",
                        name,
                    )
                    assert (
                        sql(
                            "SELECT count(*) FROM _timescaledb_config.bgw_job WHERE scheduled;",
                            name,
                        )
                        == "0"
                    )
                    out["restored_background_jobs_disabled"] = True
                    sql("SELECT timescaledb_post_restore();", name)
                    assert sql("SHOW timescaledb.restoring;", name) == "off"
                    done = state / "post-restore-done"
                    fd = os.open(
                        done,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o444,
                    )
                    with os.fdopen(fd, "w") as f:
                        f.write(marker)
                        f.flush()
                        os.fsync(f.fileno())
                    post_done = True
                    out["post_restore_verified"] = True
                time.sleep(0.1)
            stdout, stderr = p.communicate(timeout=5)
            out["returncode"] = p.returncode
            out["stderr_present"] = bool(stderr)
            try:
                out["test"] = json.loads(stdout)
            except ValueError:
                out["test"] = {"passed": False, "error": "no_structured_result"}
        except BaseException:
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=5)
            raise
except Exception as e:  # noqa: BLE001 -- sanitize diagnostics; retain failed tests
    out["error_type"] = type(e).__name__
finally:
    if unit:
        stopped = run(
            ["systemctl", "show", unit, "--property=ActiveState", "--value"]
        ).strip() in ("", "inactive", "failed")
        if not stopped:
            run(["systemctl", "stop", unit])
            stopped = run(
                ["systemctl", "show", unit, "--property=ActiveState", "--value"]
            ).strip() in ("", "inactive", "failed")
    out["test_unit_stopped"] = stopped
    if (
        PAYLOAD.get("retain_encrypted")
        and stopped
        and out.get("test", {}).get("passed")
    ):
        try:
            retain_encrypted()
        except Exception as e:  # noqa: BLE001 -- keep sanitized failure and finish own cleanup
            out["preparation_error_type"] = type(e).__name__
    out["cleanup"] = {}
    for name in created:
        try:
            assert stopped
            r = json.loads(
                sql(
                    "SELECT json_build_object('oid',oid::text,'marker',shobj_description(oid,'pg_database'),'owner',pg_get_userbyid(datdba),'sessions',(SELECT coalesce(json_agg(json_build_object('type',backend_type,'application',application_name)),'[]'::json) FROM pg_stat_activity WHERE datname='"
                    + name
                    + "')) FROM pg_database WHERE datname='"
                    + name
                    + "';"
                )
            )
            assert (
                r["oid"] == identities[name]
                and r["marker"] == marker
                and r["owner"] == "greenmind_archive_staging_ro"
            )
            if r["sessions"] and any(
                s["type"] != "TimescaleDB Background Worker Scheduler"
                for s in r["sessions"]
            ):
                for attempt in range(10):
                    time.sleep(0.3)
                    r["sessions"] = json.loads(
                        sql(
                            "SELECT coalesce(json_agg(json_build_object('type',backend_type,'application',application_name)),'[]'::json) FROM pg_stat_activity WHERE datid="
                            + r["oid"]
                            + ";"
                        )
                    )
                    if not r["sessions"] or all(
                        s["type"] == "TimescaleDB Background Worker Scheduler"
                        for s in r["sessions"]
                    ):
                        break
            if r["sessions"]:
                assert all(
                    s["type"] == "TimescaleDB Background Worker Scheduler"
                    and s["application"] == "TimescaleDB Background Worker Scheduler"
                    for s in r["sessions"]
                )
                sql('ALTER DATABASE "' + name + '" ALLOW_CONNECTIONS false;')
                sql(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datid="
                    + r["oid"]
                    + " AND backend_type='TimescaleDB Background Worker Scheduler' AND application_name='TimescaleDB Background Worker Scheduler';"
                )
                for attempt in range(10):
                    if (
                        sql(
                            "SELECT count(*) FROM pg_stat_activity WHERE datid="
                            + r["oid"]
                            + ";"
                        )
                        == "0"
                    ):
                        break
                    time.sleep(0.5)
            assert (
                sql(
                    "SELECT count(*) FROM pg_stat_activity WHERE datid="
                    + r["oid"]
                    + ";"
                )
                == "0"
            )
            sql('DROP DATABASE "' + name + '";')
            out["cleanup"][name] = (
                sql("SELECT count(*) FROM pg_database WHERE datname='" + name + "';")
                == "0"
            )
        except Exception as e:  # noqa: BLE001 -- sanitize diagnostics; retain failed tests
            out["cleanup"][name] = {"error_type": type(e).__name__}
    if root and stopped and all(out["cleanup"].get(n) is True for n in created):
        shutil.rmtree(root)
        out["own_test_files_removed"] = not root.exists()
    elif root:
        out["preserved_test_directory"] = str(root)
    try:
        after = json.loads(run(["docker", "inspect", container]))[0]
        out["existing_container_unchanged"] = before is None or (
            before["Id"] == after["Id"]
            and before["State"]["StartedAt"] == after["State"]["StartedAt"]
        )
        out["units_after"] = stopped_units()
        out["profiles_ledger_pause_unchanged"] = all(
            hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() == h
            for p, h in original_hashes.items()
        )
    except Exception as e:  # noqa: BLE001 -- sanitize diagnostics; retain failed tests
        out["final_check_error_type"] = type(e).__name__
    print(json.dumps(out))
