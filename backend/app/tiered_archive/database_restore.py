"""Restore authenticated schema/master dumps into an empty local test database."""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from sqlalchemy import create_engine

from app.raw_archive.policy import ArchiveBlocked, checksum

from . import parquet
from .backup import (
    MASTER_PROOF_VERSION,
    MAX_BYTES,
    client_command,
    current_v4_descriptor,
    decrypt,
    master_proof,
    v4_descriptor,
    validate_master_proof,
)
from .codec import decode, digest
from .pipeline import verify_file
from .recovery import restore_url
from .runtime import candidate_from_json


def run_client(command, env, *, checkpoint, output=None):
    """Children stay inside the caller's cgroup; diagnostics cannot leak credentials."""
    started = time.monotonic()
    with subprocess.Popen(  # noqa: S603 -- fixed PostgreSQL clients, no shell
        command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ) as process:
        try:
            while process.poll() is None:
                checkpoint()
                if time.monotonic() - started > 120 or (
                    output is not None and output.exists() and output.stat().st_size > MAX_BYTES
                ):
                    raise ArchiveBlocked("Database restore client exceeds resource budget")
                time.sleep(0.1)
            checkpoint()
            if process.returncode or (output is not None and output.stat().st_size > MAX_BYTES):
                raise ArchiveBlocked("Database restore client failed")
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise


def restore_backup(
    folder,
    database_url,
    manifest_sha256,
    key,
    *,
    checkpoint,
    provider_target=None,
    post_restore=None,
):
    """SQL errors roll back; a failed post-commit value proof never yields success."""
    url = restore_url(database_url)
    folder = Path(folder)
    if not folder.is_absolute() or folder.is_symlink():
        raise ArchiveBlocked("Absolute isolated backup restore directory required")
    manifest_path = folder / "manifest.json"
    if checksum(manifest_path) != manifest_sha256:
        raise ArchiveBlocked("Restore manifest changed")
    manifest = json.loads(manifest_path.read_text())
    candidate = candidate_from_json(manifest["candidate"])
    parts = manifest["files"]
    if (
        candidate.dataset != "database_backup"
        or candidate.organization != "00000000-0000-0000-0000-000000000000"
        or candidate.rows != 1
        or candidate.identity != manifest.get("identity")
        or len(parts) not in (3, 4, 5)
        or [part["format"] for part in parts] != ["parquet", *(["bin"] * (len(parts) - 1))]
        or parts[0]["rows"] != 1
    ):
        raise ArchiveBlocked("Complete schema/master backup bundle required")
    for index, part in enumerate(parts):
        if not 0 < part["bytes"] <= MAX_BYTES:
            raise ArchiveBlocked("Backup part exceeds restore budget")
        verify_file(folder / f"{index:04d}.{part['format']}", part, checkpoint)
    rows = list(parquet.read(folder / "0000.parquet", parts[0], checkpoint=checkpoint))
    if len(rows) != 1 or parts[0]["content_sha256"] != candidate.content_sha256:
        raise ArchiveBlocked("Backup index differs from source candidate")
    body = rows[0]["body"]
    if (
        body["source"] != candidate.source
        or body["at"] != candidate.end
        or rows[0]["identity"] != digest(body)
        or body["files"]
        != [{key: value for key, value in part.items() if key != "key"} for part in parts[1:]]
    ):
        raise ArchiveBlocked("Encrypted backup index binding mismatch")
    from . import schema_provider

    if body.get("backup_format") == schema_provider.FORMAT:
        return _restore_provider(
            folder,
            url,
            manifest_sha256,
            key,
            candidate,
            parts,
            body,
            provider_target=provider_target,
            post_restore=post_restore,
            checkpoint=checkpoint,
        )
    if body.get("backup_format") == "postgres-full-v1":
        raise ArchiveBlocked("Native backup requires the controlled Timescale restore coordinator")
    proof = body.get("master_proof")
    if (
        not isinstance(proof, dict)
        or type(proof.get("version")) is not int
        or proof.get("version") not in {2, 3, 4}
        or type(parts[0].get("master_proof_version")) is not int
        or parts[0]["master_proof_version"] != proof["version"]
    ):
        raise ArchiveBlocked("Backup lacks typed master-data proof; retain legacy dumps")
    proof_version = proof["version"]
    validate_master_proof(proof, candidate.source)
    if proof_version == 4:
        from .backup_scope import FORMAT

        if (
            body.get("backup_format") != FORMAT
            or not current_v4_descriptor(parts)
            or any(parts[0].get(k) != v for k, v in v4_descriptor(proof, body["files"]).items())
        ):
            raise ArchiveBlocked("V4 authenticated backup scope binding differs")
    elif len(parts) != 3 or body.get("backup_format") not in (None, "schema-master-v2"):
        raise ArchiveBlocked("Legacy master backup file version differs")
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PGHOST": url.host,
        "PGPORT": str(url.port or 5432),
        "PGDATABASE": url.database,
        "PGUSER": url.username,
        "PGPASSWORD": url.password or "",
        "PGCONNECT_TIMEOUT": "3",
        "PGOPTIONS": "-c statement_timeout=8000 -c lock_timeout=150 -c idle_in_transaction_session_timeout=30000 -c default_transaction_read_only=off",
    }
    checkpoint()
    with tempfile.TemporaryDirectory(prefix="restore-private-", dir=folder) as temporary:
        work = Path(temporary)
        if proof_version == 4:
            from .backup_scope import AUTH_FORMAT

            authenticated = work / "proof-auth.json"
            decrypt(
                folder / "0003.bin",
                authenticated,
                key,
                parts[3]["plaintext_sha256"],
                checkpoint=checkpoint,
            )
            expected = dict(
                schema=AUTH_FORMAT,
                version=4,
                source=candidate.source,
                captured_at=candidate.end,
                master_proof=proof,
                master_proof_sha256=digest(proof),
                dump_files=body["files"][:2],
            )
            if decode(authenticated.read_bytes()) != expected:
                raise ArchiveBlocked("Authenticated V4 source, capture or proof binding differs")
        # Authenticate BOTH dumps completely before opening a database session.
        for index, name in ((1, "schema"), (2, "masters")):
            decrypt(
                folder / f"{index:04d}.bin",
                work / f"{name}.dump",
                key,
                parts[index]["plaintext_sha256"],
                checkpoint=checkpoint,
            )
        _legacy_schema_without_data(work / "schema.dump", env, checkpoint=checkpoint)
        scripts = []
        for name, section, source in (
            ("pre", "pre-data", "schema"),
            ("masters", "data", "masters"),
            ("post", "post-data", "schema"),
        ):
            target = work / f"{name}.sql"
            target.touch(mode=0o600, exist_ok=False)
            run_client(
                [
                    *client_command("pg_restore"),
                    "--no-owner",
                    "--no-acl",
                    "--exit-on-error",
                    "--section=" + section,
                    "--file=" + str(target),
                    str(work / f"{source}.dump"),
                ],
                env,
                checkpoint=checkpoint,
                output=target,
            )
            scripts.append(target)
        guard = work / "guard.sql"
        guard.write_text(
            "SELECT pg_advisory_xact_lock(1735157614,hashtext(current_database()));\n"
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=current_user AND "
            "(rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) OR "
            "(SELECT datdba FROM pg_database WHERE datname=current_database()) <> "
            "(SELECT oid FROM pg_roles WHERE rolname=current_user) THEN "
            "RAISE EXCEPTION 'Restore requires an unprivileged target database owner'; END IF; "
            "IF current_database() <> '" + url.database + "' OR EXISTS ("
            "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' "
            "UNION ALL SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' "
            "UNION ALL SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
            "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' "
            "UNION ALL SELECT 1 FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%' "
            "AND n.nspname NOT IN ('public','information_schema')) "
            "THEN RAISE EXCEPTION 'Restore requires empty dedicated database'; END IF; END $$;\n"
        )
        guard.chmod(0o600)
        run_client(
            [
                *client_command("psql"),
                "-X",
                "--no-password",
                "--set=ON_ERROR_STOP=1",
                "--single-transaction",
                *[arg for path in [guard, *scripts] for arg in ("--file", str(path))],
            ],
            env,
            checkpoint=checkpoint,
        )
    # No success receipt until PostgreSQL's stored typed values and table shapes
    # match the proof made in the same snapshot as both original pg_dump files.
    engine = create_engine(url, pool_size=1, max_overflow=0, connect_args={"connect_timeout": 3})
    try:
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as db,
            db.begin(),
        ):
            from sqlalchemy import text

            db.execute(text("SET TRANSACTION READ ONLY"))
            db.execute(text("SET LOCAL statement_timeout='8000ms'"))
            actual = master_proof(
                db,
                candidate.source,
                checkpoint=checkpoint,
                proof_version=proof_version,
                **({"schema_scope": proof["schema_scope"]} if proof_version == 4 else {}),
            )
        if actual != proof:
            raise ArchiveBlocked("Restored master-data proof differs; isolated database retained")
        return {
            "schema_restored": True,
            "typed_values_verified": True,
            "master_proof_version": proof_version,
            "current_recovery_scope": proof_version
            in ({3, 4} if candidate.source == "gateway" else {MASTER_PROOF_VERSION}),
            "master_tables": len(actual["tables"]),
            "master_rows": sum(item["rows"] for item in actual["tables"].values()),
            "source_accesses": 0,
            "manifest_sha256": manifest_sha256,
        }
    finally:
        engine.dispose()


def _legacy_schema_without_data(path, env, *, checkpoint):
    """Legacy PlainPG still restores; unproved TS schema DATA never disappears."""
    target = path.with_suffix(".toc")
    target.touch(mode=0o600, exist_ok=False)
    run_client(
        [*client_command("pg_restore"), "--list", "--file=" + str(target), str(path)],
        env,
        checkpoint=checkpoint,
        output=target,
    )
    if target.stat().st_size > 512 * 1024:
        raise ArchiveBlocked("Legacy schema TOC exceeds bounded inspection")
    raw = target.read_bytes()
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeError:
        raise ArchiveBlocked("Legacy schema TOC is not supported text") from None
    entries = [line for line in lines if line and not line.startswith(";")]
    if not 1 <= len(entries) <= 4096 or any(
        len(line) > 8192 or not re.fullmatch(r"[1-9][0-9]*; [0-9]+ [0-9]+ .+", line)
        for line in entries
    ):
        raise ArchiveBlocked("Malformed bounded legacy schema TOC")
    if any(
        re.match(r"[1-9][0-9]*; [0-9]+ [0-9]+ EXTENSION - timescaledb(?:\s|$)", line)
        for line in entries
    ):
        raise ArchiveBlocked(
            "Legacy Timescale schema requires explicit provider state; retain original archive"
        )
    if any(
        re.match(
            r"[1-9][0-9]*; [0-9]+ [0-9]+ (?:TABLE DATA|SEQUENCE SET|MATERIALIZED VIEW DATA|BLOB)",
            line,
        )
        for line in entries
    ):
        raise ArchiveBlocked(
            "Legacy schema DATA requires explicit controlled provider restore; retain original archive"
        )


def _provider_target_guard(target, url):
    """Fixed SQL guard for the existing supervisor's NEW marked target only."""
    if (
        os.geteuid() != 0
        or not isinstance(target, dict)
        or set(target) != {"coordinator", "database", "oid", "owner_oid", "marker_sha256"}
        or target["coordinator"] != "existing-native-timescale-restore-v1"
        or target["database"] != url.database
        or type(target["oid"]) is not int
        or target["oid"] <= 0
        or type(target["owner_oid"]) is not int
        or target["owner_oid"] <= 0
        or not isinstance(target["marker_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", target["marker_sha256"])
    ):
        raise ArchiveBlocked("Root existing supervisor target authority required")
    # marker is compared as a SHA; no source/profile value is interpolated.
    return (
        "SELECT pg_advisory_xact_lock(1735157614,hashtext(current_database()));\n"
        "DO $$ BEGIN IF (SELECT oid FROM pg_database WHERE datname=current_database()) <> "
        + str(target["oid"])
        + " OR (SELECT datdba FROM pg_database WHERE datname=current_database()) <> "
        + str(target["owner_oid"])
        + " OR (SELECT oid FROM pg_roles WHERE rolname=current_user) <> "
        + str(target["owner_oid"])
        + " OR current_user <> session_user OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname=current_user "
        "AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) "
        "OR md5(current_database()) <> md5('" + url.database + "') "
        "OR encode(sha256(convert_to(coalesce((SELECT pg_catalog.shobj_description(oid,'pg_database') "
        "FROM pg_database WHERE datname=current_database()),''),'UTF8')),'hex') <> '"
        + target["marker_sha256"]
        + "' "
        "OR pg_catalog.has_database_privilege('public',current_database(),'CONNECT') "
        "OR pg_catalog.has_database_privilege('public',current_database(),'TEMPORARY') "
        "OR current_setting('timescaledb.restoring',true) IS DISTINCT FROM 'on' "
        "OR NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname='timescaledb' AND extversion='2.17.2') "
        "OR EXISTS (SELECT 1 FROM pg_extension WHERE extname NOT IN ('timescaledb','plpgsql')) "
        "OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' "
        "AND c.relkind IN ('r','p','v','m','S','f','c') "
        "AND NOT EXISTS (SELECT 1 FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid "
        "WHERE d.classid='pg_class'::regclass AND d.objid=c.oid "
        "AND d.refclassid='pg_extension'::regclass AND d.deptype='e' AND e.extname='timescaledb')) "
        "OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' "
        "AND NOT EXISTS (SELECT 1 FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid "
        "WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid "
        "AND d.refclassid='pg_extension'::regclass AND d.deptype='e' AND e.extname='timescaledb')) "
        "THEN RAISE EXCEPTION 'Exact empty provider target required'; END IF; END $$;\n"
    )


def _provider_target_identity(db, target, url, restoring):
    from sqlalchemy import text

    actual = dict(
        db.execute(
            text("""SELECT current_database() AS database,
      (SELECT oid::bigint FROM pg_catalog.pg_database WHERE datname=current_database()) AS oid,
      (SELECT datdba::bigint FROM pg_catalog.pg_database WHERE datname=current_database()) AS owner_oid,
      (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=current_user) AS actor_oid,
      (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=session_user) AS session_oid,
      encode(sha256(convert_to(coalesce((SELECT pg_catalog.shobj_description(oid,'pg_database')
        FROM pg_catalog.pg_database WHERE datname=current_database()),''),'UTF8')),'hex') AS marker_sha256,
      (SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
        FROM pg_catalog.pg_roles WHERE rolname=current_user) AS privileged,
      pg_catalog.has_database_privilege('public',current_database(),'CONNECT') AS public_connect,
      pg_catalog.has_database_privilege('public',current_database(),'TEMPORARY') AS public_temp,
      current_setting('timescaledb.restoring',true) AS restoring""")
        )
        .mappings()
        .one()
    )
    if actual != {
        "database": url.database,
        "oid": target["oid"],
        "owner_oid": target["owner_oid"],
        "actor_oid": target["owner_oid"],
        "session_oid": target["owner_oid"],
        "marker_sha256": target["marker_sha256"],
        "privileged": False,
        "public_connect": False,
        "public_temp": False,
        "restoring": restoring,
    }:
        raise ArchiveBlocked("Controlled restore target identity or phase changed")


def _restore_provider(
    folder,
    url,
    manifest_sha256,
    key,
    candidate,
    parts,
    body,
    *,
    provider_target,
    post_restore,
    checkpoint,
):
    from sqlalchemy import text

    from . import native_backup, schema_provider

    guard_sql = _provider_target_guard(provider_target, url)
    if not callable(post_restore):
        raise ArchiveBlocked("Existing controlled supervisor post-restore handshake required")
    proof = body.get("master_proof")
    schema_provider.validate_proof(proof, candidate.source)
    if (
        candidate.environment != "staging"
        or len(parts) != 5
        or parts[0].get("backup_format") != schema_provider.FORMAT
        or any(
            parts[0].get(k) != value
            for k, value in schema_provider.descriptor(
                proof, candidate.source, body["files"]
            ).items()
        )
    ):
        raise ArchiveBlocked("Explicit provider bundle descriptor differs")
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PGHOST": url.host,
        "PGPORT": str(url.port or 5432),
        "PGDATABASE": url.database,
        "PGUSER": url.username,
        "PGPASSWORD": url.password or "",
        "PGCONNECT_TIMEOUT": "3",
        "PGOPTIONS": "-c statement_timeout=8000 -c lock_timeout=150 -c idle_in_transaction_session_timeout=30000 -c default_transaction_read_only=off",
    }
    with tempfile.TemporaryDirectory(prefix="restore-private-", dir=folder) as temporary:
        work = Path(temporary)
        for index, name in (
            (1, "schema.dump"),
            (2, "masters.dump"),
            (3, "provider.dump"),
            (4, "auth.json"),
        ):
            decrypt(
                folder / f"{index:04d}.bin",
                work / name,
                key,
                parts[index]["plaintext_sha256"],
                checkpoint=checkpoint,
            )
        if (work / "auth.json").stat().st_size > 256 * 1024:
            raise ArchiveBlocked("Provider authentication metadata exceeds budget")
        expected = {
            "schema": schema_provider.AUTH_FORMAT,
            "format": schema_provider.FORMAT,
            "source": candidate.source,
            "environment": "staging",
            "version": schema_provider.proof_version(candidate.source),
            "captured_at": candidate.end,
            "master_proof": proof,
            "dump_files": body["files"][:3],
        }
        if decode((work / "auth.json").read_bytes()) != expected:
            raise ArchiveBlocked("Authenticated provider/source/master/dump binding differs")
        scope = proof["provider"]["scope"]
        masters = schema_provider.validate_proof(proof, candidate.source)
        provider_entries = schema_provider.inspect_dump(
            work / "provider.dump", scope, "provider", masters, env, checkpoint=checkpoint
        )
        schema_provider.inspect_dump(
            work / "masters.dump", scope, "masters", masters, env, checkpoint=checkpoint
        )
        schema_provider.inspect_dump(
            work / "schema.dump", scope, "schema", masters, env, checkpoint=checkpoint
        )
        selected = work / "provider-data.toc"
        selected.write_text("\n".join(provider_entries) + "\n")
        selected.chmod(0o600)
        scripts = []
        for name, section, source in (
            ("pre", "pre-data", "schema"),
            ("provider", "data", "provider"),
            ("masters", "data", "masters"),
            ("post", "post-data", "schema"),
        ):
            target = work / f"{name}.sql"
            target.touch(mode=0o600, exist_ok=False)
            run_client(
                [
                    *client_command("pg_restore"),
                    "--no-owner",
                    "--no-acl",
                    "--exit-on-error",
                    "--section=" + section,
                    *(["--use-list=" + str(selected)] if name == "provider" else []),
                    "--file=" + str(target),
                    str(work / f"{source}.dump"),
                ],
                env,
                checkpoint=checkpoint,
                output=target,
            )
            scripts.append(target)
        guard = work / "guard.sql"
        guard.write_text(guard_sql)
        guard.chmod(0o600)
        run_client(
            [
                *client_command("psql"),
                "-X",
                "--no-password",
                "--set=ON_ERROR_STOP=1",
                "--single-transaction",
                *[v for file in [guard, *scripts] for v in ("--file", str(file))],
            ],
            env,
            checkpoint=checkpoint,
        )
    engine = create_engine(
        url,
        pool_size=1,
        max_overflow=0,
        connect_args={
            "connect_timeout": 3,
            "options": "-c default_transaction_read_only=off -c statement_timeout=8000 -c lock_timeout=150 -c idle_in_transaction_session_timeout=30000",
        },
    )
    try:
        # Restore exact authenticated sequence values using the same bounded
        # per-name binding used by the existing native coordinator.
        with engine.begin() as db:
            _provider_target_identity(db, provider_target, url, "on")
            existing = native_backup.sequence_proof(db, checkpoint=checkpoint)
            sequences = proof["provider"]["sequences"]
            if set(existing) != set(sequences):
                raise ArchiveBlocked("Restored provider sequence scope differs")
            for name, item in sequences.items():
                checkpoint()
                if existing[name]["settings"] != item["settings"]:
                    raise ArchiveBlocked("Restored provider sequence settings differ")
                db.execute(
                    text("SELECT setval(CAST(:name AS regclass),:value,:called)"),
                    {"name": name, "value": item["last_value"], "called": item["is_called"]},
                )
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as db,
            db.begin(),
        ):
            db.execute(text("SET TRANSACTION READ ONLY"))
            _provider_target_identity(db, provider_target, url, "on")
            legacy = schema_provider.legacy_proof(proof, candidate.source)
            actual = master_proof(
                db,
                candidate.source,
                checkpoint=checkpoint,
                proof_version=legacy["version"],
                **(
                    {"schema_scope": legacy["schema_scope"]}
                    if candidate.source == "gateway"
                    else {}
                ),
            )
            if (
                actual != legacy
                or native_backup.structure_proof(db, checkpoint=checkpoint)
                != proof["provider"]["structure"]
                or native_backup.sequence_proof(db, checkpoint=checkpoint) != sequences
                or native_backup.jobs_proof(db) != proof["provider"]["jobs_sha256"]
            ):
                raise ArchiveBlocked("Restored provider/schema/master stored proof differs")
        engine.dispose()
        # Existing supervisor must quarantine only this owned target's jobs,
        # then call timescaledb_post_restore. No callback can manufacture PASS:
        # actual stored state is checked again below.
        post_restore(
            checkpoint=checkpoint,
            evidence={
                "format": "greenmind.provider-restore-pre-quarantine.v1",
                "source": candidate.source,
                "manifest_sha256": manifest_sha256,
                "source_jobs_sha256": proof["provider"]["jobs_sha256"],
                "restored_jobs_sha256": proof["provider"]["jobs_sha256"],
                "master_proof_sha256": digest(proof),
                "structure_sha256": digest(proof["provider"]["structure"]),
                "sequences_sha256": digest(sequences),
                "target_sha256": digest(provider_target),
            },
        )
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as db,
            db.begin(),
        ):
            db.execute(text("SET TRANSACTION READ ONLY"))
            _provider_target_identity(db, provider_target, url, "off")
            if (
                db.execute(text("SHOW timescaledb.restoring")).scalar_one() != "off"
                or db.execute(
                    text("SELECT count(*) FROM timescaledb_information.jobs WHERE scheduled")
                ).scalar_one()
                != 0
            ):
                raise ArchiveBlocked("Controlled provider post-restore quarantine incomplete")
            final_master = master_proof(
                db,
                candidate.source,
                checkpoint=checkpoint,
                proof_version=legacy["version"],
                **(
                    {"schema_scope": legacy["schema_scope"]}
                    if candidate.source == "gateway"
                    else {}
                ),
            )
            if (
                final_master != legacy
                or native_backup.structure_proof(db, checkpoint=checkpoint)
                != proof["provider"]["structure"]
                or native_backup.sequence_proof(db, checkpoint=checkpoint) != sequences
            ):
                raise ArchiveBlocked("Controlled post-restore changed verified schema or masters")
        return {
            "schema_restored": True,
            "typed_values_verified": True,
            "master_proof_version": proof["version"],
            "backup_format": schema_provider.FORMAT,
            "schema_provider_data_replayed": len(provider_entries),
            "source_accesses": 0,
            "provider_structure_verified": True,
            "provider_jobs_quarantined": True,
            "provider_jobs_original_sha256": proof["provider"]["jobs_sha256"],
            "provider_jobs_original_verified_before_quarantine": True,
            "provider_final_scheduled_jobs": 0,
            "current_recovery_scope": True,
            "master_tables": len(proof["tables"]),
            "master_rows": sum(table["rows"] for table in proof["tables"].values()),
            "manifest_sha256": manifest_sha256,
            "production_acceptance": False,
        }
    finally:
        engine.dispose()
