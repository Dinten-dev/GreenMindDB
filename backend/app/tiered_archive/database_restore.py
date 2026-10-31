"""Restore authenticated schema/master dumps into an empty local test database."""

import json
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


def restore_backup(folder, database_url, manifest_sha256, key, *, checkpoint):
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
        or len(parts) not in (3, 4)
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
