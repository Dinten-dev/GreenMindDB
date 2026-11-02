"""Explicit PG15/Timescale schema DATA authority for root-owned restore jobs.

No source grants, extension setup, SQL execution or process startup at import.
The allowlist and TOC pins must come from an independent trusted source review.
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from sqlalchemy import text

from app.raw_archive.policy import ArchiveBlocked

from .codec import digest

FORMAT = "schema-master-provider-v1"
AUDIT_V3_FORMAT = "schema-master-provider-sql-audit-v3"
AUDIT_V3_AUTH_FORMAT = "greenmind.schema-master-provider-audit-auth.v3"
AUTH_FORMAT = "greenmind.schema-master-provider-auth.v1"
SCOPE_FORMAT = "greenmind.pg15-timescale2172-source-authority.v1"
PRODUCTION_SCOPE_FORMAT = "greenmind.pg15-timescale2172-production-authority.v1"
MAX_TOC_BYTES = 512 * 1024
MAX_TOC_ENTRIES = 4096
NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
DATA_TYPES = {"TABLE DATA": "r", "SEQUENCE SET": "S"}


def need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def proof_version(source):
    need(source in {"gateway", "direct"}, "Known schema provider source required")
    return 5 if source == "gateway" else 4


def read_scope(path, expected_sha256):
    """No credentials or authority file may be handed to an unprivileged worker."""
    need(os.geteuid() == 0 and SHA.fullmatch(expected_sha256 or ""), "Root provider scope required")
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, "Exact provider authority path required")
    parent = path.parent.stat()
    need(
        parent.st_uid == 0 and stat.S_IMODE(parent.st_mode) == 0o700,
        "Private provider scope directory required",
    )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        need(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == 0
            and before.st_nlink == 1
            and stat.S_IMODE(before.st_mode) in {0o400, 0o600}
            and before.st_size <= MAX_TOC_BYTES,
            "Private root provider authority required",
        )
        raw = stream.read(MAX_TOC_BYTES + 1)
        after = os.fstat(stream.fileno())
    need(
        len(raw) == before.st_size
        and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        and hashlib.sha256(raw).hexdigest() == expected_sha256,
        "Provider authority changed",
    )
    return validate_scope(json.loads(raw))


def validate_scope(scope):
    from .native_backup import (
        PRODUCTION_SOURCE_BINDINGS,
        PRODUCTION_SOURCE_DATABASES,
        SOURCE_DATABASES,
    )

    production = isinstance(scope, dict) and scope.get("format") == PRODUCTION_SCOPE_FORMAT
    need(
        isinstance(scope, dict)
        and set(scope)
        == {
            "format",
            "environment",
            "source",
            "database",
            "source_identity_sha256",
            "postgres_major",
            "extension",
            "extension_version",
            "extensions",
            "config_relations",
            "schema_toc_sha256",
            "masters_toc_sha256",
            "provider_toc_sha256",
            "master_sequences",
            "sequence_names",
        }
        | ({"source_binding"} if production else set()),
        "Exact independently pinned provider scope required",
    )
    need(
        scope["format"] == (PRODUCTION_SCOPE_FORMAT if production else SCOPE_FORMAT)
        and scope["environment"] == ("production" if production else "staging")
        and scope["source"] in SOURCE_DATABASES
        and scope["database"]
        == (PRODUCTION_SOURCE_DATABASES if production else SOURCE_DATABASES)[scope["source"]]
        and scope["postgres_major"] == 15
        and scope["extension"] == "timescaledb"
        and scope["extension_version"] == "2.17.2",
        "Unsupported schema restore provider",
    )
    if production:
        binding = scope["source_binding"]
        need(
            isinstance(binding, dict)
            and binding == PRODUCTION_SOURCE_BINDINGS[scope["source"]]
            and all(
                type(binding[name]) is int
                for name in ("port", "database_oid", "role_oid", "session_oid")
            ),
            "Exact reviewed production source binding required",
        )
    need(
        all(
            isinstance(scope[k], str) and SHA.fullmatch(scope[k])
            for k in (
                "source_identity_sha256",
                "schema_toc_sha256",
                "masters_toc_sha256",
                "provider_toc_sha256",
            )
        ),
        "Independent provider and TOC pins required",
    )
    expected_extensions = {"plpgsql": "1.0", "timescaledb": "2.17.2"}
    if scope["source"] == "gateway":
        expected_extensions.update({"pgcrypto": "1.3", "uuid-ossp": "1.1"})
    need(
        scope["extensions"] == expected_extensions,
        "Exact reviewed per-source extension roster required",
    )
    rows = scope["config_relations"]
    need(isinstance(rows, list) and 1 <= len(rows) <= 64, "Bounded exact provider roster required")
    identities = []
    for row in rows:
        need(
            isinstance(row, dict)
            and set(row) == {"schema", "name", "kind", "oid", "condition_sha256"}
            and isinstance(row["schema"], str)
            and isinstance(row["name"], str)
            and NAME.fullmatch(row["schema"])
            and NAME.fullmatch(row["name"])
            and row["kind"] in {"r", "S"}
            and type(row["oid"]) is int
            and row["oid"] > 0
            and isinstance(row["condition_sha256"], str)
            and SHA.fullmatch(row["condition_sha256"]),
            "Exact trusted extconfig relation required",
        )
        need(
            row["schema"] in {"_timescaledb_catalog", "_timescaledb_config"},
            "Provider DATA cannot authorize application tables",
        )
        identities.append((row["schema"], row["name"]))
    need(
        len(set(identities)) == len(rows) and identities == sorted(identities),
        "Provider roster order differs",
    )
    sequences = scope["master_sequences"]
    need(
        isinstance(sequences, list)
        and len(sequences) <= 128
        and sequences == sorted(set(sequences))
        and all(
            isinstance(v, str) and re.fullmatch(r"public\.[a-z_][a-z0-9_]{0,62}", v)
            for v in sequences
        ),
        "Exact public master sequence scope required",
    )
    all_sequences = scope["sequence_names"]
    need(
        isinstance(all_sequences, list)
        and 1 <= len(all_sequences) <= 128
        and all_sequences == sorted(set(all_sequences))
        and all(
            isinstance(v, str)
            and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}\.[a-z_][a-z0-9_]{0,62}", v)
            and not v.split(".", 1)[0].startswith("pg_")
            and v.split(".", 1)[0] != "information_schema"
            for v in all_sequences
        )
        and set(sequences) <= set(all_sequences)
        and {r["schema"] + "." + r["name"] for r in rows if r["kind"] == "S"} <= set(all_sequences),
        "Exact full source sequence scope required",
    )
    return scope


def capture_provider(db, scope, *, checkpoint):
    """Read fixed catalogs in the existing exported-snapshot transaction."""
    from datetime import UTC, datetime

    validate_scope(scope)
    checkpoint()
    identity = dict(
        db.execute(
            text("""SELECT current_database() AS database,
      (SELECT oid::bigint FROM pg_catalog.pg_database WHERE datname=current_database()) AS oid,
      inet_server_addr()::text AS address,inet_server_port() AS port,
      pg_catalog.pg_postmaster_start_time() AS postmaster_start,
      (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=current_user) AS actor,
      (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=session_user) AS session""")
        )
        .mappings()
        .one()
    )
    need(
        isinstance(identity["postmaster_start"], datetime)
        and identity["postmaster_start"].tzinfo is not None,
        "Exact provider postmaster timestamp required",
    )
    identity["postmaster_start"] = identity["postmaster_start"].astimezone(UTC)
    need(
        identity["database"] == scope["database"]
        and digest(identity) == scope["source_identity_sha256"],
        "Provider source identity differs",
    )
    if scope["environment"] == "production":
        binding = scope["source_binding"]
        need(
            identity["oid"] == binding["database_oid"]
            and identity["actor"] == binding["role_oid"]
            and identity["session"] == binding["session_oid"],
            "Production provider database or principal differs",
        )
        need(
            db.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
            and db.execute(
                text("""SELECT rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole
                  OR rolreplication OR rolbypassrls) FROM pg_catalog.pg_roles
                  WHERE oid=:role AND rolname=:name"""),
                {"role": binding["role_oid"], "name": binding["role"]},
            ).scalar_one()
            is True,
            "Unprivileged read-only production provider principal required",
        )
    version = db.execute(text("SELECT current_setting('server_version_num')::int")).scalar_one()
    need(150000 <= version < 160000, "PG15 provider contract required")
    extensions = dict(
        db.execute(
            text("SELECT extname,extversion FROM pg_catalog.pg_extension ORDER BY extname")
        ).all()
    )
    need(extensions == scope["extensions"], "Source extension roster differs from root authority")
    rows = [
        dict(r)
        for r in db.execute(
            text("""SELECT n.nspname AS schema,c.relname AS name,
      c.relkind AS kind,c.oid::bigint AS oid,config.condition AS condition,
      EXISTS(SELECT 1 FROM pg_catalog.pg_depend dep WHERE dep.classid='pg_catalog.pg_class'::regclass
        AND dep.objid=c.oid AND dep.refclassid='pg_catalog.pg_extension'::regclass
        AND dep.refobjid=e.oid AND dep.deptype='e') AS member
      FROM pg_catalog.pg_extension e
      CROSS JOIN LATERAL unnest(e.extconfig,e.extcondition) AS config(oid,condition)
      JOIN pg_catalog.pg_class c ON c.oid=config.oid
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE e.extname='timescaledb' ORDER BY n.nspname,c.relname LIMIT 65""")
        )
        .mappings()
        .all()
    ]
    need(
        len(rows) <= 64 and all(r.pop("member") is True for r in rows),
        "Provider membership differs",
    )
    for row in rows:
        row["condition_sha256"] = hashlib.sha256(row.pop("condition").encode()).hexdigest()
    need(
        rows == scope["config_relations"],
        "Provider configuration or filters differ from root authority",
    )
    checkpoint()
    return scope


def toc(raw, *, database):
    """Bounded PG15 custom TOC; comments are excluded from the stable entry pin."""
    need(
        isinstance(raw, bytes) and len(raw) <= MAX_TOC_BYTES and b"\x00" not in raw,
        "Provider TOC exceeds supported bounded text",
    )
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeError:
        raise ArchiveBlocked("Provider TOC encoding differs") from None
    need(
        any(re.fullmatch(r";\s+Format: CUSTOM\s*", line) for line in lines)
        and any(
            re.fullmatch(r";\s+Dumped from database version: 15\.[^\r\n]*", line) for line in lines
        )
        and any(
            re.fullmatch(r";\s+Dumped by pg_dump version: 15\.[^\r\n]*", line) for line in lines
        )
        and any(
            re.fullmatch(r";\s+dbname: " + re.escape(database) + r"\s*", line) for line in lines
        ),
        "Exact PG15 custom source TOC header required",
    )
    entries = []
    for line in lines:
        if not line or line.startswith(";"):
            continue
        need(
            len(line) <= 8192 and re.fullmatch(r"[1-9][0-9]*; [0-9]+ [0-9]+ .+", line),
            "Malformed provider TOC entry",
        )
        entries.append(line)
    need(
        1 <= len(entries) <= MAX_TOC_ENTRIES
        and len({line.split(";", 1)[0] for line in entries}) == len(entries),
        "Provider TOC identity differs",
    )
    return entries


def validate_toc(raw, scope, role, master_names):
    validate_scope(scope)
    need(role in {"schema", "masters", "provider"}, "Exact backup TOC role required")
    entries = toc(raw, database=scope["database"])
    need(digest(entries) == scope[role + "_toc_sha256"], "TOC differs from independent root pin")
    allowed = {(r["schema"], r["name"]): r for r in scope["config_relations"]}
    selected = []
    seen = set()
    for line in entries:
        rest = line.split(" ", 3)[3]
        parsed = re.fullmatch(
            r"(TABLE DATA|SEQUENCE SET) ([a-z_][a-z0-9_]*) ([a-z_][a-z0-9_]*) ([^\s]+)", rest
        )
        if parsed:
            kind, schema, name, owner = parsed.groups()
            identity = (schema, name)
            need(identity not in seen, "Duplicate archive DATA relation")
            seen.add(identity)
            if role == "provider":
                row = allowed.get(identity)
                need(
                    row is not None
                    and row["kind"] == DATA_TYPES[kind]
                    and int(line.split(" ", 3)[2]) == (row["oid"] if kind == "TABLE DATA" else 0),
                    "Unexpected schema DATA outside exact provider scope",
                )
                selected.append(line)
            else:
                need(role == "masters", "Schema-only archive unexpectedly contains DATA")
                need(
                    schema == "public"
                    and (
                        (kind == "TABLE DATA" and name in master_names)
                        or (
                            kind == "SEQUENCE SET"
                            and schema + "." + name in scope["master_sequences"]
                        )
                    ),
                    "Unexpected master TABLE DATA or sequence",
                )
        elif role in {"masters", "provider"}:
            need(
                re.fullmatch(r"(?:ENCODING|STDSTRINGS|SEARCHPATH) - [^\r\n]+", rest) is not None,
                "Master archive contains unexpected non-data SQL",
            )
        else:
            # Trusted schema definitions are pinned independently as a complete
            # ordered TOC. Database/reconnect/blob/server/subscription entries
            # can never be authorized by the Provider DATA allowlist.
            need(
                not re.search(
                    r"\b(DATABASE|BLOB|FOREIGN SERVER|SUBSCRIPTION|PUBLICATION|DATA)\b", rest
                )
                and not rest.startswith("SEQUENCE SET "),
                "Out-of-target schema TOC entry forbidden",
            )
    if role == "provider":
        need(seen == set(allowed), "Required extension configuration DATA omitted")
    elif role == "masters":
        required = {("public", name) for name in master_names} | {
            tuple(name.split(".")) for name in scope["master_sequences"]
        }
        need(seen == required, "Required master DATA omitted")
    return selected


def legacy_proof(proof, source):
    """New incompatible versions are explicit; old proof meanings never change."""
    need(
        isinstance(proof, dict)
        and type(proof.get("version")) is int
        and proof["version"] == proof_version(source),
        "Explicit provider master proof version required",
    )
    keys = {"version", "tables", "provider"} | ({"schema_scope"} if source == "gateway" else set())
    need(set(proof) == keys, "Exact provider master proof fields required")
    provider = proof["provider"]
    need(
        isinstance(provider, dict)
        and set(provider) == {"scope", "structure", "jobs_sha256", "sequences"},
        "Complete authenticated provider state required",
    )
    scope = validate_scope(provider["scope"])
    need(scope["source"] == source, "Provider source differs from master proof")
    shapes = provider["structure"]
    need(
        isinstance(shapes, dict)
        and set(shapes)
        == {
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
        and all(isinstance(v, str) and SHA.fullmatch(v) for v in shapes.values())
        and isinstance(provider["jobs_sha256"], str)
        and SHA.fullmatch(provider["jobs_sha256"]),
        "Provider structure or jobs proof missing",
    )
    sequences = provider["sequences"]
    need(
        isinstance(sequences, dict)
        and 1 <= len(sequences) <= 128
        and sorted(sequences) == scope["sequence_names"],
        "Bounded exact provider sequences required",
    )
    for name, value in sequences.items():
        need(
            isinstance(name, str)
            and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}\.[a-z_][a-z0-9_]{0,62}", name)
            and isinstance(value, dict)
            and set(value) == {"settings", "last_value", "is_called"}
            and isinstance(value["settings"], list)
            and len(value["settings"]) == 6
            and all(type(n) is int for n in value["settings"][:5])
            and type(value["settings"][5]) is bool
            and type(value["last_value"]) is int
            and type(value["is_called"]) is bool
            and value["settings"][3] <= value["last_value"] <= value["settings"][2],
            "Exact authenticated sequence state required",
        )
    return {
        **{k: v for k, v in proof.items() if k != "provider"},
        "version": 4 if source == "gateway" else 3,
    }


def validate_proof(proof, source):
    from .backup import validate_master_proof

    return validate_master_proof(legacy_proof(proof, source), source)


def descriptor(proof, source, files):
    validate_proof(proof, source)
    need(len(files) == 4, "Provider schema/master/auth file set required")
    return {
        "backup_format": FORMAT,
        "master_proof_version": proof_version(source),
        "master_schema_scope": proof.get("schema_scope"),
        "master_table_names": sorted(proof["tables"]),
        "master_proof_sha256": digest(proof),
        "provider_scope_sha256": digest(proof["provider"]["scope"]),
        "master_auth_plaintext_sha256": files[3]["plaintext_sha256"],
        "master_dump_plaintext_sha256": [v["plaintext_sha256"] for v in files[:3]],
    }


def inspect_dump(path, scope, role, master_names, env, *, checkpoint):
    from .backup import client_command
    from .database_restore import run_client

    target = Path(path).with_suffix(".toc")
    target.touch(mode=0o600, exist_ok=False)
    run_client(
        [*client_command("pg_restore"), "--list", "--file=" + str(target), str(path)],
        env,
        checkpoint=checkpoint,
        output=target,
    )
    need(target.stat().st_size <= MAX_TOC_BYTES, "Provider TOC exceeds bounded budget")
    return validate_toc(target.read_bytes(), scope, role, master_names)


def capture(database_url, source, output, key, scope, *, checkpoint, schema_capture=None, audit_capture=None, parent_capture=None):
    """Root-only new Gateway5/Direct4 capture using the existing dump/encryption pipeline."""
    from datetime import timedelta

    from app.raw_archive.policy import checksum

    from . import backup, parquet
    from .native_backup import SYSTEM_ORG, source_url
    from .policy import Candidate

    need(os.geteuid() == 0, "Root provider capture required")
    validate_scope(scope)
    source_url(database_url, source, scope["environment"], provider_scope=scope)
    need(scope["source"] == source, "Exact source provider authority required")
    output = Path(output)
    need(
        output.is_absolute()
        and output.resolve() == output
        and output.is_dir()
        and output.stat().st_uid == 0
        and not output.stat().st_mode & 0o077,
        "Private root provider capture directory required",
    )
    if audit_capture is not None:
        from .sql_audit_v3_backup import AuditSnapshotCapture

        need(type(audit_capture) is AuditSnapshotCapture and audit_capture.output == output
             and audit_capture.key == key and source == "gateway",
             "Exact Gateway V3 audit capture and Archive key required")
    if parent_capture is not None:
        from .parent_snapshot import ParentSnapshotCapture

        need(type(parent_capture) is ParentSnapshotCapture and parent_capture.output == output
             and parent_capture.source == source, "Exact provider-bound parent capture required")
    dumped = backup.dump(
        database_url,
        source,
        output,
        checkpoint=checkpoint,
        with_proof=True,
        provider_scope=scope,
        **({"schema_capture": schema_capture} if schema_capture is not None else {}),
        **({"audit_capture": audit_capture} if audit_capture is not None else {}),
        **({"parent_capture": parent_capture} if parent_capture is not None else {}),
    )
    if audit_capture is None:
        originals, proof, at = dumped
        audit_ref = None
    else:
        originals, proof, at, audit_ref = dumped
    files = []
    for index, path in enumerate(originals, 1):
        target = output / f"{index:04d}.bin"
        reference = backup.encrypt(path, target, key, checkpoint=checkpoint)
        verification = output / f"{index:04d}.verify"
        try:
            backup.decrypt(
                target, verification, key, reference["plaintext_sha256"], checkpoint=checkpoint
            )
        finally:
            verification.unlink(missing_ok=True)
        files.append((target, reference))
    metadata = {
        "schema": AUTH_FORMAT,
        "format": FORMAT,
        "source": source,
        "environment": scope["environment"],
        "version": proof_version(source),
        "captured_at": at,
        "master_proof": proof,
        "dump_files": [r for _, r in files],
    }
    if audit_ref is not None:
        metadata["schema"] = AUDIT_V3_AUTH_FORMAT
        metadata["format"] = AUDIT_V3_FORMAT
        metadata["audit_v3"] = audit_ref
    from .codec import encode

    plain = output / "provider-auth.json"
    raw = encode(metadata)
    need(len(raw) <= backup.AUTH_MAX_BYTES, "Provider authentication metadata exceeds budget")
    with plain.open("xb") as stream:
        plain.chmod(0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        target = output / "0004.bin"
        files.append((target, backup.encrypt(plain, target, key, checkpoint=checkpoint)))
    finally:
        plain.unlink(missing_ok=True)
    if audit_ref is not None:
        audit_file = output / "sql-audit-v3.bin"
        need(audit_file.is_file() and checksum(audit_file) == audit_ref["file"]["sha256"], "Sealed V3 audit artifact differs")
        files.append((audit_file, audit_ref["file"]))
    body = {
        "backup_format": AUDIT_V3_FORMAT if audit_ref is not None else FORMAT,
        "source": source,
        "at": at,
        "files": [r for _, r in files],
        "master_proof": proof,
    }
    if audit_ref is not None:
        body["audit_v3"] = audit_ref
    row = {
        "identity": digest(body),
        "organization": SYSTEM_ORG,
        "sensor": "system",
        "time_us": int(at.timestamp() * 1_000_000),
        "body": body,
    }
    index = output / "0000.parquet"
    ref = dict(
        parquet.write(index, [row], checkpoint=checkpoint),
        format="parquet",
        **descriptor(proof, source, [r for _, r in files[:4]]),
    )
    if audit_ref is not None:
        ref["backup_format"] = AUDIT_V3_FORMAT
        ref["audit_v3_plaintext_sha256"] = audit_ref["file"]["plaintext_sha256"]
        for name in ("epoch", "genesis_sha256", "schema_sha256", "snapshot_sha256"):
            ref["audit_v3_" + name] = audit_ref[name]
    candidate = Candidate(
        scope["environment"],
        source,
        "database_backup",
        SYSTEM_ORG,
        at - timedelta(microseconds=1),
        at,
        ref["content_sha256"],
        1,
        "features",
        at,
    )
    final_files = [(index, ref), *files]
    if parent_capture is not None:
        parent_capture.complete(candidate, final_files, proof, checkpoint=checkpoint)
    return candidate, final_files


def current_descriptor(files, source):
    """Only explicit new descriptors can identify new provider backups."""
    try:
        from .backup_scope import validate_scope as validate_gateway_scope

        primary = files[0]
        if (
            len(files) != 5
            or [f.get("format") for f in files] != ["parquet", "bin", "bin", "bin", "bin"]
            or primary.get("backup_format") != FORMAT
            or type(primary.get("master_proof_version")) is not int
            or primary["master_proof_version"] != proof_version(source)
            or not SHA.fullmatch(primary.get("master_proof_sha256", ""))
            or not SHA.fullmatch(primary.get("provider_scope_sha256", ""))
            or primary.get("master_auth_plaintext_sha256") != files[4].get("plaintext_sha256")
            or primary.get("master_dump_plaintext_sha256")
            != [f.get("plaintext_sha256") for f in files[1:4]]
            or not all(
                isinstance(f.get("plaintext_sha256"), str) and SHA.fullmatch(f["plaintext_sha256"])
                for f in files[1:]
            )
            or type(files[4].get("bytes")) is not int
            or not 0 < files[4]["bytes"] <= 256 * 1024 + 128
        ):
            return False
        from .backup import master_tables

        schema_scope = primary.get("master_schema_scope")
        if source == "gateway":
            validate_gateway_scope(schema_scope)
        elif schema_scope is not None:
            return False
        return primary.get("master_table_names") == sorted(
            master_tables(
                source, proof_version=4 if source == "gateway" else 3, schema_scope=schema_scope
            )
        )
    except (ArchiveBlocked, KeyError, TypeError, ValueError, IndexError):
        return False
