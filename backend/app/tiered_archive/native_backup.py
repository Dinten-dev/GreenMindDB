"""Versioned bounded native backups for the existing local Staging copies.

This format is deliberately not a deletion authorization. Production partitions
and legacy schema/master bundles retain their separate workflows.
"""

import hashlib
import json
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.raw_archive.policy import ArchiveBlocked, checksum

from . import backup, parquet
from .codec import digest
from .pipeline import verify_file
from .policy import POLICY_SHA256, Candidate, utc

FORMAT = "postgres-full-v1"
MAX = backup.MAX_BYTES
SYSTEM_ORG = "00000000-0000-0000-0000-000000000000"
SOURCE_DATABASES = {"gateway": "plantdb_staging", "direct": "greenmind_direct_staging"}
# Only the separate root-authorized provider format can use these existing
# production readers. postgres-full-v1 remains restricted to Staging.
PRODUCTION_SOURCE_DATABASES = {"gateway": "plantdb", "direct": "greenmind_direct_production"}
PRODUCTION_SOURCE_BINDINGS = {
    source: {
        "host": "127.0.0.1",
        "port": 5432,
        "database_oid": oid,
        "role": "greenmind_archive_reader_33fe395",
        "role_oid": 499352,
        "session_oid": 499352,
    }
    for source, oid in (("gateway", 16384), ("direct", 301614))
}
# Authenticated postgres-full-v1 archives retain their original minimum scope.
# New captures/seals must additionally cover every current authority table.
NATIVE_V1_MINIMUM_TABLES = {
    "gateway": backup.LEGACY_MASTER_TABLES_V2,
    "direct": backup.DIRECT_MASTER_TABLES,
}


class Sink:
    def __init__(self, budget, checkpoint):
        self.h = hashlib.sha256()
        self.bytes = 0
        self.checkpoint = checkpoint
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
            self.checkpoint()
            self.checked_bytes, self.checked_at = self.bytes, now
        self.h.update(b)
        return len(b)

    def digest(self):
        self.checkpoint()
        return self.h.hexdigest()


def sequence_proof(db, *, checkpoint):
    quote = db.dialect.identifier_preparer.quote
    rows = db.execute(
        text(
            "SELECT n.nspname,c.relname,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2 LIMIT 129"
        )
    ).all()
    if not 1 <= len(rows) <= 128:
        raise ArchiveBlocked("Native sequence scope exceeds budget")
    values = {}
    for schema, name, *settings in rows:
        checkpoint()
        last, called = db.execute(
            text("SELECT last_value,is_called FROM " + quote(schema) + "." + quote(name))
        ).one()
        values[schema + "." + name] = {
            "settings": settings,
            "last_value": last,
            "is_called": called,
        }
    return values


def content_proof(db, *, checkpoint):
    db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    db.execute(text("SET LOCAL extra_float_digits=3"))
    names = (
        db.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        )
        .scalars()
        .all()
    )
    if not 1 <= len(names) <= 128:
        raise ArchiveBlocked("Native public table scope exceeds budget")
    quote = db.dialect.identifier_preparer.quote
    tables = {}
    budget = [0]
    for name in names:
        checkpoint()
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
            raise ArchiveBlocked("Full proof requires primary key for every public table")
        count = db.execute(text("SELECT count(*) FROM public." + quote(name))).scalar_one()
        sink = Sink(budget, checkpoint)
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

    shapes = structure_proof(db, checkpoint=checkpoint, budget=budget)
    return {
        "public_tables": tables,
        "structure": shapes,
        "proof_bytes": budget[0],
        "sequences": sequence_proof(db, checkpoint=checkpoint),
    }


def structure_proof(db, *, checkpoint, budget=None):
    """Same native structural fingerprints without scanning public payload rows."""
    budget = [0] if budget is None else budget

    def hash_query(query):
        sink = Sink(budget, checkpoint)
        with db.connection.driver_connection.cursor() as cursor:
            cursor.copy_expert("COPY (" + query + ") TO STDOUT WITH (FORMAT binary)", sink)
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
    return {name: hash_query(query) for name, query in definitions.items()}


def jobs_proof(db):
    rows = db.execute(
        text(
            "SELECT job_id,application_name,proc_schema,proc_name,schedule_interval::text,max_runtime::text,max_retries,retry_period::text,owner::text,scheduled,fixed_schedule,config::text FROM timescaledb_information.jobs WHERE job_id>=1000 ORDER BY job_id LIMIT 129"
        )
    ).all()
    if len(rows) > 128:
        raise ArchiveBlocked("Native policy scope exceeds budget")
    return hashlib.sha256(
        json.dumps([list(r) for r in rows], separators=(",", ":")).encode()
    ).hexdigest()


def validate_proof(proof, source, *, current_scope=False):
    """Reject incomplete native metadata before it can drive a restore."""
    current = backup.master_tables(source)
    required = current if current_scope else NATIVE_V1_MINIMUM_TABLES[source]
    if not isinstance(proof, dict) or set(proof) != {
        "public_tables",
        "structure",
        "proof_bytes",
        "sequences",
    }:
        raise ArchiveBlocked("Incomplete native backup proof")
    tables, sequences = proof["public_tables"], proof["sequences"]
    if (
        not isinstance(tables, dict)
        or not 1 <= len(tables) <= 128
        or not set(required) <= set(tables)
        or not isinstance(sequences, dict)
        or not 1 <= len(sequences) <= 128
        or type(proof["proof_bytes"]) is not int
        or not 0 < proof["proof_bytes"] <= MAX
    ):
        raise ArchiveBlocked("Native proof scope exceeds policy")
    for name, item in tables.items():
        if (
            not re.fullmatch(r"[a-z_][a-z0-9_]*", name)
            or not isinstance(item, dict)
            or set(item) != {"rows", "binary_sha256", "bytes"}
            or type(item["rows"]) is not int
            or item["rows"] < 0
            or type(item["bytes"]) is not int
            or not 0 < item["bytes"] <= MAX
            or not re.fullmatch(r"[a-f0-9]{64}", str(item["binary_sha256"]))
        ):
            raise ArchiveBlocked("Malformed native table proof")
    if sum(item["bytes"] for item in tables.values()) > proof["proof_bytes"]:
        raise ArchiveBlocked("Native proof byte budget differs")
    expected_shapes = {
        "columns",
        "constraints",
        "indexes",
        "views",
        "extensions",
        "hypertables",
        "chunks",
        "dimensions",
        "compression_settings",
    }
    if (
        not isinstance(proof["structure"], dict)
        or set(proof["structure"]) != expected_shapes
        or any(not re.fullmatch(r"[a-f0-9]{64}", str(h)) for h in proof["structure"].values())
    ):
        raise ArchiveBlocked("Incomplete native structure proof")
    for name, item in sequences.items():
        if (
            not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", name)
            or not isinstance(item, dict)
            or set(item) != {"settings", "last_value", "is_called"}
            or not isinstance(item["settings"], list)
            or len(item["settings"]) != 6
            or any(type(n) is not int for n in item["settings"][:5])
            or type(item["settings"][5]) is not bool
            or type(item["last_value"]) is not int
            or type(item["is_called"]) is not bool
            or not item["settings"][3] <= item["last_value"] <= item["settings"][2]
        ):
            raise ArchiveBlocked("Malformed native sequence proof")


def source_url(database_url, source, environment, *, provider_scope=None):
    """Full-v1 stays Staging-only; production needs an explicit provider authority."""
    url = make_url(database_url)
    if provider_scope is not None:
        from .schema_provider import validate_scope

        validate_scope(provider_scope)
        if provider_scope["source"] != source or provider_scope["environment"] != environment:
            raise ArchiveBlocked("Provider source URL and authority differ")
        if environment == "production":
            binding = provider_scope["source_binding"]
            if (
                url.drivername not in {"postgresql", "postgresql+psycopg2"}
                or (url.host, url.port, url.database, url.username)
                != (
                    binding["host"],
                    binding["port"],
                    provider_scope["database"],
                    binding["role"],
                )
                or not url.password
                or url.query
            ):
                raise ArchiveBlocked("Exact existing production provider reader required")
            return url
    if (
        environment != "staging"
        or source not in SOURCE_DATABASES
        or url.drivername not in {"postgresql", "postgresql+psycopg2"}
        or url.host != "127.0.0.1"
        or url.port != 5433
        or url.database != SOURCE_DATABASES[source]
        or url.username != "greenmind_archive_staging_ro"
        or not url.password
        or url.query
    ):
        raise ArchiveBlocked("Native backup requires the existing read-only Staging source")
    return url


def capture(database_url, source, output, key, *, environment, checkpoint):
    """Capture one consistent bounded full dump and seal the native archive bundle."""
    from .database_restore import run_client

    url = source_url(database_url, source, environment)
    output = Path(output)
    if not output.is_absolute() or output.resolve() != output or not output.is_dir():
        raise ArchiveBlocked("Private absolute native capture directory required")
    if output.stat().st_mode & 0o077:
        raise ArchiveBlocked("Native capture directory must be private")
    dump = output / "native-full.dump"
    with dump.open("xb"):
        dump.chmod(0o600)
    engine = create_engine(
        url,
        pool_size=1,
        max_overflow=0,
        connect_args={
            "connect_timeout": 3,
            "options": "-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150",
        },
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PGHOST": url.host,
        "PGPORT": str(url.port),
        "PGDATABASE": url.database,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGCONNECT_TIMEOUT": "3",
        "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150",
    }
    try:
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as db,
            db.begin(),
        ):
            checkpoint()
            db.execute(text("SET TRANSACTION READ ONLY"))
            if db.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
                raise ArchiveBlocked("Native source is not read-only")
            if any(
                db.execute(
                    text(
                        "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                ).one()
            ):
                raise ArchiveBlocked("Native source role is privileged")
            if db.execute(text("SELECT pg_database_size(current_database())")).scalar_one() > MAX:
                raise ArchiveBlocked("Native Staging source exceeds 64 MiB")
            backup.check_master_tables(db, source, checkpoint=checkpoint)
            captured_at = db.execute(text("SELECT transaction_timestamp()")).scalar_one()
            snapshot = db.execute(text("SELECT pg_export_snapshot()")).scalar_one()
            proof = content_proof(db, checkpoint=checkpoint)
            validate_proof(proof, source, current_scope=True)
            jobs = jobs_proof(db)
            run_client(
                [
                    *backup.client_command("pg_dump"),
                    "--no-password",
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    "--snapshot=" + snapshot,
                    "--file=" + str(dump),
                ],
                env,
                checkpoint=checkpoint,
                output=dump,
            )
            if sequence_proof(db, checkpoint=checkpoint) != proof["sequences"]:
                raise ArchiveBlocked("Source sequence changed during consistent capture")
        return seal(source, captured_at, dump, proof, jobs, key, output, checkpoint=checkpoint)
    finally:
        engine.dispose()
        dump.unlink(missing_ok=True)


def seal(source, captured_at, dump, proof, jobs, key, output, *, checkpoint):
    """Build the regular catalogue/manifest inputs without publishing anything."""
    if source not in SOURCE_DATABASES or captured_at.tzinfo is None:
        raise ArchiveBlocked("Known native Staging source and capture time required")
    validate_proof(proof, source, current_scope=True)
    if not re.fullmatch(r"[a-f0-9]{64}", str(jobs)):
        raise ArchiveBlocked("Native policy proof missing")
    output = Path(output)
    if not output.is_dir() or output.stat().st_mode & 0o077:
        raise ArchiveBlocked("Native capture directory must be private")
    files = []
    sealed = output / "0001.bin"
    dump_ref = backup.encrypt(dump, sealed, key, checkpoint=checkpoint)
    files.append((sealed, dump_ref))
    proof_sha256 = digest(proof)
    payload = {
        "backup_format": FORMAT,
        "version": 1,
        "source": source,
        "source_database": SOURCE_DATABASES[source],
        "captured_at": captured_at.isoformat(),
        "dump_plaintext_sha256": dump_ref["plaintext_sha256"],
        "proof": proof,
        "proof_sha256": proof_sha256,
        "jobs_proof": jobs,
    }
    metadata = output / "native-metadata.json"
    try:
        with metadata.open("x") as body:
            metadata.chmod(0o600)
            json.dump(payload, body, sort_keys=True, separators=(",", ":"))
        sealed = output / "0002.bin"
        files.append((sealed, backup.encrypt(metadata, sealed, key, checkpoint=checkpoint)))
        for number, (path, ref) in enumerate(files):
            verify = output / (str(number) + ".verify")
            try:
                backup.decrypt(path, verify, key, ref["plaintext_sha256"], checkpoint=checkpoint)
            finally:
                verify.unlink(missing_ok=True)
    finally:
        metadata.unlink(missing_ok=True)
    body = {
        "backup_format": FORMAT,
        "native_proof_version": 1,
        "source": source,
        "at": captured_at,
        "native_proof_sha256": proof_sha256,
        "files": [ref for _, ref in files],
    }
    row = {
        "identity": digest(body),
        "organization": SYSTEM_ORG,
        "sensor": "system",
        "time_us": int(captured_at.timestamp() * 1_000_000),
        "body": body,
    }
    index = output / "0000.parquet"
    reference = dict(
        parquet.write(index, [row], checkpoint=checkpoint),
        format="parquet",
        backup_format=FORMAT,
        native_proof_version=1,
        native_proof_sha256=proof_sha256,
    )
    candidate = Candidate(
        "staging",
        source,
        "database_backup",
        SYSTEM_ORG,
        captured_at - timedelta(microseconds=1),
        captured_at,
        reference["content_sha256"],
        1,
        "features",
        captured_at,
        active=True,
    )
    return candidate, [(index, reference), *files]


@contextmanager
def authenticated_bundle(folder, manifest_sha256, key, *, checkpoint):
    """Authenticate all native inputs without opening any database connection."""
    from .runtime import candidate_from_json

    folder = Path(folder)
    if not folder.is_absolute() or folder.resolve() != folder or not folder.is_dir():
        raise ArchiveBlocked("Absolute isolated native bundle required")
    manifest_path = folder / "manifest.json"
    if (
        manifest_path.is_symlink()
        or manifest_path.stat().st_size > 65536
        or checksum(manifest_path) != manifest_sha256
    ):
        raise ArchiveBlocked("Native manifest differs")
    manifest = json.loads(manifest_path.read_text())
    candidate = candidate_from_json(manifest["candidate"])
    candidate.validate()
    parts = manifest["files"]
    if (
        type(manifest.get("version")) is not int
        or manifest["version"] != 1
        or manifest.get("policy") != POLICY_SHA256
        or manifest.get("identity") != candidate.identity
        or candidate.environment != "staging"
        or candidate.source not in SOURCE_DATABASES
        or candidate.dataset != "database_backup"
        or candidate.organization != SYSTEM_ORG
        or candidate.rows != 1
        or len(parts) != 3
        or [p["format"] for p in parts] != ["parquet", "bin", "bin"]
        or parts[0].get("backup_format") != FORMAT
        or type(parts[0].get("native_proof_version")) is not int
        or parts[0]["native_proof_version"] != 1
    ):
        raise ArchiveBlocked("Complete versioned native Staging bundle required")
    for number, part in enumerate(parts):
        path = folder / f"{number:04d}.{part['format']}"
        if path.is_symlink() or type(part["bytes"]) is not int or not 0 < part["bytes"] <= MAX:
            raise ArchiveBlocked("Native bundle artifact exceeds policy")
        verify_file(path, part, checkpoint)
    rows = list(parquet.read(folder / "0000.parquet", parts[0], checkpoint=checkpoint))
    if len(rows) != 1 or parts[0]["content_sha256"] != candidate.content_sha256:
        raise ArchiveBlocked("Native index differs from candidate")
    row, body = rows[0], rows[0]["body"]
    if (
        row["organization"] != SYSTEM_ORG
        or row["sensor"] != "system"
        or type(row["time_us"]) is not int
        or row["time_us"] != int(candidate.end.timestamp() * 1_000_000)
        or row["identity"] != digest(body)
        or body.get("backup_format") != FORMAT
        or type(body.get("native_proof_version")) is not int
        or body["native_proof_version"] != 1
        or body["source"] != candidate.source
        or body["at"] != candidate.end
        or body["native_proof_sha256"] != parts[0]["native_proof_sha256"]
        or body["files"] != [{k: v for k, v in p.items() if k != "key"} for p in parts[1:]]
    ):
        raise ArchiveBlocked("Native index and encrypted files differ")
    with tempfile.TemporaryDirectory(prefix="native-auth-", dir=folder) as temporary:
        temporary = Path(temporary)
        for number, name in ((1, "full.dump"), (2, "metadata.json")):
            backup.decrypt(
                folder / f"{number:04d}.bin",
                temporary / name,
                key,
                parts[number]["plaintext_sha256"],
                checkpoint=checkpoint,
            )
        path = temporary / "metadata.json"
        if path.stat().st_size > 256 * 1024:
            raise ArchiveBlocked("Native recovery metadata exceeds budget")
        metadata = json.loads(path.read_text())
        if (
            metadata.get("backup_format") != FORMAT
            or type(metadata.get("version")) is not int
            or metadata["version"] != 1
            or metadata["source"] != candidate.source
            or metadata["source_database"] != SOURCE_DATABASES[candidate.source]
            or utc(metadata["captured_at"]) != candidate.end
            or metadata["dump_plaintext_sha256"] != parts[1]["plaintext_sha256"]
            or metadata["proof_sha256"] != body["native_proof_sha256"]
            or digest(metadata["proof"]) != metadata["proof_sha256"]
            or not re.fullmatch(r"[a-f0-9]{64}", str(metadata["jobs_proof"]))
        ):
            raise ArchiveBlocked("Authenticated native metadata binding differs")
        validate_proof(metadata["proof"], candidate.source)
        yield temporary / "full.dump", metadata


def supervisor_post_restore(state, marker, *, checkpoint, evidence=None):
    """Reuse the existing supervisor request/done handshake; never run admin SQL."""
    import os

    state = Path(state)
    if (
        not state.is_absolute()
        or state.resolve() != state
        or not state.is_dir()
        or state.stat().st_mode & 0o077
        or not isinstance(marker, str)
        or not 1 <= len(marker.encode()) <= 256
        or "\x00" in marker
    ):
        raise ArchiveBlocked("Private existing supervisor state required")
    if evidence is not None:
        allowed = {
            "format",
            "source",
            "manifest_sha256",
            "source_jobs_sha256",
            "restored_jobs_sha256",
            "master_proof_sha256",
            "structure_sha256",
            "sequences_sha256",
            "target_sha256",
        }
        if (
            os.geteuid() != 0
            or not isinstance(evidence, dict)
            or set(evidence) != allowed
            or evidence["format"] != "greenmind.provider-restore-pre-quarantine.v1"
            or evidence["source"] not in {"gateway", "direct"}
            or any(
                not isinstance(evidence[k], str) or not re.fullmatch("[a-f0-9]{64}", evidence[k])
                for k in allowed - {"format", "source"}
            )
            or evidence["source_jobs_sha256"] != evidence["restored_jobs_sha256"]
        ):
            raise ArchiveBlocked("Exact provider pre-quarantine evidence required")
        journal = state / "provider-before-post-restore.json"
        descriptor = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode())
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    request = state / "post-restore-request"
    descriptor = os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(marker.encode())
        stream.flush()
        os.fsync(stream.fileno())
    began = time.monotonic()
    done = state / "post-restore-done"
    while not done.is_file():
        checkpoint()
        if time.monotonic() - began > 120:
            raise ArchiveBlocked("Controlled post-restore acknowledgement timeout")
        time.sleep(0.1)
    import stat

    descriptor = os.open(done, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        raw = stream.read(257)
        after = os.fstat(stream.fileno())
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o444
        or info.st_nlink != 1
        or info.st_size > 256
        or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or raw != marker.encode()
    ):
        raise ArchiveBlocked("Controlled post-restore acknowledgement differs")
    checkpoint()
